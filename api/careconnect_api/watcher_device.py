"""Shared W1-A / SenseCAP Watcher upsert for ``ai_device``.

Used by ``POST /api/v1/watcher/heartbeat``. XiaoZhi WebSocket registration
mirrors this in ``xiaozhi-server/config/careconnect_db.py:ensure_watcher_device``
(separate process; pymysql, same column semantics).

Does not bind ``agent_id``, and never overwrites alias, client_device_id,
or existing telemetry unless new values are passed in.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AiDevice

log = logging.getLogger("watcher_device")

W1A_DEVICE_TYPE = "W1-A"
W1A_FIRMWARE_TYPE = "xiaozhi"
W1A_BOARD = "sensecap_watcher"


def _now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_mac_upper(raw: str) -> str:
    """Heartbeat create format: strip + uppercase, keep separators."""
    return (raw or "").strip().upper()


def mac_lookup_candidates(raw: str) -> list[str]:
    """Formats that may already exist in ``ai_device``.

    Heartbeat stores ``strip().upper()`` (often colon-separated). Onboarding
    stores uppercase hex with separators stripped. Look up both so we never
    insert a duplicate physical Watcher.
    """
    upper = normalize_mac_upper(raw)
    stripped = upper.replace(":", "").replace("-", "").replace(" ", "")
    out: list[str] = []
    for cand in (upper, stripped):
        if cand and cand not in out:
            out.append(cand)
    if len(stripped) == 12 and all(c in "0123456789ABCDEF" for c in stripped):
        colon = ":".join(stripped[i : i + 2] for i in range(0, 12, 2))
        if colon not in out:
            out.append(colon)
    return out


def device_id_for_mac(mac: str) -> str:
    return f"watcher-{mac.lower()[:24]}"


async def get_watcher_device(db: AsyncSession, mac: str) -> AiDevice | None:
    for cand in mac_lookup_candidates(mac):
        dev = (
            await db.execute(select(AiDevice).where(AiDevice.mac_address == cand))
        ).scalar_one_or_none()
        if dev is not None:
            return dev
    return None


async def ensure_watcher_device(
    db: AsyncSession,
    mac: str,
    *,
    battery: int | None = None,
    fw: str | None = None,
    rssi: int | None = None,
    touch_last_connected: bool = False,
) -> AiDevice:
    """Create a minimal W1-A row or update liveness on the existing one.

    New rows: mac_address as ``strip().upper()``, device_type/firmware/board
    as W1-A/xiaozhi/sensecap_watcher, sort=0, agent_id left NULL.
    Existing rows: agent_id, alias, client_device_id, mac_address format, and
    unspecified telemetry are preserved.
    """
    stored_mac = normalize_mac_upper(mac)
    if not stored_mac:
        raise ValueError("mac is required")

    now = _now_utc_naive()
    created = False
    dev = await get_watcher_device(db, stored_mac)

    if dev is None:
        dev = AiDevice(
            id=device_id_for_mac(stored_mac),
            mac_address=stored_mac,
            device_type=W1A_DEVICE_TYPE,
            firmware_type=W1A_FIRMWARE_TYPE,
            board=W1A_BOARD,
            sort=0,
        )
        db.add(dev)
        created = True

    dev.last_seen = now
    if touch_last_connected:
        dev.last_connected_at = now
    if battery is not None:
        dev.battery = battery
    if fw is not None:
        dev.fw = fw
    if rssi is not None:
        dev.rssi = rssi

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Concurrent insert of the same watcher — load the winner and apply liveness.
        dev = await get_watcher_device(db, stored_mac)
        if dev is None:
            raise
        created = False
        dev.last_seen = now
        if touch_last_connected:
            dev.last_connected_at = now
        if battery is not None:
            dev.battery = battery
        if fw is not None:
            dev.fw = fw
        if rssi is not None:
            dev.rssi = rssi
        await db.commit()

    if created:
        log.info("watcher registered mac=%s", dev.mac_address)
    else:
        log.info("watcher updated mac=%s", dev.mac_address)
    return dev
