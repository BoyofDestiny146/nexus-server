"""Integration: Devices API Board column uses friendly display map.

Registration still stamps ai_device.board = sensecap_watcher by default.
After OTA persists firmware board.type (m5stack-core-s3), the dashboard
must show Cube V1.0 without renaming Watcher product copy elsewhere.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.models import AiDevice

_EUI = "AABBCCDDEE99"


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


@pytest.mark.asyncio
async def test_onboard_default_board_displays_as_watcher(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    resp = await client.post(
        "/api/agent/onboard",
        json={"name": "Board Map Tester", "eui": _EUI},
        headers=_auth(admin_token),
    )
    assert resp.json()["code"] == 0, resp.json()

    # Stored slug remains the registration default.
    row = (
        await db_session.execute(select(AiDevice).where(AiDevice.mac_address == _EUI))
    ).scalar_one()
    assert row.board == "sensecap_watcher"

    devices = await client.get("/api/admin/device/all", headers=_auth(admin_token))
    body = devices.json()
    assert body["code"] == 0, body
    match = next(d for d in body["data"]["list"] if d["macAddress"] == _EUI)
    assert match["board"] == "Watcher"


@pytest.mark.asyncio
async def test_m5stack_core_s3_board_displays_as_cube_v1(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    resp = await client.post(
        "/api/agent/onboard",
        json={"name": "Cube Tester", "eui": _EUI},
        headers=_auth(admin_token),
    )
    assert resp.json()["code"] == 0, resp.json()

    # Simulate OTA persisting firmware board.type onto the sticky DB field.
    row = (
        await db_session.execute(select(AiDevice).where(AiDevice.mac_address == _EUI))
    ).scalar_one()
    row.board = "m5stack-core-s3"
    await db_session.commit()

    devices = await client.get("/api/admin/device/all", headers=_auth(admin_token))
    body = devices.json()
    assert body["code"] == 0, body
    match = next(d for d in body["data"]["list"] if d["macAddress"] == _EUI)
    assert match["board"] == "Cube V1.0"
