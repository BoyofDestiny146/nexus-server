"""Fire-and-forget Care & Wellness assessment after a CLIENT escalation match.

Never raises into the chat notify path. Sales-active clients are not assessed
here — that would mix a Care row into a Sales-profile UI without a documented
cross-profile contract.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .client_profile import load_profile
from .db import async_session_factory
from .models import AiAgent
from .assessment_engine.escalation_match import (
    configured_phrases,
    is_client_originated_text,
    matching_escalation_phrases,
)
from .assessment_engine.profiles import CARE_WELLNESS_ID
from .assessment_engine.storage import resolved_definition
from .assessment_engine.run_lock import (
    claim_escalation_message,
    release_agent_lock,
    try_acquire_agent_lock,
)
from .assessment_engine.schedule import TRIGGER_ESCALATION, assess_on_escalation_phrases
from .triage.runner import run_for_agent

log = logging.getLogger("assessment_escalation")


def maybe_queue_escalation_assessment(
    *,
    agent_id: str,
    chat_type: int | None,
    content: str,
    message_id: int | None,
) -> None:
    """Schedule a background task. Safe to call from the notify handler."""
    if not is_client_originated_text(chat_type, content):
        return
    if not content or not str(content).strip():
        return
    if message_id is None:
        log.info("escalation skip agent=%s reason=missing_message_id", agent_id)
        return
    try:
        asyncio.get_running_loop().create_task(
            _escalation_task(agent_id, str(content), int(message_id)),
            name=f"escalation-assessment-{agent_id}-{message_id}",
        )
    except Exception:
        log.warning("escalation queue failed agent=%s", agent_id, extra={"agent_id": agent_id})


async def _escalation_task(agent_id: str, content: str, message_id: int) -> None:
    locked = False
    try:
        locked = await try_acquire_agent_lock(agent_id)
        if not locked:
            for _ in range(40):
                await asyncio.sleep(0.5)
                locked = await try_acquire_agent_lock(agent_id)
                if locked:
                    break
        if not locked:
            log.warning("escalation skip agent=%s message=%s reason=locked", agent_id, message_id)
            return
        async with async_session_factory() as session:
            agent = await session.get(AiAgent, agent_id)
            if agent is None:
                return
            profile_id = resolved_definition(agent.profile_json).id
            if profile_id != CARE_WELLNESS_ID:
                log.info(
                    "escalation skip agent=%s profile=%s reason=care_wellness_only",
                    agent_id,
                    profile_id,
                )
                return
            if not assess_on_escalation_phrases(agent.profile_json):
                log.info(
                    "escalation skip agent=%s message=%s reason=assess_on_escalation_disabled",
                    agent_id,
                    message_id,
                )
                return
            phrases = configured_phrases(load_profile(agent.profile_json).get("escalationPhrases"))
            hits = matching_escalation_phrases(content, phrases)
            if not hits:
                return
            claimed = await claim_escalation_message(agent_id, message_id)
            if not claimed:
                log.info("escalation skip agent=%s message=%s reason=duplicate", agent_id, message_id)
                return
            log.info(
                "escalation assessment agent=%s message=%s phrases=%d",
                agent_id,
                message_id,
                len(hits),
            )
            await run_for_agent(
                session,
                agent_id,
                trigger_type=TRIGGER_ESCALATION,
                trigger_message_id=message_id,
            )
    except Exception:
        log.warning("escalation assessment failed agent=%s message=%s", agent_id, message_id)
    finally:
        if locked:
            await release_agent_lock(agent_id)


def escalation_decision(
    *,
    chat_type: int | None,
    content: str,
    phrases: list[str],
    profile_id: str,
    assess_on_escalation: bool = True,
) -> dict[str, Any]:
    """Pure helper for tests: should this CLIENT turn run a Care assessment?

    Phrase matching is independent of ``assess_on_escalation``. When the
    flag is false, Watcher/guardrail behavior may still use the phrases,
    but this path does not queue an assessment.
    """
    if not is_client_originated_text(chat_type, content):
        return {"run": False, "reason": "not_client"}
    if profile_id != CARE_WELLNESS_ID:
        return {"run": False, "reason": "care_wellness_only"}
    hits = matching_escalation_phrases(content, phrases)
    if not hits:
        return {"run": False, "reason": "no_match"}
    if not assess_on_escalation:
        return {"run": False, "reason": "assess_on_escalation_disabled", "phrases": hits}
    return {"run": True, "reason": TRIGGER_ESCALATION, "phrases": hits}
