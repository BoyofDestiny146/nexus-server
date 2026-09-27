"""Agent Personality library endpoints.

GET    /api/personalities
GET    /api/personalities/{id}
POST   /api/personalities/{id}/duplicate
PUT    /api/personalities/{id}          (custom only)
POST   /api/personalities/{id}/deactivate
DELETE /api/personalities/{id}          (custom, unused only)
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, get_current_user, require_root
from ..db import get_db
from ..envelope import APIException
from ..personalities.store import (
    count_personality_usage,
    duplicate_personality,
    get_personality,
    list_personalities,
    public_personality,
)

router = APIRouter(tags=["personalities"])


class PersonalityWrite(BaseModel):
    name: str | None = None
    description: str | None = None
    promptTemplate: str | None = None
    isActive: bool | None = None


class DuplicateRequest(BaseModel):
    name: str | None = None


@router.get("/personalities", response_model=None)
async def list_library(
    includeInactive: bool = False,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = await list_personalities(db, include_inactive=includeInactive)
    return {"personalities": [public_personality(r) for r in rows]}


@router.get("/personalities/{personality_id}", response_model=None)
async def get_one(
    personality_id: str,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await get_personality(db, personality_id)
    if row is None:
        raise APIException(404, "personality not found")
    data = public_personality(row)
    data["inUseCount"] = await count_personality_usage(db, row.id)
    return data


@router.post("/personalities/{personality_id}/duplicate", response_model=None)
async def duplicate(
    personality_id: str,
    payload: DuplicateRequest | None = None,
    _user: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    source = await get_personality(db, personality_id)
    if source is None:
        raise APIException(404, "personality not found")
    if not source.is_active and not source.is_system:
        raise APIException(400, "cannot duplicate an inactive personality")
    copy = await duplicate_personality(
        db, source, name=(payload.name if payload else None)
    )
    return public_personality(copy)


@router.put("/personalities/{personality_id}", response_model=None)
async def update_custom(
    personality_id: str,
    payload: PersonalityWrite,
    _user: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await get_personality(db, personality_id)
    if row is None:
        raise APIException(404, "personality not found")
    if row.is_system:
        raise APIException(400, "system personalities cannot be edited")
    provided = payload.model_fields_set
    if "name" in provided:
        name = (payload.name or "").strip()
        if not name:
            raise APIException(400, "name is required")
        row.name = name[:128]
    if "description" in provided:
        desc = (payload.description or "").strip()
        row.description = desc[:512] or None
    if "promptTemplate" in provided:
        text = (payload.promptTemplate or "").strip()
        if not text:
            raise APIException(400, "promptTemplate is required")
        row.prompt_template = text
    if "isActive" in provided and payload.isActive is not None:
        row.is_active = 1 if payload.isActive else 0
    row.updated_at = datetime.now()
    await db.commit()
    await db.refresh(row)
    return public_personality(row)


@router.post("/personalities/{personality_id}/deactivate", response_model=None)
async def deactivate(
    personality_id: str,
    _user: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await get_personality(db, personality_id)
    if row is None:
        raise APIException(404, "personality not found")
    if row.is_system:
        raise APIException(400, "system personalities cannot be deactivated")
    row.is_active = 0
    row.updated_at = datetime.now()
    await db.commit()
    await db.refresh(row)
    return public_personality(row)


@router.delete("/personalities/{personality_id}", response_model=None)
async def delete_custom(
    personality_id: str,
    _user: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await get_personality(db, personality_id)
    if row is None:
        raise APIException(404, "personality not found")
    if row.is_system:
        raise APIException(400, "system personalities cannot be deleted")
    used = await count_personality_usage(db, row.id)
    if used:
        raise APIException(
            409,
            "personality is assigned to clients; deactivate it instead of deleting",
            data={"inUseCount": used},
        )
    await db.delete(row)
    await db.commit()
    return {"deleted": True, "id": personality_id}
