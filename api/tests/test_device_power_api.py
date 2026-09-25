"""Devices Control power settings: persist, push, voice untouched."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import SysUser
from careconnect_api.settings import settings


_MAC = "AA:BB:CC:DD:EE:77"
_MAC_KEY = "aa:bb:cc:dd:ee:77"

CATALOG = {
    "default": "kokoro:af_heart",
    "voices": [
        {
            "id": "kokoro:af_heart",
            "label": "Hazel",
            "engine": "kokoro",
            "local": True,
            "recommended": True,
        }
    ],
    "speeds": [{"id": "slow", "label": "Slow"}, {"id": "normal", "label": "Normal"}],
    "default_speed": "normal",
    "lengths": [{"id": "brief", "label": "Brief"}],
    "default_length": "brief",
}


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
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        return _FakeResp(200, CATALOG)

    async def post(self, url, json=None):
        return _FakeResp(200, None, content=b"RIFF" + b"\x00" * 32, content_type="audio/wav")


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


@pytest.fixture
def settings_push(monkeypatch):
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        sent = int(kwargs.pop("_sent", 0)) if False else None
        return {"attempted": True, "ok": True, "sent": getattr(_fake, "sent", 0)}

    _fake.sent = 0
    monkeypatch.setattr(
        "careconnect_api.routers.voice.notify_xiaozhi_device_settings", _fake
    )
    return calls, _fake


@pytest.mark.asyncio
async def test_invalid_timeout_rejected(
    client: AsyncClient, admin_token: str, voice_file: Path, tts, settings_push
):
    resp = await client.put(
        f"/api/device/{_MAC}/voice",
        json={"sleepTimeoutSec": 45, "listenScreenOff": True, "sleepMode": "screen_off"},
        headers=_auth(admin_token),
    )
    body = resp.json()
    assert body["code"] != 0
    assert "sleepTimeoutSec" in body["msg"]


@pytest.mark.asyncio
async def test_save_offline_persists_pending(
    client: AsyncClient, admin_token: str, voice_file: Path, tts, settings_push
):
    calls, fake = settings_push
    fake.sent = 0
    resp = await client.put(
        f"/api/device/{_MAC}/voice",
        json={
            "voice": "kokoro:af_heart",
            "speed": "normal",
            "response_length": "brief",
            "volume": 80,
            "sleepTimeoutSec": 300,
            "listenScreenOff": True,
            "sleepMode": "screen_off",
        },
        headers=_auth(admin_token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["voice"] == "kokoro:af_heart"
    assert data["volume"] == 80
    assert data["sleepTimeoutSec"] == 300
    assert data["listenScreenOff"] is True
    assert data["sleepMode"] == "screen_off"
    assert data["powerApplyState"] == "pending_offline"
    assert "will apply when Watcher reconnects" in data["powerSaveMessage"]
    saved = json.loads(voice_file.read_text())
    row = saved["devices"][_MAC_KEY]
    assert row["voice"] == "kokoro:af_heart"
    assert row["sleepTimeoutSec"] == 300
    assert row.get("powerApplied") in (None, {},)
    assert int(row.get("powerPushSent") or 0) == 0
    assert calls


@pytest.mark.asyncio
async def test_save_online_sends_device_control(
    client: AsyncClient, admin_token: str, voice_file: Path, tts, settings_push
):
    calls, fake = settings_push
    fake.sent = 1
    resp = await client.put(
        f"/api/device/{_MAC}/voice",
        json={
            "sleepTimeoutSec": 60,
            "listenScreenOff": True,
            "sleepMode": "screen_off",
        },
        headers=_auth(admin_token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["powerApplyState"] == "pending_ack"
    assert "waiting for Watcher" in body["data"]["powerSaveMessage"]
    assert calls
    assert calls[0]["sleep_timeout_sec"] == 60
    assert calls[0]["listen_screen_off"] is True
    saved = json.loads(voice_file.read_text())
    assert saved["devices"][_MAC_KEY]["powerPushSent"] == 1
    assert "powerApplied" not in saved["devices"][_MAC_KEY] or saved["devices"][_MAC_KEY].get(
        "powerApplied"
    ) in (None,)


@pytest.mark.asyncio
async def test_deep_sleep_disallows_listen_on(
    client: AsyncClient, admin_token: str, voice_file: Path, tts, settings_push
):
    resp = await client.put(
        f"/api/device/{_MAC}/voice",
        json={
            "sleepTimeoutSec": 300,
            "listenScreenOff": True,
            "sleepMode": "deep_sleep",
        },
        headers=_auth(admin_token),
    )
    data = resp.json()["data"]
    assert data["sleepMode"] == "deep_sleep"
    assert data["listenScreenOff"] is False


@pytest.mark.asyncio
async def test_voice_untouched_when_saving_power(
    client: AsyncClient, admin_token: str, voice_file: Path, tts, settings_push
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
    resp = await client.put(
        f"/api/device/{_MAC}/voice",
        json={"sleepTimeoutSec": 120, "listenScreenOff": False, "sleepMode": "screen_off"},
        headers=_auth(admin_token),
    )
    data = resp.json()["data"]
    assert data["voice"] == "kokoro:af_heart"
    assert data["speed"] == "slow"
    assert data["volume"] == 42
    assert data["sleepTimeoutSec"] == 120
    row = json.loads(voice_file.read_text())["devices"][_MAC_KEY]
    assert row["voice"] == "kokoro:af_heart"
    assert row["volume"] == 42
    assert row["sleepTimeoutSec"] == 120
