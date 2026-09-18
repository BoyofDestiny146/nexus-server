"""Tests for the optional client_device_id (external device identifier).

Covers:
  - onboard persists clientDeviceId and the response echoes it
  - onboard without clientDeviceId leaves it null (optional)
  - GET /api/device/by-client-id/{id} resolves the device + bound agent (root)
  - GET /api/device/by-client-id/{id} 404 for an unknown id
  - GET /api/v1/watcher/by-client-id/{id}/status resolves via the client API key
  - GET /api/v1/watcher/by-client-id/{id}/status 404 for an unknown id
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

_API_KEY = "test-api-key-abc123"
_HEADERS_KEY = {"X-API-Key": _API_KEY}
_CLIENT_ID = "CLIENT-DEV-0001"
_EUI = "AABBCCDDEE01"


@pytest_asyncio.fixture(scope="function")
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    from careconnect_api.bootstrap_root import seed_two_admins

    await seed_two_admins(db_session)
    resp = await client.post(
        "/api/user/login",
        json={"username": "admin1", "password": "AdminPassword1!"},
    )
    data = resp.json()
    assert data["code"] == 0, data
    return data["data"]["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _onboard(client: AsyncClient, token: str, **extra) -> dict:
    payload = {"name": "Device-Id Tester", "eui": _EUI, **extra}
    resp = await client.post("/api/agent/onboard", json=payload, headers=_auth(token))
    data = resp.json()
    assert data["code"] == 0, data
    return data["data"]


@pytest.mark.asyncio
async def test_onboard_persists_client_device_id(client: AsyncClient, admin_token: str):
    data = await _onboard(client, admin_token, clientDeviceId=_CLIENT_ID)
    assert data["deviceId"]  # internal watcher id still present
    # Dashboard lookup by the client's external id
    resp = await client.get(
        f"/api/device/by-client-id/{_CLIENT_ID}", headers=_auth(admin_token)
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["clientDeviceId"] == _CLIENT_ID
    assert body["data"]["macAddress"] == _EUI
    assert body["data"]["agentId"]


@pytest.mark.asyncio
async def test_onboard_without_client_device_id_is_null(client: AsyncClient, admin_token: str):
    await _onboard(client, admin_token)  # no clientDeviceId
    # Not resolvable by a client id that was never set
    resp = await client.get(
        "/api/device/by-client-id/NEVER-SET", headers=_auth(admin_token)
    )
    assert resp.json()["code"] == 404


@pytest.mark.asyncio
async def test_dashboard_lookup_unknown_404(client: AsyncClient, admin_token: str):
    resp = await client.get(
        "/api/device/by-client-id/does-not-exist", headers=_auth(admin_token)
    )
    assert resp.json()["code"] == 404


@pytest.mark.asyncio
async def test_client_api_status_by_client_id(client: AsyncClient, admin_token: str):
    await _onboard(client, admin_token, clientDeviceId=_CLIENT_ID)
    resp = await client.get(
        f"/api/v1/watcher/by-client-id/{_CLIENT_ID}/status", headers=_HEADERS_KEY
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["clientDeviceId"] == _CLIENT_ID
    assert body["data"]["mac"] == _EUI
    assert body["data"]["online"] is False  # never heartbeated


@pytest.mark.asyncio
async def test_client_api_status_unknown_404(client: AsyncClient):
    resp = await client.get(
        "/api/v1/watcher/by-client-id/nope/status", headers=_HEADERS_KEY
    )
    assert resp.json()["code"] == 404


@pytest.mark.asyncio
async def test_client_api_status_requires_key(client: AsyncClient):
    resp = await client.get(f"/api/v1/watcher/by-client-id/{_CLIENT_ID}/status")
    assert resp.json()["code"] == 401


async def _add_chat_turn(db_session: AsyncSession, agent_id: str, when):
    from careconnect_api.models import AiAgentChatHistory

    # SQLite doesn't autoincrement BigInteger PKs (MariaDB does), so set id here.
    db_session.add(
        AiAgentChatHistory(
            id=int(when.timestamp() * 1000),
            agent_id=agent_id, mac_address="ignored", chat_type=1,
            content="hello", created_at=when,
        )
    )
    await db_session.commit()


async def _agent_for_client_id(db_session: AsyncSession, client_id: str) -> str:
    from sqlalchemy import select
    from careconnect_api.models import AiDevice

    dev = (
        await db_session.execute(
            select(AiDevice).where(AiDevice.client_device_id == client_id)
        )
    ).scalar_one()
    return dev.agent_id


@pytest.mark.asyncio
async def test_status_last_seen_derived_from_recent_conversation(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    """No heartbeat is ever POSTed; a recent chat turn makes the device online."""
    from datetime import datetime, timedelta

    await _onboard(client, admin_token, clientDeviceId=_CLIENT_ID)
    agent_id = await _agent_for_client_id(db_session, _CLIENT_ID)
    await _add_chat_turn(db_session, agent_id, datetime.utcnow() - timedelta(seconds=15))

    resp = await client.get(
        f"/api/v1/watcher/by-client-id/{_CLIENT_ID}/status", headers=_HEADERS_KEY
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["last_seen"] is not None
    assert body["data"]["online"] is True  # within the 120s window


@pytest.mark.asyncio
async def test_status_last_seen_derived_old_conversation_offline(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    """An old conversation surfaces last_seen but reports offline."""
    from datetime import datetime, timedelta

    await _onboard(client, admin_token, clientDeviceId=_CLIENT_ID)
    agent_id = await _agent_for_client_id(db_session, _CLIENT_ID)
    await _add_chat_turn(db_session, agent_id, datetime.utcnow() - timedelta(days=3))

    resp = await client.get(
        f"/api/v1/watcher/by-client-id/{_CLIENT_ID}/status", headers=_HEADERS_KEY
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["last_seen"] is not None  # real timestamp, not null
    assert body["data"]["online"] is False


# ---------- PATCH /api/device/{id}/client-id (edit external id) ----------

async def _patch_client_id(client: AsyncClient, token: str, device_id: str, value):
    resp = await client.patch(
        f"/api/device/{device_id}/client-id",
        json={"clientDeviceId": value},
        headers=_auth(token),
    )
    return resp.json()


@pytest.mark.asyncio
async def test_patch_sets_client_id_on_existing_device(client: AsyncClient, admin_token: str):
    data = await _onboard(client, admin_token)  # created WITHOUT a client id
    body = await _patch_client_id(client, admin_token, data["deviceId"], "EXT-42")
    assert body["code"] == 0, body
    assert body["data"]["clientDeviceId"] == "EXT-42"
    # resolvable by the new id
    resp = await client.get("/api/device/by-client-id/EXT-42", headers=_auth(admin_token))
    assert resp.json()["data"]["macAddress"] == _EUI


@pytest.mark.asyncio
async def test_patch_changes_and_clears_client_id(client: AsyncClient, admin_token: str):
    data = await _onboard(client, admin_token, clientDeviceId=_CLIENT_ID)
    body = await _patch_client_id(client, admin_token, data["deviceId"], "EXT-NEW")
    assert body["code"] == 0 and body["data"]["clientDeviceId"] == "EXT-NEW"
    # old id no longer resolves
    resp = await client.get(
        f"/api/device/by-client-id/{_CLIENT_ID}", headers=_auth(admin_token)
    )
    assert resp.json()["code"] == 404
    # blank clears
    body = await _patch_client_id(client, admin_token, data["deviceId"], "  ")
    assert body["code"] == 0 and body["data"]["clientDeviceId"] is None
    resp = await client.get("/api/device/by-client-id/EXT-NEW", headers=_auth(admin_token))
    assert resp.json()["code"] == 404


@pytest.mark.asyncio
async def test_patch_conflict_with_other_device_409(client: AsyncClient, admin_token: str):
    await _onboard(client, admin_token, clientDeviceId=_CLIENT_ID)
    other = await _onboard(
        client, admin_token, name="Second Client", eui="AABBCCDDEE02"
    )
    body = await _patch_client_id(client, admin_token, other["deviceId"], _CLIENT_ID)
    assert body["code"] == 409, body


@pytest.mark.asyncio
async def test_patch_unknown_device_404(client: AsyncClient, admin_token: str):
    body = await _patch_client_id(client, admin_token, "no-such-device", "EXT-1")
    assert body["code"] == 404


@pytest.mark.asyncio
async def test_dashboard_last_connected_derived_from_conversation(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    """Nothing writes ai_device.last_connected_at, so dashboard rows derive it
    from the device's last chat turn (was always "never")."""
    from datetime import datetime, timedelta

    await _onboard(client, admin_token, clientDeviceId=_CLIENT_ID)
    agent_id = await _agent_for_client_id(db_session, _CLIENT_ID)

    resp = await client.get(f"/api/device/bind/{agent_id}", headers=_auth(admin_token))
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"][0]["lastConnectedAt"] is None  # no chat yet

    await _add_chat_turn(db_session, agent_id, datetime.utcnow() - timedelta(minutes=2))
    resp = await client.get(f"/api/device/bind/{agent_id}", headers=_auth(admin_token))
    assert resp.json()["data"][0]["lastConnectedAt"] is not None
