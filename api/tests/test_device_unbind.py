"""Watcher unbind + delete/reset: keep the device row on unbind, person always.

Covers the first-slice contract:
  * POST /api/device/{deviceId}/unbind keeps the same ai_device row
  * agent_id / alias cleared; client/person preserved
  * voice_config.json row cleared
  * per-device Chroma forget is requested (clearMemory=true)
  * DELETE removes the device only
  * reconnect after delete recreates the canonical unbound Watcher
  * attach after unbind succeeds without force
  * offline XiaoZhi / live WS close failure does not roll back DB
  * no factory / Wi-Fi / OTA reset commands are emitted
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import (
    AiAgent,
    AiAgentChatHistory,
    AiDevice,
    AiMedicalAssessment,
)
from careconnect_api.settings import settings
from careconnect_api.watcher_device import (
    device_id_for_mac,
    get_watcher_device,
    mac_lookup_candidates,
)

_API_KEY = "test-api-key-abc123"
_HEADERS_KEY = {"X-API-Key": _API_KEY}

_COLON_MAC = "E0:72:A1:DB:36:40"
_STRIPPED_MAC = "E072A1DB3640"
_MAC_KEY = "e0:72:a1:db:36:40"
_CANONICAL_ID = "watcher-e072a1db3640"

FORBIDDEN_RESET_TOKENS = (
    "factory",
    "wifi reset",
    "wifi_reset",
    "nvs",
    "ota_url",
    "server_url",
    "call_mcp_tool",
    "reboot",
)


@pytest_asyncio.fixture(scope="function")
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    from careconnect_api.models import SysUser as _SysUser

    _ = client
    user = _SysUser(
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


@pytest.fixture
def voice_file(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "voice_config.json"
    monkeypatch.setattr(settings, "voice_config_path", str(path))
    return path


class _XiaozhiControl:
    """httpx.AsyncClient stand-in for POST /internal/device/session-close."""

    fail_connect = False
    fail_http = False
    calls: list[dict] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).calls.append({"url": url, "json": json, "headers": headers})
        if self.fail_connect:
            raise httpx.ConnectError("xiaozhi offline")

        class _Resp:
            def __init__(self, status: int, payload: dict):
                self.status_code = status
                self._payload = payload
                self.text = json_dumps(payload)

            def json(self):
                return self._payload

        if self.fail_http:
            return _Resp(500, {"ok": False, "error": "cannot close websocket"})
        return _Resp(
            200,
            {
                "ok": True,
                "closed": 0,
                "memoryCleared": True,
                "collectionsRemoved": ["agent_e0_72_a1_db_36_40"],
                "resetCommandsEmitted": [],
            },
        )


def json_dumps(payload: dict) -> str:
    return json.dumps(payload)


@pytest.fixture
def xiaozhi_ok(monkeypatch):
    _XiaozhiControl.fail_connect = False
    _XiaozhiControl.fail_http = False
    _XiaozhiControl.calls = []
    monkeypatch.setattr(
        "careconnect_api.xiaozhi_control.httpx.AsyncClient", _XiaozhiControl
    )
    return _XiaozhiControl


@pytest.fixture
def xiaozhi_offline(monkeypatch):
    _XiaozhiControl.fail_connect = True
    _XiaozhiControl.fail_http = False
    _XiaozhiControl.calls = []
    monkeypatch.setattr(
        "careconnect_api.xiaozhi_control.httpx.AsyncClient", _XiaozhiControl
    )
    return _XiaozhiControl


@pytest.fixture
def xiaozhi_close_fails(monkeypatch):
    _XiaozhiControl.fail_connect = False
    _XiaozhiControl.fail_http = True
    _XiaozhiControl.calls = []
    monkeypatch.setattr(
        "careconnect_api.xiaozhi_control.httpx.AsyncClient", _XiaozhiControl
    )
    return _XiaozhiControl


async def _onboard_client(client: AsyncClient, token: str, name: str = "George") -> str:
    resp = await client.post(
        "/api/agent/onboard",
        json={"name": name},
        headers=_auth(token),
    )
    data = resp.json()
    assert data["code"] == 0, data
    return data["data"]["agentId"]


async def _auto_register(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/watcher/heartbeat",
        json={"mac": _COLON_MAC, "battery": 77, "fw": "1.9.0", "rssi": -50},
        headers=_HEADERS_KEY,
    )
    assert resp.json()["code"] == 0, resp.json()


async def _attach(
    client: AsyncClient, token: str, agent_id: str, *, force: bool = False, alias: str = "george"
) -> dict:
    resp = await client.post(
        "/api/device/attach",
        json={
            "agentId": agent_id,
            "eui": _COLON_MAC,
            "alias": alias,
            "deviceType": "W1-A",
            "firmwareType": "xiaozhi",
            "force": force,
        },
        headers=_auth(token),
    )
    return resp.json()


async def _seed_person_rows(db_session: AsyncSession, agent_id: str) -> None:
    now = datetime(2026, 9, 22, 12, 0, 0)
    db_session.add(
        AiAgentChatHistory(
            id=101,
            mac_address=_COLON_MAC,
            agent_id=agent_id,
            session_id="sess-1",
            chat_type=1,
            content="hello from the living room",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.add(
        AiMedicalAssessment(
            id=202,
            agent_id=agent_id,
            for_date=date(2026, 9, 22),
            risk_level="low",
            confidence=0.9,
            concerns_json="[]",
            recommendations_json="[]",
            source_msg_count=1,
            llm_model="qwen2.5:3b",
        )
    )
    await db_session.commit()


async def _reload(db_session: AsyncSession) -> AiDevice | None:
    db_session.expire_all()
    return await get_watcher_device(db_session, _COLON_MAC)


async def _row_count(db_session: AsyncSession) -> int:
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


def _assert_no_reset_commands(payload: dict | None) -> None:
    blob = json.dumps(payload or {}).lower()
    for token in FORBIDDEN_RESET_TOKENS:
        assert token not in blob, f"reset command {token!r} in {payload}"


async def _write_voice(voice_file: Path) -> None:
    voice_file.write_text(
        json.dumps(
            {
                "default": {},
                "devices": {
                    _MAC_KEY: {
                        "voice": "kokoro:af_heart",
                        "speed": "normal",
                        "volume": 70,
                    }
                },
            }
        )
    )


@pytest.mark.asyncio
async def test_unbind_keeps_same_device_row_and_clears_binding(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    voice_file: Path,
    xiaozhi_ok,
):
    agent_id = await _onboard_client(client, admin_token)
    await _auto_register(client)
    attached = await _attach(client, admin_token, agent_id)
    assert attached["code"] == 0, attached
    await _seed_person_rows(db_session, agent_id)
    await _write_voice(voice_file)

    before = await _reload(db_session)
    assert before is not None
    assert before.id == _CANONICAL_ID
    assert before.agent_id == agent_id
    assert before.alias == "george"
    assert before.battery == 77
    assert before.fw == "1.9.0"
    assert before.rssi == -50
    assert before.board == "sensecap_watcher"

    resp = await client.post(
        f"/api/device/{_CANONICAL_ID}/unbind", headers=_auth(admin_token)
    )
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["unbound"] is True
    assert data["clientPreserved"] is True
    assert data["deviceId"] == _CANONICAL_ID
    assert data["agentId"] is None
    assert data["alias"] is None
    assert data["previousAgentId"] == agent_id

    after = await _reload(db_session)
    assert after is not None
    assert after.id == _CANONICAL_ID
    assert after.mac_address == _COLON_MAC
    assert after.agent_id is None
    assert after.alias is None
    assert after.battery == 77
    assert after.fw == "1.9.0"
    assert after.rssi == -50
    assert after.board == "sensecap_watcher"
    assert after.device_type == "W1-A"
    assert after.firmware_type == "xiaozhi"
    assert await _row_count(db_session) == 1

    db_session.expire_all()
    agent = await db_session.get(AiAgent, agent_id)
    assert agent is not None
    assert agent.agent_name == "George"

    chats = (
        await db_session.execute(
            select(AiAgentChatHistory).where(AiAgentChatHistory.agent_id == agent_id)
        )
    ).scalars().all()
    assert len(chats) == 1
    assessments = (
        await db_session.execute(
            select(AiMedicalAssessment).where(AiMedicalAssessment.agent_id == agent_id)
        )
    ).scalars().all()
    assert len(assessments) == 1

    saved = json.loads(voice_file.read_text())
    assert _MAC_KEY not in saved.get("devices", {})

    assert len(xiaozhi_ok.calls) == 1
    call = xiaozhi_ok.calls[0]
    assert call["url"].endswith("/internal/device/session-close")
    assert call["json"]["clearMemory"] is True
    assert call["json"]["deviceId"] == _CANONICAL_ID
    _assert_no_reset_commands(call["json"])
    assert "X-Internal-Token" in call["headers"]


@pytest.mark.asyncio
async def test_attach_after_unbind_succeeds_without_force(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    xiaozhi_ok,
):
    alice = await _onboard_client(client, admin_token, name="Alice")
    bob = await _onboard_client(client, admin_token, name="Bob")
    await _auto_register(client)
    bound = await _attach(client, admin_token, alice, alias="alice-watch")
    assert bound["code"] == 0, bound

    unbound = await client.post(
        f"/api/device/{_CANONICAL_ID}/unbind", headers=_auth(admin_token)
    )
    assert unbound.json()["code"] == 0, unbound.json()

    rebound = await _attach(client, admin_token, bob, alias="bob-watch", force=False)
    assert rebound["code"] == 0, rebound
    assert rebound["data"]["agentId"] == bob
    assert rebound["data"]["deviceId"] == _CANONICAL_ID
    assert rebound["data"].get("previousAgentId") in (None, "")

    after = await _reload(db_session)
    assert after is not None
    assert after.agent_id == bob
    assert after.alias == "bob-watch"
    assert after.id == _CANONICAL_ID
    assert after.mac_address == _COLON_MAC
    assert after.battery == 77
    assert await _row_count(db_session) == 1


@pytest.mark.asyncio
async def test_delete_removes_device_only_and_preserves_client(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    voice_file: Path,
    xiaozhi_ok,
):
    agent_id = await _onboard_client(client, admin_token)
    await _auto_register(client)
    attached = await _attach(client, admin_token, agent_id)
    assert attached["code"] == 0, attached
    await _seed_person_rows(db_session, agent_id)
    await _write_voice(voice_file)

    deleted = await client.delete(
        f"/api/device/{_CANONICAL_ID}", headers=_auth(admin_token)
    )
    body = deleted.json()
    assert body["code"] == 0, body
    assert body["data"]["deleted"] is True
    assert body["data"]["clientPreserved"] is True
    assert body["data"]["agentId"] == agent_id

    db_session.expire_all()
    assert await get_watcher_device(db_session, _COLON_MAC) is None
    assert await db_session.get(AiDevice, _CANONICAL_ID) is None
    agent = await db_session.get(AiAgent, agent_id)
    assert agent is not None
    assert agent.agent_name == "George"
    chats = (
        await db_session.execute(
            select(AiAgentChatHistory).where(AiAgentChatHistory.agent_id == agent_id)
        )
    ).scalars().all()
    assert len(chats) == 1
    assessments = (
        await db_session.execute(
            select(AiMedicalAssessment).where(AiMedicalAssessment.agent_id == agent_id)
        )
    ).scalars().all()
    assert len(assessments) == 1

    saved = json.loads(voice_file.read_text())
    assert _MAC_KEY not in saved.get("devices", {})
    assert xiaozhi_ok.calls[0]["json"]["clearMemory"] is True
    _assert_no_reset_commands(xiaozhi_ok.calls[0]["json"])


@pytest.mark.asyncio
async def test_reconnect_after_delete_recreates_canonical_unbound_watcher(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    xiaozhi_ok,
):
    agent_id = await _onboard_client(client, admin_token)
    await _auto_register(client)
    await _attach(client, admin_token, agent_id)

    deleted = await client.delete(
        f"/api/device/{_CANONICAL_ID}", headers=_auth(admin_token)
    )
    assert deleted.json()["code"] == 0

    await _auto_register(client)
    again = await _reload(db_session)
    assert again is not None
    assert again.id == _CANONICAL_ID
    assert again.mac_address == _COLON_MAC
    assert again.agent_id is None
    assert again.alias is None
    assert again.board == "sensecap_watcher"
    assert again.device_type == "W1-A"
    assert again.firmware_type == "xiaozhi"
    assert await _row_count(db_session) == 1
    agent = await db_session.get(AiAgent, agent_id)
    assert agent is not None


@pytest.mark.asyncio
async def test_offline_unbind_and_delete_do_not_require_mcp(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    xiaozhi_offline,
):
    agent_id = await _onboard_client(client, admin_token)
    await _auto_register(client)
    await _attach(client, admin_token, agent_id)

    unbound = await client.post(
        f"/api/device/{_CANONICAL_ID}/unbind", headers=_auth(admin_token)
    )
    assert unbound.json()["code"] == 0, unbound.json()
    after = await _reload(db_session)
    assert after is not None
    assert after.agent_id is None
    assert after.id == _CANONICAL_ID
    assert await db_session.get(AiAgent, agent_id) is not None

    alice = await _onboard_client(client, admin_token, name="Alice")
    rebound = await _attach(client, admin_token, alice, alias="alice")
    assert rebound["code"] == 0, rebound

    deleted = await client.delete(
        f"/api/device/{_CANONICAL_ID}", headers=_auth(admin_token)
    )
    assert deleted.json()["code"] == 0, deleted.json()
    db_session.expire_all()
    assert await get_watcher_device(db_session, _COLON_MAC) is None
    assert await db_session.get(AiAgent, alice) is not None
    assert await db_session.get(AiAgent, agent_id) is not None
    # Control was attempted over HTTP only — never an MCP tool name.
    for call in xiaozhi_offline.calls:
        _assert_no_reset_commands(call["json"])
        assert "mcp" not in call["url"].lower()


@pytest.mark.asyncio
async def test_live_ws_close_failure_does_not_break_unbind_or_delete(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    xiaozhi_close_fails,
):
    agent_id = await _onboard_client(client, admin_token)
    await _auto_register(client)
    await _attach(client, admin_token, agent_id)

    unbound = await client.post(
        f"/api/device/{_CANONICAL_ID}/unbind", headers=_auth(admin_token)
    )
    assert unbound.json()["code"] == 0, unbound.json()
    after = await _reload(db_session)
    assert after is not None
    assert after.agent_id is None

    deleted = await client.delete(
        f"/api/device/{_CANONICAL_ID}", headers=_auth(admin_token)
    )
    assert deleted.json()["code"] == 0, deleted.json()
    db_session.expire_all()
    assert await get_watcher_device(db_session, _COLON_MAC) is None
    assert await db_session.get(AiAgent, agent_id) is not None


@pytest.mark.asyncio
async def test_unbind_missing_device_404(
    client: AsyncClient, admin_token: str, xiaozhi_ok
):
    resp = await client.post(
        "/api/device/watcher-does-not-exist/unbind", headers=_auth(admin_token)
    )
    assert resp.json()["code"] == 404
    assert xiaozhi_ok.calls == []
