"""Nexus Assessment Engine wrapper around the existing Care & Wellness runner.

Phase 1 resolve path:

    assess_agent
        → load agent.profile_json.assessmentProfile
        → registry (unknown / unimplemented → care_wellness)
        → triage.runner.run_for_agent

``run_for_agent`` is called unchanged. Cron continues to call ``run_for_all``
directly so the nightly path is byte-identical.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AiAgent, AiMedicalAssessment
from ..triage.runner import run_for_agent
from .profiles import CARE_WELLNESS_ID
from .storage import resolved_definition

log = logging.getLogger("assessment_engine")


async def assess_agent(
    db: AsyncSession,
    agent_id: str,
    for_date: date | None = None,
) -> AiMedicalAssessment | None:
    """Resolve the agent's Assessment Profile, then run Care & Wellness.

    Unimplemented and unknown stored values cannot activate a different
    engine: they resolve to ``care_wellness`` and still call ``run_for_agent``.
    """
    agent = await db.get(AiAgent, agent_id)
    profile_id = CARE_WELLNESS_ID
    if agent is not None:
        profile_id = resolved_definition(agent.profile_json).id
    if profile_id != CARE_WELLNESS_ID:
        log.warning(
            "assessment engine: refusing unimplemented profile %s for agent %s",
            profile_id,
            agent_id,
        )
        profile_id = CARE_WELLNESS_ID
    log.info("assessment engine: agent=%s profile=%s", agent_id, profile_id)
    return await run_for_agent(db, agent_id, for_date)
