"""Public client-facing API — watcher heartbeat and status endpoints.

Auth: static API key in X-API-Key header. Validated against the key stored in
~/.config/careconnect/client-api-key (auto-generated on first boot if missing)
or overridden via the CC_CLIENT_API_KEY_FILE env var.

These endpoints bypass JWT/RBAC entirely — they are for the W1-A watcher
hardware client, not for dashboard users.

Mounted under /api/v1 in main.py.

Endpoints
---------
POST /api/v1/watcher/heartbeat
    Body: {mac, battery?, fw?, rssi?}
    Upserts last_seen (UTC now) + telemetry for that device in ai_device.
    If no ai_device row exists for the MAC, creates a minimal one.

GET /api/v1/watcher/{mac}/status
    Returns {mac, clientDeviceId, online, last_seen, battery, fw, rssi}.
    online = last_seen within settings.watcher_online_window_seconds of UTC now.

GET /api/v1/watcher/by-client-id/{client_device_id}/status
    Same shape, resolved by the client's external device id.

GET /api/v1/watchers
    List of all devices with status.

Liveness note: the W1-A firmware never POSTs /watcher/heartbeat, so last_seen /
online are derived — the most recent of an explicit heartbeat and the device's
last conversation (MAX ai_agent_chat_history.created_at, joined on agent_id).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..envelope import APIException
from ..models import AiAgentChatHistory, AiDevice
from ..settings import settings
from ..watcher_device import (
    ensure_watcher_device,
    get_watcher_device,
    normalize_mac_upper,
)


log = logging.getLogger("public")

router = APIRouter(prefix="/v1", tags=["public"])


# ---------- auth dependency ----------

async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Validate the X-API-Key header against settings.client_api_key.

    Uses a simple equality check (the key is already a high-entropy random
    token; timing attacks against a 48-char random string are not a realistic
    threat vector on a local network).
    """
    expected = settings.client_api_key
    if not x_api_key or x_api_key != expected:
        raise APIException(401, "invalid or missing X-API-Key")


# ---------- request / response models ----------

class HeartbeatRequest(BaseModel):
    mac: str
    battery: int | None = None
    fw: str | None = None
    rssi: int | None = None


# ---------- helpers ----------

def _now_utc_naive() -> datetime:
    """UTC now as a naive datetime (MariaDB/SQLite store without tz info)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_online(last_seen: datetime | None) -> bool:
    if last_seen is None:
        return False
    window = timedelta(seconds=settings.watcher_online_window_seconds)
    return (_now_utc_naive() - last_seen) <= window


def _status_row(dev: AiDevice) -> dict[str, Any]:
    return {
        "mac": dev.mac_address,
        "clientDeviceId": dev.client_device_id,
        "online": _is_online(dev.last_seen),
        "last_seen": dev.last_seen.isoformat() if dev.last_seen else None,
        "battery": dev.battery,
        "fw": dev.fw,
        "rssi": dev.rssi,
    }


async def _last_conversation_age(
    db: AsyncSession, dev: AiDevice
) -> tuple[datetime | None, float | None]:
    """(timestamp, age-in-seconds) of this device's most recent chat turn — the
    liveness signal we fall back on because the W1-A firmware never POSTs
    /watcher/heartbeat.

    Joined on ``agent_id`` (the clean 32-char FK), NOT the MAC: ai_agent_chat_history
    stores the MAC lower-case with colons while ai_device stores it upper-case
    without, so a MAC join would silently miss. xiaozhi-server writes a chat-history
    row on every conversation turn, so MAX(created_at) is an always-fresh heartbeat.

    The age is computed against the DATABASE clock (func.now()), not Python UTC:
    xiaozhi-server stamps created_at with SQL NOW(), which runs in the DB server's
    local timezone (CDT on the Jetson). Comparing that against utcnow() made every
    device look ~5h stale, i.e. permanently offline.
    """
    if not dev.agent_id:
        return None, None
    from sqlalchemy import DateTime

    ts, db_now = (
        await db.execute(
            select(
                func.max(AiAgentChatHistory.created_at),
                func.now(type_=DateTime()),
            ).where(AiAgentChatHistory.agent_id == dev.agent_id)
        )
    ).first()
    if ts is None:
        return None, None
    if isinstance(ts, str):  # SQLite returns strings for aggregate datetimes
        ts = datetime.fromisoformat(ts)
    if isinstance(db_now, str):
        db_now = datetime.fromisoformat(db_now)
    return ts, (db_now - ts).total_seconds()


async def _status_for(db: AsyncSession, dev: AiDevice) -> dict[str, Any]:
    """Status row with an *effective* last_seen = the most recent of an explicit
    heartbeat POST and the device's last conversation. Keeps the response shape
    identical; only last_seen/online reflect the derived signal."""
    convo_ts, convo_age = await _last_conversation_age(db, dev)
    hb = dev.last_seen
    hb_age = (_now_utc_naive() - hb).total_seconds() if hb else None

    # Each source is aged against its own clock (heartbeats are written in UTC
    # by this API; chat turns in DB-local time by xiaozhi-server), so the ages
    # are comparable even though the raw timestamps are not.
    if convo_age is not None and (hb_age is None or convo_age <= hb_age):
        eff_ts, eff_age = convo_ts, convo_age
    else:
        eff_ts, eff_age = hb, hb_age

    row = _status_row(dev)
    row["last_seen"] = eff_ts.isoformat() if eff_ts else None
    row["online"] = (
        eff_age is not None
        and eff_age <= settings.watcher_online_window_seconds
    )
    return row


# ---------- endpoints ----------

@router.post("/watcher/heartbeat", response_model=None, dependencies=[Depends(require_api_key)])
async def watcher_heartbeat(
    payload: HeartbeatRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Upsert last_seen + telemetry for a watcher device.

    If no row exists for the MAC, a minimal ai_device row is created.
    Idempotent: repeated calls only advance last_seen and update telemetry.
    """
    mac = normalize_mac_upper(payload.mac)
    if not mac:
        raise APIException(400, "mac is required")

    try:
        dev = await ensure_watcher_device(
            db,
            mac,
            battery=payload.battery,
            fw=payload.fw,
            rssi=payload.rssi,
        )
    except Exception:
        await db.rollback()
        raise

    log.info(
        "heartbeat: mac=%s battery=%s fw=%s rssi=%s",
        mac, payload.battery, payload.fw, payload.rssi,
    )
    last_seen = dev.last_seen.isoformat() if dev.last_seen else None
    return {"mac": mac, "last_seen": last_seen}


@router.get("/watcher/{mac}/status", response_model=None, dependencies=[Depends(require_api_key)])
async def watcher_status(
    mac: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return current status for one device by MAC address."""
    mac = normalize_mac_upper(mac)
    dev = await get_watcher_device(db, mac)

    if dev is None:
        raise APIException(404, f"device {mac} not found")

    return await _status_for(db, dev)


@router.get(
    "/watcher/by-client-id/{client_device_id}/status",
    response_model=None,
    dependencies=[Depends(require_api_key)],
)
async def watcher_status_by_client_id(
    client_device_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return current status for one device by the client's external device id.

    Lets the client subsystem poll status with its own identifier instead of
    the MAC. 404 if no device carries that client_device_id.
    """
    dev = (
        await db.execute(
            select(AiDevice).where(AiDevice.client_device_id == client_device_id)
        )
    ).scalar_one_or_none()

    if dev is None:
        raise APIException(404, f"device with clientDeviceId {client_device_id} not found")

    return await _status_for(db, dev)


@router.get("/watchers", response_model=None, dependencies=[Depends(require_api_key)])
async def list_watchers(
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return status for all devices in ai_device, sorted by last_seen descending."""
    rows = (
        await db.execute(
            select(AiDevice).order_by(
                AiDevice.last_seen.is_(None),
                AiDevice.last_seen.desc(),
            )
        )
    ).scalars().all()

    return [await _status_for(db, dev) for dev in rows]
