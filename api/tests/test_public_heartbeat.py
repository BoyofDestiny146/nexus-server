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
    assert data["mac"] == _MAC
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
    stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=300)
    await db_session.execute(
        update(AiDevice)
        .where(AiDevice.mac_address == _MAC)
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
    macs = {d["mac"] for d in data["data"]}
    assert _MAC in macs
    assert _MAC2 in macs


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
