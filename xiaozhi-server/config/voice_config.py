"""careconnect per-device voice configuration.

Stores which TTS voice + speed each watcher uses, keyed by MAC (colon form,
lowercased). Backed by a small JSON file so the dashboard/api and the
xiaozhi-server share one source of truth without a DB migration.

Shape:
    {
      "default": {"voice": "kokoro:af_heart", "speed": "normal"},
      "devices": {"44:1b:f6:81:a6:84": {"voice": "edge:en-US-AvaNeural", "speed": "slow"}}
    }

``volume`` (0–100) is optional and device-side: the dashboard persists it
here; MCP ``set_volume`` applies it on the Watcher. It is not a MariaDB
column.
"""
from __future__ import annotations
import json
import os
import threading
from pathlib import Path

_PATH = Path(os.environ.get(
    "CC_VOICE_CONFIG",
    str(Path(__file__).resolve().parent.parent / "data" / "voice_config.json"),
))
# response_length defaults to "brief" — elderly users want short, simple replies
# (the AI was far too verbose out of the box). brief|normal|detailed.
_DEFAULT = {"voice": "kokoro:af_heart", "speed": "normal", "response_length": "brief"}
_FIELDS = ("voice", "speed", "response_length", "volume")
_lock = threading.Lock()
_MCP_VOLUME_FALLBACK = 95


def _norm(mac: str) -> str:
    return (mac or "").strip().lower().replace("-", ":")


def _clamp_volume(value, default: int = _MCP_VOLUME_FALLBACK) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(100, n))


def _load() -> dict:
    try:
        d = json.loads(_PATH.read_text())
        if not isinstance(d, dict):
            raise ValueError
        d.setdefault("default", dict(_DEFAULT))
        d.setdefault("devices", {})
        return d
    except Exception:
        return {"default": dict(_DEFAULT), "devices": {}}


def _save(d: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2))
    tmp.replace(_PATH)
    try:
        os.chmod(_PATH, 0o666)
    except OSError:
        pass


def get_voice(mac: str) -> dict:
    """Return {'voice','speed','response_length', optional 'volume'} for a device."""
    d = _load()
    entry = d["devices"].get(_norm(mac))
    base = dict(_DEFAULT)
    base.update({k: v for k, v in d.get("default", {}).items() if k in _FIELDS})
    if isinstance(entry, dict):
        base.update({k: v for k, v in entry.items() if k in _FIELDS})
    if "volume" in base:
        base["volume"] = _clamp_volume(base["volume"])
    return base


def get_response_length(mac: str) -> str:
    """brief | normal | detailed (per-device, falls back to default)."""
    return get_voice(mac).get("response_length", "brief")


def get_device_volume(mac: str, default: int = _MCP_VOLUME_FALLBACK) -> int:
    """Speaker volume 0–100. Unset → ``default`` (MCP audible fallback)."""
    cfg = get_voice(mac)
    if "volume" not in cfg:
        return default
    return _clamp_volume(cfg.get("volume"), default)


def set_voice(mac: str, voice: str | None = None, speed: str | None = None,
              response_length: str | None = None, volume: int | None = None) -> dict:
    with _lock:
        d = _load()
        cur = d["devices"].get(_norm(mac), {})
        if voice is not None:
            cur["voice"] = voice
        if speed is not None:
            cur["speed"] = speed
        if response_length is not None:
            cur["response_length"] = response_length
        if volume is not None:
            cur["volume"] = _clamp_volume(volume)
        d["devices"][_norm(mac)] = cur
        _save(d)
        return get_voice(mac)


def set_default(voice: str | None = None, speed: str | None = None,
                response_length: str | None = None, volume: int | None = None) -> dict:
    with _lock:
        d = _load()
        if voice is not None:
            d["default"]["voice"] = voice
        if speed is not None:
            d["default"]["speed"] = speed
        if response_length is not None:
            d["default"]["response_length"] = response_length
        if volume is not None:
            d["default"]["volume"] = _clamp_volume(volume)
        _save(d)
        return d["default"]


def all_config() -> dict:
    return _load()
