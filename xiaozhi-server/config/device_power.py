"""Per-Watcher power / sleep settings (MAC-keyed voice_config.json).

Mirrors ``careconnect_api.device_power`` so xiaozhi-server can apply the
same validation without importing the API package.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import voice_config as voice_config

SLEEP_TIMEOUT_SEC = (0, 30, 60, 120, 300, 600, 1800)
SLEEP_MODES = ("screen_off", "deep_sleep")
POWER_KEYS = ("sleepTimeoutSec", "listenScreenOff", "sleepMode")

DEFAULT_POWER: dict[str, Any] = {
    "sleepTimeoutSec": 300,
    "listenScreenOff": True,
    "sleepMode": "screen_off",
}


class PowerSettingsError(ValueError):
    pass


def public_power(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    src = raw or {}
    try:
        timeout_i = int(src.get("sleepTimeoutSec", DEFAULT_POWER["sleepTimeoutSec"]))
    except (TypeError, ValueError):
        timeout_i = int(DEFAULT_POWER["sleepTimeoutSec"])
    listen = src.get("listenScreenOff", DEFAULT_POWER["listenScreenOff"])
    if isinstance(listen, str):
        listen_b = listen.strip().lower() in ("1", "true", "on", "yes")
    else:
        listen_b = bool(listen)
    mode_s = str(src.get("sleepMode") or DEFAULT_POWER["sleepMode"]).strip()
    if mode_s == "deep_sleep":
        listen_b = False
    return {
        "sleepTimeoutSec": timeout_i,
        "listenScreenOff": listen_b,
        "sleepMode": mode_s,
    }


def normalize_power(raw: Mapping[str, Any] | None, *, partial: bool = False) -> dict[str, Any]:
    if not raw:
        return {} if partial else dict(DEFAULT_POWER)
    out: dict[str, Any] = {}
    if "sleepTimeoutSec" in raw:
        try:
            timeout = int(raw["sleepTimeoutSec"])
        except (TypeError, ValueError) as exc:
            raise PowerSettingsError("sleepTimeoutSec must be an integer") from exc
        if timeout not in SLEEP_TIMEOUT_SEC:
            raise PowerSettingsError(
                f"sleepTimeoutSec must be one of {list(SLEEP_TIMEOUT_SEC)}"
            )
        out["sleepTimeoutSec"] = timeout
    elif not partial:
        out["sleepTimeoutSec"] = DEFAULT_POWER["sleepTimeoutSec"]
    if "sleepMode" in raw:
        mode = str(raw["sleepMode"] or "").strip()
        if mode not in SLEEP_MODES:
            raise PowerSettingsError(f"sleepMode must be one of {list(SLEEP_MODES)}")
        out["sleepMode"] = mode
    elif not partial:
        out["sleepMode"] = DEFAULT_POWER["sleepMode"]
    if "listenScreenOff" in raw:
        val = raw["listenScreenOff"]
        if isinstance(val, bool):
            listen = val
        elif val in (0, 1):
            listen = bool(val)
        elif isinstance(val, str) and val.strip().lower() in (
            "true",
            "false",
            "on",
            "off",
            "1",
            "0",
        ):
            listen = val.strip().lower() in ("true", "on", "1")
        else:
            raise PowerSettingsError("listenScreenOff must be true or false")
        out["listenScreenOff"] = listen
    elif not partial:
        out["listenScreenOff"] = DEFAULT_POWER["listenScreenOff"]
    if out.get("sleepMode") == "deep_sleep":
        out["listenScreenOff"] = False
    return out


def _entry(mac: str) -> dict[str, Any]:
    data = voice_config._load()
    cur = data.get("devices", {}).get(voice_config._norm(mac))
    return dict(cur) if isinstance(cur, dict) else {}


def desired_saved(mac: str) -> dict[str, Any] | None:
    entry = _entry(mac)
    if not any(k in entry for k in POWER_KEYS):
        return None
    return public_power(entry)


def keeps_listening(mac: str) -> bool:
    cfg = desired_saved(mac)
    if cfg is None:
        return False
    return cfg["sleepMode"] == "screen_off" and bool(cfg["listenScreenOff"])


def wire_payload(power: Mapping[str, Any]) -> dict[str, Any]:
    cfg = public_power(power)
    return {
        "type": "device_settings",
        "sleepTimeoutSec": cfg["sleepTimeoutSec"],
        "listenScreenOff": cfg["listenScreenOff"],
        "sleepMode": cfg["sleepMode"],
    }


def mark_push_sent(mac: str, sent: int) -> None:
    with voice_config._lock:
        data = voice_config._load()
        key = voice_config._norm(mac)
        cur = dict(data.get("devices", {}).get(key) or {})
        cur["powerPushSent"] = int(sent)
        data.setdefault("devices", {})[key] = cur
        voice_config._save(data)


def mark_applied(mac: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    cfg = normalize_power(payload, partial=False)
    with voice_config._lock:
        data = voice_config._load()
        key = voice_config._norm(mac)
        cur = dict(data.get("devices", {}).get(key) or {})
        for k, v in cfg.items():
            cur[k] = v
        cur["powerApplied"] = dict(cfg)
        data.setdefault("devices", {})[key] = cur
        voice_config._save(data)
    return cfg
