"""R61A Bridging Care outbox: scheduled persist-first, verified mapping, no live POST."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.assessment_engine.schedule import (
    TRIGGER_ESCALATION,
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULED,
)
from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.bridgingcare_delivery import (
    ERROR_EXECUTE_DISABLED,
    STAGING_INGEST_URL,
    deliver_job,
    enqueue_bridgingcare_after_persist,
    reconcile_missing_bridgingcare_jobs,
    reset_bridgingcare_runtime_for_tests,
)
from careconnect_api.bridgingcare_mapping import (
    BETTY_ACCOUNT_ID,
    BETTY_CLIENT_ID,
    BETTY_COMPANION_DISPLAY_NAME,
    BETTY_COMPANION_ID,
    BILL_BOB_AGENT_ID,
    BILL_BOB_DISPLAY_NAME,
    BILL_BOB_ORGANIZATION_ID,
    assert_distinct_identities,
    bind_verified_mapping,
    is_nexus_agent_id,
    lookup_agents_named,
    resolve_verified_mapping,
)
from careconnect_api.bridgingcare_payload import (
    TOP_LEVEL_KEYS,
    validate_bridgingcare_payload,
    serialize_bridgingcare_assessment,
)
from careconnect_api.chat_events import CHAT_TYPE_CLIENT
from careconnect_api.envelope import APIException
from careconnect_api.models import (
    AiAgent,
    AiAgentChatHistory,
    AiMedicalAssessment,
    CcAssessmentDeliveryJob,
    CcBridgingcareCompanionMap,
    CcBridgingcareDeliveryJob,
    SysUser,
)
from careconnect_api.settings import settings
from careconnect_api.triage.runner import TriageResult, run_for_agent

API_DIR = Path(__file__).resolve().parents[1]
REPO = API_DIR.parent
MIGRATION = (API_DIR / "migrations" / "030_bridgingcare_assessment_delivery.sql").read_text()
VERIFY = (API_DIR / "migrations" / "030_bridgingcare_assessment_delivery.verify.sql").read_text()
SCHEMA = (
    REPO / "docs" / "contracts" / "bridgingcare-ai-companion-assessment-1.0.schema.json"
).read_text()
CC_MIGRATION = (API_DIR / "migrations" / "027_cc_assessment_delivery.sql").read_text()
SCHEDULER_SRC = (API_DIR / "careconnect_api" / "scheduler.py").read_text()
SCHEDULER_JOB_SRC = (API_DIR / "careconnect_api" / "assessment_scheduler.py").read_text()
RUNNER_SRC = (API_DIR / "careconnect_api" / "triage" / "runner.py").read_text()
CC_DELIVERY_SRC = (API_DIR / "careconnect_api" / "assessment_delivery.py").read_text()

_ASSESSMENT_PK = 91000
_CHAT_PK = 92000


@pytest.fixture(autouse=True)
def _reset_bc_runtime():
    reset_bridgingcare_runtime_for_tests()
    yield
    reset_bridgingcare_runtime_for_tests()


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    _ = client
    user = SysUser(
        id=1,
        username="admin1",
        password=hash_password("AdminPassword1!"),
        super_admin=ROLE_ROOT,
        status=1,
    )
    db_session.add(user)
    await db_session.commit()
    token, _expire = issue_token(user.id, user.username, ROLE_ROOT)
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _onboard(client: AsyncClient, token: str, name: str) -> str:
    resp = await client.post(
        "/api/agent/onboard", json={"name": name}, headers=_auth(token)
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["agentId"]


async def _seed_assessment(
    db: AsyncSession,
    agent_id: str,
    *,
    trigger_type: str = TRIGGER_SCHEDULED,
    source_msg_count: int = 3,
    generated_at: datetime | None = None,
    for_date: date | None = None,
) -> AiMedicalAssessment:
    global _ASSESSMENT_PK
    _ASSESSMENT_PK += 1
    when = generated_at or datetime.now()
    row = AiMedicalAssessment(
        id=_ASSESSMENT_PK,
        agent_id=agent_id,
        for_date=for_date or when.date(),
        risk_level="low",
        confidence=Decimal("0.600"),
        concerns_json='["fatigue"]',
        recommendations_json='["Phone check-in"]',
        source_msg_count=source_msg_count,
        llm_model="qwen2.5:3b",
        generated_at=when,
        trigger_type=trigger_type,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _bind_betty(db: AsyncSession, agent_id: str) -> CcBridgingcareCompanionMap:
    row = await bind_verified_mapping(
        db,
        agent_id=agent_id,
        companion_id=BETTY_COMPANION_ID,
        account_id=BETTY_ACCOUNT_ID,
        client_id=BETTY_CLIENT_ID,
        expected_agent_name=BILL_BOB_DISPLAY_NAME,
    )
    await db.commit()
    await db.refresh(row)
    return row


class _CaptureClient:
    def __init__(self, store: dict, status: int = 200, error: Exception | None = None):
        self._store = store
        self._status = status
        self._error = error

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        self._store["calls"] = self._store.get("calls", 0) + 1
        self._store["url"] = url
        self._store["json"] = json
        self._store["headers"] = headers

        if self._error:
            raise self._error

        class _Resp:
            status_code = self._status

            def json(inner_self):
                return {"ok": True}

        return _Resp()


def test_migration_030_is_additive_and_separate_from_careconnect():
    assert "CREATE TABLE IF NOT EXISTS cc_bridgingcare_companion_map" in MIGRATION
    assert "CREATE TABLE IF NOT EXISTS cc_bridgingcare_delivery_job" in MIGRATION
    assert "CREATE TABLE IF NOT EXISTS cc_bridgingcare_delivery_attempt" in MIGRATION
    assert "UNIQUE KEY uq_bc_delivery_assessment (assessment_id)" in MIGRATION
    assert "ALTER TABLE ai_medical_assessment" not in MIGRATION
    sql_lines = [
        ln for ln in MIGRATION.splitlines() if ln.strip() and not ln.strip().startswith("--")
    ]
    assert all("cc_assessment_delivery_job" not in ln for ln in sql_lines)
    assert all("DROP" not in ln for ln in sql_lines)
    assert "cc_assessment_delivery_job" in CC_MIGRATION
    assert "cc_bridgingcare_delivery_job" in VERIFY
    assert "cc_assessment_delivery_job" in VERIFY
    assert "Nx-" in SCHEMA
    assert '"assessmentId"' in SCHEMA
    assert "externalAssessmentId" not in SCHEMA
    assert "ai_agent.id" in SCHEMA
    prepared = (
        REPO / "docs" / "contracts" / "bridgingcare-bill-bob-betty-mapping.prepared.sql"
    ).read_text()
    assert BILL_BOB_AGENT_ID in prepared
    assert "Bill Bob" in prepared
    assert BETTY_COMPANION_ID in prepared
    assert "PREPARED ONLY" in prepared


def test_payload_matches_r60c_receiver_contract():
    row = AiMedicalAssessment(
        id=24,
        agent_id=BILL_BOB_AGENT_ID,
        for_date=date(2026, 10, 17),
        risk_level="low",
        confidence=Decimal("0.600"),
        concerns_json='["fatigue"]',
        recommendations_json='["Phone check-in"]',
        source_msg_count=3,
        llm_model="qwen2.5:3b",
        generated_at=datetime(2026, 10, 17, 12, 0, 0),
        trigger_type=TRIGGER_SCHEDULED,
    )
    payload = serialize_bridgingcare_assessment(
        companion_id=BETTY_COMPANION_ID,
        account_id=BETTY_ACCOUNT_ID,
        client_id=BETTY_CLIENT_ID,
        assessment=row,
        tz_name="America/Chicago",
    )
    assert list(payload.keys()) == list(TOP_LEVEL_KEYS)
    assert set(payload["assessment"]) == {
        "riskLevel",
        "confidence",
        "concerns",
        "recommendations",
    }
    assert payload["schemaVersion"] == "1.0"
    assert payload["assessmentId"] == "24"
    assert payload["source"] == "nexus"
    assert payload["companionId"] == BETTY_COMPANION_ID
    assert payload["accountId"] == BETTY_ACCOUNT_ID
    assert payload["clientId"] == BETTY_CLIENT_ID
    assert payload["generatedAt"] == "2026-10-17T17:00:00Z"
    assert payload["forDate"] == "2026-10-17"
    assert payload["assessment"]["riskLevel"] == "low"
    assert payload["assessment"]["confidence"] == 0.6
    assert 0.0 <= payload["assessment"]["confidence"] <= 1.0
    assert payload["assessment"]["concerns"] == ["fatigue"]
    assert payload["assessment"]["recommendations"] == ["Phone check-in"]
    assert "externalAssessmentId" not in payload
    assert BILL_BOB_AGENT_ID not in json.dumps(payload)
    assert BILL_BOB_ORGANIZATION_ID not in json.dumps(payload)
    assert validate_bridgingcare_payload(payload) == []

    extra = dict(payload)
    extra["timestamp"] = "nope"
    assert "unknown fields" in validate_bridgingcare_payload(extra)[0]


def test_scheduler_and_runner_hooks_do_not_change_schedule():
    assert 'id="assessment_due_check"' in SCHEDULER_SRC
    assert 'id="assessment_delivery"' in SCHEDULER_SRC
    assert 'id="bridgingcare_delivery"' in SCHEDULER_SRC
    assert "tick_due_assessments" in SCHEDULER_SRC
    assert "trigger_type=TRIGGER_SCHEDULED" in SCHEDULER_JOB_SRC
    assert "enqueue_bridgingcare_after_persist" in RUNNER_SRC
    assert "enqueue_careconnect_delivery" in RUNNER_SRC
    assert "enqueue_bridgingcare" not in CC_DELIVERY_SRC


def test_betty_registration_is_not_a_nexus_agent_id():
    assert is_nexus_agent_id(BILL_BOB_AGENT_ID) is True
    assert is_nexus_agent_id(BETTY_COMPANION_ID) is False
    assert BETTY_COMPANION_ID.startswith("Nx-")
    assert BILL_BOB_AGENT_ID != BETTY_COMPANION_ID
    assert BILL_BOB_ORGANIZATION_ID != BETTY_ACCOUNT_ID
    assert BILL_BOB_DISPLAY_NAME != BETTY_COMPANION_DISPLAY_NAME
    assert is_nexus_agent_id("1fb8c2e23f294ec586269affbe4e1f2c") is True
    with pytest.raises(APIException, match="nexus client id"):
        assert_distinct_identities(
            agent_id=BETTY_COMPANION_ID,
            companion_id=BETTY_COMPANION_ID,
            account_id=BETTY_ACCOUNT_ID,
            client_id=BETTY_CLIENT_ID,
        )


@pytest.mark.asyncio
async def test_resolves_bill_bob_client_separately_from_betty_companion(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    assert is_nexus_agent_id(agent_id)
    assert agent_id != BETTY_COMPANION_ID
    matches = await lookup_agents_named(db_session, BILL_BOB_DISPLAY_NAME)
    assert [a.id for a in matches] == [agent_id]
    betty_named = await lookup_agents_named(db_session, BETTY_COMPANION_DISPLAY_NAME)
    assert betty_named == []
    agent = await db_session.get(AiAgent, agent_id)
    assert agent is not None
    assert agent.agent_name == BILL_BOB_DISPLAY_NAME
    assert agent.organization_id != BETTY_ACCOUNT_ID

    with pytest.raises(APIException, match="companion id is not a Bridging Care registration identifier"):
        await bind_verified_mapping(
            db_session,
            agent_id=agent_id,
            companion_id=agent_id,
            account_id=BETTY_ACCOUNT_ID,
            client_id=BETTY_CLIENT_ID,
            expected_agent_name=BILL_BOB_DISPLAY_NAME,
        )
    with pytest.raises(APIException, match="CareConnect public client id"):
        await bind_verified_mapping(
            db_session,
            agent_id=agent_id,
            companion_id=BETTY_COMPANION_ID,
            account_id=agent.organization_id or BETTY_ACCOUNT_ID,
            client_id=BETTY_CLIENT_ID,
            expected_agent_name=BILL_BOB_DISPLAY_NAME,
            careconnect_public_id=BETTY_CLIENT_ID,
        )
    with pytest.raises(APIException, match="not companion Betty"):
        await bind_verified_mapping(
            db_session,
            agent_id=agent_id,
            companion_id=BETTY_COMPANION_ID,
            account_id=BETTY_ACCOUNT_ID,
            client_id=BETTY_CLIENT_ID,
            expected_agent_name=BETTY_COMPANION_DISPLAY_NAME,
        )
    with pytest.raises(APIException, match="client name does not match the verified Nexus client"):
        await bind_verified_mapping(
            db_session,
            agent_id=agent_id,
            companion_id=BETTY_COMPANION_ID,
            account_id=BETTY_ACCOUNT_ID,
            client_id=BETTY_CLIENT_ID,
            expected_agent_name="Someone Else",
        )

    mapped = await _bind_betty(db_session, agent_id)
    assert mapped.companion_id == BETTY_COMPANION_ID
    assert mapped.account_id == BETTY_ACCOUNT_ID
    assert mapped.client_id == BETTY_CLIENT_ID
    assert mapped.agent_id == agent_id
    assert mapped.expected_agent_name == BILL_BOB_DISPLAY_NAME
    resolved = await resolve_verified_mapping(db_session, agent_id)
    assert resolved is not None
    assert resolved.companion_id != resolved.agent_id
    assert resolved.expected_agent_name == BILL_BOB_DISPLAY_NAME


@pytest.mark.asyncio
async def test_missing_mapping_keeps_assessment_without_outbox(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    row = await _seed_assessment(db_session, agent_id)
    job = await enqueue_bridgingcare_after_persist(db_session, agent_id, row)
    assert job is None
    n = (
        await db_session.execute(
            select(func.count()).select_from(CcBridgingcareDeliveryJob)
        )
    ).scalar_one()
    assert int(n or 0) == 0
    assert await db_session.get(AiMedicalAssessment, row.id) is not None


@pytest.mark.asyncio
async def test_only_scheduled_eligible_assessments_enqueue(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    await _bind_betty(db_session, agent_id)
    scheduled = await _seed_assessment(db_session, agent_id, trigger_type=TRIGGER_SCHEDULED)
    manual = await _seed_assessment(db_session, agent_id, trigger_type=TRIGGER_MANUAL)
    escalated = await _seed_assessment(db_session, agent_id, trigger_type=TRIGGER_ESCALATION)
    empty = await _seed_assessment(
        db_session, agent_id, trigger_type=TRIGGER_SCHEDULED, source_msg_count=0
    )
    assert await enqueue_bridgingcare_after_persist(db_session, agent_id, scheduled) is not None
    assert await enqueue_bridgingcare_after_persist(db_session, agent_id, manual) is None
    assert await enqueue_bridgingcare_after_persist(db_session, agent_id, escalated) is None
    assert await enqueue_bridgingcare_after_persist(db_session, agent_id, empty) is None
    jobs = (
        await db_session.execute(select(CcBridgingcareDeliveryJob))
    ).scalars().all()
    assert [j.assessment_id for j in jobs] == [scheduled.id]


@pytest.mark.asyncio
async def test_job_freezes_mapping_and_does_not_touch_careconnect_queue(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    await _bind_betty(db_session, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    job = await enqueue_bridgingcare_after_persist(db_session, agent_id, row)
    assert job is not None
    payload = json.loads(job.payload_json)
    assert payload["companionId"] == BETTY_COMPANION_ID
    assert payload["accountId"] == BETTY_ACCOUNT_ID
    assert payload["clientId"] == BETTY_CLIENT_ID
    assert payload["assessmentId"] == str(row.id)
    assert "externalAssessmentId" not in payload
    assert payload["source"] == "nexus"
    assert validate_bridgingcare_payload(payload) == []
    assert agent_id not in json.dumps(payload)
    assert "organization" not in json.dumps(payload).lower()
    again = await enqueue_bridgingcare_after_persist(db_session, agent_id, row)
    assert again is not None
    assert again.id == job.id
    cc = (
        await db_session.execute(
            select(func.count()).select_from(CcAssessmentDeliveryJob)
        )
    ).scalar_one()
    assert int(cc or 0) == 0


@pytest.mark.asyncio
async def test_execute_disabled_never_posts(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    await _bind_betty(db_session, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    job = await enqueue_bridgingcare_after_persist(db_session, agent_id, row)
    job_id = int(job.id)
    assessment_id = int(row.id)
    captured = {"calls": 0}
    monkeypatch.setattr(settings, "bridgingcare_ingest_url", STAGING_INGEST_URL)
    monkeypatch.setenv("BRIDGINGCARE_EXECUTE_ENABLED", "false")
    monkeypatch.setattr(
        "careconnect_api.bridgingcare_delivery.httpx.AsyncClient",
        _CaptureClient(captured),
    )
    ok = await deliver_job(db_session, job_id)
    assert ok is False
    assert captured["calls"] == 0
    db_session.expire_all()
    reloaded = await db_session.get(CcBridgingcareDeliveryJob, job_id)
    assert reloaded.status == "pending"
    assert reloaded.delivered_at is None
    assert reloaded.last_error_category == ERROR_EXECUTE_DISABLED
    assert reloaded.attempt_count == 0
    assert json.loads(reloaded.payload_json)["assessmentId"] == str(assessment_id)


@pytest.mark.asyncio
async def test_execute_posts_frozen_snapshot_once(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    await _bind_betty(db_session, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    job = await enqueue_bridgingcare_after_persist(db_session, agent_id, row)
    job_id = int(job.id)
    assessment_id = int(row.id)
    expected = serialize_bridgingcare_assessment(
        companion_id=BETTY_COMPANION_ID,
        account_id=BETTY_ACCOUNT_ID,
        client_id=BETTY_CLIENT_ID,
        assessment=row,
    )
    captured = {"calls": 0}
    llm_calls = {"n": 0}

    async def _llm(_dialogue: str) -> TriageResult:
        llm_calls["n"] += 1
        raise AssertionError("retry must not generate a new assessment")

    monkeypatch.setattr("careconnect_api.triage.runner._invoke_llm", _llm)
    monkeypatch.setattr(settings, "bridgingcare_ingest_url", STAGING_INGEST_URL)
    monkeypatch.setenv("BRIDGINGCARE_EXECUTE_ENABLED", "true")
    monkeypatch.setattr(
        "careconnect_api.bridgingcare_delivery.httpx.AsyncClient",
        _CaptureClient(captured),
    )
    assert await deliver_job(db_session, job_id) is True
    assert llm_calls["n"] == 0
    assert captured["calls"] == 1
    assert captured["url"] == STAGING_INGEST_URL
    assert captured["json"] == expected
    assert captured["json"]["companionId"] == BETTY_COMPANION_ID
    assert captured["json"]["assessmentId"] == str(assessment_id)
    assert validate_bridgingcare_payload(captured["json"]) == []
    db_session.expire_all()
    reloaded = await db_session.get(CcBridgingcareDeliveryJob, job_id)
    assert json.loads(reloaded.payload_json) == captured["json"]
    assert reloaded.status == "delivered"
    assert reloaded.delivered_at is not None
    assert await db_session.get(AiMedicalAssessment, assessment_id) is not None
    assert await deliver_job(db_session, job_id) is False
    assert captured["calls"] == 1


@pytest.mark.asyncio
async def test_historical_assessments_are_not_backfilled(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    historical = await _seed_assessment(
        db_session,
        agent_id,
        generated_at=datetime(2026, 10, 17, 12, 0, 0),
        for_date=date(2026, 10, 17),
    )
    assert await enqueue_bridgingcare_after_persist(db_session, agent_id, historical) is None
    await _bind_betty(db_session, agent_id)
    assert await enqueue_bridgingcare_after_persist(db_session, agent_id, historical) is None
    assert await reconcile_missing_bridgingcare_jobs(db_session) == 0
    n = (
        await db_session.execute(
            select(func.count()).select_from(CcBridgingcareDeliveryJob)
        )
    ).scalar_one()
    assert int(n or 0) == 0
    assert await db_session.get(AiMedicalAssessment, historical.id) is not None


@pytest.mark.asyncio
async def test_reconcile_recovers_post_activation_missed_enqueue(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    await _bind_betty(db_session, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    created = await reconcile_missing_bridgingcare_jobs(db_session)
    assert created == 1
    job = (
        await db_session.execute(
            select(CcBridgingcareDeliveryJob).where(
                CcBridgingcareDeliveryJob.assessment_id == row.id
            )
        )
    ).scalar_one()
    assert job.companion_id == BETTY_COMPANION_ID
    assert json.loads(job.payload_json)["assessmentId"] == str(row.id)
    assert await reconcile_missing_bridgingcare_jobs(db_session) == 0


@pytest.mark.asyncio
async def test_run_for_agent_scheduled_enqueues_after_persist(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    await _bind_betty(db_session, agent_id)
    global _CHAT_PK
    _CHAT_PK += 1
    db_session.add(
        AiAgentChatHistory(
            id=_CHAT_PK,
            agent_id=agent_id,
            session_id="s-bc",
            chat_type=CHAT_TYPE_CLIENT,
            content="I slept poorly and feel tired",
            created_at=datetime.now(),
        )
    )
    await db_session.commit()

    llm_calls = {"n": 0}

    async def _llm(_dialogue: str) -> TriageResult:
        llm_calls["n"] += 1
        return TriageResult("low", 0.6, ["fatigue"], ["Phone check-in"])

    monkeypatch.setattr("careconnect_api.triage.runner._invoke_llm", _llm)
    captured = {"calls": 0}
    monkeypatch.setattr(
        "careconnect_api.bridgingcare_delivery.httpx.AsyncClient",
        _CaptureClient(captured),
    )
    row = await run_for_agent(db_session, agent_id, trigger_type=TRIGGER_SCHEDULED)
    assert row is not None
    assert row.trigger_type == TRIGGER_SCHEDULED
    assert llm_calls["n"] == 1
    job = (
        await db_session.execute(
            select(CcBridgingcareDeliveryJob).where(
                CcBridgingcareDeliveryJob.assessment_id == row.id
            )
        )
    ).scalar_one()
    assert job.status == "pending"
    assert job.companion_id == BETTY_COMPANION_ID
    job_payload = json.loads(job.payload_json)
    assert job_payload["companionId"] == BETTY_COMPANION_ID
    assert job_payload["clientId"] == BETTY_CLIENT_ID
    assert job_payload["assessmentId"] == str(row.id)
    assert validate_bridgingcare_payload(job_payload) == []
    assert captured["calls"] == 0
    manual = await run_for_agent(db_session, agent_id, trigger_type=TRIGGER_MANUAL)
    assert manual is not None
    extra = (
        await db_session.execute(
            select(func.count()).select_from(CcBridgingcareDeliveryJob).where(
                CcBridgingcareDeliveryJob.assessment_id == manual.id
            )
        )
    ).scalar_one()
    assert int(extra or 0) == 0
