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
    Body: {voice?, speed?} — at least one field required.
    Validates voice/speed against the live TTS catalog (400 on unknown
    value). Atomically updates the JSON file via temp-file rename under a
    per-process lock. Returns the new {voice, speed}.

POST /voice/preview
    Body: {voice, speed, text?} — stream a WAV preview from the TTS server.
    Default text = "Hi, I'm Hazel. It's really nice to talk with you today."
    Returns audio/wav bytes via StreamingResponse.

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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, get_current_user
from ..db import get_db
from ..envelope import APIException
from ..models import AiDevice
from ..rbac import assert_can_access_agent
from ..settings import settings


log = logging.getLogger("voice")

router = APIRouter(tags=["voice"])

# Serialises concurrent read-modify-write operations within one process.
_file_lock = asyncio.Lock()


async def _authorize_device_mac(db: AsyncSession, user: CurrentUser, mac_colon: str) -> None:
    """RBAC gate for per-device voice endpoints (fixes an IDOR).

    ``mac_colon`` is the normalised lower-colon MAC. The dashboard stores MACs
    uppercase-contiguous (see onboard._normalize_eui), so we resolve against
    that form. Rules:

    * device bound to an agent → caller must be scoped to that agent
      (``assert_can_access_agent`` — root passes, scoped admins only for their
      own clients).
    * device unbound, or MAC not in our system → root only (these are
      admin/onboarding actions, not per-patient access).
    """
    if user.is_root:
        return
    db_mac = mac_colon.replace(":", "").upper()
    dev = (
        await db.execute(select(AiDevice).where(AiDevice.mac_address == db_mac))
    ).scalar_one_or_none()
    if dev is not None and dev.agent_id:
        await assert_can_access_agent(db, user, dev.agent_id)
        return
    # unbound device or unknown MAC → not a per-patient resource a scoped
    # admin may touch.
    raise APIException(403, "not permitted for this device")

_DEFAULT_PREVIEW_TEXT = "Hi, I'm Hazel. It's really nice to talk with you today."

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
    """Atomically write voice_config.json via temp-file rename."""
    path = Path(settings.voice_config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same directory so rename is atomic (same fs).
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".voice_config_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)  # atomic on POSIX
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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


# ── Request / response models ─────────────────────────────────────────────────

class VoiceUpdatePayload(BaseModel):
    voice: str | None = None
    speed: str | None = None
    response_length: str | None = None  # brief | normal | detailed


class PreviewPayload(BaseModel):
    voice: str
    speed: str
    text: str | None = None


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
        list({v.get("engine") for v in catalog.get("voices", [])}),
    )
    return catalog


@router.get("/device/{mac}/voice", response_model=None)
async def get_device_voice(
    mac: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the configured {voice, speed} for a device.

    Falls back to the catalog default when no per-device entry exists.
    """
    mac = _normalise_mac(mac)
    await _authorize_device_mac(db, user, mac)

    # Read file without the write-lock — reads are idempotent.
    config = _read_config()
    device_cfg = config["devices"].get(mac)

    if device_cfg:
        log.debug("device voice lookup: mac=%s → %s", mac, device_cfg)
        return {"mac": mac, "voice": device_cfg.get("voice"), "speed": device_cfg.get("speed"),
                "response_length": device_cfg.get("response_length", "brief"), "source": "device"}

    # Fall back to file default, or the catalog default if file has none.
    file_default = config.get("default") or {}
    if file_default.get("voice"):
        log.debug("device voice lookup: mac=%s → file default %s", mac, file_default)
        return {"mac": mac, "voice": file_default.get("voice"), "speed": file_default.get("speed"),
                "response_length": file_default.get("response_length", "brief"), "source": "default"}

    # Last resort: pull from TTS catalog.
    try:
        catalog = await _fetch_catalog()
        catalog_default = catalog.get("default", "")
        catalog_speed = catalog.get("default_speed", "normal")
        log.debug("device voice lookup: mac=%s → catalog default %s", mac, catalog_default)
        return {"mac": mac, "voice": catalog_default, "speed": catalog_speed, "source": "catalog_default"}
    except APIException:
        log.warning("device voice lookup: mac=%s — TTS unreachable, returning nulls", mac)
        return {"mac": mac, "voice": None, "speed": None, "source": "unknown"}


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
    if payload.voice is None and payload.speed is None and payload.response_length is None:
        raise APIException(400, "at least one of 'voice', 'speed', 'response_length' must be provided")

    mac = _normalise_mac(mac)
    # RBAC gate BEFORE any catalog fetch / file lock / write.
    await _authorize_device_mac(db, user, mac)

    # Validate against TTS catalog before touching the file.
    catalog = await _fetch_catalog()
    valid_voice_ids = {v["id"] for v in catalog.get("voices", [])}
    valid_speed_ids = {s["id"] for s in catalog.get("speeds", [])}
    valid_length_ids = {l["id"] for l in catalog.get("lengths", [])} or {"brief", "normal", "detailed"}

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
        existing = config["devices"].get(mac, {})

        new_voice = payload.voice if payload.voice is not None else existing.get("voice")
        new_speed = payload.speed if payload.speed is not None else existing.get("speed")
        new_length = (payload.response_length if payload.response_length is not None
                      else existing.get("response_length"))

        # If still unset (device never configured), fall back to catalog defaults.
        if new_voice is None:
            new_voice = catalog.get("default", "")
        if new_speed is None:
            new_speed = catalog.get("default_speed", "normal")
        if new_length is None:
            new_length = catalog.get("default_length", "brief")

        config["devices"][mac] = {"voice": new_voice, "speed": new_speed,
                                  "response_length": new_length}
        _write_config(config)

    log.info("device voice updated: mac=%s voice=%s speed=%s length=%s",
             mac, new_voice, new_speed, new_length)
    return {"mac": mac, "voice": new_voice, "speed": new_speed, "response_length": new_length}


@router.post("/voice/preview", response_model=None)
async def preview_voice(
    payload: PreviewPayload,
    _user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """Stream a WAV preview from the TTS server for in-browser playback.

    POSTs to {tts_url}/v1/audio/speech and forwards the audio/wav bytes
    directly. The TTS server streams the response; we use httpx streaming
    to avoid buffering the full audio in memory.
    """
    text = payload.text if payload.text else _DEFAULT_PREVIEW_TEXT

    log.debug("voice preview: voice=%s speed=%s text_len=%d", payload.voice, payload.speed, len(text))

    try:
        client = httpx.AsyncClient(timeout=30.0)

        async def _iter_audio():
            try:
                async with client.stream(
                    "POST",
                    f"{settings.tts_url}/v1/audio/speech",
                    json={
                        "input": text,
                        "voice": payload.voice,
                        "speed": payload.speed,
                        "response_format": "wav",
                    },
                ) as resp:
                    if resp.status_code != 200:
                        # Can't raise APIException inside a generator after
                        # headers are sent; log and yield nothing.
                        log.warning(
                            "voice preview: TTS returned HTTP %d", resp.status_code
                        )
                        return
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        yield chunk
            finally:
                await client.aclose()

        return StreamingResponse(
            _iter_audio(),
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )

    except Exception as exc:
        raise APIException(502, f"TTS server unreachable: {exc}") from exc
