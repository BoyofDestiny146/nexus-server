"""Devices → Voice settings: Save, Preview, volume, envelope errors.

The dashboard ``unexpected 500`` came from unhandled PermissionError on
``/data/voice_config.json`` and from ``POST /voice/preview`` forwarding a
raw TTS 500. These tests pin the envelope contract and the happy path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import AiAgent, AiDevice, SysUser
from careconnect_api.settings import settings
from careconnect_api.watcher_device import device_id_for_mac, get_watcher_device


_MAC = "AA:BB:CC:DD:EE:77"
_MAC_KEY = "aa:bb:cc:dd:ee:77"
_API_KEY = "test-api-key-abc123"

CATALOG = {
    "default": "kokoro:af_heart",
    "voices": [
        {
            "id": "kokoro:af_heart",
            "label": "Hazel",
            "engine": "kokoro",
            "local": True,
            "recommended": True,
        },
        {
            "id": "edge:en-US-AvaNeural",
            "label": "Ava",
            "engine": "edge",
            "local": False,
            "recommended": True,
        },
    ],
    "speeds": [
        {"id": "slow", "label": "Slow"},
        {"id": "normal", "label": "Normal"},
        {"id": "fast", "label": "Fast"},
    ],
    "default_speed": "normal",
    "lengths": [
        {"id": "brief", "label": "Brief"},
        {"id": "normal", "label": "Normal"},
        {"id": "detailed", "label": "Detailed"},
    ],
    "default_length": "brief",
}

_WAV = b"RIFF" + b"\x00" * 32


class _FakeResp:
    def __init__(self, status=200, payload=None, content=b"", content_type="application/json"):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.headers = {"content-type": content_type}
        self.text = "" if payload is None else json.dumps(payload)

    def json(self):
        return self._payload


class _FakeTtsClient:
    """httpx.AsyncClient stand-in. Edge voices simulate the production TTS 500."""

    fail_edge = True

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        return _FakeResp(200, CATALOG)

    async def post(self, url, json=None):
        voice = (json or {}).get("voice", "")
        if self.fail_edge and str(voice).startswith("edge:"):
            payload = {"error": "No audio was received", "voice": voice}
            return _FakeResp(
                500,
                payload,
                content=json_bytes(payload),
                content_type="application/json",
            )
        return _FakeResp(200, None, content=_WAV, content_type="audio/wav")


def json_bytes(payload: dict) -> bytes:
    return json.dumps(payload).encode()


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
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


@pytest.fixture
def voice_file(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "voice_config.json"
    monkeypatch.setattr(settings, "voice_config_path", str(path))
    return path


@pytest.fixture
def tts(monkeypatch):
    monkeypatch.setattr(
        "careconnect_api.routers.voice.httpx.AsyncClient", _FakeTtsClient
    )
    return _FakeTtsClient


@pytest.mark.asyncio
async def test_save_voice_settings_succeeds(
    client: AsyncClient, admin_token: str, voice_file: Path, tts
):
    resp = await client.put(
        f"/api/device/{_MAC}/voice",
        json={
            "voice": "edge:en-US-AvaNeural",
            "speed": "normal",
            "response_length": "brief",
            "volume": 80,
        },
        headers=_auth(admin_token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["voice"] == "edge:en-US-AvaNeural"
    assert body["data"]["speed"] == "normal"
    assert body["data"]["response_length"] == "brief"
    assert body["data"]["volume"] == 80
    saved = json.loads(voice_file.read_text())
    assert saved["devices"][_MAC_KEY]["voice"] == "edge:en-US-AvaNeural"
    assert saved["devices"][_MAC_KEY]["volume"] == 80


@pytest.mark.asyncio
async def test_get_voice_returns_volume(
    client: AsyncClient, admin_token: str, voice_file: Path, tts
):
    voice_file.write_text(
        json.dumps(
            {
                "default": {},
                "devices": {
                    _MAC_KEY: {
                        "voice": "kokoro:af_heart",
                        "speed": "slow",
                        "response_length": "brief",
                        "volume": 42,
                    }
                },
            }
        )
    )
    resp = await client.get(
        f"/api/device/{_MAC}/voice", headers=_auth(admin_token)
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["volume"] == 42
    assert body["data"]["speed"] == "slow"


@pytest.mark.asyncio
async def test_preview_local_voice_returns_wav(
    client: AsyncClient, admin_token: str, tts
):
    resp = await client.post(
        "/api/voice/preview",
        json={"voice": "kokoro:af_heart", "speed": "normal", "text": "Hello from CareConnect."},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/wav")
    assert resp.content[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_preview_tts_500_is_envelope_not_raw_500(
    client: AsyncClient, admin_token: str, tts
):
    resp = await client.post(
        "/api/voice/preview",
        json={"voice": "edge:en-US-AvaNeural", "speed": "normal"},
        headers=_auth(admin_token),
    )
    body = resp.json()
    assert "code" in body and "msg" in body and "data" in body
    assert body["code"] == 502
    assert "No audio was received" in body["msg"]
    assert resp.status_code == 200  # CareConnect envelope stays HTTP 200


@pytest.mark.asyncio
async def test_save_oserror_is_envelope(
    client: AsyncClient, admin_token: str, tts, monkeypatch
):
    def _boom(*_a, **_k):
        raise PermissionError("[Errno 13] Permission denied: '/data/.voice_config_x.tmp'")

    monkeypatch.setattr("careconnect_api.routers.voice.tempfile.mkstemp", _boom)
    resp = await client.put(
        f"/api/device/{_MAC}/voice",
        json={"voice": "kokoro:af_heart", "speed": "normal"},
        headers=_auth(admin_token),
    )
    body = resp.json()
    assert "code" in body and "msg" in body and "data" in body
    assert body["code"] != 0
    assert "Permission denied" in body["msg"] or "cannot create" in body["msg"]


@pytest.mark.asyncio
async def test_unhandled_exception_is_envelope(
    client: AsyncClient, admin_token: str, monkeypatch
):
    async def _boom():
        raise RuntimeError("tts catalog exploded")

    monkeypatch.setattr("careconnect_api.routers.voice._fetch_catalog", _boom)
    resp = await client.put(
        f"/api/device/{_MAC}/voice",
        json={"voice": "kokoro:af_heart", "speed": "normal"},
        headers=_auth(admin_token),
    )
    body = resp.json()
    assert "code" in body and "msg" in body and "data" in body
    assert body["code"] == 500
    assert "exploded" in body["msg"]


@pytest.mark.asyncio
async def test_delete_device_does_not_delete_client(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    voice_file: Path,
    tts,
):
    onboard = await client.post(
        "/api/agent/onboard",
        json={"name": "B Dalton"},
        headers=_auth(admin_token),
    )
    assert onboard.json()["code"] == 0, onboard.json()
    agent_id = onboard.json()["data"]["agentId"]

    hb = await client.post(
        "/api/v1/watcher/heartbeat",
        json={"mac": _MAC, "battery": 80},
        headers={"X-API-Key": _API_KEY},
    )
    assert hb.json()["code"] == 0, hb.json()

    attach = await client.post(
        "/api/device/attach",
        json={
            "agentId": agent_id,
            "eui": _MAC,
            "deviceType": "W1-A",
            "firmwareType": "xiaozhi",
        },
        headers=_auth(admin_token),
    )
    assert attach.json()["code"] == 0, attach.json()
    device_id = attach.json()["data"]["deviceId"]
    assert device_id == device_id_for_mac(_MAC)

    await client.put(
        f"/api/device/{_MAC}/voice",
        json={"voice": "kokoro:af_heart", "speed": "normal", "volume": 70},
        headers=_auth(admin_token),
    )
    assert _MAC_KEY in json.loads(voice_file.read_text())["devices"]

    deleted = await client.delete(
        f"/api/device/{device_id}", headers=_auth(admin_token)
    )
    body = deleted.json()
    assert body["code"] == 0, body
    assert body["data"]["deleted"] is True
    assert body["data"]["clientPreserved"] is True
    assert body["data"]["agentId"] == agent_id

    db_session.expire_all()
    agent = await db_session.get(AiAgent, agent_id)
    assert agent is not None
    assert agent.agent_name == "B Dalton"
    assert await get_watcher_device(db_session, _MAC) is None
    leftover = await db_session.get(AiDevice, device_id)
    assert leftover is None
    saved = json.loads(voice_file.read_text())
    assert _MAC_KEY not in saved.get("devices", {})
