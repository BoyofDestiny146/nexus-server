"""CareConnect ingest POST + gated outbound push (external URL only)."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import AiMedicalAssessment, ClientIntegration, SysUser
from careconnect_api.partner_auth import AUTH_FAIL_MSG
from careconnect_api.partner_payload import serialize_client_assessment_payload
from careconnect_api.partner_push import is_self_push_url, push_assessment_best_effort
from careconnect_api.settings import settings


_INGEST = "/api/v1/integrations/careconnect/ingest"
_ASSESSMENT = "/api/v1/integrations/careconnect/assessment"
_ASSESSMENT_PK = 9100
_LOCAL_INGEST = "https://care.nexus.warehouse-13.biz/api/v1/integrations/careconnect/ingest"
_EXTERNAL_INGEST = "https://careconnect.example.org/api/v1/integrations/careconnect/ingest"


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


def _partner(public_id: str, secret: str) -> dict[str, str]:
    return {
        "X-Client-Id": public_id,
        "X-Client-Secret": secret,
        "Content-Type": "application/json",
    }


def _blob(payload) -> str:
    return json.dumps(payload)


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


async def _seed_assessment(db: AsyncSession, agent_id: str) -> AiMedicalAssessment:
    global _ASSESSMENT_PK
    _ASSESSMENT_PK += 1
    row = AiMedicalAssessment(
        id=_ASSESSMENT_PK,
        agent_id=agent_id,
        for_date=date(2026, 9, 23),
        risk_level="low",
        confidence=Decimal("0.600"),
        concerns_json='["internal-only"]',
        recommendations_json='["Phone check-in to assess client\'s mental state"]',
        source_msg_count=3,
        llm_model="qwen2.5:3b",
        generated_at=datetime(2026, 9, 23, 9, 25, 0),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _v1_payload(public_id: str, assessment: AiMedicalAssessment) -> dict:
    return serialize_client_assessment_payload(
        public_client_id=public_id,
        assessment=assessment,
        tz_name="America/Chicago",
    )


class _CaptureClient:
    def __init__(self, store: dict, error: Exception | None = None):
        self._store = store
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
        sent = json
        if self._error:
            raise self._error

        class _Resp:
            status_code = 200

            def json(self):
                return {"code": 0, "msg": "success", "data": sent}

        return _Resp()


def test_self_push_url_detects_local_careconnect_host():
    assert is_self_push_url(_LOCAL_INGEST)
    assert is_self_push_url("http://127.0.0.1:8080/api/v1/integrations/careconnect/ingest")
    assert is_self_push_url("http://api:8080/api/v1/integrations/careconnect/ingest")
    assert is_self_push_url(
        "https://care.nexus.warehouse-13.biz/api/v1/integrations/careconnect/ingest",
        portal_base="https://care.nexus.warehouse-13.biz",
    )
    assert not is_self_push_url(_EXTERNAL_INGEST)
    assert not is_self_push_url(
        _EXTERNAL_INGEST, portal_base="https://care.nexus.warehouse-13.biz"
    )


@pytest.mark.asyncio
async def test_valid_ingest_accepted_and_payload_preserved(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, caplog
):
    agent_id = await _onboard(client, admin_token, "Ingest Ada")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    payload = _v1_payload(public_id, row)
    caplog.set_level(logging.INFO)

    resp = await client.post(_INGEST, json=payload, headers=_partner(public_id, secret))
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["schemaVersion"] == payload["schemaVersion"]
    assert data["clientId"] == public_id
    assert data["clientId"] != agent_id
    assert data["date"] == payload["date"]
    assert data["time"] == payload["time"]
    assert data["timestamp"] == payload["timestamp"]
    assert data["assessment"] == payload["assessment"]
    assert data["recommendations"] == payload["recommendations"]
    assert agent_id not in _blob(body)
    assert secret not in _blob(body)
    assert secret not in caplog.text
    assert "internal-only" not in _blob(body)


@pytest.mark.asyncio
async def test_ingest_bad_credentials_rejected(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, "Ingest Bea")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    payload = _v1_payload(public_id, row)

    wrong = await client.post(
        _INGEST, json=payload, headers=_partner(public_id, "not-the-secret")
    )
    missing = await client.post(_INGEST, json=payload)
    unknown = await client.post(
        _INGEST, json=payload, headers=_partner("Nx-NOEXIST01", secret)
    )
    for resp in (wrong, missing, unknown):
        body = resp.json()
        assert body["code"] == 401
        assert body["msg"] == AUTH_FAIL_MSG
        assert body["data"] is None
        assert secret not in _blob(body)


@pytest.mark.asyncio
async def test_ingest_requires_matching_nx_client_id(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    a = await _onboard(client, admin_token, "Ingest A")
    b = await _onboard(client, admin_token, "Ingest B")
    a_id, a_secret = await _connect_cc(client, admin_token, a)
    b_id, b_secret = await _connect_cc(client, admin_token, b)
    row_a = await _seed_assessment(db_session, a)
    payload_b_id = _v1_payload(b_id, row_a)

    resp = await client.post(
        _INGEST, json=payload_b_id, headers=_partner(a_id, a_secret)
    )
    body = resp.json()
    assert body["code"] == 400
    assert "clientId" in body["msg"]
    assert a not in _blob(body)
    assert b not in _blob(body)
    assert a_secret not in _blob(body)
    assert b_secret not in _blob(body)


@pytest.mark.asyncio
async def test_env_unset_makes_no_outbound_http_call(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Push Off")
    await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    captured: dict = {"calls": 0}
    monkeypatch.setattr(settings, "careconnect_ingest_url", "")
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient", _CaptureClient(captured)
    )
    ok = await push_assessment_best_effort(db_session, agent_id, row)
    assert ok is False
    assert captured.get("calls", 0) == 0
    still = (
        await db_session.execute(
            select(AiMedicalAssessment).where(AiMedicalAssessment.agent_id == agent_id)
        )
    ).scalars().all()
    assert len(still) == 1


@pytest.mark.asyncio
async def test_same_host_url_self_push_skipped(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch, caplog
):
    agent_id = await _onboard(client, admin_token, "Push Self")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    captured: dict = {"calls": 0}
    monkeypatch.setattr(settings, "careconnect_ingest_url", _LOCAL_INGEST)
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient", _CaptureClient(captured)
    )
    caplog.set_level(logging.INFO)
    ok = await push_assessment_best_effort(db_session, agent_id, row)
    assert ok is False
    assert captured.get("calls", 0) == 0
    assert "self-push skipped" in caplog.text
    assert public_id in caplog.text
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_external_host_posts_v1_payload_without_logging_secret(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch, caplog
):
    agent_id = await _onboard(client, admin_token, "Push Cara")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    captured: dict = {"calls": 0}
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL_INGEST)
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient", _CaptureClient(captured)
    )
    caplog.set_level(logging.INFO)

    ok = await push_assessment_best_effort(db_session, agent_id, row)
    assert ok is True
    assert captured["calls"] == 1
    assert captured["url"] == _EXTERNAL_INGEST
    sent = captured["json"]
    assert sent["clientId"] == public_id
    assert sent["clientId"] != agent_id
    assert sent["assessment"]["level"] == "Low"
    assert sent["assessment"]["confidence"] == 60
    assert sent["recommendations"] == ["Phone check-in to assess client's mental state"]
    assert agent_id not in _blob(sent)
    assert captured["headers"]["X-Client-Id"] == public_id
    assert captured["headers"]["X-Client-Secret"] == secret
    assert secret not in caplog.text
    assert "internal-only" not in _blob(sent)


@pytest.mark.asyncio
async def test_failed_external_push_keeps_committed_assessment(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch, caplog
):
    agent_id = await _onboard(client, admin_token, "Push Dee")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    row = await _seed_assessment(db_session, agent_id)
    captured: dict = {"calls": 0}
    monkeypatch.setattr(settings, "careconnect_ingest_url", _EXTERNAL_INGEST)
    monkeypatch.setattr(
        "careconnect_api.partner_push.httpx.AsyncClient",
        _CaptureClient(captured, error=httpx.ConnectError("destination down")),
    )
    caplog.set_level(logging.INFO)

    ok = await push_assessment_best_effort(db_session, agent_id, row)
    assert ok is False
    assert captured["calls"] == 1
    assert secret not in caplog.text

    pk = row.id
    db_session.expire_all()
    still = (
        await db_session.execute(
            select(AiMedicalAssessment).where(AiMedicalAssessment.id == pk)
        )
    ).scalar_one()
    assert still.risk_level == "low"

    readback = await client.get(_ASSESSMENT, headers=_partner(public_id, secret))
    body = readback.json()
    assert body["code"] == 0, body
    assert body["data"]["clientId"] == public_id
    assert body["data"]["assessment"]["level"] == "Low"


@pytest.mark.asyncio
async def test_bcrypt_only_integration_still_authenticates_get(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    """Dashboard/GET must work without secret_enc."""
    agent_id = await _onboard(client, admin_token, "Hash Only")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    await _seed_assessment(db_session, agent_id)
    db_session.expire_all()
    row = (
        await db_session.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == "careconnect",
            )
        )
    ).scalar_one()
    row.secret_enc = None
    await db_session.commit()

    readback = await client.get(_ASSESSMENT, headers=_partner(public_id, secret))
    body = readback.json()
    assert body["code"] == 0, body
    assert body["data"]["clientId"] == public_id
    listed = (
        await client.get(
            f"/api/agent/{agent_id}/integrations", headers=_auth(admin_token)
        )
    ).json()
    assert listed["code"] == 0
    cc = next(r for r in listed["data"]["list"] if r["provider"] == "careconnect")
    assert cc["connected"] is True
    assert cc.get("secret") is None
    assert secret not in _blob(listed)
