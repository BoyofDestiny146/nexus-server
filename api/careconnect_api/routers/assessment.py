"""Medical-risk assessment read endpoints (Phase 2/5 port).

Mounted at /api/agent/{agent_id}/assessment/* by main.py (parent prefix is
/api). The Java original lives at:
  xiaozhi.modules.agent.controller.MedicalAssessmentController

All read endpoints honor per-admin client scoping via
``rbac.assert_can_access_agent``. The ``regenerate`` endpoint synchronously
re-runs the triage compute for one agent (root only) — see
:mod:`careconnect_api.triage.runner`.

Field mapping notes
-------------------
The ai_medical_assessment table stores concerns/recommendations as TEXT
columns containing a JSON-encoded array of strings. We parse them
server-side and return real Python lists; the dashboard's
DecodedAssessment shape consumes ``concerns`` / ``recommendations``
directly. (The legacy ``concernsJson`` / ``recommendationsJson`` raw
strings are no longer needed downstream.)
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, get_current_user, require_root
from ..db import get_db
from ..envelope import APIException
from ..models import AiMedicalAssessment
from ..rbac import assert_can_access_agent
from ..triage.runner import run_for_agent


router = APIRouter(tags=["assessment"])


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
    }


@router.get("/agent/{agent_id}/assessment/latest", response_model=None)
async def latest(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any] | None:
    """Most recent medical-risk assessment for this agent, or null."""
    await assert_can_access_agent(db, user, agent_id)

    row = (
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


@router.post("/agent/{agent_id}/assessment/regenerate", response_model=None)
async def regenerate(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_root),
) -> dict[str, Any]:
    """Synchronously recompute today's triage assessment for one agent and
    return the freshly-inserted row. Root only. May be slow (~20-30s) if the
    Ollama qwen2.5:7b model is cold."""
    row = await run_for_agent(db, agent_id)
    if row is None:
        raise APIException(404, f"agent {agent_id} not found")
    return _serialize(row)
