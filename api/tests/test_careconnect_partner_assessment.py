"""CareConnect partner assessment API (X-Client-Id + X-Client-Secret)."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import AiMedicalAssessment, SysUser
from careconnect_api.partner_auth import AUTH_FAIL_MSG
from careconnect_api.partner_payload import (
    SCHEMA_VERSION,
    build_client_assessment_payload,
    serialize_client_assessment_payload,
)


_ASSESSMENT_PATH = "/api/v1/integrations/careconnect/assessment"
_PORTAL = "https://care.nexus.warehouse-13.biz"
_ENDPOINT = f"{_PORTAL}{_ASSESSMENT_PATH}"
_API_KEY = "test-api-key-abc123"
_ASSESSMENT_PK = 8000


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


async def _seed_assessment(
    db: AsyncSession,
    agent_id: str,
    *,
    risk: str = "low",
    confidence: float = 0.60,
    recs: list[str] | None = None,
    generated_at: datetime | None = None,
    for_date: date | None = None,
) -> AiMedicalAssessment:
    global _ASSESSMENT_PK
    _ASSESSMENT_PK += 1
    row = AiMedicalAssessment(
        id=_ASSESSMENT_PK,
        agent_id=agent_id,
        for_date=for_date or date(2026, 9, 23),
        risk_level=risk,
        confidence=Decimal("0.600") if confidence == 0.60 else Decimal(str(confidence)),
        concerns_json='["internal-only concern"]',
        recommendations_json=json.dumps(
            recs if recs is not None else ["Phone check-in to assess client's mental state"]
        ),
        source_msg_count=4,
        llm_model="qwen2.5:3b",
        generated_at=generated_at or datetime(2026, 9, 23, 9, 25, 0),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def test_serializer_maps_assessment_fields_without_internal_ids():
    row = AiMedicalAssessment(
        id=99,
        agent_id="agent-internal-id",
        for_date=date(2026, 9, 23),
        risk_level="low",
        confidence=Decimal("0.600"),
        concerns_json='["do-not-leak"]',
        recommendations_json='["Phone check-in to assess client\'s mental state"]',
        generated_at=datetime(2026, 9, 23, 9, 25, 0),
    )
    payload = serialize_client_assessment_payload(
        public_client_id="Nx-A7K29P4QX",
        assessment=row,
        tz_name="America/Chicago",
    )
    assert payload["schemaVersion"] == SCHEMA_VERSION
    assert payload["clientId"] == "Nx-A7K29P4QX"
    assert payload["date"] == "2026-09-23"
    assert payload["time"] == "09:25:00"
    assert payload["timestamp"] == "2026-09-23T09:25:00-05:00"
    assert payload["assessment"] == {"level": "Low", "confidence": 60}
    assert payload["recommendations"] == ["Phone check-in to assess client's mental state"]
    blob = _blob(payload)
    assert "agent-internal-id" not in blob
    assert "do-not-leak" not in blob
    assert '"id": 99' not in blob
    assert "secret" not in blob.lower()


@pytest.mark.asyncio
async def test_valid_credentials_return_envelope_and_mapped_payload(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, caplog
):
    agent_id = await _onboard(client, admin_token, "Ada")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    await _seed_assessment(db_session, agent_id)
    caplog.set_level(logging.INFO)

    resp = await client.get(_ASSESSMENT_PATH, headers=_partner(public_id, secret))
    body = resp.json()
    assert body["code"] == 0, body
    assert body["msg"] == "success"
    data = body["data"]
    assert data["schemaVersion"] == "1.0"
    assert data["clientId"] == public_id
    assert data["clientId"].startswith("Nx-")
    assert data["clientId"] != agent_id
    assert data["date"] == "2026-09-23"
    assert data["time"] == "09:25:00"
    assert data["timestamp"] == "2026-09-23T09:25:00-05:00"
    assert data["assessment"]["level"] == "Low"
    assert data["assessment"]["confidence"] == 60
    assert data["recommendations"] == ["Phone check-in to assess client's mental state"]
    blob = _blob(body)
    assert agent_id not in blob
    assert secret not in blob
    assert secret not in caplog.text
    assert "secret_hash" not in blob
    assert "internal-only concern" not in blob


@pytest.mark.asyncio
async def test_invalid_secret_is_auth_failure(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, "Bea")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    await _seed_assessment(db_session, agent_id)
    resp = await client.get(_ASSESSMENT_PATH, headers=_partner(public_id, "wrong-secret"))
    body = resp.json()
    assert body["code"] == 401
    assert body["msg"] == AUTH_FAIL_MSG
    assert body["data"] is None
    assert secret not in _blob(body)


@pytest.mark.asyncio
async def test_unknown_nx_id_does_not_leak_existence(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, "Cara")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    await _seed_assessment(db_session, agent_id)
    unknown = await client.get(
        _ASSESSMENT_PATH, headers=_partner("Nx-NOEXIST01", secret)
    )
    missing = await client.get(_ASSESSMENT_PATH)
    wrong = await client.get(
        _ASSESSMENT_PATH, headers=_partner(public_id, "nope")
    )
    for resp in (unknown, missing, wrong):
        body = resp.json()
        assert body["code"] == 401
        assert body["msg"] == AUTH_FAIL_MSG
        assert body["data"] is None
        assert "not found" not in body["msg"].lower()
        assert public_id not in body["msg"]


@pytest.mark.asyncio
async def test_missing_headers_auth_failure(client: AsyncClient, admin_token: str):
    agent_id = await _onboard(client, admin_token, "Dee")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    only_id = await client.get(_ASSESSMENT_PATH, headers={"X-Client-Id": public_id})
    only_secret = await client.get(
        _ASSESSMENT_PATH, headers={"X-Client-Secret": secret}
    )
    query = await client.get(
        f"{_ASSESSMENT_PATH}?clientId={public_id}&secret={secret}"
    )
    for resp in (only_id, only_secret, query):
        body = resp.json()
        assert body["code"] == 401
        assert body["msg"] == AUTH_FAIL_MSG
        assert secret not in _blob(body)


@pytest.mark.asyncio
async def test_secret_never_in_listing_or_logs(
    client: AsyncClient, admin_token: str, caplog
):
    caplog.set_level(logging.INFO)
    agent_id = await _onboard(client, admin_token, "Eve")
    created = (
        await client.post(
            f"/api/agent/{agent_id}/integrations/careconnect",
            headers=_auth(admin_token),
        )
    ).json()["data"]
    secret = created["secret"]
    listed = (
        await client.get(
            f"/api/agent/{agent_id}/integrations", headers=_auth(admin_token)
        )
    ).json()
    cc = next(r for r in listed["data"]["list"] if r["provider"] == "careconnect")
    assert cc["portal"] == _PORTAL
    assert cc["assessmentEndpoint"] == _ENDPOINT
    assert cc.get("secret") is None
    assert secret not in _blob(listed)
    assert '"secret":' not in _blob(listed["data"])
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_payload_belongs_only_to_authenticated_client(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    a = await _onboard(client, admin_token, "Client A")
    b = await _onboard(client, admin_token, "Client B")
    a_id, a_secret = await _connect_cc(client, admin_token, a)
    b_id, b_secret = await _connect_cc(client, admin_token, b)
    await _seed_assessment(
        db_session, a, recs=["Stay with A"], risk="urgent", confidence=0.9
    )
    await _seed_assessment(
        db_session, b, recs=["Stay with B"], risk="moderate", confidence=0.4
    )

    a_body = (
        await client.get(_ASSESSMENT_PATH, headers=_partner(a_id, a_secret))
    ).json()["data"]
    b_body = (
        await client.get(_ASSESSMENT_PATH, headers=_partner(b_id, b_secret))
    ).json()["data"]
    assert a_body["clientId"] == a_id
    assert b_body["clientId"] == b_id
    assert a_body["recommendations"] == ["Stay with A"]
    assert b_body["recommendations"] == ["Stay with B"]
    assert a_body["assessment"]["level"] == "Urgent"
    assert b_body["assessment"]["level"] == "Moderate"
    assert "Stay with B" not in _blob(a_body)
    assert "Stay with A" not in _blob(b_body)
    assert a not in _blob(a_body)
    assert b not in _blob(b_body)


@pytest.mark.asyncio
async def test_disconnected_credentials_stop_working(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, "Fay")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    await _seed_assessment(db_session, agent_id)
    ok = await client.get(_ASSESSMENT_PATH, headers=_partner(public_id, secret))
    assert ok.json()["code"] == 0
    deleted = await client.delete(
        f"/api/agent/{agent_id}/integrations/careconnect",
        headers=_auth(admin_token),
    )
    assert deleted.json()["code"] == 0
    after = await client.get(_ASSESSMENT_PATH, headers=_partner(public_id, secret))
    assert after.json()["code"] == 401
    assert after.json()["msg"] == AUTH_FAIL_MSG


@pytest.mark.asyncio
async def test_rotated_old_secret_stops_new_secret_works(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, "Gia")
    public_id, old_secret = await _connect_cc(client, admin_token, agent_id)
    await _seed_assessment(db_session, agent_id)
    rotated = (
        await client.post(
            f"/api/agent/{agent_id}/integrations/careconnect/rotate",
            headers=_auth(admin_token),
        )
    ).json()["data"]
    new_secret = rotated["secret"]
    assert new_secret != old_secret
    assert rotated["publicId"] == public_id

    stale = await client.get(_ASSESSMENT_PATH, headers=_partner(public_id, old_secret))
    assert stale.json()["code"] == 401
    fresh = await client.get(_ASSESSMENT_PATH, headers=_partner(public_id, new_secret))
    assert fresh.json()["code"] == 0
    assert fresh.json()["data"]["clientId"] == public_id
    assert old_secret not in _blob(fresh.json())
    assert new_secret not in _blob(fresh.json())


@pytest.mark.asyncio
async def test_careconnect_secret_cannot_login_or_use_watcher_key(
    client: AsyncClient, admin_token: str
):
    agent_id = await _onboard(client, admin_token, "Hana")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)

    dash = await client.post(
        "/api/user/login",
        json={"username": "admin1", "password": secret},
    )
    assert dash.json()["code"] == 401

    nx_login = await client.post(
        "/api/user/login",
        json={"username": public_id, "password": secret},
    )
    assert nx_login.json()["code"] == 401

    watcher = await client.get(
        "/api/v1/watchers",
        headers={"X-API-Key": secret},
    )
    assert watcher.json()["code"] == 401

    jwt_only = await client.get(_ASSESSMENT_PATH, headers=_auth(admin_token))
    assert jwt_only.json()["code"] == 401
    assert jwt_only.json()["msg"] == AUTH_FAIL_MSG

    api_key_only = await client.get(
        _ASSESSMENT_PATH, headers={"X-API-Key": _API_KEY}
    )
    assert api_key_only.json()["code"] == 401


@pytest.mark.asyncio
async def test_builder_loads_latest_row_only(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, "Ivy")
    public_id, _secret = await _connect_cc(client, admin_token, agent_id)
    await _seed_assessment(
        db_session,
        agent_id,
        for_date=date(2026, 9, 20),
        generated_at=datetime(2026, 9, 20, 8, 0, 0),
        recs=["older"],
        risk="moderate",
        confidence=0.2,
    )
    await _seed_assessment(
        db_session,
        agent_id,
        for_date=date(2026, 9, 23),
        generated_at=datetime(2026, 9, 23, 9, 25, 0),
        recs=["newest"],
        risk="elevated",
        confidence=0.75,
    )
    payload = await build_client_assessment_payload(db_session, agent_id, public_id)
    assert payload["clientId"] == public_id
    assert payload["recommendations"] == ["newest"]
    assert payload["assessment"]["level"] == "Elevated"
    assert payload["assessment"]["confidence"] == 75
    assert agent_id not in _blob(payload)
