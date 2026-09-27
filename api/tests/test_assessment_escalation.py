"""Escalation-phrase assessment trigger: CLIENT-only, one run per message."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.assessment_engine.escalation_match import matching_escalation_phrases
from careconnect_api.assessment_engine.profiles import CARE_WELLNESS_ID, SALES_PRODUCT_ID
from careconnect_api.assessment_engine.run_lock import reset_locks_for_tests
from careconnect_api.assessment_escalation import _escalation_task, escalation_decision
from careconnect_api.chat_events import CHAT_TYPE_CAREGIVER, CHAT_TYPE_CLIENT, CHAT_TYPE_SYSTEM
from careconnect_api.client_profile import dump_profile
from careconnect_api.models import AiAgent, AiAgentChatHistory, AiMedicalAssessment
from careconnect_api.settings import settings
from careconnect_api.triage.runner import TriageResult


PHRASES = ["I fell", "chest pain"]


def test_client_phrase_matches_once_across_multiple_phrases():
    hits = matching_escalation_phrases("I fell and have chest pain", PHRASES)
    assert hits == ["I fell", "chest pain"]
    decision = escalation_decision(
        chat_type=CHAT_TYPE_CLIENT,
        content="I fell and have chest pain",
        phrases=PHRASES,
        profile_id=CARE_WELLNESS_ID,
    )
    assert decision["run"] is True
    assert decision["reason"] == "escalation_phrase"


def test_caregiver_and_system_text_do_not_trigger():
    for chat_type, content in (
        (CHAT_TYPE_CAREGIVER, "I fell"),
        (CHAT_TYPE_SYSTEM, "[[revel]] I fell"),
        (3, "[[gcal]] chest pain reminder"),
        (CHAT_TYPE_CLIENT, "hello there"),
        (CHAT_TYPE_CLIENT, '[[revel]] {"kind":"context"} I fell'),
        (CHAT_TYPE_CLIENT, "[[gcal]] chest pain reminder"),
    ):
        decision = escalation_decision(
            chat_type=chat_type,
            content=content,
            phrases=PHRASES,
            profile_id=CARE_WELLNESS_ID,
        )
        assert decision["run"] is False


def test_sales_profile_does_not_run_sales_or_silent_care_cross_profile():
    decision = escalation_decision(
        chat_type=CHAT_TYPE_CLIENT,
        content="I fell",
        phrases=PHRASES,
        profile_id=SALES_PRODUCT_ID,
    )
    assert decision["run"] is False
    assert decision["reason"] == "care_wellness_only"


def test_escalation_flag_off_matches_but_does_not_queue_assessment():
    decision = escalation_decision(
        chat_type=CHAT_TYPE_CLIENT,
        content="I fell and have chest pain",
        phrases=PHRASES,
        profile_id=CARE_WELLNESS_ID,
        assess_on_escalation=False,
    )
    assert decision["run"] is False
    assert decision["reason"] == "assess_on_escalation_disabled"
    assert decision["phrases"] == ["I fell", "chest pain"]
    still_client_only = escalation_decision(
        chat_type=CHAT_TYPE_CAREGIVER,
        content="I fell",
        phrases=PHRASES,
        profile_id=CARE_WELLNESS_ID,
        assess_on_escalation=True,
    )
    assert still_client_only["run"] is False


def test_guardrail_phrases_are_independent_of_assessment_toggle():
    from pathlib import Path

    render = (
        Path(__file__).resolve().parents[1]
        / "careconnect_api"
        / "personalities"
        / "render.py"
    ).read_text()
    match = (
        Path(__file__).resolve().parents[1]
        / "careconnect_api"
        / "assessment_engine"
        / "escalation_match.py"
    ).read_text()
    assert "escalationPhrases" in render
    assert "assessOnEscalationPhrases" not in render
    assert "is_client_originated_text" in match
    assert "chat_type != 1" in match



@pytest.mark.asyncio
async def test_same_message_cannot_trigger_twice(db_engine, db_session: AsyncSession, monkeypatch):
    reset_locks_for_tests()
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)
    monkeypatch.setattr("careconnect_api.assessment_escalation.async_session_factory", factory)
    agent = AiAgent(
        id="agt_esc",
        agent_name="Esc",
        profile_json=dump_profile({
            "assessmentProfile": CARE_WELLNESS_ID,
            "escalationPhrases": PHRASES,
        }),
    )
    db_session.add(agent)
    db_session.add(
        AiAgentChatHistory(
            id=42,
            agent_id="agt_esc",
            session_id="s1",
            chat_type=CHAT_TYPE_CLIENT,
            content="I fell in the kitchen",
            created_at=datetime.now(),
        )
    )
    await db_session.commit()

    calls: list[tuple] = []

    async def fake_run(db, agent_id, for_date=None, **kwargs):
        calls.append((agent_id, kwargs.get("trigger_type"), kwargs.get("trigger_message_id")))
        row = AiMedicalAssessment(
            agent_id=agent_id,
            for_date=datetime.now().date(),
            risk_level="elevated",
            confidence=Decimal("0.800"),
            generated_at=datetime.now(),
            trigger_type=kwargs.get("trigger_type"),
            trigger_message_id=kwargs.get("trigger_message_id"),
        )
        db.add(row)
        await db.commit()
        return row

    monkeypatch.setattr("careconnect_api.assessment_escalation.run_for_agent", fake_run)
    await _escalation_task("agt_esc", "I fell in the kitchen", 42)
    await _escalation_task("agt_esc", "I fell in the kitchen", 42)
    assert len(calls) == 1
    assert calls[0] == ("agt_esc", "escalation_phrase", 42)


@pytest.mark.asyncio
async def test_assess_on_escalation_false_skips_run_and_does_not_consume_claim(
    db_engine, db_session: AsyncSession, monkeypatch
):
    reset_locks_for_tests()
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)
    monkeypatch.setattr("careconnect_api.assessment_escalation.async_session_factory", factory)
    from careconnect_api.assessment_engine.schedule import apply_assessment_schedule

    agent = AiAgent(
        id="agt_esc_off",
        agent_name="EscOff",
        profile_json=dump_profile({
            "assessmentProfile": CARE_WELLNESS_ID,
            "escalationPhrases": PHRASES,
        }),
    )
    apply_assessment_schedule(
        agent,
        {
            "enabled": True,
            "mode": "interval",
            "intervalMinutes": 1440,
            "onlyIfNewData": True,
            "assessOnEscalationPhrases": False,
        },
    )
    db_session.add(agent)
    db_session.add(
        AiAgentChatHistory(
            id=77,
            agent_id="agt_esc_off",
            session_id="s1",
            chat_type=CHAT_TYPE_CLIENT,
            content="I fell in the kitchen",
            created_at=datetime.now(),
        )
    )
    await db_session.commit()

    calls: list[tuple] = []

    async def fake_run(db, agent_id, for_date=None, **kwargs):
        calls.append((agent_id, kwargs.get("trigger_type"), kwargs.get("trigger_message_id")))
        return None

    monkeypatch.setattr("careconnect_api.assessment_escalation.run_for_agent", fake_run)
    await _escalation_task("agt_esc_off", "I fell in the kitchen", 77)
    assert calls == []

    db_session.expire_all()
    stored_agent = await db_session.get(AiAgent, "agt_esc_off")
    assert stored_agent is not None
    apply_assessment_schedule(stored_agent, {"assessOnEscalationPhrases": True})
    await db_session.commit()
    await _escalation_task("agt_esc_off", "I fell in the kitchen", 77)
    assert calls == [("agt_esc_off", "escalation_phrase", 77)]


@pytest.mark.asyncio
async def test_assess_on_escalation_true_permits_current_path(
    db_engine, db_session: AsyncSession, monkeypatch
):
    reset_locks_for_tests()
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)
    monkeypatch.setattr("careconnect_api.assessment_escalation.async_session_factory", factory)
    agent = AiAgent(
        id="agt_esc_on",
        agent_name="EscOn",
        profile_json=dump_profile({
            "assessmentProfile": CARE_WELLNESS_ID,
            "escalationPhrases": PHRASES,
        }),
    )
    db_session.add(agent)
    await db_session.commit()
    calls: list[str] = []

    async def fake_run(db, agent_id, for_date=None, **kwargs):
        calls.append(agent_id)
        return None

    monkeypatch.setattr("careconnect_api.assessment_escalation.run_for_agent", fake_run)
    await _escalation_task("agt_esc_on", "I fell", 88)
    assert calls == ["agt_esc_on"]


@pytest.mark.asyncio
async def test_escalation_failure_does_not_raise(monkeypatch):
    reset_locks_for_tests()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("careconnect_api.assessment_escalation.run_for_agent", boom)
    await _escalation_task("missing-agent", "I fell", 99)


@pytest.mark.asyncio
async def test_notify_still_publishes_when_escalation_queued(client: AsyncClient, monkeypatch):
    reset_locks_for_tests()
    queued: list[dict] = []

    def fake_queue(**kwargs):
        queued.append(kwargs)

    monkeypatch.setattr(
        "careconnect_api.routers.internal.maybe_queue_escalation_assessment",
        fake_queue,
    )
    monkeypatch.setattr(
        "careconnect_api.routers.internal.publish_chat_turn",
        _async_publish,
    )
    resp = await client.post(
        "/api/internal/notify/chat-turn",
        headers={"X-Internal-Token": settings.internal_token},
        json={
            "agentId": "agt_esc",
            "sessionId": "s1",
            "chatType": 1,
            "content": "I fell",
            "id": 7,
        },
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert queued and queued[0]["chat_type"] == 1
    assert queued[0]["message_id"] == 7


def test_maybe_queue_only_runs_for_client_text(monkeypatch):
    created: list[str] = []

    class _Loop:
        def create_task(self, coro, name=None):
            created.append(name or "")
            coro.close()
            return None

    monkeypatch.setattr(
        "careconnect_api.assessment_escalation.asyncio.get_running_loop",
        lambda: _Loop(),
    )
    from careconnect_api.assessment_escalation import maybe_queue_escalation_assessment

    maybe_queue_escalation_assessment(agent_id="agt_esc", chat_type=2, content="I fell", message_id=8)
    maybe_queue_escalation_assessment(agent_id="agt_esc", chat_type=3, content="I fell", message_id=9)
    maybe_queue_escalation_assessment(
        agent_id="agt_esc", chat_type=1, content='[[revel]] {"kind":"context"} I fell', message_id=10
    )
    assert created == []
    maybe_queue_escalation_assessment(agent_id="agt_esc", chat_type=1, content="I fell", message_id=11)
    assert created == ["escalation-assessment-agt_esc-11"]


async def _async_publish(agent_id, payload):
    return 1


@pytest.mark.asyncio
async def test_triggering_message_is_in_runner_window(db_session: AsyncSession, monkeypatch):
    agent = AiAgent(id="agt_win", agent_name="Win", profile_json=dump_profile({}))
    db_session.add(agent)
    db_session.add(
        AiAgentChatHistory(
            id=1,
            agent_id="agt_win",
            session_id="s1",
            chat_type=CHAT_TYPE_CLIENT,
            content="I fell just now",
            created_at=datetime.now(),
        )
    )
    await db_session.commit()
    captured: list[str] = []

    async def fake_llm(dialogue: str):
        captured.append(dialogue)
        return TriageResult("elevated", 0.7, ["fall"], [])

    monkeypatch.setattr("careconnect_api.triage.runner._invoke_llm", fake_llm)
    from careconnect_api.triage.runner import run_for_agent

    row = await run_for_agent(db_session, "agt_win", trigger_type="escalation_phrase", trigger_message_id=1)
    assert row is not None
    assert captured and "I fell just now" in captured[0]
    assert row.trigger_type == "escalation_phrase"
