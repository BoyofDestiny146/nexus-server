"""Voice-selection API — per-device TTS voice/speed management.

Mounted at /api by main.py (admin-gated, same pattern as device.py).

Endpoints
---------
GET  /voices
    Proxy to the multi-engine TTS server's voice catalog. Returns the raw
    catalog JSON: {default, voices:[{id,label,engine,local,recommended}],
    speeds:[{id,label}], default_speed}.

GET  /device/{mac}/voice
    Return the configured {voice, speed} for a device. Falls back to the
    catalog default if no per-device row exists. MAC is normalised to
    lowercase colon-separated form before lookup.

PUT  /device/{mac}/voice
    Body: {voice?, speed?, response_length?, volume?} — at least one field
    required. Validates voice/speed against the live TTS catalog (400 on
    unknown value). Volume is 0–100 and lives in voice_config.json (not
    MariaDB). Atomically updates the JSON file via temp-file rename under a
    per-process lock. Returns the new {voice, speed, response_length, volume}.

POST /voice/preview
    Body: {voice, speed, text?} — synthesise a WAV preview from the TTS
    server. Default text = "Hi, I'm Hazel. It's really nice to talk with you today."
    Returns audio/wav bytes. TTS failures are a CareConnect envelope, not a
    raw HTTP 500.

File locking
------------
Read-modify-write on voice_config.json uses asyncio.Lock (module-level
singleton). Concurrent API calls on the same process are serialised. The
final write goes to a .tmp sibling and is renamed into place atomically
(POSIX rename semantics), so a crash never leaves a partial file.

MAC normalisation
-----------------
Input MAC strings are lowercased and stripped of non-hex characters other
than colons. Any non-colon separator (dash, dot, space) is normalised to a
colon. The result is validated to be exactly 6 two-hex-digit groups joined
by colons (e.g. "44:1b:f6:81:a6:84").
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, get_current_user
from ..db import get_db
from ..envelope import APIException
from ..rbac import assert_can_access_agent
from ..settings import settings
from ..watcher_device import get_watcher_device


log = logging.getLogger("voice")

router = APIRouter(tags=["voice"])

# Serialises concurrent read-modify-write operations within one process.
_file_lock = asyncio.Lock()

_DEFAULT_PREVIEW_TEXT = "Hi, I'm Hazel. It's really nice to talk with you today."
_DEFAULT_VOLUME = 80


async def _authorize_device_mac(db: AsyncSession, user: CurrentUser, mac_colon: str) -> None:
    """RBAC gate for per-device voice endpoints (fixes an IDOR).

    ``mac_colon`` is the normalised lower-colon MAC. Production Watcher rows
    may store colon, stripped, or mixed-case MACs, so lookup goes through
    ``get_watcher_device``. Rules:

    * device bound to an agent → caller must be scoped to that agent
      (``assert_can_access_agent`` — root passes, scoped admins only for their
      own clients).
    * device unbound, or MAC not in our system → root only (these are
      admin/onboarding actions, not per-patient access).
    """
    if user.is_root:
        return
    dev = await get_watcher_device(db, mac_colon)
    if dev is not None and dev.agent_id:
        await assert_can_access_agent(db, user, dev.agent_id)
        return
    raise APIException(403, "not permitted for this device")


# ── MAC normalisation ─────────────────────────────────────────────────────────

_MAC_CLEAN = re.compile(r"[^0-9a-fA-F]")


def _normalise_mac(raw: str) -> str:
    """Lower-case colon-form MAC, e.g. "44:1B:F6:81:A6:84" → "44:1b:f6:81:a6:84".

    Accepts colons, dashes, dots, or spaces as separators; strips them,
    splits into 6 two-character hex groups, validates, and re-joins.

    Raises APIException(400) on invalid input so callers never see a
    KeyError on a silently-mangled key.
    """
    digits = _MAC_CLEAN.sub("", raw)
    if len(digits) != 12:
        raise APIException(400, f"invalid MAC address: {raw!r}")
    try:
        groups = [digits[i : i + 2] for i in range(0, 12, 2)]
        int("".join(groups), 16)  # validate all hex
    except ValueError:
        raise APIException(400, f"invalid MAC address: {raw!r}")
    return ":".join(g.lower() for g in groups)


def _clamp_volume(value: Any, default: int = _DEFAULT_VOLUME) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(100, n))


# ── Voice config file helpers ─────────────────────────────────────────────────

def _read_config() -> dict[str, Any]:
    """Read voice_config.json; return empty structure on missing/corrupt file."""
    path = Path(settings.voice_config_path)
    if not path.exists():
        return {"default": {}, "devices": {}}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        data.setdefault("default", {})
        data.setdefault("devices", {})
        return data
    except Exception as exc:
        log.warning("voice_config read error (%s): %s — treating as empty", path, exc)
        return {"default": {}, "devices": {}}


def _write_config(data: dict[str, Any]) -> None:
    """Atomically write voice_config.json via temp-file rename.

    OSError (the production Save 500: appuser cannot write the root-owned
    cc-voice volume) is converted to APIException so the dashboard gets a
    real ``msg`` instead of ``unexpected 500``.
    """
    path = Path(settings.voice_config_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise APIException(
            500,
            f"voice config directory is not writable ({path.parent}): {exc}",
        ) from exc

    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".voice_config_", suffix=".tmp")
    except OSError as exc:
        raise APIException(
            500,
            f"cannot create voice config temp file in {path.parent}: {exc}",
        ) from exc

    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on POSIX
        try:
            os.chmod(path, 0o666)
        except OSError:
            pass
    except APIException:
        raise
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise APIException(500, f"cannot write voice config {path}: {exc}") from exc
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


async def forget_device_voice(mac: str) -> None:
    """Drop a device's voice_config.json row after the ai_device row is deleted."""
    try:
        key = _normalise_mac(mac)
    except APIException:
        return
    async with _file_lock:
        config = _read_config()
        devices = config.get("devices") or {}
        if key not in devices:
            return
        del devices[key]
        config["devices"] = devices
        _write_config(config)


# ── TTS catalog fetch ─────────────────────────────────────────────────────────

async def _fetch_catalog() -> dict[str, Any]:
    """GET {tts_url}/voices — returns parsed JSON or raises APIException."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as ac:
            r = await ac.get(f"{settings.tts_url}/voices")
        if r.status_code != 200:
            raise APIException(502, f"TTS /voices returned HTTP {r.status_code}")
        return r.json()
    except APIException:
        raise
    except Exception as exc:
        raise APIException(502, f"TTS server unreachable: {exc}") from exc


def _tts_error_message(response: httpx.Response) -> str:
    """Prefer the TTS server's own error text over a generic HTTP status."""
    try:
        payload = response.json()
    except Exception:
        text = (response.text or "").strip()
        suffix = f": {text[:300]}" if text else ""
        return f"TTS preview failed (HTTP {response.status_code}){suffix}"
    if isinstance(payload, dict):
        for key in ("msg", "error", "detail", "message"):
            val = payload.get(key)
            if val:
                return f"TTS preview failed: {val}"
    return f"TTS preview failed (HTTP {response.status_code})"


# ── Request / response models ─────────────────────────────────────────────────

class VoiceUpdatePayload(BaseModel):
    voice: str | None = None
    speed: str | None = None
    response_length: str | None = None  # brief | normal | detailed
    volume: int | None = Field(default=None, ge=0, le=100)


class PreviewPayload(BaseModel):
    voice: str
    speed: str
    text: str | None = None


def _voice_payload(mac: str, cfg: dict[str, Any], source: str) -> dict[str, Any]:
    volume = cfg.get("volume")
    return {
        "mac": mac,
        "voice": cfg.get("voice"),
        "speed": cfg.get("speed"),
        "response_length": cfg.get("response_length", "brief"),
        "volume": _clamp_volume(volume, _DEFAULT_VOLUME) if volume is not None else _DEFAULT_VOLUME,
        "source": source,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/voices", response_model=None)
async def list_voices(
    _user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Proxy the TTS server's voice catalog.

    Returns {default, voices, speeds, default_speed} straight from the TTS
    server. Cache-friendly — callers may add their own HTTP cache layer;
    the TTS catalog changes only on server restart.
    """
    catalog = await _fetch_catalog()
    log.debug(
        "voice catalog proxied: %d voices, engines=%s",
        len(catalog.get("voices", [])),
        list({v.get("engine") for v in catalog.get("voices", []) if isinstance(v, dict)}),
    )
    return catalog


@router.get("/device/{mac}/voice", response_model=None)
async def get_device_voice(
    mac: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the configured {voice, speed, response_length, volume} for a device.

    Falls back to the catalog default when no per-device entry exists.
    """
    mac = _normalise_mac(mac)
    await _authorize_device_mac(db, user, mac)

    config = _read_config()
    device_cfg = config["devices"].get(mac)

    if device_cfg:
        log.debug("device voice lookup: mac=%s → %s", mac, device_cfg)
        return _voice_payload(mac, device_cfg, "device")

    file_default = config.get("default") or {}
    if file_default.get("voice"):
        log.debug("device voice lookup: mac=%s → file default %s", mac, file_default)
        return _voice_payload(mac, file_default, "default")

    try:
        catalog = await _fetch_catalog()
        catalog_default = {
            "voice": catalog.get("default", ""),
            "speed": catalog.get("default_speed", "normal"),
            "response_length": catalog.get("default_length", "brief"),
            "volume": _DEFAULT_VOLUME,
        }
        log.debug("device voice lookup: mac=%s → catalog default %s", mac, catalog_default)
        return _voice_payload(mac, catalog_default, "catalog_default")
    except APIException:
        log.warning("device voice lookup: mac=%s — TTS unreachable, returning nulls", mac)
        return {
            "mac": mac,
            "voice": None,
            "speed": None,
            "response_length": "brief",
            "volume": _DEFAULT_VOLUME,
            "source": "unknown",
        }


@router.put("/device/{mac}/voice", response_model=None)
async def set_device_voice(
    mac: str,
    payload: VoiceUpdatePayload,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Set the voice and/or speed for a device.

    Validates the requested voice/speed against the live TTS catalog (HTTP 400
    on unknown values). Atomically updates the config file; the xiaozhi-server
    will pick up the new value on the next turn (it reads the file per-request).
    """
    if (
        payload.voice is None
        and payload.speed is None
        and payload.response_length is None
        and payload.volume is None
    ):
        raise APIException(
            400,
            "at least one of 'voice', 'speed', 'response_length', 'volume' must be provided",
        )

    mac = _normalise_mac(mac)
    await _authorize_device_mac(db, user, mac)

    catalog = await _fetch_catalog()
    valid_voice_ids = {
        v["id"] for v in catalog.get("voices", []) if isinstance(v, dict) and "id" in v
    }
    valid_speed_ids = {
        s["id"] for s in catalog.get("speeds", []) if isinstance(s, dict) and "id" in s
    }
    valid_length_ids = {
        length["id"]
        for length in catalog.get("lengths", [])
        if isinstance(length, dict) and "id" in length
    } or {"brief", "normal", "detailed"}

    if payload.voice is not None and payload.voice not in valid_voice_ids:
        raise APIException(
            400,
            f"unknown voice {payload.voice!r}; valid: {sorted(valid_voice_ids)}",
        )
    if payload.speed is not None and payload.speed not in valid_speed_ids:
        raise APIException(
            400,
            f"unknown speed {payload.speed!r}; valid: {sorted(valid_speed_ids)}",
        )
    if payload.response_length is not None and payload.response_length not in valid_length_ids:
        raise APIException(
            400,
            f"unknown response_length {payload.response_length!r}; valid: {sorted(valid_length_ids)}",
        )

    async with _file_lock:
        config = _read_config()
        existing = dict(config["devices"].get(mac, {}) or {})

        new_voice = payload.voice if payload.voice is not None else existing.get("voice")
        new_speed = payload.speed if payload.speed is not None else existing.get("speed")
        new_length = (
            payload.response_length
            if payload.response_length is not None
            else existing.get("response_length")
        )
        new_volume = (
            _clamp_volume(payload.volume)
            if payload.volume is not None
            else existing.get("volume")
        )

        if new_voice is None:
            new_voice = catalog.get("default", "")
        if new_speed is None:
            new_speed = catalog.get("default_speed", "normal")
        if new_length is None:
            new_length = catalog.get("default_length", "brief")
        if new_volume is None:
            new_volume = _DEFAULT_VOLUME
        else:
            new_volume = _clamp_volume(new_volume)

        existing["voice"] = new_voice
        existing["speed"] = new_speed
        existing["response_length"] = new_length
        existing["volume"] = new_volume
        config["devices"][mac] = existing
        _write_config(config)

    log.info(
        "device voice updated: mac=%s voice=%s speed=%s length=%s volume=%s",
        mac,
        new_voice,
        new_speed,
        new_length,
        new_volume,
    )
    return {
        "mac": mac,
        "voice": new_voice,
        "speed": new_speed,
        "response_length": new_length,
        "volume": new_volume,
    }


@router.post("/voice/preview", response_model=None)
async def preview_voice(
    payload: PreviewPayload,
    _user: CurrentUser = Depends(get_current_user),
) -> Response:
    """Return a WAV preview from the TTS server for in-browser playback.

    Buffer the TTS response (previews are a sentence or two) so a non-200
    from piper-tts can be turned into a CareConnect envelope *before* any
    audio headers are sent. Streaming would lock us into HTTP 200 and the
    dashboard would show ``unexpected 500`` / empty playback.
    """
    text = payload.text if payload.text else _DEFAULT_PREVIEW_TEXT
    if not str(text).strip():
        raise APIException(400, "preview text is empty")

    log.debug(
        "voice preview: voice=%s speed=%s text_len=%d",
        payload.voice,
        payload.speed,
        len(text),
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.tts_url}/v1/audio/speech",
                json={
                    "input": text,
                    "voice": payload.voice,
                    "speed": payload.speed,
                    "response_format": "wav",
                },
            )
    except APIException:
        raise
    except Exception as exc:
        raise APIException(502, f"TTS server unreachable: {exc}") from exc

    if resp.status_code != 200:
        raise APIException(502, _tts_error_message(resp))

    content_type = (resp.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        raise APIException(502, _tts_error_message(resp))

    wav = resp.content or b""
    if len(wav) < 12 or wav[:4] != b"RIFF":
        raise APIException(502, "TTS preview returned empty or invalid audio")

    return Response(
        content=wav,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )
