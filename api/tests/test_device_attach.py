"""Attach an auto-registered Watcher without duplicating the ai_device row.

Production auto-reg stores colon-separated MAC (``E0:72:A1:DB:36:40``) under
canonical id ``watcher-<stripped-hex>``. The dashboard attach path previously
looked up stripped hex only, missed the row, inserted the same PK, and 500'd.

These tests seed the device the same way heartbeat/WS registration does, then
call ``POST /api/device/attach``.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.models import AiDevice
from careconnect_api.watcher_device import (
    device_id_for_mac,
    get_watcher_device,
    mac_lookup_candidates,
)

_API_KEY = "test-api-key-abc123"
_HEADERS_KEY = {"X-API-Key": _API_KEY}

_COLON_MAC = "E0:72:A1:DB:36:40"
_STRIPPED_MAC = "E072A1DB3640"
_LOWER_COLON = "e0:72:a1:db:36:40"
_CANONICAL_ID = "watcher-e072a1db3640"


@pytest_asyncio.fixture(scope="function")
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    """Mint a root JWT directly.

    Login via password is blocked by a pre-existing bcrypt verify mismatch
    against hashes written through seed_two_admins; attach does not need that
    path. ``client`` is requested so the in-memory app/DB is wired first.
    """
    from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
    from careconnect_api.models import SysUser

    _ = client
    user = SysUser(
        id=1,
        username="admin1",
        password=hash_password("unused-in-these-tests"),
        super_admin=ROLE_ROOT,
        status=1,
    )
    db_session.add(user)
    await db_session.commit()
    token, _expire = issue_token(user.id, user.username, ROLE_ROOT)
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _onboard_client(client: AsyncClient, token: str, name: str = "George") -> str:
    resp = await client.post(
        "/api/agent/onboard",
        json={"name": name},
        headers=_auth(token),
    )
    data = resp.json()
    assert data["code"] == 0, data
    return data["data"]["agentId"]


async def _auto_register_colon_watcher(client: AsyncClient) -> None:
    """Same path as production WS/heartbeat: colon MAC, telemetry populated."""
    resp = await client.post(
        "/api/v1/watcher/heartbeat",
        json={"mac": _COLON_MAC, "battery": 77, "fw": "1.9.0", "rssi": -50},
        headers=_HEADERS_KEY,
    )
    body = resp.json()
    assert body["code"] == 0, body


async def _attach(
    client: AsyncClient,
    token: str,
    agent_id: str,
    eui: str,
    *,
    alias: str | None = "george",
    force: bool = False,
) -> dict:
    payload: dict = {
        "agentId": agent_id,
        "eui": eui,
        "deviceType": "W1-A",
        "firmwareType": "xiaozhi",
        "force": force,
    }
    if alias is not None:
        payload["alias"] = alias
    resp = await client.post("/api/device/attach", json=payload, headers=_auth(token))
    return resp.json()


async def _watcher_row_count(db_session: AsyncSession) -> int:
    n = (
        await db_session.execute(
            select(func.count())
            .select_from(AiDevice)
            .where(
                AiDevice.mac_address.in_(mac_lookup_candidates(_COLON_MAC))
                | (AiDevice.id == device_id_for_mac(_COLON_MAC))
            )
        )
    ).scalar_one()
    return int(n or 0)


async def _reload(db_session: AsyncSession, mac: str = _COLON_MAC) -> AiDevice:
    db_session.expire_all()
    dev = await get_watcher_device(db_session, mac)
    assert dev is not None
    return dev


@pytest.mark.asyncio
async def test_colon_stored_watcher_attach_with_colon_input(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard_client(client, admin_token)
    await _auto_register_colon_watcher(client)
    before = await _reload(db_session)
    assert before.id == _CANONICAL_ID
    assert before.mac_address == _COLON_MAC
    assert before.agent_id is None
    assert before.battery == 77
    assert before.fw == "1.9.0"
    assert before.rssi == -50

    body = await _attach(client, admin_token, agent_id, _COLON_MAC)
    assert body["code"] == 0, body
    assert body["data"]["deviceId"] == _CANONICAL_ID
    assert body["data"]["agentId"] == agent_id
    assert body["data"]["alias"] == "george"

    after = await _reload(db_session)
    assert after.id == _CANONICAL_ID
    assert after.mac_address == _COLON_MAC
    assert after.agent_id == agent_id
    assert after.alias == "george"
    assert after.battery == 77
    assert after.fw == "1.9.0"
    assert after.rssi == -50
    assert after.client_device_id is None
    assert await _watcher_row_count(db_session) == 1


@pytest.mark.asyncio
async def test_colon_stored_watcher_attach_with_stripped_input(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard_client(client, admin_token)
    await _auto_register_colon_watcher(client)

    body = await _attach(client, admin_token, agent_id, _STRIPPED_MAC)
    assert body["code"] == 0, body
    assert body["data"]["deviceId"] == _CANONICAL_ID

    after = await _reload(db_session, _STRIPPED_MAC)
    assert after.id == _CANONICAL_ID
    assert after.mac_address == _COLON_MAC  # existing spelling preserved
    assert after.agent_id == agent_id
    assert after.battery == 77
    assert after.fw == "1.9.0"
    assert after.rssi == -50
    assert await _watcher_row_count(db_session) == 1


@pytest.mark.asyncio
async def test_attach_does_not_duplicate_auto_registered_row(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard_client(client, admin_token)
    await _auto_register_colon_watcher(client)
    assert await _watcher_row_count(db_session) == 1

    body = await _attach(client, admin_token, agent_id, _LOWER_COLON)
    assert body["code"] == 0, body
    assert await _watcher_row_count(db_session) == 1
    assert (await _reload(db_session)).id == _CANONICAL_ID


@pytest.mark.asyncio
async def test_same_client_reattach_is_idempotent(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard_client(client, admin_token)
    await _auto_register_colon_watcher(client)

    first = await _attach(client, admin_token, agent_id, _COLON_MAC, alias="george")
    assert first["code"] == 0, first
    second = await _attach(client, admin_token, agent_id, _STRIPPED_MAC, alias="george")
    assert second["code"] == 0, second
    assert second["data"]["deviceId"] == first["data"]["deviceId"] == _CANONICAL_ID
    assert second["data"]["agentId"] == agent_id

    after = await _reload(db_session)
    assert after.mac_address == _COLON_MAC
    assert after.battery == 77
    assert after.fw == "1.9.0"
    assert after.rssi == -50
    assert await _watcher_row_count(db_session) == 1


@pytest.mark.asyncio
async def test_other_client_attach_without_force_returns_409(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_a = await _onboard_client(client, admin_token, name="Alice")
    agent_b = await _onboard_client(client, admin_token, name="Bob")
    await _auto_register_colon_watcher(client)

    bound = await _attach(client, admin_token, agent_a, _COLON_MAC, alias="alice-watch")
    assert bound["code"] == 0, bound

    conflict = await _attach(client, admin_token, agent_b, _STRIPPED_MAC, alias="bob-watch")
    assert conflict["code"] == 409, conflict
    assert conflict["data"]["existingAgentId"] == agent_a
    assert conflict["data"]["deviceId"] == _CANONICAL_ID

    # Row unchanged — still Alice's, colon MAC and telemetry intact.
    after = await _reload(db_session)
    assert after.agent_id == agent_a
    assert after.alias == "alice-watch"
    assert after.mac_address == _COLON_MAC
    assert after.battery == 77
    assert await _watcher_row_count(db_session) == 1

    # force=true still rebinds.
    forced = await _attach(
        client, admin_token, agent_b, _COLON_MAC, alias="bob-watch", force=True
    )
    assert forced["code"] == 0, forced
    assert forced["data"]["agentId"] == agent_b
    assert forced["data"]["previousAgentId"] == agent_a
    rebound = await _reload(db_session)
    assert rebound.agent_id == agent_b
    assert rebound.alias == "bob-watch"
    assert rebound.mac_address == _COLON_MAC
    assert rebound.battery == 77
    assert await _watcher_row_count(db_session) == 1
