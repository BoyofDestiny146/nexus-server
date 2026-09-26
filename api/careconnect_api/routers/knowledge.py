"""Knowledge Fabric admin + resolver endpoints.

Mounted at /api by main.py.

    GET    /knowledge-base
    POST   /knowledge-base
    GET    /knowledge-base/{id}
    PUT    /knowledge-base/{id}
    GET    /knowledge-base/{id}/topics
    POST   /knowledge-base/{id}/topics
    PUT    /knowledge-topic/{topic_id}
    DELETE /knowledge-topic/{topic_id}
    GET    /agent/{agent_id}/knowledge-bases
    PUT    /agent/{agent_id}/knowledge-bases
    GET    /device/{mac}/knowledge-context
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, get_current_user
from ..db import get_db
from ..envelope import APIException
from ..knowledge import (
    _flag,
    list_client_assignments,
    load_topics,
    normalize_mac,
    replace_client_assignments,
    resolve_device_knowledge_context,
    serialize_base,
    serialize_topic,
    topic_counts,
    validate_slug,
    validate_topic_key,
)
from ..models import AiAgent, KnowledgeBase, KnowledgeTopic
from ..rbac import assert_can_access_agent
from ..watcher_device import get_watcher_device


router = APIRouter(tags=["knowledge"])


class KnowledgeBaseIn(BaseModel):
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    enabled: bool | int | None = None
    knowledgeType: str | None = None


class KnowledgeTopicIn(BaseModel):
    topicKey: str | None = None
    title: str | None = None
    description: str | None = None
    enabled: bool | int | None = None
    revelTag: str | None = None
    revelAutoTrigger: bool | int | None = None
    sortOrder: int | None = Field(default=None, ge=0, le=10_000)


class KnowledgeAssignmentIn(BaseModel):
    knowledgeBaseId: int
    enabled: bool | int = True


class ClientKnowledgePut(BaseModel):
    assignments: list[KnowledgeAssignmentIn] = Field(default_factory=list)


async def _get_base(db: AsyncSession, knowledge_base_id: int) -> KnowledgeBase:
    row = (
        await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id))
    ).scalar_one_or_none()
    if row is None:
        raise APIException(404, f"knowledge base {knowledge_base_id} not found")
    return row


async def _get_topic(db: AsyncSession, topic_id: int) -> KnowledgeTopic:
    row = (
        await db.execute(select(KnowledgeTopic).where(KnowledgeTopic.id == topic_id))
    ).scalar_one_or_none()
    if row is None:
        raise APIException(404, f"knowledge topic {topic_id} not found")
    return row


async def _authorize_device_mac(db: AsyncSession, user: CurrentUser, mac: str) -> None:
    if user.is_root:
        return
    dev = await get_watcher_device(db, mac)
    if dev is not None and dev.agent_id:
        await assert_can_access_agent(db, user, dev.agent_id)
        return
    raise APIException(403, "not permitted for this device")


@router.get("/knowledge-base", response_model=None)
async def list_knowledge_bases(
    keywords: str | None = Query(None),
    enabled: bool | None = Query(None),
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(KnowledgeBase)
    if keywords:
        like = f"%{keywords.strip()}%"
        stmt = stmt.where(
            or_(KnowledgeBase.name.like(like), KnowledgeBase.slug.like(like))
        )
    if enabled is not None:
        stmt = stmt.where(KnowledgeBase.enabled == (1 if enabled else 0))
    stmt = stmt.order_by(KnowledgeBase.name.asc(), KnowledgeBase.id.asc())
    rows = list((await db.execute(stmt)).scalars().all())
    counts = await topic_counts(db, [r.id for r in rows])
    return {
        "list": [serialize_base(r, topic_count=counts.get(r.id, 0)) for r in rows],
        "total": len(rows),
    }


@router.post("/knowledge-base", response_model=None)
async def create_knowledge_base(
    payload: KnowledgeBaseIn,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    name = (payload.name or "").strip()
    if not name:
        raise APIException(400, "name is required")
    slug = validate_slug(payload.slug, name=name)
    clash = (
        await db.execute(select(KnowledgeBase.id).where(KnowledgeBase.slug == slug))
    ).scalar_one_or_none()
    if clash is not None:
        raise APIException(409, f"slug {slug!r} is already in use")
    row = KnowledgeBase(
        name=name,
        slug=slug,
        description=(payload.description or "").strip() or None,
        enabled=_flag(payload.enabled, 1),
        knowledge_type=(payload.knowledgeType or "").strip() or None,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise APIException(409, f"slug {slug!r} is already in use") from exc
    await db.refresh(row)
    return serialize_base(row, topic_count=0, topics=[])


@router.get("/knowledge-base/{knowledge_base_id}", response_model=None)
async def get_knowledge_base(
    knowledge_base_id: int,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await _get_base(db, knowledge_base_id)
    topics = [serialize_topic(t) for t in await load_topics(db, row.id)]
    return serialize_base(row, topic_count=len(topics), topics=topics)


@router.put("/knowledge-base/{knowledge_base_id}", response_model=None)
async def update_knowledge_base(
    knowledge_base_id: int,
    payload: KnowledgeBaseIn,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await _get_base(db, knowledge_base_id)
    provided = payload.model_fields_set
    if "name" in provided:
        name = (payload.name or "").strip()
        if not name:
            raise APIException(400, "name cannot be blank")
        row.name = name
    if "slug" in provided:
        slug = validate_slug(payload.slug, name=row.name)
        clash = (
            await db.execute(
                select(KnowledgeBase.id).where(
                    KnowledgeBase.slug == slug, KnowledgeBase.id != row.id
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise APIException(409, f"slug {slug!r} is already in use")
        row.slug = slug
    if "description" in provided:
        row.description = (payload.description or "").strip() or None
    if "enabled" in provided:
        row.enabled = _flag(payload.enabled, row.enabled)
    if "knowledgeType" in provided:
        row.knowledge_type = (payload.knowledgeType or "").strip() or None
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise APIException(409, "slug is already in use") from exc
    await db.refresh(row)
    topics = [serialize_topic(t) for t in await load_topics(db, row.id)]
    return serialize_base(row, topic_count=len(topics), topics=topics)


@router.get("/knowledge-base/{knowledge_base_id}/topics", response_model=None)
async def list_topics(
    knowledge_base_id: int,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _get_base(db, knowledge_base_id)
    topics = [serialize_topic(t) for t in await load_topics(db, knowledge_base_id)]
    return {"list": topics, "total": len(topics)}


@router.post("/knowledge-base/{knowledge_base_id}/topics", response_model=None)
async def create_topic(
    knowledge_base_id: int,
    payload: KnowledgeTopicIn,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _get_base(db, knowledge_base_id)
    title = (payload.title or "").strip()
    if not title:
        raise APIException(400, "title is required")
    topic_key = validate_topic_key(payload.topicKey or "")
    max_sort = (
        await db.execute(
            select(func.max(KnowledgeTopic.sort_order)).where(
                KnowledgeTopic.knowledge_base_id == knowledge_base_id
            )
        )
    ).scalar_one()
    row = KnowledgeTopic(
        knowledge_base_id=knowledge_base_id,
        topic_key=topic_key,
        title=title,
        description=(payload.description or "").strip() or None,
        enabled=_flag(payload.enabled, 1),
        revel_tag=(payload.revelTag or "").strip() or None,
        revel_auto_trigger=_flag(payload.revelAutoTrigger, 0),
        sort_order=payload.sortOrder if payload.sortOrder is not None else int(max_sort or 0) + 1,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise APIException(409, f"topicKey {topic_key!r} already exists on this knowledge base") from exc
    await db.refresh(row)
    return serialize_topic(row)


@router.put("/knowledge-topic/{topic_id}", response_model=None)
async def update_topic(
    topic_id: int,
    payload: KnowledgeTopicIn,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await _get_topic(db, topic_id)
    provided = payload.model_fields_set
    if "title" in provided:
        title = (payload.title or "").strip()
        if not title:
            raise APIException(400, "title cannot be blank")
        row.title = title
    if "topicKey" in provided:
        row.topic_key = validate_topic_key(payload.topicKey or "")
    if "description" in provided:
        row.description = (payload.description or "").strip() or None
    if "enabled" in provided:
        row.enabled = _flag(payload.enabled, row.enabled)
    if "revelTag" in provided:
        row.revel_tag = (payload.revelTag or "").strip() or None
    if "revelAutoTrigger" in provided:
        row.revel_auto_trigger = _flag(payload.revelAutoTrigger, 0)
    if "sortOrder" in provided and payload.sortOrder is not None:
        row.sort_order = payload.sortOrder
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise APIException(409, "topicKey already exists on this knowledge base") from exc
    await db.refresh(row)
    return serialize_topic(row)


@router.delete("/knowledge-topic/{topic_id}", response_model=None)
async def delete_topic(
    topic_id: int,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await _get_topic(db, topic_id)
    await db.delete(row)
    await db.commit()
    return {"deleted": True, "id": topic_id}


@router.get("/agent/{agent_id}/knowledge-bases", response_model=None)
async def get_client_knowledge_bases(
    agent_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await assert_can_access_agent(db, user, agent_id)
    agent = (
        await db.execute(select(AiAgent.id).where(AiAgent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise APIException(404, f"agent {agent_id} not found")
    items = await list_client_assignments(db, agent_id)
    return {"list": items, "total": len(items)}


@router.put("/agent/{agent_id}/knowledge-bases", response_model=None)
async def put_client_knowledge_bases(
    agent_id: str,
    payload: ClientKnowledgePut,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await assert_can_access_agent(db, user, agent_id)
    agent = (
        await db.execute(select(AiAgent.id).where(AiAgent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise APIException(404, f"agent {agent_id} not found")
    await replace_client_assignments(
        db,
        agent_id,
        [
            {"knowledgeBaseId": a.knowledgeBaseId, "enabled": a.enabled}
            for a in payload.assignments
        ],
    )
    items = await list_client_assignments(db, agent_id)
    return {"list": items, "total": len(items)}


@router.get("/device/{mac}/knowledge-context", response_model=None)
async def get_device_knowledge_context(
    mac: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Read-only client→device inheritance. Does not execute Revel."""
    mac_norm = normalize_mac(mac)
    await _authorize_device_mac(db, user, mac_norm)
    return await resolve_device_knowledge_context(db, mac_norm)
