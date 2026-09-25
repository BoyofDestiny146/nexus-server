"""Best-effort XiaoZhi live-session control from the CareConnect API.

Unbind/delete must succeed even when XiaoZhi is offline or the socket cannot
be closed. This module never rolls back API/DB state.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .settings import settings

log = logging.getLogger("xiaozhi_control")

_TIMEOUT_S = 2.0


def session_close_url() -> str:
    return (
        f"http://{settings.xiaozhi_server_host}:{settings.xiaozhi_ota_port}"
        "/internal/device/session-close"
    )


async def notify_xiaozhi_session_close(
    *,
    mac: str | None,
    device_id: str | None,
    clear_memory: bool = True,
) -> dict[str, Any]:
    """Ask XiaoZhi to close the Watcher WebSocket and drop per-device RAG.

    Returns a small status dict for logs/tests. Never raises.
    """
    payload = {
        "mac": mac or "",
        "deviceId": device_id or "",
        "clearMemory": bool(clear_memory),
    }
    url = session_close_url()
    try:
        headers = {"X-Internal-Token": settings.internal_token}
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            log.warning(
                "xiaozhi session-close HTTP %s for device=%s: %s",
                resp.status_code,
                device_id,
                (resp.text or "")[:300],
            )
            return {
                "attempted": True,
                "ok": False,
                "status": resp.status_code,
            }
        body: Any
        try:
            body = resp.json()
        except Exception:
            body = {}
        return {
            "attempted": True,
            "ok": True,
            "status": 200,
            "closed": (body or {}).get("closed", 0) if isinstance(body, dict) else 0,
            "collectionsRemoved": (
                (body or {}).get("collectionsRemoved") if isinstance(body, dict) else []
            ),
        }
    except Exception as exc:
        log.warning(
            "xiaozhi session-close failed (best-effort) device=%s mac=%s: %s",
            device_id,
            mac,
            exc,
        )
        return {"attempted": True, "ok": False, "error": str(exc)}


def speak_url() -> str:
    return (
        f"http://{settings.xiaozhi_server_host}:{settings.xiaozhi_ota_port}"
        "/internal/device/speak"
    )


async def notify_xiaozhi_speak(
    *,
    mac: str | None,
    device_id: str | None,
    text: str,
) -> dict[str, Any]:
    """Ask XiaoZhi to speak ``text`` on a live Watcher WebSocket.

    Uses existing ``ConnectionHandler._cc_speak_now`` with ``log_turn=False``
    on the HTTP path (no LLM, no firmware change). CareConnect API persists
    the system timeline row after a successful speak. Returns a small status
    dict. Never raises.
    """
    payload = {
        "mac": mac or "",
        "deviceId": device_id or "",
        "text": text or "",
    }
    url = speak_url()
    try:
        headers = {"X-Internal-Token": settings.internal_token}
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            log.warning(
                "xiaozhi speak HTTP %s for device=%s",
                resp.status_code,
                device_id,
            )
            return {
                "attempted": True,
                "ok": False,
                "spoken": 0,
                "status": resp.status_code,
            }
        body: Any
        try:
            body = resp.json()
        except Exception:
            body = {}
        spoken = 0
        if isinstance(body, dict):
            try:
                spoken = int(body.get("spoken") or 0)
            except (TypeError, ValueError):
                spoken = 0
        return {
            "attempted": True,
            "ok": bool(spoken),
            "spoken": spoken,
            "status": 200,
        }
    except Exception as exc:
        log.warning(
            "xiaozhi speak failed (best-effort) device=%s mac=%s err=%s",
            device_id,
            mac,
            type(exc).__name__,
        )
        return {"attempted": True, "ok": False, "spoken": 0, "error": type(exc).__name__}


def device_settings_url() -> str:
    return (
        f"http://{settings.xiaozhi_server_host}:{settings.xiaozhi_ota_port}"
        "/internal/device/settings"
    )


async def notify_xiaozhi_device_settings(
    *,
    mac: str | None,
    device_id: str | None,
    sleep_timeout_sec: int,
    listen_screen_off: bool,
    sleep_mode: str,
) -> dict[str, Any]:
    """Push power settings on the live Watcher WebSocket. Never raises."""
    payload = {
        "mac": mac or "",
        "deviceId": device_id or "",
        "sleepTimeoutSec": int(sleep_timeout_sec),
        "listenScreenOff": bool(listen_screen_off),
        "sleepMode": sleep_mode,
    }
    url = device_settings_url()
    try:
        headers = {"X-Internal-Token": settings.internal_token}
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            log.warning(
                "xiaozhi device-settings HTTP %s for device=%s",
                resp.status_code,
                device_id or mac,
            )
            return {
                "attempted": True,
                "ok": False,
                "sent": 0,
                "status": resp.status_code,
            }
        body: Any
        try:
            body = resp.json()
        except Exception:
            body = {}
        sent = 0
        if isinstance(body, dict):
            try:
                sent = int(body.get("sent") or body.get("matched") or 0)
            except (TypeError, ValueError):
                sent = 0
        return {
            "attempted": True,
            "ok": True,
            "sent": sent,
            "status": 200,
        }
    except Exception as exc:
        log.warning(
            "xiaozhi device-settings failed (best-effort) device=%s mac=%s err=%s",
            device_id,
            mac,
            type(exc).__name__,
        )
        return {"attempted": True, "ok": False, "sent": 0, "error": type(exc).__name__}
