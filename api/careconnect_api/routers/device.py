"""Device endpoints — port of the inherited Java DeviceController.

Mounted at /api by main.py; routes here own their /device/... or
/admin/device/... prefix individually because the dashboard splits between
the two namespaces.

Note: the W1-B bridge (SenseCAP Path E) has been removed. The
/device/unbound-recent endpoint that read careconnect-bridge.service journal
logs is gone. Device discovery is now handled via the watcher heartbeat API
under /api/v1/watcher/* (W1-A/XiaoZhi path only).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, get_current_user, require_root
from ..db import get_db
from ..envelope import APIException
from ..models import AiAgent, AiAgentChatHistory, AiDevice
from ..rbac import assert_can_access_agent


router = APIRouter(tags=["device"])


# ---------- helpers ----------

# Correlated scalar subquery: the device's most recent chat turn. Nothing in the
# stack writes ai_device.last_connected_at (the firmware never POSTs heartbeat),
# so without this every dashboard row shows "never". xiaozhi-server writes a
# chat-history row per conversation turn — join on agent_id, NOT mac (chat rows
# store the MAC lower-case with colons, ai_device upper-case without).
_LAST_CHAT_AT = (
    select(func.max(AiAgentChatHistory.created_at))
    .where(AiAgentChatHistory.agent_id == AiDevice.agent_id)
    .correlate(AiDevice)
    .scalar_subquery()
)


def _device_row(
    dev: AiDevice,
    *,
    agent_name: str | None = None,
    last_chat_at: Any = None,
) -> dict[str, Any]:
    """Map an AiDevice ORM row to the camelCase shape the Vue dashboard
    expects (see dashboard/src/api/types.ts:DeviceRow). lastConnectedAt is the
    most recent of the stored column and the device's last conversation."""
    if isinstance(last_chat_at, str):  # SQLite returns aggregate datetimes as strings
        last_chat_at = datetime.fromisoformat(last_chat_at)
    effective = dev.last_connected_at
    if last_chat_at is not None and (effective is None or last_chat_at > effective):
        effective = last_chat_at
    row: dict[str, Any] = {
        "id": dev.id,
        "macAddress": dev.mac_address,
        "clientDeviceId": dev.client_device_id,
        "agentId": dev.agent_id,
        "alias": dev.alias,
        "board": dev.board,
        "deviceType": dev.device_type,
        "firmwareType": dev.firmware_type,
        "lastConnectedAt": effective,
        "appVersion": dev.app_version,
        "autoUpdate": dev.auto_update,
    }
    if agent_name is not None:
        row["agentName"] = agent_name
    return row


# ---------- 1. GET /device/bind/{agentId} — list bound devices ----------

@router.get("/device/bind/{agent_id}", response_model=None)
async def list_devices_for_agent(
    agent_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List devices bound to the given agent. RBAC-scoped: admins see only
    agents granted via cc_admin_client_access; root sees all."""
    await assert_can_access_agent(db, user, agent_id)

    # MariaDB doesn't support `NULLS LAST` syntax — emulate with `IS NULL` first.
    rows = (
        await db.execute(
            select(AiDevice, _LAST_CHAT_AT)
            .where(AiDevice.agent_id == agent_id)
            .order_by(
                AiDevice.last_connected_at.is_(None),
                AiDevice.last_connected_at.desc(),
            )
        )
    ).all()

    return [_device_row(d, last_chat_at=chat_at) for (d, chat_at) in rows]


# ---------- 1b. GET /device/by-client-id/{client_device_id} — resolve by external id ----------

@router.get("/device/by-client-id/{client_device_id}", response_model=None)
async def get_device_by_client_id(
    client_device_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Resolve a device (and its bound patient) by the client's own external
    device id. RBAC-scoped: a non-root admin may only resolve devices bound to
    an agent they can access; an unbound device is root-only. The row is
    augmented with the bound agent's display name (left join on ai_agent)."""
    row = (
        await db.execute(
            select(AiDevice, AiAgent.agent_name)
            .select_from(AiDevice)
            .outerjoin(AiAgent, AiAgent.id == AiDevice.agent_id)
            .where(AiDevice.client_device_id == client_device_id)
        )
    ).first()

    if row is None:
        raise APIException(404, f"no device with clientDeviceId {client_device_id!r}")

    dev, agent_name = row
    if dev.agent_id:
        await assert_can_access_agent(db, user, dev.agent_id)
    elif not user.is_root:
        # Unbound device — only root may see devices not scoped to any agent.
        raise APIException(404, f"no device with clientDeviceId {client_device_id!r}")

    return _device_row(dev, agent_name=agent_name)


# ---------- 1c. PATCH /device/{device_id}/client-id — edit external id ----------

class ClientDeviceIdUpdate(BaseModel):
    # None or blank clears the id; column is VARCHAR(64).
    clientDeviceId: str | None = Field(None, max_length=64)


@router.patch("/device/{device_id}/client-id", response_model=None)
async def set_device_client_id(
    device_id: str,
    payload: ClientDeviceIdUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Set, change, or clear a device's client-supplied external id.
    RBAC-scoped like /device/by-client-id: a non-root admin may only edit
    devices bound to an agent they can access; unbound devices are root-only
    (masked as 404). A value already used by another device is a 409 — the
    by-client-id lookups assume the id resolves to a single device."""
    dev = (
        await db.execute(select(AiDevice).where(AiDevice.id == device_id))
    ).scalar_one_or_none()
    if dev is None:
        raise APIException(404, f"device {device_id} not found")

    if dev.agent_id:
        await assert_can_access_agent(db, user, dev.agent_id)
    elif not user.is_root:
        raise APIException(404, f"device {device_id} not found")

    value = (payload.clientDeviceId or "").strip() or None

    if value is not None:
        conflict = (
            await db.execute(
                select(AiDevice.id, AiDevice.mac_address)
                .where(
                    AiDevice.client_device_id == value,
                    AiDevice.id != device_id,
                )
            )
        ).first()
        if conflict is not None:
            raise APIException(
                409,
                f"clientDeviceId {value!r} is already assigned to device "
                f"{conflict.mac_address}",
                data={"deviceId": conflict.id, "macAddress": conflict.mac_address},
            )

    dev.client_device_id = value
    dev.updater = user.id
    dev.update_date = func.now()
    await db.commit()
    await db.refresh(dev)

    return _device_row(dev)


# ---------- 2. GET /admin/device/all — root-only paged list ----------

@router.get("/admin/device/all", response_model=None)
async def list_all_devices(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    keywords: str | None = Query(None),
    _user: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Root-only paged list across every device. `keywords` filters by
    mac_address LIKE OR alias LIKE. Each row is augmented with the bound
    agent's display name via a left join on ai_agent."""
    base = select(AiDevice, AiAgent.agent_name, _LAST_CHAT_AT).select_from(AiDevice).outerjoin(
        AiAgent, AiAgent.id == AiDevice.agent_id
    )
    count_base = select(func.count()).select_from(AiDevice)

    if keywords:
        like = f"%{keywords}%"
        cond = or_(AiDevice.mac_address.like(like), AiDevice.alias.like(like))
        base = base.where(cond)
        count_base = count_base.where(cond)

    total = (await db.execute(count_base)).scalar_one()

    offset = (page - 1) * limit
    rows = (
        await db.execute(
            base.order_by(
                AiDevice.last_connected_at.is_(None),
                AiDevice.last_connected_at.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
    ).all()

    items = [
        _device_row(dev, agent_name=agent_name, last_chat_at=chat_at)
        for (dev, agent_name, chat_at) in rows
    ]

    return {"list": items, "total": total, "page": page, "limit": limit}


# Note: GET /device/unbound-recent (bridge journal log scraper) was removed
# when the W1-B SenseCAP bridge was dropped. Device discovery now happens via
# the watcher heartbeat API (/api/v1/watcher/*) which uses the W1-A XiaoZhi
# path.
