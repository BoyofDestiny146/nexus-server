"""Authoritative client → Revel player mapping.

Writes must target ``revel_device_id``. ``device_key`` is frozen at mapping
time for UI/display only. Callers never supply a Revel device ID on the
display path; the operator selects a discovered device, and this table
stores the immutable id.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .knowledge import slugify
from .models import RevelPlayerMap

log = logging.getLogger("revel_player_map")

_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_DEVICE_KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_revel_device_id(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text or not _DEVICE_ID_RE.match(text):
        return None
    return text[:128]


def normalize_device_key(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text or not _DEVICE_KEY_RE.match(text) or len(text) > 128:
        return None
    return text


def public_player_map(row: RevelPlayerMap) -> dict[str, Any]:
    return {
        "deviceKey": row.device_key,
        "revelDeviceId": row.revel_device_id,
        "revelDeviceName": row.revel_device_name,
    }


def _id_suffix(revel_device_id: str) -> str:
    compact = re.sub(r"[^a-z0-9]", "", revel_device_id.casefold())
    return (compact[-8:] or "id")[:8]


def allocate_device_key(
    name: str | None,
    revel_device_id: str,
    taken: set[str],
) -> str:
    """Freeze a UI key. Duplicate display names get an id suffix; ids stay unique."""
    base = slugify(name or "", max_len=96) or "player"
    if base not in taken:
        return base
    suffix = _id_suffix(revel_device_id)
    candidate = f"{base}-{suffix}"[:128]
    if candidate not in taken:
        return candidate
    n = 2
    while True:
        extra = f"{base}-{suffix}-{n}"[:128]
        if extra not in taken:
            return extra
        n += 1


async def list_player_maps(db: AsyncSession, agent_id: str) -> list[RevelPlayerMap]:
    rows = (
        await db.execute(
            select(RevelPlayerMap)
            .where(RevelPlayerMap.agent_id == agent_id)
            .order_by(RevelPlayerMap.id.asc())
        )
    ).scalars().all()
    return list(rows)


async def get_player_map(
    db: AsyncSession,
    agent_id: str,
    *,
    device_key: str | None = None,
    revel_device_id: str | None = None,
) -> RevelPlayerMap | None:
    if not agent_id:
        return None
    stmt = select(RevelPlayerMap).where(RevelPlayerMap.agent_id == agent_id)
    if device_key:
        key = normalize_device_key(device_key)
        if not key:
            return None
        stmt = stmt.where(RevelPlayerMap.device_key == key)
    elif revel_device_id:
        did = normalize_revel_device_id(revel_device_id)
        if not did:
            return None
        stmt = stmt.where(RevelPlayerMap.revel_device_id == did)
    else:
        return None
    return (await db.execute(stmt)).scalar_one_or_none()


async def resolve_player_map(
    db: AsyncSession,
    agent_id: str,
    *,
    device_key: str | None = None,
    selected_device_id: str | None = None,
) -> RevelPlayerMap | None:
    """Resolve the write target from stored mapping only.

    ``device_key`` is the frozen UI key. Selected Revel device id comes from
    server-side integration metadata, never from the request body.
    """
    if device_key:
        return await get_player_map(db, agent_id, device_key=device_key)
    if selected_device_id:
        mapped = await get_player_map(
            db, agent_id, revel_device_id=selected_device_id
        )
        if mapped is not None:
            return mapped
    rows = await list_player_maps(db, agent_id)
    if len(rows) == 1:
        return rows[0]
    return None


async def delete_player_maps(db: AsyncSession, agent_id: str) -> None:
    rows = await list_player_maps(db, agent_id)
    for row in rows:
        await db.delete(row)


async def upsert_selected_player(
    db: AsyncSession,
    *,
    agent_id: str,
    revel_device_id: str,
    revel_device_name: str | None,
) -> RevelPlayerMap | None:
    """Create or refresh the mapping for an operator-selected discovered device.

    Existing ``device_key`` for this (agent, revel_device_id) is frozen.
    Duplicate names on a different device id get a distinct key.
    """
    did = normalize_revel_device_id(revel_device_id)
    if not agent_id or not did:
        return None
    name = (revel_device_name or "").strip()[:128] or did
    existing = await get_player_map(db, agent_id, revel_device_id=did)
    now = _now()
    if existing is not None:
        existing.revel_device_name = name
        existing.updated_at = now
        log.info(
            "revel player map refreshed agent=%s device_key=%s",
            agent_id,
            existing.device_key,
        )
        return existing

    taken = {row.device_key for row in await list_player_maps(db, agent_id)}
    key = allocate_device_key(name, did, taken)
    row = RevelPlayerMap(
        agent_id=agent_id,
        device_key=key,
        revel_device_id=did,
        revel_device_name=name,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    log.info(
        "revel player map created agent=%s device_key=%s",
        agent_id,
        key,
    )
    return row


async def upsert_from_meta(
    db: AsyncSession, agent_id: str, meta: dict[str, Any]
) -> RevelPlayerMap | None:
    device_id = normalize_revel_device_id(meta.get("deviceId"))
    if not device_id:
        return None
    name = str(meta.get("deviceName") or "").strip() or None
    devices = meta.get("discoveredDevices") if isinstance(meta.get("discoveredDevices"), list) else []
    for raw in devices:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("id") or "").strip() == device_id:
            discovered = str(raw.get("name") or "").strip()
            if discovered:
                name = discovered
            break
    return await upsert_selected_player(
        db,
        agent_id=agent_id,
        revel_device_id=device_id,
        revel_device_name=name,
    )
