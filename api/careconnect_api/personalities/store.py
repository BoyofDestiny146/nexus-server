"""Personality library persistence: seed, serialize, duplicate, usage."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AiAgent, CcAgentPersonality
from .seeds import DEFAULT_PERSONALITY_ID, SYSTEM_PERSONALITIES


def public_personality(row: CcAgentPersonality) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "category": row.category,
        "description": row.description,
        "promptTemplate": row.prompt_template,
        "isSystem": bool(row.is_system),
        "isActive": bool(row.is_active),
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def catalog_personality(row: CcAgentPersonality) -> dict[str, Any]:
    """Selector payload — no full prompt."""
    return {
        "id": row.id,
        "name": row.name,
        "category": row.category,
        "description": row.description,
        "isSystem": bool(row.is_system),
        "isActive": bool(row.is_active),
    }


async def seed_system_personalities(db: AsyncSession) -> None:
    """Idempotent upsert of built-in personalities. Custom rows are untouched."""
    now = datetime.now()
    existing = {
        row.id: row
        for row in (
            await db.execute(select(CcAgentPersonality).where(CcAgentPersonality.is_system == 1))
        ).scalars().all()
    }
    for spec in SYSTEM_PERSONALITIES:
        row = existing.get(spec["id"])
        if row is None:
            db.add(
                CcAgentPersonality(
                    id=spec["id"],
                    name=spec["name"],
                    category=spec["category"],
                    description=spec["description"],
                    prompt_template=spec["prompt_template"],
                    is_system=1,
                    is_active=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            continue
        row.name = spec["name"]
        row.category = spec["category"]
        row.description = spec["description"]
        row.prompt_template = spec["prompt_template"]
        row.is_system = 1
        row.is_active = 1
        row.updated_at = now
    await db.commit()


async def get_personality(db: AsyncSession, personality_id: str | None) -> CcAgentPersonality | None:
    if not personality_id or not str(personality_id).strip():
        return None
    return await db.get(CcAgentPersonality, str(personality_id).strip())


async def list_personalities(
    db: AsyncSession, *, include_inactive: bool = False
) -> list[CcAgentPersonality]:
    stmt = select(CcAgentPersonality)
    if not include_inactive:
        stmt = stmt.where(CcAgentPersonality.is_active == 1)
    stmt = stmt.order_by(
        CcAgentPersonality.is_system.desc(),
        CcAgentPersonality.category.asc(),
        CcAgentPersonality.name.asc(),
    )
    return list((await db.execute(stmt)).scalars().all())


async def duplicate_personality(
    db: AsyncSession, source: CcAgentPersonality, *, name: str | None = None
) -> CcAgentPersonality:
    now = datetime.now()
    copy_name = (name or "").strip() or f"Copy of {source.name}"
    row = CcAgentPersonality(
        id=f"cst_{uuid.uuid4().hex}",
        name=copy_name[:128],
        category="custom",
        description=source.description,
        prompt_template=source.prompt_template,
        is_system=0,
        is_active=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def count_personality_usage(db: AsyncSession, personality_id: str) -> int:
    needle = f'"personalityId":"{personality_id}"'
    count = (
        await db.execute(
            select(func.count()).select_from(AiAgent).where(AiAgent.profile_json.contains(needle))
        )
    ).scalar_one()
    return int(count or 0)


async def personality_mapping(db: AsyncSession, personality_id: str | None) -> dict[str, str] | None:
    row = await get_personality(db, personality_id)
    if row is None or not row.is_active:
        return None
    return {"name": row.name, "prompt_template": row.prompt_template}


async def default_personality_id(db: AsyncSession) -> str:
    row = await db.get(CcAgentPersonality, DEFAULT_PERSONALITY_ID)
    if row is not None:
        return row.id
    first = (
        await db.execute(
            select(CcAgentPersonality.id)
            .where(CcAgentPersonality.is_system == 1, CcAgentPersonality.is_active == 1)
            .limit(1)
        )
    ).scalar_one_or_none()
    return first or DEFAULT_PERSONALITY_ID
