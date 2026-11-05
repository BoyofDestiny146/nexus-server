"""Bridging Care inbound M2M API (X-Client-Id + X-Client-Secret).

GET  /api/v1/integrations/bridgingcare/companions/{companionId}/assessment-config
POST /api/v1/integrations/bridgingcare/sam/conversation
POST /api/v1/integrations/bridgingcare/sam/feedback
POST /api/v1/integrations/bridgingcare/sam/follow-up-response

Dashboard JWT, Watcher X-API-Key, CareConnect partner credentials,
and outbound Bridging Care ingest X-Api-Key are not accepted. Does not
generate assessments, mutate schedule/profile, or enable automatic delivery.
The Sam conversation does not write Watcher chat history or the
Phase 2A client discussion table. Completed answers can be stored
on cc_sam_session. Follow-up Yes/No is separate from session helpfulness.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..assessment_engine.schedule import schedule_view
from ..assessment_engine.storage import resolved_definition
from ..bridgingcare_delivery import bridgingcare_execute_enabled
from ..bridgingcare_inbound_auth import require_bridgingcare_inbound
from ..bridgingcare_mapping import resolve_verified_mapping_by_companion
from ..bridgingcare_payload import _as_utc_z
from ..db import get_db
from ..envelope import APIException
from ..knowledge import list_client_assignments
from ..knowledge_retrieval import semantic_search
from ..models import AiAgent
from ..sam_conversation import handle_sam_conversation
from ..sam_session import (
    record_completed_turn,
    record_feedback,
    record_follow_up_response,
    require_owned_session,
    session_claim_from_body,
)
from ..settings import settings


log = logging.getLogger("partner-bridgingcare")

router = APIRouter(prefix="/v1/integrations/bridgingcare", tags=["partner-bridgingcare"])

NOT_FOUND_MSG = "companion not found"

_SCHEDULE_KEYS = (
    "enabled",
    "mode",
    "intervalMinutes",
    "onlyIfNewData",
    "assessOnEscalationPhrases",
)


def _utc_or_none(value: datetime | None, tz: ZoneInfo) -> str | None:
    if value is None:
        return None
    return _as_utc_z(value, tz)


def _public_assessment_config(
    *,
    companion_id: str,
    agent: AiAgent,
    view: dict[str, Any],
    execute_enabled: bool,
) -> dict[str, Any]:
    """Project schedule_view + resolved profile. No catalog, delivery, or agent id."""
    defn = resolved_definition(agent.profile_json)
    schedule = view.get("assessmentSchedule") or {}
    tz_name = (getattr(settings, "tz", None) or "America/Chicago").strip() or "America/Chicago"
    tz = ZoneInfo(tz_name)
    return {
        "companionId": companion_id,
        "source": "nexus",
        "assessmentProfile": {
            "id": defn.id,
            "displayName": defn.displayName,
        },
        "assessmentSchedule": {key: schedule.get(key) for key in _SCHEDULE_KEYS},
        "lastAssessmentAt": _utc_or_none(view.get("lastAssessmentAt"), tz),
        "nextAssessmentAt": _utc_or_none(view.get("nextAssessmentAt"), tz),
        "bridgingCare": {
            "mapped": True,
            "executeEnabled": bool(execute_enabled),
        },
    }


@router.get("/companions/{companionId}/assessment-config", response_model=None)
async def get_companion_assessment_config(
    companionId: str,
    _inbound_id: str = Depends(require_bridgingcare_inbound),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    mapping = await resolve_verified_mapping_by_companion(db, companionId)
    if mapping is None:
        raise APIException(404, NOT_FOUND_MSG)
    agent = await db.get(AiAgent, mapping.agent_id)
    if agent is None:
        raise APIException(404, NOT_FOUND_MSG)
    view = await schedule_view(db, agent)
    log.info("bridgingcare assessment-config companion_id=%s", mapping.companion_id)
    return _public_assessment_config(
        companion_id=mapping.companion_id,
        agent=agent,
        view=view,
        execute_enabled=bridgingcare_execute_enabled(),
    )


@router.post("/sam/conversation", response_model=None)
async def sam_conversation(
    payload: dict[str, Any],
    _inbound_id: str = Depends(require_bridgingcare_inbound),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    async def search(ids: list[int], query: str) -> list[dict[str, Any]]:
        found = await semantic_search(db, knowledge_base_ids=ids, query=query)
        return list(found.get("results") or [])

    async def assignments() -> list[int]:
        rows = await list_client_assignments(db, settings.sam_client_id.strip())
        return [int(row["id"]) for row in rows if row.get("assignmentEnabled") is True]

    log.info("bridgingcare sam conversation")
    claim = session_claim_from_body(payload)
    virtual_agent_id = settings.sam_client_id.strip()
    if claim is not None and claim.session_id:
        await require_owned_session(db, claim, virtual_agent_id)
    result = await handle_sam_conversation(payload, search=search, assignments=assignments)
    if claim is None:
        return result
    question = payload.get("question") if isinstance(payload.get("question"), str) else ""
    try:
        stored = await record_completed_turn(
            db,
            claim,
            virtual_agent_id,
            question,
            str(result.get("message") or ""),
            str(result.get("followUpQuestion") or ""),
        )
        result["sessionId"] = stored.session_id
        result["turnId"] = stored.turn_id
    except APIException:
        raise
    except Exception:
        await db.rollback()
        log.exception("sam session persist failed")
    return result


@router.post("/sam/feedback", response_model=None)
async def sam_feedback(
    payload: dict[str, Any],
    _inbound_id: str = Depends(require_bridgingcare_inbound),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    claim = session_claim_from_body(payload)
    helpful = payload.get("helpful")
    if claim is None or not isinstance(helpful, bool):
        raise APIException(400, "invalid session")
    try:
        await record_feedback(db, claim, helpful)
    except APIException:
        raise
    except Exception:
        await db.rollback()
        log.exception("sam feedback persist failed")
        raise APIException(503, "feedback is unavailable") from None
    log.info("bridgingcare sam feedback")
    return {"recorded": True}


@router.post("/sam/follow-up-response", response_model=None)
async def sam_follow_up_response(
    payload: dict[str, Any],
    _inbound_id: str = Depends(require_bridgingcare_inbound),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Record Yes/No for Sam's follow-up on one turn. Not session helpfulness."""
    claim = session_claim_from_body(payload)
    response = payload.get("response")
    raw_turn = payload.get("turnId")
    turn_id = raw_turn if isinstance(raw_turn, int) else None
    if turn_id is None and isinstance(raw_turn, str) and raw_turn.strip().isdigit():
        turn_id = int(raw_turn.strip())
    if claim is None or not isinstance(response, bool) or turn_id is None:
        raise APIException(400, "invalid session")
    try:
        await record_follow_up_response(db, claim, turn_id, response)
    except APIException:
        raise
    except Exception:
        await db.rollback()
        log.exception("sam follow-up response persist failed")
        raise APIException(503, "follow-up response is unavailable") from None
    log.info("bridgingcare sam follow-up response")
    return {"recorded": True}
