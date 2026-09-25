"""Internal XiaoZhi HTTP: push CareConnect power settings to a live Watcher.

POST /internal/device/settings
Body: {"mac"|"deviceId", "sleepTimeoutSec", "listenScreenOff", "sleepMode"}
Auth: X-Internal-Token

Sends ``{"type":"device_settings", ...}`` on the existing XiaoZhi WebSocket.
Offline Watchers return sent=0. Firmware that does not understand the type
will not ack; CareConnect then keeps applyState=pending_ack rather than
lying that MariaDB/JSON save means Applied.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from aiohttp import web

from core.device_session import handler_matches

log = logging.getLogger("internal_settings")


def _expected_internal_token() -> str | None:
    try:
        from config.careconnect_db import _token

        return _token()
    except Exception:
        return None


def _power_from_body(body: dict[str, Any]) -> dict[str, Any] | None:
    from config.device_power import PowerSettingsError, normalize_power

    try:
        return normalize_power(
            {
                "sleepTimeoutSec": body.get("sleepTimeoutSec"),
                "listenScreenOff": body.get("listenScreenOff"),
                "sleepMode": body.get("sleepMode"),
            },
            partial=False,
        )
    except PowerSettingsError as exc:
        log.warning("internal settings rejected: %s", exc)
        return None


async def push_settings_to_handler(handler: Any, power: dict[str, Any]) -> bool:
    """Used on hello/reconnect. Returns True if the WS write was attempted."""
    from config.device_power import mark_push_sent, wire_payload

    ws = getattr(handler, "websocket", None)
    if ws is None:
        return False
    payload = wire_payload(power)
    try:
        await ws.send(json.dumps(payload, separators=(",", ":")))
    except Exception as exc:
        log.warning(
            "reconnect settings push failed device=%s err=%s",
            getattr(handler, "device_id", None),
            type(exc).__name__,
        )
        return False
    handler.cc_keep_listening = (
        payload["sleepMode"] == "screen_off" and bool(payload["listenScreenOff"])
    )
    mac = getattr(handler, "device_id", None) or ""
    if mac:
        try:
            mark_push_sent(mac, 1)
        except Exception:
            pass
    return True


async def handle_device_settings(request: web.Request) -> web.Response:
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
    if not mac and not device_id:
        return web.json_response(
            {"ok": False, "error": "mac or deviceId required"},
            status=400,
        )

    power = _power_from_body(body)
    if power is None:
        return web.json_response(
            {"ok": False, "error": "invalid power settings"},
            status=400,
        )

    ws_server = request.app.get("websocket_server")
    sent = 0
    conns: Iterable[Any] = getattr(ws_server, "active_connections", None) or ()
    message = json.dumps(
        {
            "type": "device_settings",
            "sleepTimeoutSec": power["sleepTimeoutSec"],
            "listenScreenOff": power["listenScreenOff"],
            "sleepMode": power["sleepMode"],
        },
        separators=(",", ":"),
    )
    from config.device_power import mark_push_sent

    for handler in list(conns):
        if not handler_matches(handler, mac, device_id):
            continue
        ws = getattr(handler, "websocket", None)
        if ws is None:
            continue
        try:
            await ws.send(message)
            handler.cc_keep_listening = (
                power["sleepMode"] == "screen_off" and bool(power["listenScreenOff"])
            )
            sent += 1
        except Exception as exc:
            log.warning(
                "internal settings send failed device=%s err=%s",
                getattr(handler, "device_id", None),
                type(exc).__name__,
            )
    ident = str(mac or device_id or "")
    if ident:
        try:
            mark_push_sent(ident, sent)
        except Exception:
            pass
    return web.json_response({"ok": True, "sent": int(sent), "matched": int(sent)})
