"""Nexus Assessment Engine wrapper.

Resolve the agent's persisted Assessment Profile, then dispatch:

* care_wellness → existing ``triage.runner.run_for_agent`` (unchanged)
* sales_product → session-scoped Sales & Product Guide runner
* unimplemented / unknown → care_wellness

The due-check scheduler calls ``run_for_agent`` directly for Care & Wellness
and never enters this module. Sales stays off the scheduler.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AiAgent, AiMedicalAssessment, CcAssessmentResult
from ..triage.runner import run_for_agent
from .profiles import CARE_WELLNESS_ID, SALES_PRODUCT_ID
from .run_lock import release_agent_lock, try_acquire_agent_lock
from .sales_runner import run_sales_for_agent
from .schedule import TRIGGER_MANUAL
from .storage import resolved_definition

log = logging.getLogger("assessment_engine")


@dataclass
class AssessmentRun:
    profile_id: str
    medical: AiMedicalAssessment | None = None
    generic: CcAssessmentResult | None = None


async def assess_agent(
    db: AsyncSession,
    agent_id: str,
    for_date: date | None = None,
    session_id: str | None = None,
    *,
    trigger_type: str | None = TRIGGER_MANUAL,
    trigger_message_id: int | None = None,
    scheduled_due_at: datetime | None = None,
) -> AssessmentRun | None:
    """Dispatch to the runner for the agent's active Assessment Profile."""
    agent = await db.get(AiAgent, agent_id)
    if agent is None:
        return None
    profile_id = resolved_definition(agent.profile_json).id
    log.info("assessment engine: agent=%s profile=%s trigger=%s", agent_id, profile_id, trigger_type)
    locked = await try_acquire_agent_lock(agent_id)
    if not locked:
        for _ in range(40):
            await asyncio.sleep(0.5)
            locked = await try_acquire_agent_lock(agent_id)
            if locked:
                break
    try:
        if not locked:
            log.warning("assessment engine: skip agent=%s reason=locked", agent_id)
            return None
        if profile_id == SALES_PRODUCT_ID:
            row = await run_sales_for_agent(db, agent_id, session_id=session_id)
            if row is None:
                return None
            return AssessmentRun(profile_id=SALES_PRODUCT_ID, generic=row)
        # Keep this call positional-only so existing monkeypatches (and the
        # source-lock tests) continue to match ``run_for_agent(db, agent_id, for_date)``.
        medical = await run_for_agent(db, agent_id, for_date)
        if medical is None:
            return None
        await _stamp_trigger_metadata(
            db,
            medical,
            trigger_type=trigger_type,
            trigger_message_id=trigger_message_id,
            scheduled_due_at=scheduled_due_at,
        )
        return AssessmentRun(profile_id=CARE_WELLNESS_ID, medical=medical)
    finally:
        if locked:
            await release_agent_lock(agent_id)


async def _stamp_trigger_metadata(
    db: AsyncSession,
    medical: AiMedicalAssessment,
    *,
    trigger_type: str | None,
    trigger_message_id: int | None,
    scheduled_due_at: datetime | None,
) -> None:
    """Fill nullable trigger columns after persist. Never required by the runner."""
    changed = False
    if trigger_type and not medical.trigger_type:
        medical.trigger_type = trigger_type
        changed = True
    if trigger_message_id is not None and medical.trigger_message_id is None:
        medical.trigger_message_id = trigger_message_id
        changed = True
    if scheduled_due_at is not None and medical.scheduled_due_at is None:
        medical.scheduled_due_at = scheduled_due_at
        changed = True
    if not changed:
        return
    try:
        from sqlalchemy import inspect as sa_inspect

        if sa_inspect(medical).persistent:
            await db.commit()
    except Exception:
        log.debug("assessment engine: trigger metadata stamp skipped", exc_info=True)
