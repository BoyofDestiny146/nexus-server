"""Assessment read/regenerate endpoints.

Mounted at /api/agent/{agent_id}/assessment/* by main.py (parent prefix is
/api).

Care & Wellness compatibility
-----------------------------
``GET .../assessment/latest`` and ``GET .../assessment/history`` remain
medical-only (``ai_medical_assessment``). The Care & Wellness right rail
still consumes that shape.

Profile-aware endpoints
-----------------------
``GET .../assessment/current`` returns a normalized envelope for the
agent's persisted Assessment Profile (care medical payload or sales
``cc_assessment_result``). ``POST .../assessment/regenerate`` dispatches
from that same persisted profile — callers cannot pick a profile in the
request. Care regenerate still returns the medical serialize used by the
existing UI. Sales returns the generic envelope.

The engine wrapper calls ``run_for_agent`` for Care & Wellness and the
sales runner for Sales & Product Guide. Nightly cron does not enter this
router.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..assessment_engine.engine import assess_agent
from ..assessment_engine.profiles import (
    CARE_WELLNESS_ID,
    SALES_PRODUCT_ID,
    catalog_dicts,
    parse_selectable_profile_id,
)
from ..assessment_engine.sales_schema import sanitize_sales_payload
from ..assessment_engine.storage import apply_assessment_profile, apply_assessment_schedule, profile_payload, resolved_definition
from ..assessment_engine.schedule import parse_schedule_put, schedule_view
from ..auth import CurrentUser, get_current_user, require_root
from ..db import get_db
from ..envelope import APIException
from ..models import AiAgent, AiMedicalAssessment, CcAssessmentResult
from ..rbac import assert_can_access_agent


router = APIRouter(tags=["assessment"])


class AssessmentSchedulePut(BaseModel):
    enabled: bool | None = None
    mode: str | None = None
    intervalMinutes: int | None = None
    onlyIfNewData: bool | None = None


class AssessmentProfilePut(BaseModel):
    assessmentProfile: str | None = None
    assessmentSchedule: AssessmentSchedulePut | None = None


def _serialize(row: AiMedicalAssessment) -> dict[str, Any]:
    """Convert an AiMedicalAssessment ORM row to the camelCased dict the
    dashboard's MedicalAssessment / DecodedAssessment types consume."""
    try:
        concerns = json.loads(row.concerns_json or "[]")
    except (ValueError, TypeError):
        concerns = []
    try:
        recommendations = json.loads(row.recommendations_json or "[]")
    except (ValueError, TypeError):
        recommendations = []

    return {
        "id": row.id,
        "agentId": row.agent_id,
        "forDate": row.for_date.isoformat() if row.for_date else None,
        "riskLevel": row.risk_level,
        "confidence": float(row.confidence) if row.confidence is not None else None,
        "concerns": concerns,
        "recommendations": recommendations,
        "sourceMsgCount": row.source_msg_count,
        "llmModel": row.llm_model,
        "generatedAt": row.generated_at,
        "triggerType": row.trigger_type,
        "triggerMessageId": row.trigger_message_id,
        "scheduledDueAt": row.scheduled_due_at,
    }


def _serialize_generic(row: CcAssessmentResult) -> dict[str, Any]:
    try:
        raw = json.loads(row.payload_json or "{}")
    except (ValueError, TypeError):
        raw = {}
    payload = sanitize_sales_payload(raw)
    return {
        "profileId": row.profile_id,
        "sessionId": row.session_id,
        "generatedAt": row.generated_at,
        "sourceMsgCount": row.source_msg_count,
        "llmModel": row.llm_model,
        "payload": payload,
    }


async def _latest_medical(db: AsyncSession, agent_id: str) -> AiMedicalAssessment | None:
    return (
        await db.execute(
            select(AiMedicalAssessment)
            .where(AiMedicalAssessment.agent_id == agent_id)
            .order_by(
                AiMedicalAssessment.for_date.desc(),
                AiMedicalAssessment.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _latest_generic(
    db: AsyncSession,
    agent_id: str,
    profile_id: str,
    session_id: str | None = None,
) -> CcAssessmentResult | None:
    stmt = select(CcAssessmentResult).where(
        CcAssessmentResult.agent_id == agent_id,
        CcAssessmentResult.profile_id == profile_id,
    )
    if session_id:
        stmt = stmt.where(CcAssessmentResult.session_id == session_id)
    return (
        await db.execute(
            stmt.order_by(
                CcAssessmentResult.generated_at.desc(),
                CcAssessmentResult.id.desc(),
            ).limit(1)
        )
    ).scalar_one_or_none()


def _serialize_care_envelope(row: AiMedicalAssessment) -> dict[str, Any]:
    medical = _serialize(row)
    return {
        "profileId": CARE_WELLNESS_ID,
        "sessionId": None,
        "generatedAt": medical["generatedAt"],
        "sourceMsgCount": medical["sourceMsgCount"],
        "llmModel": medical["llmModel"],
        "payload": {
            "riskLevel": medical["riskLevel"],
            "confidence": medical["confidence"],
            "concerns": medical["concerns"],
            "recommendations": medical["recommendations"],
        },
    }


@router.get("/agent/{agent_id}/assessment/latest", response_model=None)
async def latest(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any] | None:
    """Most recent medical-risk assessment for this agent, or null."""
    await assert_can_access_agent(db, user, agent_id)

    row = await _latest_medical(db, agent_id)
    if row is None:
        return None
    return _serialize(row)


@router.get("/agent/{agent_id}/assessment/history", response_model=None)
async def history(
    agent_id: str,
    days: int = Query(default=14, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """History of assessments for the sparkline. Oldest first."""
    await assert_can_access_agent(db, user, agent_id)

    cutoff: date = date.today() - timedelta(days=days)
    rows = (
        await db.execute(
            select(AiMedicalAssessment)
            .where(
                AiMedicalAssessment.agent_id == agent_id,
                AiMedicalAssessment.for_date >= cutoff,
            )
            .order_by(
                AiMedicalAssessment.for_date.asc(),
                AiMedicalAssessment.id.asc(),
            )
        )
    ).scalars().all()

    return [_serialize(r) for r in rows]


@router.get("/assessment/profiles", response_model=None)
async def assessment_profiles(
    _user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Catalog of Assessment Profiles. Ids and display names come from the
    registry; unimplemented profiles are listed so the dashboard can mark
    them Coming soon without allowing selection."""
    return {"profiles": catalog_dicts()}


@router.get("/agent/{agent_id}/assessment/profile", response_model=None)
async def get_assessment_profile(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Resolved Assessment Profile for this agent plus the full catalog."""
    await assert_can_access_agent(db, user, agent_id)
    agent = await db.get(AiAgent, agent_id)
    if agent is None:
        raise APIException(404, f"agent {agent_id} not found")
    payload = profile_payload(agent.profile_json)
    payload.update(await schedule_view(db, agent))
    return payload


@router.put("/agent/{agent_id}/assessment/profile", response_model=None)
async def put_assessment_profile(
    agent_id: str,
    payload: AssessmentProfilePut,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Persist an Assessment Profile selection. Only implemented registry ids
    are accepted — the frontend cannot store arbitrary or Coming-soon ids."""
    await assert_can_access_agent(db, user, agent_id)
    if payload.assessmentProfile is None and payload.assessmentSchedule is None:
        raise APIException(400, "assessmentProfile or assessmentSchedule is required")
    agent = await db.get(AiAgent, agent_id)
    if agent is None:
        raise APIException(404, f"agent {agent_id} not found")
    if payload.assessmentProfile is not None:
        profile_id = parse_selectable_profile_id(payload.assessmentProfile)
        apply_assessment_profile(agent, profile_id)
    if payload.assessmentSchedule is not None:
        profile_id = resolved_definition(agent.profile_json).id
        schedule = parse_schedule_put(
            payload.assessmentSchedule.model_dump(exclude_none=True),
            profile_id=profile_id,
        )
        apply_assessment_schedule(agent, schedule)
    await db.commit()
    await db.refresh(agent)
    out = profile_payload(agent.profile_json)
    out.update(await schedule_view(db, agent))
    return out


@router.get("/agent/{agent_id}/assessment/current", response_model=None)
async def current(
    agent_id: str,
    session_id: str | None = Query(default=None, alias="sessionId"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any] | None:
    """Latest result for the agent's persisted Assessment Profile.

    Care & Wellness → medical envelope (does not read ``cc_assessment_result``).
    Sales & Product Guide → generic envelope (does not read medical rows).
    Optional ``sessionId`` scopes sales lookup to that session only.
    """
    await assert_can_access_agent(db, user, agent_id)
    agent = await db.get(AiAgent, agent_id)
    if agent is None:
        raise APIException(404, f"agent {agent_id} not found")
    profile_id = resolved_definition(agent.profile_json).id
    if profile_id == SALES_PRODUCT_ID:
        sid = (session_id or "").strip() or None
        row = await _latest_generic(db, agent_id, SALES_PRODUCT_ID, sid)
        if row is None:
            return None
        return _serialize_generic(row)
    medical = await _latest_medical(db, agent_id)
    if medical is None:
        return None
    return _serialize_care_envelope(medical)


@router.post("/agent/{agent_id}/assessment/regenerate", response_model=None)
async def regenerate(
    agent_id: str,
    session_id: str | None = Query(default=None, alias="sessionId"),
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_root),
) -> dict[str, Any]:
    """Recompute assessment for the agent's persisted profile. Root only.

    Profile is never taken from the request body. Care & Wellness still
    returns the medical serialize the existing UI expects. Sales returns
    the generic envelope and uses the active/latest session (optional
    ``sessionId`` must belong to this agent).
    """
    run = await assess_agent(db, agent_id, session_id=session_id)
    if run is None:
        raise APIException(404, f"agent {agent_id} not found")
    if run.profile_id == SALES_PRODUCT_ID:
        if run.generic is None:
            raise APIException(404, "sales assessment could not be generated")
        return _serialize_generic(run.generic)
    if run.medical is None:
        raise APIException(404, "assessment could not be generated")
    return _serialize(run.medical)
