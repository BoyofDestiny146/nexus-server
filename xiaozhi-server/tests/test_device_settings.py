"""Internal device_settings push + reconnect apply + ack."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.api.settings_handler import handle_device_settings, push_settings_to_handler


COLON = "E0:72:A1:DB:36:40"


class FakeWs:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, message: str):
        self.sent.append(message)


class FakeHandler:
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.websocket = FakeWs()
        self.cc_keep_listening = False
        self.logger = _Log()


class _Log:
    def bind(self, **k):
        return self

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


class FakeServer:
    def __init__(self, handlers):
        self.active_connections = set(handlers)


class FakeRequest:
    def __init__(self, headers, body, app):
        self.headers = headers
        self._body = body
        self.app = app

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_settings_unauthorized(monkeypatch):
    monkeypatch.setattr(
        "core.api.settings_handler._expected_internal_token", lambda: "secret"
    )
    req = FakeRequest({}, {"mac": COLON, "sleepTimeoutSec": 300}, {})
    resp = await handle_device_settings(req)
    assert resp.status == 401


@pytest.mark.asyncio
async def test_save_online_sends_device_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "core.api.settings_handler._expected_internal_token", lambda: "secret"
    )
    cfg = tmp_path / "voice_config.json"
    monkeypatch.setenv("CC_VOICE_CONFIG", str(cfg))
    import importlib
    import config.voice_config as vc
    importlib.reload(vc)
    import config.device_power as dp
    importlib.reload(dp)

    target = FakeHandler("e0:72:a1:db:36:40")
    keep = FakeHandler("aa:bb:cc:dd:ee:00")
    app = {"websocket_server": FakeServer([keep, target])}
    req = FakeRequest(
        {"X-Internal-Token": "secret"},
        {
            "mac": COLON,
            "sleepTimeoutSec": 300,
            "listenScreenOff": True,
            "sleepMode": "screen_off",
        },
        app,
    )
    resp = await handle_device_settings(req)
    body = json.loads(resp.text)
    assert resp.status == 200
    assert body["sent"] == 1
    assert target.cc_keep_listening is True
    parsed = json.loads(target.websocket.sent[0])
    assert parsed["type"] == "device_settings"
    assert parsed["sleepTimeoutSec"] == 300
    assert keep.websocket.sent == []


@pytest.mark.asyncio
async def test_offline_sends_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "core.api.settings_handler._expected_internal_token", lambda: "secret"
    )
    cfg = tmp_path / "voice_config.json"
    monkeypatch.setenv("CC_VOICE_CONFIG", str(cfg))
    import importlib
    import config.voice_config as vc
    importlib.reload(vc)
    import config.device_power as dp
    importlib.reload(dp)

    req = FakeRequest(
        {"X-Internal-Token": "secret"},
        {
            "mac": COLON,
            "sleepTimeoutSec": 60,
            "listenScreenOff": False,
            "sleepMode": "screen_off",
        },
        {"websocket_server": FakeServer([])},
    )
    resp = await handle_device_settings(req)
    body = json.loads(resp.text)
    assert body["sent"] == 0


@pytest.mark.asyncio
async def test_reconnect_applies_pending_settings(monkeypatch, tmp_path):
    cfg = tmp_path / "voice_config.json"
    monkeypatch.setenv("CC_VOICE_CONFIG", str(cfg))
    import importlib
    import config.voice_config as vc
    importlib.reload(vc)
    import config.device_power as dp
    importlib.reload(dp)

    vc.set_voice(COLON, voice="kokoro:af_heart")
    data = json.loads(cfg.read_text())
    row = data["devices"]["e0:72:a1:db:36:40"]
    row["sleepTimeoutSec"] = 120
    row["listenScreenOff"] = True
    row["sleepMode"] = "screen_off"
    row["powerPushSent"] = 0
    cfg.write_text(json.dumps(data))

    handler = FakeHandler(COLON)
    saved = dp.desired_saved(COLON)
    assert saved["sleepTimeoutSec"] == 120
    ok = await push_settings_to_handler(handler, saved)
    assert ok is True
    parsed = json.loads(handler.websocket.sent[0])
    assert parsed["type"] == "device_settings"
    assert parsed["sleepTimeoutSec"] == 120


@pytest.mark.asyncio
async def test_ack_marks_applied(monkeypatch, tmp_path):
    cfg = tmp_path / "voice_config.json"
    monkeypatch.setenv("CC_VOICE_CONFIG", str(cfg))
    import importlib
    import config.voice_config as vc
    importlib.reload(vc)
    import config.device_power as dp
    importlib.reload(dp)

    data = {
        "default": {},
        "devices": {
            "e0:72:a1:db:36:40": {
                "voice": "kokoro:af_heart",
                "sleepTimeoutSec": 300,
                "listenScreenOff": True,
                "sleepMode": "screen_off",
                "powerPushSent": 1,
            }
        },
    }
    cfg.write_text(json.dumps(data))
    applied = dp.mark_applied(
        "e0:72:a1:db:36:40",
        {
            "sleepTimeoutSec": 300,
            "listenScreenOff": True,
            "sleepMode": "screen_off",
        },
    )
    assert applied["sleepTimeoutSec"] == 300
    row = json.loads(cfg.read_text())["devices"]["e0:72:a1:db:36:40"]
    assert row["powerApplied"]["sleepTimeoutSec"] == 300
    assert row["voice"] == "kokoro:af_heart"


def test_text_handler_acks_device_settings():
    src = (ROOT / "core/handle/textHandle.py").read_text()
    assert 'msg_json["type"] == "device_settings"' in src
    assert "mark_applied" in src


def test_hello_pushes_saved_power_settings():
    src = (ROOT / "core/handle/helloHandle.py").read_text()
    assert "_cc_push_saved_power_settings" in src
    assert "device_settings" in (ROOT / "core/api/settings_handler.py").read_text()
    http = (ROOT / "core/http_server.py").read_text()
    assert "/internal/device/settings" in http
    assert "/internal/device/speak" in http
    idle = (ROOT / "core/handle/receiveAudioHandle.py").read_text()
    assert "cc_keep_listening" in idle
    fw = (ROOT / ".." / "firmware" / "sensecap-watcher" / "power_settings.cc").read_text()
    assert "esp_deep_sleep_start" in fw
    assert "esp_sleep_enable_ext0_wakeup" in fw
    assert 'nvs_open(kNvsNs' in fw or 'nvs_open(kNvsNs,' in fw
    assert "kHeartbeat" in (ROOT / ".." / "firmware" / "sensecap-watcher" / "power_settings.h").read_text() or "kHeartbeat" in fw
