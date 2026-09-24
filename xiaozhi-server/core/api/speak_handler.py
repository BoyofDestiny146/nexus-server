"""Internal XiaoZhi HTTP: speak a server-initiated sentence on a live Watcher.

POST /internal/device/speak
Body: {"mac"|"deviceId", "text"}
Auth: X-Internal-Token (same secret as CareConnect internal API)

Looks up ``WebSocketServer.active_connections`` and calls
``ConnectionHandler._cc_speak_now(text)`` — existing TTS / audio path,
no LLM, no firmware, no MCP. Offline Watchers return spoken=0.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from aiohttp import web

from core.device_session import handler_matches

log = logging.getLogger("internal_speak")

_MAX_TEXT = 4000


def _expected_internal_token() -> str | None:
    try:
        from config.careconnect_db import _token

        return _token()
    except Exception:
        return None


def speak_on_matching_handlers(
    ws_server: Any,
    *,
    mac: str | None,
    device_id: str | None,
    text: str,
) -> int:
    """Call ``_cc_speak_now`` on matching live connections. Never raises."""
    if ws_server is None or not text:
        return 0
    conns: Iterable[Any] = getattr(ws_server, "active_connections", None) or ()
    spoken = 0
    for handler in list(conns):
        if not handler_matches(handler, mac, device_id):
            continue
        speak = getattr(handler, "_cc_speak_now", None)
        if not callable(speak):
            continue
        try:
            speak(text)
            spoken += 1
        except Exception as exc:
            log.warning(
                "internal speak failed device=%s err=%s",
                getattr(handler, "device_id", None),
                type(exc).__name__,
            )
    return spoken


async def handle_device_speak(request: web.Request) -> web.Response:
    expected = _expected_internal_token()
    provided = request.headers.get("X-Internal-Token")
    if not expected or provided != expected:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    mac = body.get("mac") or body.get("macAddress") or body.get("eui")
    device_id = body.get("deviceId") or body.get("device_id")
    text = body.get("text")
    if text is None:
        text = ""
    if not isinstance(text, str):
        text = str(text)
    text = text.strip()
    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT]

    if not mac and not device_id:
        return web.json_response(
            {"ok": False, "error": "mac or deviceId required"},
            status=400,
        )
    if not text:
        return web.json_response(
            {"ok": False, "error": "text required"},
            status=400,
        )

    ws_server = request.app.get("websocket_server")
    spoken = speak_on_matching_handlers(
        ws_server, mac=mac, device_id=device_id, text=text
    )
    return web.json_response(
        {
            "ok": True,
            "spoken": int(spoken),
            "matched": int(spoken),
        }
    )
