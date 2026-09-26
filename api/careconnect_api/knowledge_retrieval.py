"""Knowledge retrieval orchestration.

CareConnect API owns MariaDB chunks, authorization, and scoped search.
knowledge-service owns extraction, embeddings, and Qdrant. XiaoZhi is not
wired. Revel tags are returned as metadata only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import async_session_factory
from .envelope import APIException
from .knowledge import resolve_device_knowledge_context, serialize_source
from .models import KnowledgeChunk, KnowledgeSource, KnowledgeTopic
from .settings import settings

log = logging.getLogger("knowledge")

STAGE_EXTRACTING = "extracting"
STAGE_CHUNKING = "chunking"
STAGE_EMBEDDINGS = "generating_embeddings"
STAGE_INDEXING = "indexing"
STAGE_READY = "ready"

_process_session_factory = None
_in_flight: set[int] = set()


def set_process_session_factory(factory) -> None:
    """Tests point background jobs at the in-memory SQLite engine."""
    global _process_session_factory
    _process_session_factory = factory


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _percent(indexed: int, total: int) -> int | None:
    if total <= 0:
        return None
    return int(round(100.0 * max(0, min(indexed, total)) / total))


def retrieval_configured() -> bool:
    return bool((settings.knowledge_service_url or "").strip())


def _ks_url(path: str) -> str:
    return f"{settings.knowledge_service_url.rstrip('/')}{path}"


def _detail(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return (resp.text or f"HTTP {resp.status_code}")[:512]
    detail = payload.get("detail") if isinstance(payload, dict) else payload
    if isinstance(detail, list):
        return str(detail)[:512]
    return str(detail or payload)[:512]


async def ks_request(method: str, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
    if not retrieval_configured():
        raise APIException(503, "Knowledge service not configured")
    timeout = httpx.Timeout(settings.knowledge_service_timeout_s)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, _ks_url(path), json=json)
    except httpx.HTTPError as exc:
        raise APIException(503, f"Knowledge service unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise APIException(resp.status_code if resp.status_code in {400, 404, 503} else 502, _detail(resp))
    if not resp.content:
        return {}
    try:
        data = resp.json()
    except ValueError as exc:
        raise APIException(502, "Knowledge service returned non-JSON") from exc
    return data if isinstance(data, dict) else {"data": data}


async def _topic_for(db: AsyncSession, topic_id: int | None) -> KnowledgeTopic | None:
    if not topic_id:
        return None
    return (
        await db.execute(select(KnowledgeTopic).where(KnowledgeTopic.id == topic_id))
    ).scalar_one_or_none()


async def replace_chunks(
    db: AsyncSession,
    source: KnowledgeSource,
    extracted: list[dict[str, Any]],
) -> list[KnowledgeChunk]:
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id))
    rows: list[KnowledgeChunk] = []
    for item in extracted:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        row = KnowledgeChunk(
            source_id=source.id,
            knowledge_base_id=source.knowledge_base_id,
            topic_id=source.topic_id,
            chunk_index=int(item.get("chunkIndex") or len(rows)),
            text=text,
            page_number=item.get("pageNumber"),
            slide_number=item.get("slideNumber"),
            section_title=(item.get("sectionTitle") or None),
            content_hash=item.get("contentHash") or "",
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows


def _index_payload(source: KnowledgeSource, row: KnowledgeChunk, topic: KnowledgeTopic | None) -> dict[str, Any]:
    return {
        "chunkId": row.id,
        "text": row.text,
        "knowledgeBaseId": source.knowledge_base_id,
        "sourceId": source.id,
        "topicId": source.topic_id,
        "sourceType": source.source_type,
        "filename": source.original_filename or source.name,
        "enabled": bool(source.enabled),
        "pageNumber": row.page_number,
        "slideNumber": row.slide_number,
        "revelTag": topic.revel_tag if topic is not None else None,
        "revelAutoTrigger": bool(topic.revel_auto_trigger) if topic is not None else None,
    }


async def _save_progress(db: AsyncSession, source: KnowledgeSource, **fields: Any) -> KnowledgeSource:
    for key, value in fields.items():
        setattr(source, key, value)
    await db.commit()
    await db.refresh(source)
    return source


def mark_processing(source: KnowledgeSource) -> None:
    """Reset progress fields and mark the row processing. Caller commits."""
    source.status = "processing"
    source.processing_stage = STAGE_EXTRACTING
    source.processing_progress = None
    source.error_message = None
    source.extracted_char_count = None
    source.chunk_count = 0
    source.indexed_chunk_count = 0
    source.processed_at = None
    source.indexed_at = None


async def process_source(db: AsyncSession, source: KnowledgeSource) -> KnowledgeSource:
    """uploaded → processing (extract/chunk/embed/index) → ready | failed.

    Progress fields are committed after each real stage so the Sources UI can
    poll. Ready is set only after Qdrant upsert finishes (or 0-chunk sources).
    """
    mark_processing(source)
    await db.commit()
    await db.refresh(source)

    if not retrieval_configured():
        return await _save_progress(
            db,
            source,
            status="failed",
            processing_stage=STAGE_EXTRACTING,
            error_message="Knowledge service not configured",
            processed_at=_now(),
        )

    try:
        await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id))
        await _save_progress(db, source, chunk_count=0, indexed_chunk_count=0)
        try:
            await ks_request("POST", "/v1/delete-source", {"sourceId": source.id})
        except APIException:
            log.warning("qdrant delete skipped at start source=%s", source.id)

        extracted = await ks_request(
            "POST",
            "/v1/extract",
            {"storagePath": source.storage_path, "sourceType": source.source_type},
        )
        units = list(extracted.get("units") or [])
        char_count = extracted.get("characterCount")
        if char_count is None:
            char_count = sum(len((unit.get("text") or "")) for unit in units)
        await _save_progress(
            db,
            source,
            processing_stage=STAGE_CHUNKING,
            extracted_char_count=int(char_count or 0),
        )

        chunked = await ks_request("POST", "/v1/chunk", {"units": units})
        chunks = await replace_chunks(db, source, list(chunked.get("chunks") or []))
        total = len(chunks)
        await _save_progress(
            db,
            source,
            chunk_count=total,
            indexed_chunk_count=0,
            processing_progress=None,
        )

        if total == 0:
            return await _save_progress(
                db,
                source,
                status="ready",
                processing_stage=STAGE_READY,
                processing_progress=100,
                error_message=None,
                indexed_at=_now(),
                processed_at=_now(),
            )

        topic = await _topic_for(db, source.topic_id)
        batch_size = max(1, int(settings.knowledge_index_batch_size or 8))
        indexed = 0
        for start in range(0, total, batch_size):
            batch = chunks[start : start + batch_size]
            await _save_progress(
                db,
                source,
                processing_stage=STAGE_EMBEDDINGS,
                processing_progress=_percent(indexed, total),
                indexed_chunk_count=indexed,
            )
            embedded = await ks_request(
                "POST",
                "/v1/embed",
                {"texts": [row.text for row in batch]},
            )
            vectors = list(embedded.get("embeddings") or [])
            if len(vectors) != len(batch):
                raise APIException(502, "embedding batch size mismatch")
            await _save_progress(
                db,
                source,
                processing_stage=STAGE_INDEXING,
                processing_progress=_percent(indexed, total),
                indexed_chunk_count=indexed,
            )
            await ks_request(
                "POST",
                "/v1/upsert",
                {
                    "chunks": [_index_payload(source, row, topic) for row in batch],
                    "embeddings": vectors,
                },
            )
            indexed += len(batch)
            await _save_progress(
                db,
                source,
                indexed_chunk_count=indexed,
                processing_progress=_percent(indexed, total),
            )

        if indexed != total:
            raise APIException(502, f"indexed {indexed} of {total} chunks")

        source = await _save_progress(
            db,
            source,
            status="ready",
            processing_stage=STAGE_READY,
            processing_progress=100,
            indexed_chunk_count=indexed,
            error_message=None,
            indexed_at=_now(),
            processed_at=_now(),
        )
        log.info(
            "knowledge processed source=%s chunks=%s status=ready revel_execute=false",
            source.id,
            total,
        )
        return source
    except APIException as exc:
        return await _save_progress(
            db,
            source,
            status="failed",
            error_message=(exc.msg or "processing failed")[:512],
            processed_at=_now(),
        )
    except Exception as exc:
        log.exception("knowledge processing failed source=%s", source.id)
        return await _save_progress(
            db,
            source,
            status="failed",
            error_message=str(exc)[:512],
            processed_at=_now(),
        )


async def process_source_job(source_id: int) -> None:
    factory = _process_session_factory or async_session_factory
    try:
        async with factory() as db:
            row = (
                await db.execute(select(KnowledgeSource).where(KnowledgeSource.id == source_id))
            ).scalar_one_or_none()
            if row is None:
                return
            await process_source(db, row)
    except Exception:
        log.exception("knowledge background job failed source=%s", source_id)
    finally:
        _in_flight.discard(source_id)


async def queue_source_processing(
    db: AsyncSession,
    source: KnowledgeSource,
    background: Any | None = None,
    *,
    force: bool = False,
) -> KnowledgeSource:
    """Commit processing state, then run in a background task when possible."""
    if source.id in _in_flight:
        return source
    if not retrieval_configured() and not force:
        return source
    mark_processing(source)
    await db.commit()
    await db.refresh(source)
    if background is not None and retrieval_configured():
        _in_flight.add(source.id)
        background.add_task(process_source_job, source.id)
        return source
    return await process_source(db, source)


async def maybe_process_source(
    db: AsyncSession,
    source: KnowledgeSource,
    background: Any | None = None,
) -> KnowledgeSource:
    return await queue_source_processing(db, source, background, force=False)


async def sync_source_enabled(source_id: int, enabled: bool) -> None:
    if not retrieval_configured():
        return
    try:
        await ks_request("POST", "/v1/set-enabled", {"sourceId": source_id, "enabled": enabled})
    except APIException as exc:
        log.warning("qdrant enabled sync failed source=%s: %s", source_id, exc.msg)


async def delete_indexed_source(source_id: int) -> None:
    if not retrieval_configured():
        return
    try:
        await ks_request("POST", "/v1/delete-source", {"sourceId": source_id})
    except APIException as exc:
        log.warning("qdrant delete failed source=%s: %s", source_id, exc.msg)


def _require_kb_ids(raw: list[int] | None) -> list[int]:
    ids = []
    seen: set[int] = set()
    for item in raw or []:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        ids.append(value)
    if not ids:
        raise APIException(400, "knowledgeBaseIds is required; retrieval is never global")
    return ids


async def hydrate_search_results(
    db: AsyncSession,
    *,
    query: str,
    hits: list[dict[str, Any]],
) -> dict[str, Any]:
    chunk_ids = []
    for hit in hits:
        cid = hit.get("chunkId")
        if cid is None and isinstance(hit.get("payload"), dict):
            cid = hit["payload"].get("chunkId")
        if cid is not None:
            chunk_ids.append(int(cid))
    chunks: dict[int, KnowledgeChunk] = {}
    if chunk_ids:
        rows = (
            await db.execute(select(KnowledgeChunk).where(KnowledgeChunk.id.in_(chunk_ids)))
        ).scalars().all()
        chunks = {row.id: row for row in rows}
    source_ids = {row.source_id for row in chunks.values()}
    sources: dict[int, KnowledgeSource] = {}
    if source_ids:
        rows = (
            await db.execute(select(KnowledgeSource).where(KnowledgeSource.id.in_(list(source_ids))))
        ).scalars().all()
        sources = {row.id: row for row in rows}
    topic_ids = {row.topic_id for row in chunks.values() if row.topic_id} | {
        src.topic_id for src in sources.values() if src.topic_id
    }
    topics: dict[int, KnowledgeTopic] = {}
    if topic_ids:
        rows = (
            await db.execute(select(KnowledgeTopic).where(KnowledgeTopic.id.in_(list(topic_ids))))
        ).scalars().all()
        topics = {row.id: row for row in rows}

    results: list[dict[str, Any]] = []
    for hit in hits:
        payload = hit.get("payload") if isinstance(hit.get("payload"), dict) else {}
        chunk_id = hit.get("chunkId") if hit.get("chunkId") is not None else payload.get("chunkId")
        chunk = chunks.get(int(chunk_id)) if chunk_id is not None else None
        if chunk is None:
            continue
        source = sources.get(chunk.source_id)
        topic_id = chunk.topic_id or (source.topic_id if source is not None else None)
        topic = topics.get(topic_id) if topic_id else None
        score = hit.get("score")
        try:
            score_f = round(float(score), 4) if score is not None else None
        except (TypeError, ValueError):
            score_f = None
        results.append(
            {
                "knowledgeBaseId": chunk.knowledge_base_id,
                "sourceId": chunk.source_id,
                "sourceName": source.name if source is not None else payload.get("filename"),
                "topicId": topic.id if topic is not None else topic_id,
                "topic": topic.title if topic is not None else None,
                "text": chunk.text,
                "score": score_f,
                "pageNumber": chunk.page_number,
                "slideNumber": chunk.slide_number,
                "revelTag": topic.revel_tag if topic is not None else None,
                "revelAutoTrigger": bool(topic.revel_auto_trigger) if topic is not None else False,
            }
        )
    return {"query": query, "results": results}


async def semantic_search(
    db: AsyncSession,
    *,
    knowledge_base_ids: list[int],
    query: str,
    limit: int = 5,
    enabled_only: bool = True,
) -> dict[str, Any]:
    ids = _require_kb_ids(knowledge_base_ids)
    q = (query or "").strip()
    if not q:
        raise APIException(400, "query is required")
    data = await ks_request(
        "POST",
        "/v1/search",
        {
            "query": q,
            "knowledgeBaseIds": ids,
            "limit": max(1, min(int(limit or 5), 50)),
            "enabledOnly": bool(enabled_only),
        },
    )
    return await hydrate_search_results(db, query=q, hits=list(data.get("results") or []))


async def device_knowledge_search(
    db: AsyncSession,
    mac: str,
    query: str,
    limit: int = 5,
) -> dict[str, Any]:
    """Device MAC → client Knowledge Bases → enabled sources only. Never global."""
    context = await resolve_device_knowledge_context(db, mac)
    kb_ids = [int(row["id"]) for row in context.get("knowledgeBases") or []]
    payload = {
        "query": (query or "").strip(),
        "deviceMac": context.get("deviceMac"),
        "clientId": context.get("clientId"),
        "knowledgeBaseIds": kb_ids,
        "results": [],
    }
    if not kb_ids:
        return payload
    searched = await semantic_search(
        db,
        knowledge_base_ids=kb_ids,
        query=query,
        limit=limit,
        enabled_only=True,
    )
    payload["results"] = searched["results"]
    return payload


async def serialize_source_detail(
    db: AsyncSession, source: KnowledgeSource, topic: KnowledgeTopic | None = None
) -> dict[str, Any]:
    if topic is None and source.topic_id:
        topic = await _topic_for(db, source.topic_id)
    return serialize_source(source, topic=topic)
