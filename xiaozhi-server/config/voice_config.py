"""careconnect per-device voice configuration.

Stores which TTS voice + speed each watcher uses, keyed by MAC (colon form,
lowercased). Backed by a small JSON file so the dashboard/api and the
xiaozhi-server share one source of truth without a DB migration.

Shape:
    {
      "default": {"voice": "kokoro:af_heart", "speed": "normal"},
      "devices": {"44:1b:f6:81:a6:84": {"voice": "edge:en-US-AvaNeural", "speed": "slow"}}
    }
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
_FIELDS = ("voice", "speed", "response_length")
_lock = threading.Lock()


def _norm(mac: str) -> str:
    return (mac or "").strip().lower().replace("-", ":")


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


def get_voice(mac: str) -> dict:
    """Return {'voice','speed','response_length'} for a device, falling back to default."""
    d = _load()
    entry = d["devices"].get(_norm(mac))
    base = dict(_DEFAULT)
    base.update({k: v for k, v in d.get("default", {}).items() if k in _FIELDS})
    if isinstance(entry, dict):
        base.update({k: v for k, v in entry.items() if k in _FIELDS})
    return base


def get_response_length(mac: str) -> str:
    """brief | normal | detailed (per-device, falls back to default)."""
    return get_voice(mac).get("response_length", "brief")


def set_voice(mac: str, voice: str | None = None, speed: str | None = None,
              response_length: str | None = None) -> dict:
    with _lock:
        d = _load()
        cur = d["devices"].get(_norm(mac), {})
        if voice is not None:
            cur["voice"] = voice
        if speed is not None:
            cur["speed"] = speed
        if response_length is not None:
            cur["response_length"] = response_length
        d["devices"][_norm(mac)] = cur
        _save(d)
        return get_voice(mac)


def set_default(voice: str | None = None, speed: str | None = None,
                response_length: str | None = None) -> dict:
    with _lock:
        d = _load()
        if voice is not None:
            d["default"]["voice"] = voice
        if speed is not None:
            d["default"]["speed"] = speed
        if response_length is not None:
            d["default"]["response_length"] = response_length
        _save(d)
        return d["default"]


def all_config() -> dict:
    return _load()
