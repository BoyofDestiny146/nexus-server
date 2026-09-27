"""Organization grouping helpers.

Organization = who owns/groups the deployment.
Client = the person/session entity (ai_agent).
These stay separate from Personality, Voice, Knowledge, Assessment, and Revel.

v1 does not enforce organization-level authorization. Existing RBAC remains
root + cc_admin_client_access. Future path: cc_admin_organization join table.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .envelope import APIException
from .models import AiAgent, CcOrganization

UNASSIGNED_ORGANIZATION_ID = "unassigned"
ACTIVE_STATUS = "active"
INACTIVE_STATUS = "inactive"


def public_organization(row: CcOrganization, *, client_count: int | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": row.id,
        "name": row.name,
        "status": row.status,
        "mainContactName": row.main_contact_name,
        "mainContactEmail": row.main_contact_email,
        "mainContactPhone": row.main_contact_phone,
        "addressLine1": row.address_line1,
        "addressLine2": row.address_line2,
        "city": row.city,
        "state": row.state,
        "postalCode": row.postal_code,
        "country": row.country,
        "notes": row.notes,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }
    if client_count is not None:
        data["clientCount"] = client_count
    return data


def normalize_organization_ref(raw: Any) -> str | None:
    """Empty / 'unassigned' / null → Unassigned (None)."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() == UNASSIGNED_ORGANIZATION_ID:
        return None
    return text


async def get_organization(db: AsyncSession, organization_id: str | None) -> CcOrganization | None:
    oid = normalize_organization_ref(organization_id)
    if not oid:
        return None
    return await db.get(CcOrganization, oid)


async def count_organization_clients(db: AsyncSession, organization_id: str) -> int:
    count = (
        await db.execute(
            select(func.count()).select_from(AiAgent).where(AiAgent.organization_id == organization_id)
        )
    ).scalar_one()
    return int(count or 0)


async def resolve_organization_id(
    db: AsyncSession,
    raw: Any,
    *,
    allow_inactive_id: str | None = None,
) -> str | None:
    """Validate an assignment. None means Unassigned.

    Inactive orgs cannot be newly assigned. An already-linked inactive org
    may be kept when ``allow_inactive_id`` matches the current value.
    """
    oid = normalize_organization_ref(raw)
    if oid is None:
        return None
    row = await db.get(CcOrganization, oid)
    if row is None:
        raise APIException(404, "organization not found")
    if row.status != ACTIVE_STATUS and oid != allow_inactive_id:
        raise APIException(400, "organization is not active")
    return row.id
