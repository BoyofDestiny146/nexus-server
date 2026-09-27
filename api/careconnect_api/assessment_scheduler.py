"""Periodic due-check for per-client Care & Wellness assessments.

Replaces the global 02:00 ``run_for_all`` assumption. Sales stays off this
job. Each agent is evaluated independently; locking prevents double-runs.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from .assessment_engine.run_lock import release_agent_lock, try_acquire_agent_lock
from .assessment_engine.schedule import (
    ACTION_RUN,
    SKIP_NO_NEW_DATA,
    TRIGGER_SCHEDULED,
    evaluate_due,
)
from .db import async_session_factory
from .models import AiAgent
from .triage.runner import run_for_agent

log = logging.getLogger("assessment_scheduler")


async def tick_due_assessments() -> dict[str, int]:
    now = datetime.now()
    log.info("assessment due-check starting at %s", now)
    async with async_session_factory() as session:
        agent_ids = (await session.execute(select(AiAgent.id))).scalars().all()

    ran = 0
    skipped = 0
    errors = 0
    for agent_id in agent_ids:
        try:
            async with async_session_factory() as session:
                agent = await session.get(AiAgent, agent_id)
                if agent is None:
                    skipped += 1
                    continue
                decision = await evaluate_due(session, agent, now)
                action = decision["action"]
                if action != ACTION_RUN:
                    skipped += 1
                    if action == SKIP_NO_NEW_DATA:
                        log.info(
                            "assessment skip agent=%s reason=no_new_data next=%s",
                            agent_id,
                            decision.get("nextAssessmentAt"),
                        )
                    continue
                locked = await try_acquire_agent_lock(agent_id)
                if not locked:
                    skipped += 1
                    log.info("assessment skip agent=%s reason=locked", agent_id)
                    continue
                try:
                    row = await run_for_agent(
                        session,
                        agent_id,
                        trigger_type=TRIGGER_SCHEDULED,
                        scheduled_due_at=decision.get("scheduledDueAt"),
                    )
                    if row is not None:
                        ran += 1
                    else:
                        skipped += 1
                finally:
                    await release_agent_lock(agent_id)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.warning("assessment due-check failed agent=%s: %s", agent_id, exc)

    log.info(
        "assessment due-check finished ran=%d skipped=%d errors=%d",
        ran,
        skipped,
        errors,
    )
    return {"ran": ran, "skipped": skipped, "errors": errors}
