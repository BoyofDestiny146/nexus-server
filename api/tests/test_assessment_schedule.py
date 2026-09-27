"""Per-client assessment schedule defaults, due-check, and locking."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.assessment_engine.profiles import CARE_WELLNESS_ID, SALES_PRODUCT_ID
from careconnect_api.assessment_engine.run_lock import (
    release_agent_lock,
    reset_locks_for_tests,
    try_acquire_agent_lock,
)
from careconnect_api.assessment_engine.schedule import (
    ACTION_RUN,
    DEFAULT_CARE_INTERVAL_MINUTES,
    INTERVAL_MINUTES,
    SKIP_MANUAL,
    SKIP_NO_NEW_DATA,
    SKIP_NOT_DUE,
    SKIP_UNSUPPORTED,
    apply_assessment_schedule,
    default_schedule,
    evaluate_due,
    has_meaningful_new_data,
    parse_schedule,
    parse_schedule_put,
    resolved_schedule,
)
from careconnect_api.assessment_engine.storage import apply_assessment_profile
from careconnect_api.chat_events import CHAT_TYPE_CAREGIVER, CHAT_TYPE_CLIENT, CHAT_TYPE_SYSTEM, REVEL_MARKER
from careconnect_api.client_profile import dump_profile
from careconnect_api.envelope import APIException
from careconnect_api.models import AiAgent, AiAgentChatHistory, AiMedicalAssessment


def test_care_default_is_every_24_hours():
    care = default_schedule(CARE_WELLNESS_ID)
    assert care["enabled"] is True
    assert care["mode"] == "interval"
    assert care["intervalMinutes"] == DEFAULT_CARE_INTERVAL_MINUTES
    assert care["onlyIfNewData"] is True
    missing = resolved_schedule(None)
    assert missing["intervalMinutes"] == 1440


def test_sales_default_is_manual():
    sales = default_schedule(SALES_PRODUCT_ID)
    assert sales["enabled"] is False
    assert sales["mode"] == "manual"
    assert sales["intervalMinutes"] is None
    assert parse_schedule({"enabled": True, "intervalMinutes": 60}, profile_id=SALES_PRODUCT_ID)[
        "mode"
    ] == "manual"


def test_supported_intervals_persist():
    assert INTERVAL_MINUTES == (60, 120, 240, 480, 720, 1440, 4320, 10080)
    for minutes in INTERVAL_MINUTES:
        parsed = parse_schedule_put(
            {"enabled": True, "mode": "interval", "intervalMinutes": minutes, "onlyIfNewData": True},
            profile_id=CARE_WELLNESS_ID,
        )
        assert parsed["intervalMinutes"] == minutes
    with pytest.raises(APIException):
        parse_schedule_put({"cron": "0 2 * * *"}, profile_id=CARE_WELLNESS_ID)
    with pytest.raises(APIException):
        parse_schedule_put({"intervalMinutes": 7}, profile_id=CARE_WELLNESS_ID)


@pytest.mark.asyncio
async def test_due_and_skip_paths(db_session: AsyncSession):
    reset_locks_for_tests()
    now = datetime(2026, 9, 27, 12, 0, 0)
    care = AiAgent(id="agt_care", agent_name="Care", profile_json=dump_profile({"assessmentProfile": CARE_WELLNESS_ID}))
    sales = AiAgent(id="agt_sales", agent_name="Sales", profile_json=dump_profile({"assessmentProfile": SALES_PRODUCT_ID}))
    manual = AiAgent(id="agt_manual", agent_name="Manual", profile_json=dump_profile({"assessmentProfile": CARE_WELLNESS_ID}))
    apply_assessment_schedule(manual, {"enabled": False, "mode": "manual", "intervalMinutes": None, "onlyIfNewData": True})
    db_session.add_all([care, sales, manual])
    await db_session.commit()

    due = await evaluate_due(db_session, care, now)
    assert due["action"] == ACTION_RUN

    sales_decision = await evaluate_due(db_session, sales, now)
    assert sales_decision["action"] == SKIP_UNSUPPORTED

    manual_decision = await evaluate_due(db_session, manual, now)
    assert manual_decision["action"] == SKIP_MANUAL

    db_session.add(
        AiMedicalAssessment(
            id=1,
            agent_id="agt_care",
            for_date=now.date(),
            risk_level="low",
            confidence=Decimal("0.100"),
            generated_at=now - timedelta(hours=1),
        )
    )
    await db_session.commit()
    not_due = await evaluate_due(db_session, care, now)
    assert not_due["action"] == SKIP_NOT_DUE


@pytest.mark.asyncio
async def test_only_if_new_data_skips_until_chat(db_session: AsyncSession):
    now = datetime(2026, 9, 27, 12, 0, 0)
    agent = AiAgent(id="agt_new", agent_name="New", profile_json=dump_profile({"assessmentProfile": CARE_WELLNESS_ID}))
    apply_assessment_profile(agent, CARE_WELLNESS_ID)
    apply_assessment_schedule(
        agent,
        {"enabled": True, "mode": "interval", "intervalMinutes": 60, "onlyIfNewData": True},
    )
    db_session.add(agent)
    last = now - timedelta(hours=2)
    db_session.add(
        AiMedicalAssessment(
            id=11,
            agent_id="agt_new",
            for_date=last.date(),
            risk_level="low",
            confidence=Decimal("0.100"),
            generated_at=last,
        )
    )
    await db_session.commit()

    skipped = await evaluate_due(db_session, agent, now)
    assert skipped["action"] == SKIP_NO_NEW_DATA
    assert await has_meaningful_new_data(db_session, "agt_new", last) is False

    db_session.add(
        AiAgentChatHistory(
            id=21,
            agent_id="agt_new",
            session_id="s1",
            chat_type=CHAT_TYPE_CLIENT,
            content="Good morning",
            created_at=now - timedelta(minutes=5),
        )
    )
    await db_session.commit()
    ran = await evaluate_due(db_session, agent, now)
    assert ran["action"] == ACTION_RUN


@pytest.mark.asyncio
async def test_revel_and_caregiver_count_as_new_data(db_session: AsyncSession):
    since = datetime(2026, 9, 27, 8, 0, 0)
    agent = AiAgent(id="agt_ctx", agent_name="Ctx", profile_json="{}")
    db_session.add(agent)
    db_session.add(
        AiAgentChatHistory(
            id=31,
            agent_id="agt_ctx",
            session_id="s1",
            chat_type=CHAT_TYPE_SYSTEM,
            content=f"{REVEL_MARKER} {{\"kind\":\"context\"}}",
            created_at=datetime(2026, 9, 27, 9, 0, 0),
        )
    )
    await db_session.commit()
    assert await has_meaningful_new_data(db_session, "agt_ctx", since) is True

    db_session.add(
        AiAgentChatHistory(
            id=32,
            agent_id="agt_caregiver",
            session_id="s1",
            chat_type=CHAT_TYPE_CAREGIVER,
            content="I am the assistant",
            created_at=datetime(2026, 9, 27, 9, 0, 0),
        )
    )
    agent2 = AiAgent(id="agt_caregiver", agent_name="Giver", profile_json="{}")
    db_session.add(agent2)
    await db_session.commit()
    assert await has_meaningful_new_data(db_session, "agt_caregiver", since) is True


@pytest.mark.asyncio
async def test_same_client_cannot_double_run_lock():
    reset_locks_for_tests()
    assert await try_acquire_agent_lock("agt_lock") is True
    assert await try_acquire_agent_lock("agt_lock") is False
    await release_agent_lock("agt_lock")
    assert await try_acquire_agent_lock("agt_lock") is True
    await release_agent_lock("agt_lock")


@pytest.mark.asyncio
async def test_tick_runs_due_care_and_skips_sales(db_engine, db_session: AsyncSession, monkeypatch):
    reset_locks_for_tests()
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)
    monkeypatch.setattr("careconnect_api.assessment_scheduler.async_session_factory", factory)

    care = AiAgent(id="agt_tick_care", agent_name="Care", profile_json=dump_profile({"assessmentProfile": CARE_WELLNESS_ID}))
    sales = AiAgent(id="agt_tick_sales", agent_name="Sales", profile_json=dump_profile({"assessmentProfile": SALES_PRODUCT_ID}))
    db_session.add_all([care, sales])
    await db_session.commit()

    ran: list[str] = []

    async def fake_run(db, agent_id, for_date=None, **kwargs):
        ran.append(agent_id)
        return AiMedicalAssessment(
            agent_id=agent_id,
            for_date=datetime.now().date(),
            risk_level="low",
            confidence=Decimal("0.100"),
            generated_at=datetime.now(),
            trigger_type=kwargs.get("trigger_type"),
        )

    monkeypatch.setattr("careconnect_api.assessment_scheduler.run_for_agent", fake_run)
    from careconnect_api.assessment_scheduler import tick_due_assessments

    result = await tick_due_assessments()
    assert "agt_tick_care" in ran
    assert "agt_tick_sales" not in ran
    assert result["ran"] >= 1


@pytest.mark.asyncio
async def test_tick_skips_when_only_if_new_data_and_no_chat(db_engine, db_session: AsyncSession, monkeypatch):
    reset_locks_for_tests()
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)
    monkeypatch.setattr("careconnect_api.assessment_scheduler.async_session_factory", factory)
    now = datetime.now()
    last = now - timedelta(hours=48)
    agent = AiAgent(
        id="agt_stale",
        agent_name="Stale",
        profile_json=dump_profile({"assessmentProfile": CARE_WELLNESS_ID}),
    )
    apply_assessment_schedule(
        agent,
        {"enabled": True, "mode": "interval", "intervalMinutes": 60, "onlyIfNewData": True},
    )
    db_session.add(agent)
    db_session.add(
        AiMedicalAssessment(
            id=88,
            agent_id="agt_stale",
            for_date=last.date(),
            risk_level="low",
            confidence=Decimal("0.100"),
            generated_at=last,
        )
    )
    await db_session.commit()
    ran: list[str] = []

    async def fake_run(db, agent_id, for_date=None, **kwargs):
        ran.append(agent_id)
        return None

    monkeypatch.setattr("careconnect_api.assessment_scheduler.run_for_agent", fake_run)
    from careconnect_api.assessment_scheduler import tick_due_assessments
    from sqlalchemy import select as sel

    before = (
        await db_session.execute(sel(AiMedicalAssessment).where(AiMedicalAssessment.agent_id == "agt_stale"))
    ).scalars().all()
    result = await tick_due_assessments()
    after = (
        await db_session.execute(sel(AiMedicalAssessment).where(AiMedicalAssessment.agent_id == "agt_stale"))
    ).scalars().all()
    assert ran == []
    assert result["skipped"] >= 1
    assert len(after) == len(before) == 1


@pytest.mark.asyncio
async def test_care_persist_publishes_assessment_updated(db_session: AsyncSession, monkeypatch):
    published: list[tuple] = []

    async def fake_pub(agent_id, *, profile_id, generated_at):
        published.append((agent_id, profile_id))

    async def fake_llm(dialogue: str):
        from careconnect_api.triage.runner import TriageResult
        return TriageResult("low", 0.2, [], [])

    monkeypatch.setattr("careconnect_api.assessment_engine.notify.publish_assessment_run", fake_pub)
    monkeypatch.setattr("careconnect_api.triage.runner._invoke_llm", fake_llm)
    agent = AiAgent(id="agt_pub", agent_name="Pub", profile_json=dump_profile({}))
    db_session.add(agent)
    await db_session.commit()
    from careconnect_api.triage.runner import run_for_agent

    row = await run_for_agent(db_session, "agt_pub", trigger_type="manual")
    assert row is not None
    assert published == [("agt_pub", CARE_WELLNESS_ID)]
    assert row.trigger_type == "manual"


@pytest.mark.asyncio
async def test_scheduled_and_escalation_persist_publish(db_session: AsyncSession, monkeypatch):
    published: list[str] = []

    async def fake_pub(agent_id, *, profile_id, generated_at):
        published.append(profile_id)
        assert set({"agentId": agent_id, "profileId": profile_id, "generatedAt": generated_at}.keys()) == {
            "agentId",
            "profileId",
            "generatedAt",
        }

    async def fake_llm(dialogue: str):
        from careconnect_api.triage.runner import TriageResult
        return TriageResult("low", 0.2, ["secret concern"], [])

    monkeypatch.setattr("careconnect_api.assessment_engine.notify.publish_assessment_run", fake_pub)
    monkeypatch.setattr("careconnect_api.triage.runner._invoke_llm", fake_llm)
    db_session.add(AiAgent(id="agt_sched", agent_name="Sched", profile_json=dump_profile({})))
    db_session.add(AiAgent(id="agt_esc2", agent_name="Esc2", profile_json=dump_profile({})))
    await db_session.commit()
    from careconnect_api.triage.runner import run_for_agent

    scheduled = await run_for_agent(
        db_session, "agt_sched", trigger_type="scheduled", scheduled_due_at=datetime(2026, 9, 27, 12, 0, 0)
    )
    escalated = await run_for_agent(
        db_session, "agt_esc2", trigger_type="escalation_phrase", trigger_message_id=9
    )
    assert scheduled is not None and scheduled.trigger_type == "scheduled"
    assert escalated is not None and escalated.trigger_type == "escalation_phrase"
    assert published == [CARE_WELLNESS_ID, CARE_WELLNESS_ID]


@pytest.mark.asyncio
async def test_sales_persist_publishes_assessment_updated(db_session: AsyncSession, monkeypatch):
    published: list[tuple] = []

    async def fake_pub(agent_id, *, profile_id, generated_at):
        published.append((agent_id, profile_id, generated_at))

    monkeypatch.setattr("careconnect_api.assessment_engine.notify.publish_assessment_run", fake_pub)
    agent = AiAgent(
        id="agt_sales_pub",
        agent_name="SalesPub",
        profile_json=dump_profile({"assessmentProfile": SALES_PRODUCT_ID}),
    )
    db_session.add(agent)
    await db_session.commit()
    from careconnect_api.assessment_engine.sales_runner import run_sales_for_agent

    row = await run_sales_for_agent(db_session, "agt_sales_pub")
    assert row is not None
    assert published and published[0][0] == "agt_sales_pub"
    assert published[0][1] == SALES_PRODUCT_ID


@pytest.mark.asyncio
async def test_tick_skips_locked_client(db_engine, db_session: AsyncSession, monkeypatch):
    reset_locks_for_tests()
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)
    monkeypatch.setattr("careconnect_api.assessment_scheduler.async_session_factory", factory)
    care = AiAgent(
        id="agt_locked",
        agent_name="Locked",
        profile_json=dump_profile({"assessmentProfile": CARE_WELLNESS_ID}),
    )
    db_session.add(care)
    await db_session.commit()
    ran: list[str] = []

    async def fake_run(db, agent_id, for_date=None, **kwargs):
        ran.append(agent_id)
        return None

    monkeypatch.setattr("careconnect_api.assessment_scheduler.run_for_agent", fake_run)
    assert await try_acquire_agent_lock("agt_locked") is True
    from careconnect_api.assessment_scheduler import tick_due_assessments

    result = await tick_due_assessments()
    assert ran == []
    assert result["skipped"] >= 1
    await release_agent_lock("agt_locked")


@pytest.mark.asyncio
async def test_publish_payload_is_slim(monkeypatch):
    captured: list[dict] = []

    async def fake_publish(agent_id, payload):
        captured.append(payload)
        return 1

    monkeypatch.setattr("careconnect_api.assessment_engine.notify.publish_assessment_updated", fake_publish)
    from careconnect_api.assessment_engine.notify import publish_assessment_run

    await publish_assessment_run("agt_x", profile_id=CARE_WELLNESS_ID, generated_at=datetime(2026, 9, 27, 2, 0, 0))
    assert captured == [
        {"agentId": "agt_x", "profileId": CARE_WELLNESS_ID, "generatedAt": "2026-09-27T02:00:00"}
    ]
    blob = str(captured[0])
    assert "concerns" not in blob
    assert "riskLevel" not in blob
    assert "recommendations" not in blob


def test_migration_026_is_additive_only():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "migrations" / "026_assessment_trigger_metadata.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS trigger_type" in src
    assert "ADD COLUMN IF NOT EXISTS trigger_message_id" in src
    assert "ADD COLUMN IF NOT EXISTS scheduled_due_at" in src
    assert "DROP COLUMN" not in src.split("Rollback", 1)[0]
    assert "DROP TABLE" not in src.split("Rollback", 1)[0]


@pytest.mark.asyncio
async def test_schedule_get_put_round_trip(client, db_session: AsyncSession):
    from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
    from careconnect_api.models import SysUser
    from careconnect_api.client_profile import load_profile

    user = SysUser(id=1, username="admin1", password=hash_password("x"), super_admin=ROLE_ROOT, status=1)
    db_session.add(user)
    await db_session.commit()
    token, _ = issue_token(user.id, user.username, ROLE_ROOT)
    headers = {"Authorization": f"Bearer {token}"}
    onboard = await client.post("/api/agent/onboard", json={"name": "Schedule Betty", "condition": "Lives alone"}, headers=headers)
    assert onboard.json()["code"] == 0, onboard.json()
    agent_id = onboard.json()["data"]["agentId"]

    got = await client.get(f"/api/agent/{agent_id}/assessment/profile", headers=headers)
    data = got.json()["data"]
    assert data["assessmentProfile"]["id"] == CARE_WELLNESS_ID
    assert data["scheduleSupported"] is True
    assert data["assessmentSchedule"]["enabled"] is True
    assert data["assessmentSchedule"]["intervalMinutes"] == 1440
    assert data["assessmentSchedule"]["onlyIfNewData"] is True
    assert data["nextAssessmentAt"] is not None

    agent = await db_session.get(AiAgent, agent_id)
    stored = load_profile(agent.profile_json)
    assert "assessmentSchedule" not in stored

    put = await client.put(
        f"/api/agent/{agent_id}/assessment/profile",
        json={"assessmentSchedule": {"enabled": True, "mode": "interval", "intervalMinutes": 120, "onlyIfNewData": False}},
        headers=headers,
    )
    assert put.json()["code"] == 0, put.json()
    assert put.json()["data"]["assessmentSchedule"]["intervalMinutes"] == 120
    assert put.json()["data"]["assessmentSchedule"]["onlyIfNewData"] is False

    db_session.expire_all()
    agent = await db_session.get(AiAgent, agent_id)
    stored = load_profile(agent.profile_json)
    assert stored["assessmentSchedule"]["intervalMinutes"] == 120
    assert stored["condition"] == "Lives alone"

    await client.put(
        f"/api/agent/{agent_id}/assessment/profile",
        json={"assessmentProfile": SALES_PRODUCT_ID},
        headers=headers,
    )
    sales = await client.get(f"/api/agent/{agent_id}/assessment/profile", headers=headers)
    assert sales.json()["data"]["scheduleSupported"] is False
    assert sales.json()["data"]["assessmentSchedule"]["mode"] == "manual"
    assert sales.json()["data"]["nextAssessmentAt"] is None
    bad = await client.put(
        f"/api/agent/{agent_id}/assessment/profile",
        json={"assessmentSchedule": {"enabled": True, "mode": "interval", "intervalMinutes": 60}},
        headers=headers,
    )
    assert bad.json()["code"] == 400
