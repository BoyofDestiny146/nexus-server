"""Durable CareConnect assessment delivery queue, eligibility, and audit."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.assessment_delivery import (
    STATUS_DEAD_LETTER,
    STATUS_DELIVERED,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    DeliveryEnqueueError,
    _classify_http,
    assessment_eligible_for_delivery,
    enqueue_careconnect_delivery,
    idempotency_key_for,
    reset_delivery_runtime_for_tests,
    tick_assessment_deliveries,
)
from careconnect_api.assessment_engine.delivery import outbound_careconnect_requested
from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.chat_events import CHAT_TYPE_CLIENT
from careconnect_api.client_profile import dump_profile, load_profile, merge_profile
from careconnect_api.models import (
    AiAgent,
    AiAgentChatHistory,
    AiMedicalAssessment,
    CcAssessmentDeliveryAttempt,
    CcAssessmentDeliveryJob,
    CcAssessmentResult,
    SysUser,
)
from careconnect_api.partner_payload import SCHEMA_VERSION, serialize_client_assessment_payload
from careconnect_api.partner_push import push_assessment_best_effort
from careconnect_api.settings import settings
from careconnect_api.triage.runner import TriageResult, run_for_agent

_EXTERNAL = "https://careconnect.example.org/api/v1/integrations/careconnect/ingest"
_ASSESSMENT_PK = 9200
_CHAT_PK = 18000
API_DIR = Path(__file__).resolve().parents[1]
MIGRATION = (API_DIR / "migrations" / "027_cc_assessment_delivery.sql").read_text()
VERIFY = (API_DIR / "migrations" / "027_cc_assessment_delivery.verify.sql").read_text()
RUNNER_SRC = (API_DIR / "careconnect_api" / "triage" / "runner.py").read_text()
SALES_SRC = (API_DIR / "careconnect_api" / "assessment_engine" / "sales_runner.py").read_text()
ENGINE_SRC = (API_DIR / "careconnect_api" / "assessment_engine" / "engine.py").read_text()
SCHEDULER_JOB_SRC = (API_DIR / "careconnect_api" / "assessment_scheduler.py").read_text()
ESCALATION_SRC = (API_DIR / "careconnect_api" / "assessment_escalation.py").read_text()
SCHEDULER_SRC = (API_DIR / "careconnect_api" / "scheduler.py").read_text()
PARTNER_PUSH_SRC = (API_DIR / "careconnect_api" / "partner_push.py").read_text()
DELIVERY_SRC = (API_DIR / "careconnect_api" / "assessment_delivery.py").read_text()
PAYLOAD_SRC = (API_DIR / "careconnect_api" / "partner_payload.py").read_text()
MODEL_SRC = (API_DIR / "careconnect_api" / "models" / "__init__.py").read_text()


@pytest.fixture(autouse=True)
def _reset_delivery_runtime():
    reset_delivery_runtime_for_tests()
    yield
    reset_delivery_runtime_for_tests()


@pytest_asyncio.fixture(scope="function")
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


async def _connect_cc(client: AsyncClient, token: str, agent_id: str) -> tuple[str, str]:
    resp = await client.post(
        f"/api/agent/{agent_id}/integrations/careconnect",
        headers=_auth(token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["publicId"], body["data"]["secret"]


async def _seed_assessment(
    db: AsyncSession,
    agent_id: str,
    *,
    source_msg_count: int = 3,
    concerns_json: str = '["internal-only"]',
    risk_level: str = "low",
    confidence: str = "0.600",
) -> AiMedicalAssessment:
    global _ASSESSMENT_PK
    _ASSESSMENT_PK += 1
    row = AiMedicalAssessment(
        id=_ASSESSMENT_PK,
        agent_id=agent_id,
        for_date=date(2026, 10, 8),
        risk_level=risk_level,
        confidence=Decimal(confidence),
        concerns_json=concerns_json,
        recommendations_json='["Phone check-in"]',
        source_msg_count=source_msg_count,
        llm_model="qwen2.5:3b",
        generated_at=datetime(2026, 10, 8, 9, 25, 0),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _enqueue_then_kick(db: AsyncSession, agent_id: str, row: AiMedicalAssessment) -> bool:
    job = await enqueue_careconnect_delivery(db, agent_id, row)
    await db.commit()
    if job is None:
        return False
    from careconnect_api.assessment_delivery import kick_assessment_delivery

    return await kick_assessment_delivery(db, int(row.id))


async def _tick_factory(db_session: AsyncSession):
    from sqlalchemy.ext.asyncio import AsyncSession as AS
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(db_session.bind, expire_on_commit=False, class_=AS)


class _CaptureClient:
    def __init__(
        self,
        store: dict,
        error: Exception | None = None,
        status: int = 200,
        code: object = 0,
        omit_envelope: bool = False,
    ):
        self._store = store
        self._error = error
        self._status = status
        self._code = code
        self._omit_envelope = omit_envelope

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
        omit = self._omit_envelope
        code = self._code
        payload = json

        class _Resp:
            status_code = self._status

            def json(inner_self):
                if omit:
                    return {"accepted": True, "data": payload}
                return {"code": code, "msg": "success", "data": payload}

        return _Resp()


async def _job_count(db: AsyncSession, assessment_id: int) -> int:
    n = (
        await db.execute(
            select(func.count()).select_from(CcAssessmentDeliveryJob).where(
                CcAssessmentDeliveryJob.assessment_id == assessment_id
            )
        )
    ).scalar_one()
    return int(n or 0)


def test_migration_027_is_additive_and_stores_no_secrets():
    assert "CREATE TABLE IF NOT EXISTS cc_assessment_delivery_job" in MIGRATION
    assert "CREATE TABLE IF NOT EXISTS cc_assessment_delivery_attempt" in MIGRATION
    assert "UNIQUE KEY uq_cc_delivery_assessment (assessment_id)" in MIGRATION
    assert "ALTER TABLE ai_medical_assessment" not in MIGRATION
    assert "DROP TABLE" not in MIGRATION.split("Rollback")[0]
    assert "secret_enc" not in MIGRATION
    assert "X-Client-Secret" not in MIGRATION
    assert "class CcAssessmentDeliveryJob" in MODEL_SRC
    assert "class CcAssessmentDeliveryAttempt" in MODEL_SRC
    assert "cc_assessment_delivery_job" in VERIFY
    assert "secret_enc" in VERIFY


def test_worker_uses_scheduler_and_existing_json_mapper():
    assert "tick_assessment_deliveries" in SCHEDULER_SRC
    assert 'id="assessment_delivery"' in SCHEDULER_SRC
    assert "enqueue_careconnect_delivery" in RUNNER_SRC
    assert "enqueue_careconnect_delivery_safe" not in RUNNER_SRC
    assert "push_assessment_best_effort" in RUNNER_SRC
    assert "DeliveryEnqueueError" in RUNNER_SRC
    assert "await db.rollback()" in RUNNER_SRC
    assert "assessmentDelivery" not in RUNNER_SRC
    assert "enqueue_careconnect_delivery" not in SALES_SRC
    assert "push_assessment_best_effort" not in SALES_SRC
    assert "serialize_client_assessment_payload" in DELIVERY_SRC
    assert "Idempotency-Key" in PARTNER_PUSH_SRC
    assert "X-Client-Secret" in PARTNER_PUSH_SRC
    assert "Never log" in DELIVERY_SRC
    assert "await run_for_agent(db, agent_id, for_date)" in ENGINE_SRC
    assert "trigger_type=TRIGGER_SCHEDULED" in SCHEDULER_JOB_SRC
    assert "trigger_type=TRIGGER_ESCALATION" in ESCALATION_SRC
    assert "schemaVersion" in PAYLOAD_SRC
    assert f'"{SCHEMA_VERSION}"' in PAYLOAD_SRC or "SCHEMA_VERSION = " in PAYLOAD_SRC


def test_eligibility_excludes_empty_and_parse_error():
    ok = AiMedicalAssessment(
        id=1, agent_id="a", risk_level="moderate", source_msg_count=4,
        concerns_json='["fatigue"]',
    )
    empty = AiMedicalAssessment(
        id=2, agent_id="a", risk_level="low", source_msg_count=0, concerns_json="[]",
    )
    parse_err = AiMedicalAssessment(
        id=3, agent_id="a", risk_level="low", source_msg_count=5,
        concerns_json='["parse_error", "no_json_object"]',
    )
    assert assessment_eligible_for_delivery(ok) is True
    assert assessment_eligible_for_delivery(empty) is False
    assert assessment_eligible_for_delivery(parse_err) is False
    assert assessment_eligible_for_delivery(None) is False


def test_classify_http_ack_rules():
    assert _classify_http(200, 0) == (True, "", False)
    assert _classify_http(201, 0) == (True, "", False)
    assert _classify_http(202, None) == (True, "", False)
    assert _classify_http(409, 409) == (False, "conflict", False)
    assert _classify_http(200, 409) == (False, "conflict", False)
    assert _classify_http(429, 0) == (False, "http_429", True)
    assert _classify_http(500, 0) == (False, "http_5xx", True)
    assert _classify_http(400, 400) == (False, "http_4xx", False)


@pytest.mark.asyncio
async def test_valid_care_assessment_enqueues_and_posts_snapshot(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch, caplog
):
    agent_id = await _onboard(client, admin_token, "Deliver Ada")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    expected = serialize_client_assessment_payload(
        public_client_id=public_id, assessment=row, tz_name="America/Chicago",
    )
    captured: dict = {"calls": 0}
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient", _CaptureClient(captured)
    )
    caplog.set_level(logging.INFO)

    ok = await _enqueue_then_kick(db_session, agent_id, row)
    assert ok is True
    assert captured["calls"] == 1
    assert captured["json"] == expected
    assert captured["headers"]["X-Client-Id"] == public_id
    assert captured["headers"]["X-Client-Secret"] == secret
    assert captured["headers"]["Idempotency-Key"] == idempotency_key_for(row.id)
    assert secret not in caplog.text
    assert "internal-only" not in caplog.text
    job = (
        await db_session.execute(
            select(CcAssessmentDeliveryJob).where(
                CcAssessmentDeliveryJob.assessment_id == row.id
            )
        )
    ).scalar_one()
    assert job.status == STATUS_DELIVERED
    assert job.delivered_at is not None
    assert job.public_client_id == public_id
    assert job.agent_id == agent_id
    attempts = (
        await db_session.execute(
            select(CcAssessmentDeliveryAttempt).where(
                CcAssessmentDeliveryAttempt.job_id == job.id
            )
        )
    ).scalars().all()
    assert len(attempts) == 1
    assert attempts[0].success == 1
    assert attempts[0].http_status == 200
    assert attempts[0].destination == "careconnect"
    assert json.loads(job.payload_json)["clientId"] == public_id
    assert json.loads(job.payload_json)["schemaVersion"] == SCHEMA_VERSION
    assert "secret" not in job.payload_json
    still = (
        await db_session.execute(
            select(AiMedicalAssessment).where(AiMedicalAssessment.id == row.id)
        )
    ).scalar_one()
    assert still.risk_level == "low"


@pytest.mark.asyncio
async def test_kick_only_does_not_enqueue_after_commit(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Kick Only")
    await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    ok = await push_assessment_best_effort(db_session, agent_id, row)
    assert ok is False
    assert await _job_count(db_session, row.id) == 0


@pytest.mark.asyncio
async def test_duplicate_enqueue_is_idempotent(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Deliver Bea")
    await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    first = await enqueue_careconnect_delivery(db_session, agent_id, row)
    second = await enqueue_careconnect_delivery(db_session, agent_id, row)
    await db_session.commit()
    assert first is not None
    assert second is not None
    assert first.id == second.id
    assert await _job_count(db_session, row.id) == 1


@pytest.mark.asyncio
async def test_destination_none_does_not_enqueue(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Deliver Cara")
    await _connect_cc(client, admin_token, agent_id)
    agent = await db_session.get(AiAgent, agent_id)
    merged = merge_profile(load_profile(agent.profile_json), {
        "assessmentDelivery": {"destination": "none"},
    })
    agent.profile_json = dump_profile(merged)
    await db_session.commit()
    assert outbound_careconnect_requested(profile_json=agent.profile_json) is False
    row = await _seed_assessment(db_session, agent_id)
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    job = await enqueue_careconnect_delivery(db_session, agent_id, row)
    await db_session.commit()
    assert job is None
    assert await _job_count(db_session, row.id) == 0
    still = await db_session.get(AiMedicalAssessment, row.id)
    assert still is not None


@pytest.mark.asyncio
async def test_disconnected_integration_does_not_enqueue(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Deliver Dee")
    row = await _seed_assessment(db_session, agent_id)
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    job = await enqueue_careconnect_delivery(db_session, agent_id, row)
    await db_session.commit()
    assert job is None
    assert await _job_count(db_session, row.id) == 0
    assert await db_session.get(AiMedicalAssessment, row.id) is not None


@pytest.mark.asyncio
async def test_empty_and_parse_error_assessments_do_not_enqueue(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Deliver Eli")
    await _connect_cc(client, admin_token, agent_id)
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    empty = await _seed_assessment(db_session, agent_id, source_msg_count=0)
    err = await _seed_assessment(
        db_session, agent_id, concerns_json='["parse_error", "llm_call_failed"]'
    )
    assert await enqueue_careconnect_delivery(db_session, agent_id, empty) is None
    assert await enqueue_careconnect_delivery(db_session, agent_id, err) is None
    await db_session.commit()
    assert await _job_count(db_session, empty.id) == 0
    assert await _job_count(db_session, err.id) == 0


@pytest.mark.asyncio
async def test_sales_result_has_no_delivery_job(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, "Deliver Fay")
    db_session.add(CcAssessmentResult(
        agent_id=agent_id,
        profile_id="sales_product",
        payload_json="{}",
        source_msg_count=2,
        generated_at=datetime.now(),
    ))
    await db_session.commit()
    n = (
        await db_session.execute(select(func.count()).select_from(CcAssessmentDeliveryJob))
    ).scalar_one()
    assert int(n or 0) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code,omit,want_status,want_cat,want_ok",
    [
        (200, 0, False, STATUS_DELIVERED, None, True),
        (201, 0, False, STATUS_DELIVERED, None, True),
        (202, 0, True, STATUS_DELIVERED, None, True),
        (409, 409, False, STATUS_DEAD_LETTER, "conflict", False),
        (429, 0, False, STATUS_PENDING, "http_429", False),
        (500, 0, False, STATUS_PENDING, "http_5xx", False),
    ],
)
async def test_http_acknowledgment_matrix(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    monkeypatch,
    status,
    code,
    omit,
    want_status,
    want_cat,
    want_ok,
):
    agent_id = await _onboard(client, admin_token, f"Ack {status} {code}")
    await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    captured: dict = {"calls": 0}
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient",
        _CaptureClient(captured, status=status, code=code, omit_envelope=omit),
    )
    ok = await _enqueue_then_kick(db_session, agent_id, row)
    assert ok is want_ok
    assert captured["calls"] == 1
    assert captured["headers"]["Idempotency-Key"] == idempotency_key_for(row.id)
    job = (
        await db_session.execute(
            select(CcAssessmentDeliveryJob).where(
                CcAssessmentDeliveryJob.assessment_id == row.id
            )
        )
    ).scalar_one()
    assert job.status == want_status
    assert job.last_error_category == want_cat
    assert job.last_http_status == status
    assert await db_session.get(AiMedicalAssessment, row.id) is not None


@pytest.mark.asyncio
async def test_timeout_retries_same_job_and_keeps_assessment(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch, caplog
):
    agent_id = await _onboard(client, admin_token, "Deliver Gia")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    captured: dict = {"calls": 0}
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    monkeypatch.setattr(settings, "assessment_delivery_max_attempts", 8)
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient",
        _CaptureClient(captured, error=httpx.ReadTimeout("timed out")),
    )
    caplog.set_level(logging.INFO)
    ok = await _enqueue_then_kick(db_session, agent_id, row)
    assert ok is False
    assert captured["calls"] == 1
    assert secret not in caplog.text
    job = (
        await db_session.execute(
            select(CcAssessmentDeliveryJob).where(
                CcAssessmentDeliveryJob.assessment_id == row.id
            )
        )
    ).scalar_one()
    assert job.status == STATUS_PENDING
    assert job.attempt_count == 1
    assert job.last_error_category == "timeout"
    assert job.idempotency_key == idempotency_key_for(row.id)
    assert job.next_retry_at is not None
    assert job.delivered_at is None
    attempt = (
        await db_session.execute(
            select(CcAssessmentDeliveryAttempt).where(
                CcAssessmentDeliveryAttempt.job_id == job.id
            )
        )
    ).scalar_one()
    assert attempt.success == 0
    assert attempt.error_category == "timeout"
    still = (
        await db_session.execute(
            select(AiMedicalAssessment).where(AiMedicalAssessment.id == row.id)
        )
    ).scalar_one()
    assert still.id == row.id


@pytest.mark.asyncio
async def test_http_4xx_dead_letters_without_retry(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Deliver Jo")
    await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    captured: dict = {"calls": 0}
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    monkeypatch.setattr(settings, "assessment_delivery_max_attempts", 8)
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient",
        _CaptureClient(captured, status=400, code=400),
    )
    ok = await _enqueue_then_kick(db_session, agent_id, row)
    assert ok is False
    assert captured["calls"] == 1
    job = (
        await db_session.execute(
            select(CcAssessmentDeliveryJob).where(
                CcAssessmentDeliveryJob.assessment_id == row.id
            )
        )
    ).scalar_one()
    assert job.status == STATUS_DEAD_LETTER
    assert job.attempt_count == 1
    assert job.last_http_status == 400
    assert job.last_error_category == "http_4xx"


@pytest.mark.asyncio
async def test_retry_exhaustion_dead_letters(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Deliver Hal")
    await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    captured: dict = {"calls": 0}
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    monkeypatch.setattr(settings, "assessment_delivery_max_attempts", 1)
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient",
        _CaptureClient(captured, error=httpx.ConnectError("down")),
    )
    ok = await _enqueue_then_kick(db_session, agent_id, row)
    assert ok is False
    job = (
        await db_session.execute(
            select(CcAssessmentDeliveryJob).where(
                CcAssessmentDeliveryJob.assessment_id == row.id
            )
        )
    ).scalar_one()
    assert job.status == STATUS_DEAD_LETTER
    assert job.attempt_count == 1
    assert await db_session.get(AiMedicalAssessment, row.id) is not None


@pytest.mark.asyncio
async def test_tick_recovers_due_job(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Deliver Ivy")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    job = await enqueue_careconnect_delivery(db_session, agent_id, row)
    await db_session.commit()
    assert job is not None
    job_id = int(job.id)
    job.next_retry_at = datetime.now() - timedelta(seconds=1)
    await db_session.commit()
    captured: dict = {"calls": 0}
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient", _CaptureClient(captured)
    )
    factory = await _tick_factory(db_session)
    stats = await tick_assessment_deliveries(limit=5, session_factory=factory)
    assert stats["delivered"] == 1
    assert stats.get("inactive", 0) == 0
    assert captured["calls"] == 1
    assert captured["headers"]["X-Client-Secret"] == secret
    db_session.expire_all()
    reloaded = await db_session.get(CcAssessmentDeliveryJob, job_id)
    assert reloaded is not None
    assert reloaded.status == STATUS_DELIVERED
    assert reloaded.public_client_id == public_id


@pytest.mark.asyncio
async def test_tick_recovers_expired_in_progress_lease(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Lease Rec")
    await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    job = await enqueue_careconnect_delivery(db_session, agent_id, row)
    await db_session.commit()
    job.status = STATUS_IN_PROGRESS
    job.lease_expires_at = datetime.now() - timedelta(seconds=5)
    job.next_retry_at = datetime.now() - timedelta(seconds=5)
    await db_session.commit()
    job_id = int(job.id)
    captured: dict = {"calls": 0}
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient", _CaptureClient(captured)
    )
    factory = await _tick_factory(db_session)
    stats = await tick_assessment_deliveries(limit=5, session_factory=factory)
    assert stats["delivered"] == 1
    db_session.expire_all()
    reloaded = await db_session.get(CcAssessmentDeliveryJob, job_id)
    assert reloaded.status == STATUS_DELIVERED


@pytest.mark.asyncio
async def test_schema_missing_worker_is_inactive(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, caplog
):
    _ = client, admin_token
    await db_session.execute(text("DROP TABLE IF EXISTS cc_assessment_delivery_attempt"))
    await db_session.execute(text("DROP TABLE IF EXISTS cc_assessment_delivery_job"))
    await db_session.commit()
    reset_delivery_runtime_for_tests()
    caplog.set_level(logging.WARNING)
    factory = await _tick_factory(db_session)
    first = await tick_assessment_deliveries(limit=5, session_factory=factory)
    second = await tick_assessment_deliveries(limit=5, session_factory=factory)
    assert first["inactive"] == 1
    assert second["inactive"] == 1
    assert first["due"] == 0
    warnings = [r for r in caplog.records if "027_cc_assessment_delivery" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_schema_present_worker_processes_jobs(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Schema On")
    await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    await enqueue_careconnect_delivery(db_session, agent_id, row)
    await db_session.commit()
    captured: dict = {"calls": 0}
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient", _CaptureClient(captured)
    )
    factory = await _tick_factory(db_session)
    stats = await tick_assessment_deliveries(limit=5, session_factory=factory)
    assert stats["inactive"] == 0
    assert stats["delivered"] == 1
    assert captured["calls"] == 1


@pytest.mark.asyncio
async def test_rotate_same_public_id_still_posts(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Rotate Pat")
    public_id, _old = await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    job = await enqueue_careconnect_delivery(db_session, agent_id, row)
    await db_session.commit()
    assert job is not None
    rot = await client.post(
        f"/api/agent/{agent_id}/integrations/careconnect/rotate",
        headers=_auth(admin_token),
    )
    body = rot.json()
    assert body["code"] == 0, body
    new_secret = body["data"]["secret"]
    assert body["data"]["publicId"] == public_id
    captured: dict = {"calls": 0}
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient", _CaptureClient(captured)
    )
    from careconnect_api.assessment_delivery import kick_assessment_delivery

    ok = await kick_assessment_delivery(db_session, int(row.id))
    assert ok is True
    assert captured["headers"]["X-Client-Id"] == public_id
    assert captured["headers"]["X-Client-Secret"] == new_secret
    assert json.loads(job.payload_json)["clientId"] == public_id


@pytest.mark.asyncio
async def test_reconnect_mismatch_does_not_post_or_substitute(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Mismatch Quinn")
    old_id, _old_secret = await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)
    job = await enqueue_careconnect_delivery(db_session, agent_id, row)
    await db_session.commit()
    assert job is not None
    job_id = int(job.id)
    assessment_id = int(row.id)
    frozen_client = job.public_client_id
    gone = await client.delete(
        f"/api/agent/{agent_id}/integrations/careconnect",
        headers=_auth(admin_token),
    )
    assert gone.json()["code"] == 0
    new_id, _new_secret = await _connect_cc(client, admin_token, agent_id)
    assert new_id != old_id
    captured: dict = {"calls": 0}
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient", _CaptureClient(captured)
    )
    from careconnect_api.assessment_delivery import kick_assessment_delivery

    ok = await kick_assessment_delivery(db_session, int(row.id))
    assert ok is False
    assert captured.get("calls", 0) == 0
    db_session.expire_all()
    reloaded = await db_session.get(CcAssessmentDeliveryJob, job_id)
    assert reloaded is not None
    assert reloaded.public_client_id == old_id
    assert reloaded.public_client_id == frozen_client
    assert reloaded.last_error_category == "client_id_mismatch"
    assert reloaded.status == STATUS_DEAD_LETTER
    assert await db_session.get(AiMedicalAssessment, assessment_id) is not None
    attempts = (
        await db_session.execute(
            select(CcAssessmentDeliveryAttempt).where(
                CcAssessmentDeliveryAttempt.job_id == job_id
            )
        )
    ).scalars().all()
    assert len(attempts) == 1
    assert attempts[0].error_category == "client_id_mismatch"


async def _seed_chat(db: AsyncSession, agent_id: str) -> None:
    global _CHAT_PK
    _CHAT_PK += 1
    db.add(
        AiAgentChatHistory(
            id=_CHAT_PK,
            agent_id=agent_id,
            session_id="s-delivery",
            chat_type=CHAT_TYPE_CLIENT,
            content="I slept poorly and feel tired",
            created_at=datetime.now(),
        )
    )
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger_type", ["scheduled", "manual", "escalation_phrase"])
async def test_run_for_agent_enqueues_for_each_trigger(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    monkeypatch,
    trigger_type: str,
):
    agent_id = await _onboard(client, admin_token, f"Trig {trigger_type}")
    await _connect_cc(client, admin_token, agent_id)
    await _seed_chat(db_session, agent_id)
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)

    async def _llm(_dialogue: str) -> TriageResult:
        return TriageResult("low", 0.6, ["fatigue"], ["Phone check-in"])

    monkeypatch.setattr("careconnect_api.triage.runner._invoke_llm", _llm)
    captured: dict = {"calls": 0}
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient", _CaptureClient(captured)
    )
    row = await run_for_agent(db_session, agent_id, trigger_type=trigger_type)
    assert row is not None
    assert row.trigger_type == trigger_type
    job = (
        await db_session.execute(
            select(CcAssessmentDeliveryJob).where(
                CcAssessmentDeliveryJob.assessment_id == row.id
            )
        )
    ).scalar_one()
    assert job.status == STATUS_DELIVERED
    assert captured["calls"] == 1
    assert json.loads(job.payload_json)["schemaVersion"] == SCHEMA_VERSION


@pytest.mark.asyncio
async def test_enqueue_failure_rolls_back_assessment_and_keeps_chat(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Rollback Ray")
    await _connect_cc(client, admin_token, agent_id)
    await _seed_chat(db_session, agent_id)
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)

    async def _llm(_dialogue: str) -> TriageResult:
        return TriageResult("low", 0.6, ["fatigue"], ["Phone check-in"])

    monkeypatch.setattr("careconnect_api.triage.runner._invoke_llm", _llm)

    async def _boom(*_a, **_k):
        raise DeliveryEnqueueError("forced enqueue failure")

    monkeypatch.setattr(
        "careconnect_api.assessment_delivery.enqueue_careconnect_delivery", _boom
    )
    with pytest.raises(DeliveryEnqueueError):
        await run_for_agent(db_session, agent_id, trigger_type="manual")
    n_assess = (
        await db_session.execute(
            select(func.count()).select_from(AiMedicalAssessment).where(
                AiMedicalAssessment.agent_id == agent_id
            )
        )
    ).scalar_one()
    n_jobs = (
        await db_session.execute(select(func.count()).select_from(CcAssessmentDeliveryJob))
    ).scalar_one()
    n_chat = (
        await db_session.execute(
            select(func.count()).select_from(AiAgentChatHistory).where(
                AiAgentChatHistory.agent_id == agent_id
            )
        )
    ).scalar_one()
    assert int(n_assess or 0) == 0
    assert int(n_jobs or 0) == 0
    assert int(n_chat or 0) == 1


@pytest.mark.asyncio
async def test_disabled_delivery_run_persists_without_job(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Disabled Sue")
    await _connect_cc(client, admin_token, agent_id)
    await _seed_chat(db_session, agent_id)
    agent = await db_session.get(AiAgent, agent_id)
    merged = merge_profile(load_profile(agent.profile_json), {
        "assessmentDelivery": {"destination": "none"},
    })
    agent.profile_json = dump_profile(merged)
    await db_session.commit()
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL)

    async def _llm(_dialogue: str) -> TriageResult:
        return TriageResult("low", 0.6, ["fatigue"], ["Phone check-in"])

    monkeypatch.setattr("careconnect_api.triage.runner._invoke_llm", _llm)
    captured: dict = {"calls": 0}
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient", _CaptureClient(captured)
    )
    row = await run_for_agent(db_session, agent_id, trigger_type="manual")
    assert row is not None
    assert captured.get("calls", 0) == 0
    assert await _job_count(db_session, row.id) == 0
