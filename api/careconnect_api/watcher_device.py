"""Shared W1-A / SenseCAP Watcher upsert for ``ai_device``.

Used by ``POST /api/v1/watcher/heartbeat``. XiaoZhi WebSocket registration
mirrors this in ``xiaozhi-server/config/careconnect_db.py:ensure_watcher_device``
(separate process; pymysql, **same rules** as this module).

Identity rules (keep in lockstep with careconnect_db.py):
  * stripped hex (no ``:``/``-``/spaces, upper) is the physical-device key
  * ``ai_device.id`` is always ``watcher-<stripped.lower()[:24]>``
  * lookup matches colon, stripped, and original upper forms
  * new rows store colon-separated uppercase for 12-byte MACs
  * reconnect updates only liveness timestamps (and heartbeat telemetry
    when explicitly passed); never agent_id / alias / client_device_id
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
    """Strip surrounding whitespace and uppercase; keep separators."""
    return (raw or "").strip().upper()


def stripped_mac(raw: str) -> str:
    """Physical-device key: uppercase hex with separators removed."""
    return normalize_mac_upper(raw).replace(":", "").replace("-", "").replace(" ", "")


def colon_mac(raw: str) -> str | None:
    """``AA:BB:CC:DD:EE:FF`` for a 12-hex MAC, else None."""
    stripped = stripped_mac(raw)
    if len(stripped) == 12 and all(c in "0123456789ABCDEF" for c in stripped):
        return ":".join(stripped[i : i + 2] for i in range(0, 12, 2))
    return None


def canonical_store_mac(raw: str) -> str:
    """Format written on INSERT. 12-hex MACs use colon-separated uppercase."""
    return colon_mac(raw) or stripped_mac(raw) or normalize_mac_upper(raw)


def mac_lookup_candidates(raw: str) -> list[str]:
    """Every ``ai_device.mac_address`` spelling that means this Watcher."""
    upper = normalize_mac_upper(raw)
    stripped = stripped_mac(raw)
    out: list[str] = []
    for cand in (upper, stripped, colon_mac(raw)):
        if cand and cand not in out:
            out.append(cand)
    return out


def device_id_for_mac(mac: str) -> str:
    """Deterministic PK. Same for colon / stripped / mixed-case input.

    Matches onboard ``_device_id_for`` which keys off stripped hex.
    """
    key = stripped_mac(mac).lower()
    return f"watcher-{key[:24]}"


def display_mac(raw: str) -> str:
    """Log form: colon-separated uppercase when the MAC is 12 hex."""
    return colon_mac(raw) or normalize_mac_upper(raw)


async def get_watcher_device(db: AsyncSession, mac: str) -> AiDevice | None:
    for cand in mac_lookup_candidates(mac):
        dev = (
            await db.execute(select(AiDevice).where(AiDevice.mac_address == cand))
        ).scalar_one_or_none()
        if dev is not None:
            return dev
    # Fallback: same physical device already inserted under the canonical id
    # (concurrent mixed-format create).
    did = device_id_for_mac(mac)
    if did != "watcher-":
        return (
            await db.execute(select(AiDevice).where(AiDevice.id == did))
        ).scalar_one_or_none()
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

    New rows: canonical colon MAC, W1-A/xiaozhi/sensecap_watcher, sort=0,
    agent_id NULL. Existing rows: agent_id, alias, client_device_id,
    mac_address spelling, and unspecified telemetry are preserved.
    """
    if not stripped_mac(mac):
        raise ValueError("mac is required")

    now = _now_utc_naive()
    created = False
    dev = await get_watcher_device(db, mac)

    if dev is None:
        store_mac = canonical_store_mac(mac)
        dev = AiDevice(
            id=device_id_for_mac(mac),
            mac_address=store_mac,
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
        # Concurrent insert of the same PK — load the winner and apply liveness.
        dev = await get_watcher_device(db, mac)
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

    shown = display_mac(dev.mac_address or mac)
    if created:
        log.info("watcher registered mac=%s", shown)
    else:
        log.info("watcher updated mac=%s", shown)
    return dev
