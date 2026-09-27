"""Nexus Assessment Engine wrapper.

Resolve the agent's persisted Assessment Profile, then dispatch:

* care_wellness → existing ``triage.runner.run_for_agent`` (unchanged)
* sales_product → session-scoped Sales & Product Guide runner
* unimplemented / unknown → care_wellness

Cron continues to call ``run_for_all`` directly and never enters this module.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AiAgent, AiMedicalAssessment, CcAssessmentResult
from ..triage.runner import run_for_agent
from .profiles import CARE_WELLNESS_ID, SALES_PRODUCT_ID
from .sales_runner import run_sales_for_agent
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
) -> AssessmentRun | None:
    """Dispatch to the runner for the agent's active Assessment Profile."""
    agent = await db.get(AiAgent, agent_id)
    if agent is None:
        return None
    profile_id = resolved_definition(agent.profile_json).id
    log.info("assessment engine: agent=%s profile=%s", agent_id, profile_id)
    if profile_id == SALES_PRODUCT_ID:
        row = await run_sales_for_agent(db, agent_id, session_id=session_id)
        if row is None:
            return None
        return AssessmentRun(profile_id=SALES_PRODUCT_ID, generic=row)
    medical = await run_for_agent(db, agent_id, for_date)
    if medical is None:
        return None
    return AssessmentRun(profile_id=CARE_WELLNESS_ID, medical=medical)
