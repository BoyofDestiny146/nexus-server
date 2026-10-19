"""Bridging Care inbound M2M API (X-Client-Id + X-Client-Secret).

GET  /api/v1/integrations/bridgingcare/companions/{companionId}/assessment-config

Read-only. Dashboard JWT, Watcher X-API-Key, CareConnect partner credentials,
and outbound Bridging Care ingest X-Api-Key are not accepted. Does not
generate assessments, mutate schedule/profile, or enable automatic delivery.
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
from ..models import AiAgent
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
