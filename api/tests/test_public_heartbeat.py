"""Tests for the public watcher heartbeat API (Task 1).

TDD: these tests were written before the implementation.

Coverage:
  - valid X-API-Key -> 200 on all three endpoints
  - missing X-API-Key -> 401
  - wrong X-API-Key -> 401
  - POST heartbeat upserts device row (last_seen populated)
  - GET status shows online=True right after heartbeat
  - GET status shows online=False when last_seen is beyond the window
  - GET /watchers lists all devices
  - heartbeat on a MAC not in ai_device auto-creates the row
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

# The API key written by conftest
_API_KEY = "test-api-key-abc123"
_HEADERS_OK = {"X-API-Key": _API_KEY}
_MAC = "AABBCCDDEEFF"
_MAC2 = "112233445566"


@pytest.mark.asyncio
async def test_heartbeat_missing_key_401(client: AsyncClient):
    resp = await client.post("/api/v1/watcher/heartbeat", json={"mac": _MAC})
    assert resp.status_code == 200  # envelope always 200
    data = resp.json()
    assert data["code"] == 401, f"expected 401, got {data}"


@pytest.mark.asyncio
async def test_heartbeat_wrong_key_401(client: AsyncClient):
    resp = await client.post(
        "/api/v1/watcher/heartbeat",
        json={"mac": _MAC},
        headers={"X-API-Key": "totally-wrong-key"},
    )
    data = resp.json()
    assert data["code"] == 401


@pytest.mark.asyncio
async def test_heartbeat_valid_key_200(client: AsyncClient):
    resp = await client.post(
        "/api/v1/watcher/heartbeat",
        json={"mac": _MAC, "battery": 85, "fw": "1.9.0", "rssi": -62},
        headers=_HEADERS_OK,
    )
    data = resp.json()
    assert data["code"] == 0, data
    assert data["data"]["mac"] == _MAC


@pytest.mark.asyncio
async def test_status_missing_key_401(client: AsyncClient):
    # Seed a device first (valid key)
    await client.post("/api/v1/watcher/heartbeat", json={"mac": _MAC}, headers=_HEADERS_OK)
    resp = await client.get(f"/api/v1/watcher/{_MAC}/status")
    data = resp.json()
    assert data["code"] == 401


@pytest.mark.asyncio
async def test_status_shows_online_after_heartbeat(client: AsyncClient):
    resp = await client.post(
        "/api/v1/watcher/heartbeat",
        json={"mac": _MAC, "battery": 90},
        headers=_HEADERS_OK,
    )
    assert resp.json()["code"] == 0

    resp2 = await client.get(f"/api/v1/watcher/{_MAC}/status", headers=_HEADERS_OK)
    data = resp2.json()["data"]
    assert data["online"] is True
    from careconnect_api.watcher_device import stripped_mac
    assert stripped_mac(data["mac"]) == stripped_mac(_MAC)
    assert data["battery"] == 90
    assert data["last_seen"] is not None


@pytest.mark.asyncio
async def test_status_404_unknown_mac(client: AsyncClient):
    resp = await client.get("/api/v1/watcher/DEADBEEF0000/status", headers=_HEADERS_OK)
    data = resp.json()
    assert data["code"] == 404


@pytest.mark.asyncio
async def test_status_shows_offline_after_window(client: AsyncClient, db_session: AsyncSession):
    """Seed a device with last_seen in the past (beyond the online window)."""
    from careconnect_api.models import AiDevice

    # First create the device via heartbeat.
    await client.post("/api/v1/watcher/heartbeat", json={"mac": _MAC}, headers=_HEADERS_OK)

    # Now backdating last_seen to 300 seconds ago (well beyond the 120s default).
    from careconnect_api.watcher_device import mac_lookup_candidates

    stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=300)
    await db_session.execute(
        update(AiDevice)
        .where(AiDevice.mac_address.in_(mac_lookup_candidates(_MAC)))
        .values(last_seen=stale)
    )
    await db_session.commit()

    resp = await client.get(f"/api/v1/watcher/{_MAC}/status", headers=_HEADERS_OK)
    data = resp.json()["data"]
    assert data["online"] is False


@pytest.mark.asyncio
async def test_watchers_list(client: AsyncClient):
    # Create two devices
    await client.post("/api/v1/watcher/heartbeat", json={"mac": _MAC}, headers=_HEADERS_OK)
    await client.post("/api/v1/watcher/heartbeat", json={"mac": _MAC2}, headers=_HEADERS_OK)

    resp = await client.get("/api/v1/watchers", headers=_HEADERS_OK)
    data = resp.json()
    assert data["code"] == 0
    from careconnect_api.watcher_device import stripped_mac
    macs = {stripped_mac(d["mac"]) for d in data["data"]}
    assert stripped_mac(_MAC) in macs
    assert stripped_mac(_MAC2) in macs


@pytest.mark.asyncio
async def test_watchers_list_missing_key_401(client: AsyncClient):
    resp = await client.get("/api/v1/watchers")
    data = resp.json()
    assert data["code"] == 401


@pytest.mark.asyncio
async def test_heartbeat_upserts_telemetry(client: AsyncClient):
    """Second heartbeat updates telemetry fields."""
    await client.post(
        "/api/v1/watcher/heartbeat",
        json={"mac": _MAC, "battery": 80, "fw": "1.8.0", "rssi": -70},
        headers=_HEADERS_OK,
    )
    await client.post(
        "/api/v1/watcher/heartbeat",
        json={"mac": _MAC, "battery": 95, "fw": "1.9.0", "rssi": -55},
        headers=_HEADERS_OK,
    )
    resp = await client.get(f"/api/v1/watcher/{_MAC}/status", headers=_HEADERS_OK)
    data = resp.json()["data"]
    assert data["battery"] == 95
    assert data["fw"] == "1.9.0"
    assert data["rssi"] == -55


# ---------- WS-registration helper (shared with XiaoZhi ensure_watcher_device) ----------

_REAL_MAC = "E0:72:A1:DB:36:40"
_REAL_MAC_STRIPPED = "E072A1DB3640"
_REAL_MAC_LOWER = "e0:72:a1:db:36:40"
_CANONICAL_ID = "watcher-e072a1db3640"


@pytest.mark.asyncio
async def test_device_id_identical_for_all_mac_spellings():
    from careconnect_api.watcher_device import device_id_for_mac

    assert device_id_for_mac(_REAL_MAC_LOWER) == _CANONICAL_ID
    assert device_id_for_mac(_REAL_MAC) == _CANONICAL_ID
    assert device_id_for_mac(_REAL_MAC_STRIPPED) == _CANONICAL_ID
    assert device_id_for_mac("e0-72-a1-db-36-40") == _CANONICAL_ID


@pytest.mark.asyncio
async def test_ensure_first_time_registration(db_session: AsyncSession):
    from careconnect_api.models import AiDevice
    from careconnect_api.watcher_device import ensure_watcher_device, mac_lookup_candidates

    dev = await ensure_watcher_device(
        db_session, _REAL_MAC_LOWER, touch_last_connected=True
    )
    assert dev.id == _CANONICAL_ID
    assert dev.mac_address == _REAL_MAC
    assert dev.device_type == "W1-A"
    assert dev.firmware_type == "xiaozhi"
    assert dev.board == "sensecap_watcher"
    assert dev.sort == 0
    assert dev.agent_id is None
    assert dev.client_device_id is None
    assert dev.alias is None
    assert dev.last_connected_at is not None
    assert dev.last_seen is not None

    n = (
        await db_session.execute(
            select(AiDevice).where(AiDevice.mac_address.in_(mac_lookup_candidates(_REAL_MAC)))
        )
    ).scalars().all()
    assert len(n) == 1


@pytest.mark.asyncio
async def test_ensure_reconnect_does_not_duplicate(db_session: AsyncSession):
    from careconnect_api.models import AiDevice
    from careconnect_api.watcher_device import ensure_watcher_device, mac_lookup_candidates

    first = await ensure_watcher_device(
        db_session, _REAL_MAC, touch_last_connected=True
    )
    first_id = first.id
    first_connected = first.last_connected_at

    second = await ensure_watcher_device(
        db_session, _REAL_MAC_LOWER, touch_last_connected=True
    )
    assert second.id == first_id == _CANONICAL_ID
    assert second.last_connected_at >= first_connected

    n = (
        await db_session.execute(
            select(AiDevice).where(AiDevice.mac_address.in_(mac_lookup_candidates(_REAL_MAC)))
        )
    ).scalars().all()
    assert len(n) == 1


@pytest.mark.asyncio
async def test_ensure_all_mac_spellings_are_one_device(db_session: AsyncSession):
    from careconnect_api.models import AiDevice
    from careconnect_api.watcher_device import (
        ensure_watcher_device,
        get_watcher_device,
        mac_lookup_candidates,
    )

    a = await ensure_watcher_device(db_session, _REAL_MAC_LOWER, touch_last_connected=True)
    b = await ensure_watcher_device(db_session, _REAL_MAC, touch_last_connected=True)
    c = await ensure_watcher_device(db_session, _REAL_MAC_STRIPPED, touch_last_connected=True)

    assert a.id == b.id == c.id == _CANONICAL_ID
    assert await get_watcher_device(db_session, _REAL_MAC_LOWER) is not None
    assert await get_watcher_device(db_session, _REAL_MAC) is not None
    assert await get_watcher_device(db_session, _REAL_MAC_STRIPPED) is not None

    n = (
        await db_session.execute(
            select(AiDevice).where(
                AiDevice.mac_address.in_(mac_lookup_candidates(_REAL_MAC))
                | (AiDevice.id == _CANONICAL_ID)
            )
        )
    ).scalars().all()
    assert len(n) == 1


@pytest.mark.asyncio
async def test_heartbeat_and_ws_generate_identical_device_id(db_session: AsyncSession):
    """Heartbeat create path and WS ensure() must mint the same PK."""
    from careconnect_api.models import AiDevice
    from careconnect_api.watcher_device import device_id_for_mac, ensure_watcher_device

    await ensure_watcher_device(db_session, _REAL_MAC_LOWER, touch_last_connected=True)
    row = (
        await db_session.execute(select(AiDevice).where(AiDevice.id == _CANONICAL_ID))
    ).scalar_one()
    assert row.id == device_id_for_mac(_REAL_MAC)
    assert row.id == device_id_for_mac(_REAL_MAC_STRIPPED)


@pytest.mark.asyncio
async def test_ensure_preserves_agent_id_and_metadata(db_session: AsyncSession):
    from careconnect_api.watcher_device import ensure_watcher_device, get_watcher_device

    await ensure_watcher_device(db_session, _REAL_MAC, touch_last_connected=True)
    existing = await get_watcher_device(db_session, _REAL_MAC)
    existing.agent_id = "agentbound001"
    existing.alias = "Living room"
    existing.client_device_id = "EXT-99"
    existing.battery = 80
    existing.fw = "1.9.0"
    existing.rssi = -62
    await db_session.commit()

    # WS reconnect: timestamps only (no telemetry kwargs).
    await ensure_watcher_device(db_session, _REAL_MAC_STRIPPED, touch_last_connected=True)
    again = await get_watcher_device(db_session, _REAL_MAC_LOWER)
    assert again.agent_id == "agentbound001"
    assert again.alias == "Living room"
    assert again.client_device_id == "EXT-99"
    assert again.battery == 80
    assert again.fw == "1.9.0"
    assert again.rssi == -62
    assert again.device_type == "W1-A"
    assert again.last_connected_at is not None


@pytest.mark.asyncio
async def test_ensure_finds_onboard_stripped_mac_without_duplicate(db_session: AsyncSession):
    """Onboarding stores uppercase hex without colons; Device-Id has colons."""
    from careconnect_api.models import AiDevice
    from careconnect_api.watcher_device import (
        device_id_for_mac,
        ensure_watcher_device,
        get_watcher_device,
        mac_lookup_candidates,
    )

    db_session.add(
        AiDevice(
            id=device_id_for_mac(_REAL_MAC_STRIPPED),
            mac_address=_REAL_MAC_STRIPPED,
            device_type="W1-A",
            firmware_type="xiaozhi",
            board="sensecap_watcher",
            sort=0,
            agent_id="alreadybound",
        )
    )
    await db_session.commit()

    dev = await ensure_watcher_device(
        db_session, _REAL_MAC_LOWER, touch_last_connected=True
    )
    assert dev.id == _CANONICAL_ID
    assert dev.mac_address == _REAL_MAC_STRIPPED  # existing spelling preserved
    assert dev.agent_id == "alreadybound"

    n = (
        await db_session.execute(
            select(AiDevice).where(AiDevice.mac_address.in_(mac_lookup_candidates(_REAL_MAC)))
        )
    ).scalars().all()
    assert len(n) == 1
    assert await get_watcher_device(db_session, _REAL_MAC) is not None
