"""Internal XiaoZhi HTTP: close a live Watcher session + forget device RAG.

POST /internal/device/session-close
Body: {"mac", "deviceId", "clearMemory": true}
Auth: X-Internal-Token (same secret as CareConnect internal API)

Closes the matching WebSocket if present and deletes Chroma collections
keyed to that physical device. Never sends firmware, Wi-Fi, OTA, or MCP
tool calls.
"""
from __future__ import annotations

import logging

from aiohttp import web

from core.device_session import (
    close_live_sessions,
    forget_device_collections,
    resolve_chroma_path,
)

log = logging.getLogger("session_close")


def _expected_internal_token() -> str | None:
    try:
        from config.careconnect_db import _token

        return _token()
    except Exception:
        return None


async def handle_session_close(request: web.Request) -> web.Response:
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
    clear_memory = body.get("clearMemory", True)
    if clear_memory is None:
        clear_memory = True

    if not mac and not device_id:
        return web.json_response(
            {"ok": False, "error": "mac or deviceId required"},
            status=400,
        )

    ws_server = request.app.get("websocket_server")
    closed = 0
    close_error = None
    try:
        closed = await close_live_sessions(
            ws_server, mac=mac, device_id=device_id
        )
    except Exception as exc:
        close_error = str(exc)
        log.warning("session-close live socket lookup failed: %s", exc)

    removed: list[str] = []
    memory_error = None
    if clear_memory:
        try:
            config = request.app.get("config") or {}
            removed = forget_device_collections(
                mac=mac,
                device_id=device_id,
                persist_path=resolve_chroma_path(config),
                config=config,
            )
        except Exception as exc:
            memory_error = str(exc)
            log.warning("session-close chroma forget failed: %s", exc)

    return web.json_response(
        {
            "ok": True,
            "closed": int(closed),
            "memoryCleared": bool(clear_memory),
            "collectionsRemoved": removed,
            "closeError": close_error,
            "memoryError": memory_error,
            "resetCommandsEmitted": [],
        }
    )
