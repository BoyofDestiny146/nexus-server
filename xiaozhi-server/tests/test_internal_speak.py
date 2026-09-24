"""Internal XiaoZhi POST /internal/device/speak → ConnectionHandler._cc_speak_now.

No firmware, no LLM, no reminder-wrapper. Offline Watchers return spoken=0.
"""
from __future__ import annotations

import ast
import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.api.speak_handler import (  # noqa: E402
    handle_device_speak,
    speak_on_matching_handlers,
)


COLON = "E0:72:A1:DB:36:40"
CANONICAL = "watcher-e072a1db3640"
SPOKEN = (
    "Hi, please take your heat medication.\n"
    "Remember only at 4:00 with food."
)


class FakeHandler:
    def __init__(self, device_id: str, *, fail: bool = False):
        self.device_id = device_id
        self.spoken: list[str] = []
        self.fail = fail

    def _cc_speak_now(self, text: str, log_turn: bool = True):
        if self.fail:
            raise RuntimeError("tts down")
        self.spoken.append(text)


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
async def test_speak_unauthorized(monkeypatch):
    monkeypatch.setattr(
        "core.api.speak_handler._expected_internal_token", lambda: "secret"
    )
    req = FakeRequest({}, {"mac": COLON, "text": SPOKEN}, {})
    resp = await handle_device_speak(req)
    assert resp.status == 401


@pytest.mark.asyncio
async def test_speak_matching_handler_gets_verbatim_text(monkeypatch):
    monkeypatch.setattr(
        "core.api.speak_handler._expected_internal_token", lambda: "secret"
    )
    keep = FakeHandler("aa:bb:cc:dd:ee:00")
    target = FakeHandler("e0:72:a1:db:36:40")
    app = {"websocket_server": FakeServer([keep, target])}
    req = FakeRequest(
        {"X-Internal-Token": "secret"},
        {"deviceId": CANONICAL, "text": SPOKEN},
        app,
    )
    resp = await handle_device_speak(req)
    body = json.loads(resp.body)
    assert body["ok"] is True
    assert body["spoken"] == 1
    assert target.spoken == [SPOKEN]
    assert keep.spoken == []
    assert "This is your reminder" not in target.spoken[0]


@pytest.mark.asyncio
async def test_speak_offline_is_zero(monkeypatch):
    monkeypatch.setattr(
        "core.api.speak_handler._expected_internal_token", lambda: "secret"
    )
    app = {"websocket_server": FakeServer([])}
    req = FakeRequest(
        {"X-Internal-Token": "secret"},
        {"mac": COLON, "text": SPOKEN},
        app,
    )
    resp = await handle_device_speak(req)
    body = json.loads(resp.body)
    assert body["ok"] is True
    assert body["spoken"] == 0


def test_speak_uses_cc_speak_now_not_reminder_wrapper():
    src = inspect.getsource(speak_on_matching_handlers)
    assert "_cc_speak_now" in src
    assert "This is your reminder" not in src
    tree = ast.parse(Path(ROOT / "core/api/speak_handler.py").read_text())
    dumped = ast.dump(tree)
    assert "call_mcp_tool" not in dumped.lower()
    http_src = (ROOT / "core/http_server.py").read_text()
    assert "/internal/device/speak" in http_src
