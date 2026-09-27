"""Organization CRUD.

GET    /api/organizations
GET    /api/organizations/{id}
POST   /api/organizations
PUT    /api/organizations/{id}
POST   /api/organizations/{id}/deactivate
DELETE /api/organizations/{id}   (blocked if clients are still assigned)
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, get_current_user, require_root
from ..db import get_db
from ..envelope import APIException
from ..models import AiAgent, CcOrganization
from ..organizations import (
    ACTIVE_STATUS,
    INACTIVE_STATUS,
    count_organization_clients,
    get_organization,
    public_organization,
)

router = APIRouter(tags=["organizations"])


class OrganizationWrite(BaseModel):
    name: str | None = None
    status: str | None = None
    mainContactName: str | None = None
    mainContactEmail: str | None = None
    mainContactPhone: str | None = None
    addressLine1: str | None = None
    addressLine2: str | None = None
    city: str | None = None
    state: str | None = None
    postalCode: str | None = None
    country: str | None = None
    notes: str | None = None


def _clean(value: str | None, limit: int) -> str | None:
    text = " ".join((value or "").split())
    return text[:limit] or None


def _apply_write(row: CcOrganization, payload: OrganizationWrite, *, creating: bool) -> None:
    provided = payload.model_fields_set
    if creating or "name" in provided:
        name = _clean(payload.name, 128)
        if not name:
            raise APIException(400, "name is required")
        row.name = name
    if "status" in provided and payload.status is not None:
        status = payload.status.strip().lower()
        if status not in (ACTIVE_STATUS, INACTIVE_STATUS):
            raise APIException(400, "status must be active or inactive")
        row.status = status
    if creating or "mainContactName" in provided:
        row.main_contact_name = _clean(payload.mainContactName, 128)
    if creating or "mainContactEmail" in provided:
        row.main_contact_email = _clean(payload.mainContactEmail, 128)
    if creating or "mainContactPhone" in provided:
        row.main_contact_phone = _clean(payload.mainContactPhone, 64)
    if creating or "addressLine1" in provided:
        row.address_line1 = _clean(payload.addressLine1, 256)
    if creating or "addressLine2" in provided:
        row.address_line2 = _clean(payload.addressLine2, 256)
    if creating or "city" in provided:
        row.city = _clean(payload.city, 128)
    if creating or "state" in provided:
        row.state = _clean(payload.state, 64)
    if creating or "postalCode" in provided:
        row.postal_code = _clean(payload.postalCode, 32)
    if creating or "country" in provided:
        row.country = _clean(payload.country, 64)
    if creating or "notes" in provided:
        notes = (payload.notes or "").strip()
        row.notes = notes or None


@router.get("/organizations", response_model=None)
async def list_organizations(
    includeInactive: bool = False,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(CcOrganization)
    if not includeInactive:
        stmt = stmt.where(CcOrganization.status == ACTIVE_STATUS)
    stmt = stmt.order_by(CcOrganization.name.asc())
    rows = list((await db.execute(stmt)).scalars().all())
    out = []
    for row in rows:
        out.append(
            public_organization(row, client_count=await count_organization_clients(db, row.id))
        )
    return {"organizations": out}


@router.get("/organizations/{organization_id}", response_model=None)
async def get_one(
    organization_id: str,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await get_organization(db, organization_id)
    if row is None:
        raise APIException(404, "organization not found")
    clients = list(
        (
            await db.execute(
                select(AiAgent)
                .where(AiAgent.organization_id == row.id)
                .order_by(AiAgent.agent_name.asc())
            )
        ).scalars().all()
    )
    data = public_organization(row, client_count=len(clients))
    data["clients"] = [
        {"id": a.id, "agentName": a.agent_name, "createdAt": a.created_at}
        for a in clients
    ]
    return data


@router.post("/organizations", response_model=None)
async def create_organization(
    payload: OrganizationWrite,
    _user: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    now = datetime.now()
    row = CcOrganization(
        id=uuid4().hex,
        status=ACTIVE_STATUS,
        created_at=now,
        updated_at=now,
    )
    _apply_write(row, payload, creating=True)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return public_organization(row, client_count=0)


@router.put("/organizations/{organization_id}", response_model=None)
async def update_organization(
    organization_id: str,
    payload: OrganizationWrite,
    _user: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await get_organization(db, organization_id)
    if row is None:
        raise APIException(404, "organization not found")
    _apply_write(row, payload, creating=False)
    row.updated_at = datetime.now()
    await db.commit()
    await db.refresh(row)
    return public_organization(
        row, client_count=await count_organization_clients(db, row.id)
    )


@router.post("/organizations/{organization_id}/deactivate", response_model=None)
async def deactivate_organization(
    organization_id: str,
    _user: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await get_organization(db, organization_id)
    if row is None:
        raise APIException(404, "organization not found")
    row.status = INACTIVE_STATUS
    row.updated_at = datetime.now()
    await db.commit()
    await db.refresh(row)
    return public_organization(
        row, client_count=await count_organization_clients(db, row.id)
    )


@router.delete("/organizations/{organization_id}", response_model=None)
async def delete_organization(
    organization_id: str,
    _user: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await get_organization(db, organization_id)
    if row is None:
        raise APIException(404, "organization not found")
    used = await count_organization_clients(db, row.id)
    if used:
        raise APIException(
            409,
            "organization still has clients; reassign or deactivate instead of deleting",
            data={"clientCount": used},
        )
    await db.delete(row)
    await db.commit()
    return {"deleted": True, "id": organization_id}
