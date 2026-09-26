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
    GET    /knowledge-base/{id}/sources
    POST   /knowledge-base/{id}/sources
    GET    /knowledge-source/{source_id}
    PUT    /knowledge-source/{source_id}
    POST   /knowledge-source/{source_id}/replace
    POST   /knowledge-source/{source_id}/reprocess
    DELETE /knowledge-source/{source_id}
    GET    /knowledge-source/{source_id}/file
    GET    /knowledge-base/{id}/test-search
    POST   /knowledge/search
    GET    /agent/{agent_id}/knowledge-bases
    PUT    /agent/{agent_id}/knowledge-bases
    GET    /device/{mac}/knowledge-context
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, get_current_user
from ..db import get_db
from ..envelope import APIException
from ..knowledge import (
    _flag,
    client_counts,
    keyword_test_search,
    list_assigned_clients,
    list_client_assignments,
    load_sources,
    load_topics,
    normalize_mac,
    replace_client_assignments,
    resolve_device_knowledge_context,
    serialize_base,
    serialize_source,
    serialize_topic,
    source_counts,
    topic_counts,
    validate_slug,
    validate_topic_key,
)
from ..knowledge_retrieval import (
    delete_indexed_source,
    maybe_process_source,
    queue_source_processing,
    retrieval_configured,
    semantic_search,
    serialize_source_detail,
    sync_source_enabled,
)
from ..knowledge_storage import (
    MAX_SOURCE_BYTES,
    TEXT_SOURCE_TYPES,
    absolute_source_path,
    delete_source_file,
    display_filename,
    extension_for,
    mime_for,
    validate_source_type,
    write_source_bytes,
)
from ..models import AiAgent, KnowledgeBase, KnowledgeSource, KnowledgeTopic
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


class KnowledgeSourceMetaIn(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | int | None = None
    topicId: int | None = None
    bodyText: str | None = None


class KnowledgeSearchIn(BaseModel):
    knowledgeBaseIds: list[int] = Field(default_factory=list)
    query: str = ""
    limit: int = Field(default=5, ge=1, le=50)


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


async def _get_source(db: AsyncSession, source_id: int) -> KnowledgeSource:
    row = (
        await db.execute(select(KnowledgeSource).where(KnowledgeSource.id == source_id))
    ).scalar_one_or_none()
    if row is None:
        raise APIException(404, f"knowledge source {source_id} not found")
    return row


async def _topic_for_base(
    db: AsyncSession, knowledge_base_id: int, topic_id: int | None
) -> KnowledgeTopic | None:
    if topic_id is None:
        return None
    topic = await _get_topic(db, topic_id)
    if topic.knowledge_base_id != knowledge_base_id:
        raise APIException(400, "topic does not belong to this knowledge base")
    return topic


async def _source_with_topic(
    db: AsyncSession, source: KnowledgeSource
) -> dict[str, Any]:
    return await serialize_source_detail(db, source)


async def _base_detail(db: AsyncSession, row: KnowledgeBase) -> dict[str, Any]:
    topics = [serialize_topic(t) for t in await load_topics(db, row.id)]
    sources = await load_sources(db, row.id)
    clients = await list_assigned_clients(db, row.id)
    return serialize_base(
        row,
        topic_count=len(topics),
        source_count=len(sources),
        client_count=len(clients),
        topics=topics,
        assigned_clients=clients,
    )


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
    ids = [r.id for r in rows]
    topics = await topic_counts(db, ids)
    sources = await source_counts(db, ids)
    clients = await client_counts(db, ids)
    return {
        "list": [
            serialize_base(
                r,
                topic_count=topics.get(r.id, 0),
                source_count=sources.get(r.id, 0),
                client_count=clients.get(r.id, 0),
            )
            for r in rows
        ],
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
    return await _base_detail(db, row)


@router.get("/knowledge-base/{knowledge_base_id}", response_model=None)
async def get_knowledge_base(
    knowledge_base_id: int,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await _get_base(db, knowledge_base_id)
    return await _base_detail(db, row)


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
    return await _base_detail(db, row)


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


def _parse_optional_int(raw: str | int | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise APIException(400, "topicId must be an integer") from exc


async def _read_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_SOURCE_BYTES:
            raise APIException(400, f"file exceeds {MAX_SOURCE_BYTES} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/knowledge-base/{knowledge_base_id}/sources", response_model=None)
async def list_sources(
    knowledge_base_id: int,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _get_base(db, knowledge_base_id)
    rows = await load_sources(db, knowledge_base_id)
    items = [serialize_source(source, topic=topic) for source, topic in rows]
    return {"list": items, "total": len(items)}


@router.post("/knowledge-base/{knowledge_base_id}/sources", response_model=None)
async def create_source(
    knowledge_base_id: int,
    background: BackgroundTasks,
    name: str = Form(...),
    sourceType: str = Form(...),
    description: str | None = Form(None),
    enabled: str | bool | int | None = Form(True),
    topicId: str | None = Form(None),
    bodyText: str | None = Form(None),
    file: UploadFile | None = File(None),
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _get_base(db, knowledge_base_id)
    source_type = validate_source_type(sourceType)
    trimmed = (name or "").strip()
    if not trimmed:
        raise APIException(400, "name is required")
    topic = await _topic_for_base(db, knowledge_base_id, _parse_optional_int(topicId))
    filename = file.filename if file is not None else None
    content = b""
    if source_type == "text":
        if not (bodyText or "").strip():
            raise APIException(400, "bodyText is required for manual text sources")
        content = (bodyText or "").encode("utf-8")
        filename = display_filename(trimmed, ".txt")
    else:
        if file is None:
            raise APIException(400, "file is required for this source type")
        content = await _read_upload(file)
        if not content:
            raise APIException(400, "file is empty")
        filename = file.filename or display_filename(trimmed, extension_for(source_type, file.filename))
    if len(content) > MAX_SOURCE_BYTES:
        raise APIException(400, f"file exceeds {MAX_SOURCE_BYTES} bytes")
    ext = extension_for(source_type, filename)
    row = KnowledgeSource(
        knowledge_base_id=knowledge_base_id,
        name=trimmed,
        source_type=source_type,
        original_filename=filename,
        description=(description or "").strip() or None,
        enabled=_flag(enabled, 1),
        status="uploaded",
        processing_stage="uploaded",
        chunk_count=0,
        indexed_chunk_count=0,
        mime_type=mime_for(source_type, ext),
        file_size=len(content),
        topic_id=topic.id if topic is not None else None,
    )
    db.add(row)
    await db.flush()
    row.storage_path = write_source_bytes(knowledge_base_id, row.id, ext, content)
    await db.commit()
    await db.refresh(row)
    row = await maybe_process_source(db, row, background)
    return await _source_with_topic(db, row)


@router.get("/knowledge-source/{source_id}", response_model=None)
async def get_source(
    source_id: int,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await _get_source(db, source_id)
    return await _source_with_topic(db, row)


@router.put("/knowledge-source/{source_id}", response_model=None)
async def update_source(
    source_id: int,
    payload: KnowledgeSourceMetaIn,
    background: BackgroundTasks,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await _get_source(db, source_id)
    provided = payload.model_fields_set
    if "name" in provided:
        name = (payload.name or "").strip()
        if not name:
            raise APIException(400, "name cannot be blank")
        row.name = name
    if "description" in provided:
        row.description = (payload.description or "").strip() or None
    if "enabled" in provided:
        row.enabled = _flag(payload.enabled, row.enabled)
    if "topicId" in provided:
        topic = await _topic_for_base(db, row.knowledge_base_id, payload.topicId)
        row.topic_id = topic.id if topic is not None else None
    if "bodyText" in provided:
        if row.source_type not in TEXT_SOURCE_TYPES:
            raise APIException(400, "bodyText is only valid for text sources")
        text = payload.bodyText or ""
        if not text.strip():
            raise APIException(400, "bodyText cannot be blank")
        content = text.encode("utf-8")
        ext = extension_for(row.source_type, row.original_filename)
        row.storage_path = write_source_bytes(
            row.knowledge_base_id,
            row.id,
            ext,
            content,
            previous_relative=row.storage_path,
        )
        row.file_size = len(content)
        row.status = "uploaded"
        row.processing_stage = "uploaded"
        row.error_message = None
        row.original_filename = display_filename(row.name, ext)
        row.mime_type = mime_for(row.source_type, ext)
        row.indexed_at = None
        row.processed_at = None
    enabled_changed = "enabled" in provided
    await db.commit()
    await db.refresh(row)
    if "bodyText" in provided:
        row = await maybe_process_source(db, row, background)
    elif enabled_changed:
        await sync_source_enabled(row.id, bool(row.enabled))
    return await _source_with_topic(db, row)


@router.post("/knowledge-source/{source_id}/replace", response_model=None)
async def replace_source_file(
    source_id: int,
    background: BackgroundTasks,
    file: UploadFile | None = File(None),
    bodyText: str | None = Form(None),
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await _get_source(db, source_id)
    if row.source_type in TEXT_SOURCE_TYPES and bodyText is not None:
        if not bodyText.strip():
            raise APIException(400, "bodyText cannot be blank")
        content = bodyText.encode("utf-8")
        filename = display_filename(row.name, extension_for(row.source_type, row.original_filename))
    else:
        if file is None:
            raise APIException(400, "file is required to replace this source")
        content = await _read_upload(file)
        if not content:
            raise APIException(400, "file is empty")
        filename = file.filename or display_filename(
            row.name, extension_for(row.source_type, file.filename)
        )
    ext = extension_for(row.source_type, filename)
    row.storage_path = write_source_bytes(
        row.knowledge_base_id,
        row.id,
        ext,
        content,
        previous_relative=row.storage_path,
    )
    row.original_filename = filename
    row.mime_type = mime_for(row.source_type, ext)
    row.file_size = len(content)
    row.status = "uploaded"
    row.processing_stage = "uploaded"
    row.error_message = None
    row.indexed_at = None
    row.processed_at = None
    await db.commit()
    await db.refresh(row)
    row = await maybe_process_source(db, row, background)
    return await _source_with_topic(db, row)


@router.post("/knowledge-source/{source_id}/reprocess", response_model=None)
async def reprocess_source(
    source_id: int,
    background: BackgroundTasks,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await _get_source(db, source_id)
    row = await queue_source_processing(db, row, background, force=True)
    detail = await _source_with_topic(db, row)
    detail["reprocess"] = True
    detail["retrievalConfigured"] = retrieval_configured()
    if row.status == "failed" and not retrieval_configured():
        detail["message"] = row.error_message or "Knowledge service not configured"
    else:
        detail["message"] = None
    return detail


@router.delete("/knowledge-source/{source_id}", response_model=None)
async def delete_source(
    source_id: int,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await _get_source(db, source_id)
    relative = row.storage_path
    source_id_out = row.id
    await db.delete(row)
    await db.commit()
    delete_source_file(relative)
    await delete_indexed_source(source_id_out)
    return {"deleted": True, "id": source_id_out}


@router.get("/knowledge-source/{source_id}/file", response_model=None)
async def download_source_file(
    source_id: int,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_source(db, source_id)
    path = absolute_source_path(row.storage_path)
    if not path.is_file():
        raise APIException(404, "source file not found")
    filename = row.original_filename or display_filename(row.name, path.suffix)
    return FileResponse(
        path,
        media_type=row.mime_type or "application/octet-stream",
        filename=filename,
    )


@router.get("/knowledge-base/{knowledge_base_id}/test-search", response_model=None)
async def test_search_knowledge(
    knowledge_base_id: int,
    q: str | None = Query(None),
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _get_base(db, knowledge_base_id)
    if retrieval_configured() and (q or "").strip():
        searched = await semantic_search(
            db,
            knowledge_base_ids=[knowledge_base_id],
            query=q or "",
            limit=5,
        )
        return {
            "mode": "semantic",
            "retrievalConfigured": True,
            "query": searched["query"],
            "list": [
                {
                    "sourceId": row["sourceId"],
                    "source": row["sourceName"],
                    "sourceType": None,
                    "topic": row["topic"],
                    "topicKey": None,
                    "matchedText": row["text"],
                    "score": row["score"],
                    "pageNumber": row["pageNumber"],
                    "slideNumber": row["slideNumber"],
                    "revelTag": row["revelTag"],
                    "revelAutoTrigger": row["revelAutoTrigger"],
                }
                for row in searched["results"]
            ],
            "total": len(searched["results"]),
            "message": None if searched["results"] else "No indexed matches for this Knowledge Base.",
        }
    payload = await keyword_test_search(db, knowledge_base_id, q or "")
    payload["retrievalConfigured"] = retrieval_configured()
    return payload


@router.post("/knowledge/search", response_model=None)
async def search_knowledge(
    payload: KnowledgeSearchIn,
    _user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await semantic_search(
        db,
        knowledge_base_ids=payload.knowledgeBaseIds,
        query=payload.query,
        limit=payload.limit,
    )


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
