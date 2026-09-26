"""NEXUS Knowledge Fabric — catalog, client assignment, device inheritance.

Phase 1 pipeline (later stages are no-ops until override tables exist):

    (future) global / common knowledge
        ↓
    client Knowledge Base assignments   ← Phase 1
        ↓
    (future) agent narrowing / extra grants
        ↓
    bound Watcher inherits client assignments
        ↓
    (future) device narrowing / extra grants
        ↓
    (future) role / tool permissions
        ↓
    enabled Knowledge Topics + Revel metadata
        ↓
    structured Revel tool decision (Phase 5, CC_KNOWLEDGE_REVEL_ENABLED)
        — not LLM-output keyword scanning
        — not document-text command execution
        — default deny; flag off unless explicitly enabled
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .envelope import APIException
from .models import (
    AiAgent,
    ClientKnowledgeBase,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeSource,
    KnowledgeTopic,
)
from .watcher_device import get_watcher_device

log = logging.getLogger("knowledge")

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_TOPIC_KEY = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_MAC_CLEAN = re.compile(r"[^0-9a-fA-F]")


def slugify(name: str, *, max_len: int = 64) -> str:
    raw = (name or "").strip().lower()
    slug = _SLUG_STRIP.sub("-", raw).strip("-")
    return slug[:max_len]


def validate_topic_key(key: str) -> str:
    value = (key or "").strip().lower()
    if not _TOPIC_KEY.match(value):
        raise APIException(
            400,
            "topicKey must be lowercase letters, digits, and underscores",
        )
    return value


def validate_slug(slug: str | None, *, name: str) -> str:
    value = (slug or "").strip().lower() or slugify(name)
    if not value:
        raise APIException(400, "slug is required")
    if _SLUG_STRIP.sub("", value) != value.replace("-", ""):
        raise APIException(400, "slug must be lowercase letters, digits, and hyphens")
    if len(value) > 64:
        raise APIException(400, "slug is too long")
    return value


def _flag(value: Any, default: int = 1) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if int(value) else 0
    if isinstance(value, str):
        return 0 if value.strip().lower() in {"0", "false", "off", "no"} else 1
    return 1 if value else 0


def serialize_topic(row: KnowledgeTopic) -> dict[str, Any]:
    return {
        "id": row.id,
        "knowledgeBaseId": row.knowledge_base_id,
        "topicKey": row.topic_key,
        "title": row.title,
        "description": row.description,
        "enabled": bool(row.enabled),
        "revelTag": row.revel_tag,
        "revelAutoTrigger": bool(row.revel_auto_trigger),
        "sortOrder": row.sort_order,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def serialize_base(
    row: KnowledgeBase,
    *,
    topic_count: int | None = None,
    source_count: int | None = None,
    client_count: int | None = None,
    topics: list[dict[str, Any]] | None = None,
    assigned_clients: list[dict[str, Any]] | None = None,
    assigned: bool | None = None,
    assignment_enabled: bool | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.id,
        "name": row.name,
        "slug": row.slug,
        "description": row.description,
        "enabled": bool(row.enabled),
        "knowledgeType": row.knowledge_type,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }
    if topic_count is not None:
        out["topicCount"] = topic_count
    if source_count is not None:
        out["sourceCount"] = source_count
    if client_count is not None:
        out["clientCount"] = client_count
    if topics is not None:
        out["topics"] = topics
    if assigned_clients is not None:
        out["assignedClients"] = assigned_clients
    if assigned is not None:
        out["assigned"] = assigned
    if assignment_enabled is not None:
        out["assignmentEnabled"] = assignment_enabled
    return out


def serialize_source(
    row: KnowledgeSource,
    *,
    topic: KnowledgeTopic | None = None,
    chunk_count: int | None = None,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "knowledgeBaseId": row.knowledge_base_id,
        "name": row.name,
        "sourceType": row.source_type,
        "originalFilename": row.original_filename,
        "description": row.description,
        "enabled": bool(row.enabled),
        "status": row.status,
        "mimeType": row.mime_type,
        "fileSize": row.file_size,
        "topicId": row.topic_id,
        "topicTitle": topic.title if topic is not None else None,
        "topicKey": topic.topic_key if topic is not None else None,
        "revelTag": topic.revel_tag if topic is not None else None,
        "errorMessage": row.error_message,
        "hasFile": bool(row.storage_path),
        "storagePath": row.storage_path,
        "chunkCount": int(chunk_count if chunk_count is not None else (row.chunk_count or 0)),
        "indexedChunkCount": int(row.indexed_chunk_count or 0),
        "extractedCharCount": row.extracted_char_count,
        "processingStage": row.processing_stage,
        "processingProgress": row.processing_progress,
        "indexedAt": row.indexed_at,
        "processedAt": row.processed_at,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


async def topic_counts(
    db: AsyncSession, base_ids: list[int]
) -> dict[int, int]:
    if not base_ids:
        return {}
    rows = (
        await db.execute(
            select(KnowledgeTopic.knowledge_base_id, func.count(KnowledgeTopic.id))
            .where(KnowledgeTopic.knowledge_base_id.in_(base_ids))
            .group_by(KnowledgeTopic.knowledge_base_id)
        )
    ).all()
    return {int(base_id): int(count) for base_id, count in rows}


async def source_counts(
    db: AsyncSession, base_ids: list[int]
) -> dict[int, int]:
    if not base_ids:
        return {}
    rows = (
        await db.execute(
            select(KnowledgeSource.knowledge_base_id, func.count(KnowledgeSource.id))
            .where(KnowledgeSource.knowledge_base_id.in_(base_ids))
            .group_by(KnowledgeSource.knowledge_base_id)
        )
    ).all()
    return {int(base_id): int(count) for base_id, count in rows}


async def client_counts(
    db: AsyncSession, base_ids: list[int]
) -> dict[int, int]:
    if not base_ids:
        return {}
    rows = (
        await db.execute(
            select(
                ClientKnowledgeBase.knowledge_base_id,
                func.count(ClientKnowledgeBase.agent_id),
            )
            .where(ClientKnowledgeBase.knowledge_base_id.in_(base_ids))
            .group_by(ClientKnowledgeBase.knowledge_base_id)
        )
    ).all()
    return {int(base_id): int(count) for base_id, count in rows}


async def list_assigned_clients(
    db: AsyncSession, knowledge_base_id: int
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(AiAgent.id, AiAgent.agent_name, ClientKnowledgeBase.enabled)
            .join(ClientKnowledgeBase, ClientKnowledgeBase.agent_id == AiAgent.id)
            .where(ClientKnowledgeBase.knowledge_base_id == knowledge_base_id)
            .order_by(AiAgent.agent_name.asc(), AiAgent.id.asc())
        )
    ).all()
    return [
        {
            "id": agent_id,
            "agentName": agent_name,
            "assignmentEnabled": bool(enabled),
        }
        for agent_id, agent_name, enabled in rows
    ]


async def chunk_counts(
    db: AsyncSession, source_ids: list[int]
) -> dict[int, int]:
    if not source_ids:
        return {}
    rows = (
        await db.execute(
            select(KnowledgeChunk.source_id, func.count(KnowledgeChunk.id))
            .where(KnowledgeChunk.source_id.in_(source_ids))
            .group_by(KnowledgeChunk.source_id)
        )
    ).all()
    return {int(source_id): int(count) for source_id, count in rows}


async def load_sources(
    db: AsyncSession, knowledge_base_id: int
) -> list[tuple[KnowledgeSource, KnowledgeTopic | None]]:
    rows = (
        await db.execute(
            select(KnowledgeSource, KnowledgeTopic)
            .outerjoin(KnowledgeTopic, KnowledgeTopic.id == KnowledgeSource.topic_id)
            .where(KnowledgeSource.knowledge_base_id == knowledge_base_id)
            .order_by(KnowledgeSource.name.asc(), KnowledgeSource.id.asc())
        )
    ).all()
    return [(source, topic) for source, topic in rows]


async def load_topics(
    db: AsyncSession, knowledge_base_id: int, *, enabled_only: bool = False
) -> list[KnowledgeTopic]:
    stmt = select(KnowledgeTopic).where(
        KnowledgeTopic.knowledge_base_id == knowledge_base_id
    )
    if enabled_only:
        stmt = stmt.where(KnowledgeTopic.enabled == 1)
    stmt = stmt.order_by(KnowledgeTopic.sort_order.asc(), KnowledgeTopic.id.asc())
    return list((await db.execute(stmt)).scalars().all())


def _apply_global_scope(bases: list[KnowledgeBase]) -> list[KnowledgeBase]:
    """Phase 1: no global/common catalog besides explicit client assignments."""
    return bases


def _apply_agent_overrides(
    bases: list[KnowledgeBase], _agent_id: str
) -> list[KnowledgeBase]:
    """Phase 1: no agent narrowing or extra grants."""
    return bases


def _apply_device_overrides(
    bases: list[KnowledgeBase], _device_id: str
) -> list[KnowledgeBase]:
    """Phase 1: no device narrowing or extra grants."""
    return bases


def _apply_tool_permissions(
    topics: list[KnowledgeTopic],
) -> list[KnowledgeTopic]:
    """Phase 1: no tool-permission filter. Revel tags stay metadata."""
    return topics


def normalize_mac(raw: str) -> str:
    digits = _MAC_CLEAN.sub("", raw or "")
    if len(digits) != 12:
        raise APIException(400, f"invalid MAC address: {raw!r}")
    try:
        int(digits, 16)
    except ValueError:
        raise APIException(400, f"invalid MAC address: {raw!r}") from None
    return ":".join(digits[i : i + 2].lower() for i in range(0, 12, 2))


async def resolve_device_knowledge_context(
    db: AsyncSession, mac: str
) -> dict[str, Any]:
    """Read-only inheritance. Never calls Revel. Never scans LLM text."""
    mac_norm = normalize_mac(mac)
    device = await get_watcher_device(db, mac_norm)
    if device is None:
        return {
            "deviceMac": mac_norm,
            "clientId": None,
            "knowledgeBases": [],
        }

    agent_id = device.agent_id
    if not agent_id:
        return {
            "deviceMac": mac_norm,
            "clientId": None,
            "knowledgeBases": [],
        }

    assigned_rows = (
        await db.execute(
            select(ClientKnowledgeBase, KnowledgeBase)
            .join(KnowledgeBase, KnowledgeBase.id == ClientKnowledgeBase.knowledge_base_id)
            .where(
                ClientKnowledgeBase.agent_id == agent_id,
                ClientKnowledgeBase.enabled == 1,
                KnowledgeBase.enabled == 1,
            )
            .order_by(KnowledgeBase.name.asc(), KnowledgeBase.id.asc())
        )
    ).all()

    bases = _apply_global_scope([kb for _link, kb in assigned_rows])
    bases = _apply_agent_overrides(bases, agent_id)
    bases = _apply_device_overrides(bases, device.id)

    payload: list[dict[str, Any]] = []
    for kb in bases:
        topics = _apply_tool_permissions(
            await load_topics(db, kb.id, enabled_only=True)
        )
        payload.append(
            {
                "id": kb.id,
                "slug": kb.slug,
                "name": kb.name,
                "topics": [
                    {
                        "topicKey": t.topic_key,
                        "title": t.title,
                        "revelTag": t.revel_tag,
                        "revelAutoTrigger": bool(t.revel_auto_trigger),
                    }
                    for t in topics
                ],
            }
        )

    log.info(
        "knowledge context mac=%s client=%s bases=%d revel_execute=false",
        mac_norm,
        agent_id,
        len(payload),
    )
    return {
        "deviceMac": mac_norm,
        "clientId": agent_id,
        "knowledgeBases": payload,
    }


async def replace_client_assignments(
    db: AsyncSession,
    agent_id: str,
    assignments: list[dict[str, Any]],
) -> list[ClientKnowledgeBase]:
    """Replace-all: missing rows are deleted (unchecked in the Edit Client UI)."""
    wanted: dict[int, int] = {}
    for item in assignments:
        kb_id = int(item["knowledgeBaseId"])
        wanted[kb_id] = _flag(item.get("enabled"), 1)

    if wanted:
        existing_ids = set(
            (
                await db.execute(
                    select(KnowledgeBase.id).where(KnowledgeBase.id.in_(list(wanted)))
                )
            ).scalars().all()
        )
        missing = [kid for kid in wanted if kid not in existing_ids]
        if missing:
            raise APIException(404, f"knowledge base {missing[0]} not found")

    current = list(
        (
            await db.execute(
                select(ClientKnowledgeBase).where(ClientKnowledgeBase.agent_id == agent_id)
            )
        ).scalars().all()
    )
    current_ids = {row.knowledge_base_id for row in current}

    for row in current:
        if row.knowledge_base_id not in wanted:
            await db.delete(row)

    for kb_id, enabled in wanted.items():
        if kb_id in current_ids:
            for row in current:
                if row.knowledge_base_id == kb_id:
                    row.enabled = enabled
                    break
        else:
            db.add(
                ClientKnowledgeBase(
                    agent_id=agent_id,
                    knowledge_base_id=kb_id,
                    enabled=enabled,
                )
            )

    await db.commit()
    refreshed = list(
        (
            await db.execute(
                select(ClientKnowledgeBase).where(ClientKnowledgeBase.agent_id == agent_id)
            )
        ).scalars().all()
    )
    return refreshed


async def list_client_assignments(
    db: AsyncSession, agent_id: str
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(KnowledgeBase, ClientKnowledgeBase)
            .join(
                ClientKnowledgeBase,
                (ClientKnowledgeBase.knowledge_base_id == KnowledgeBase.id)
                & (ClientKnowledgeBase.agent_id == agent_id),
            )
            .order_by(KnowledgeBase.name.asc())
        )
    ).all()
    return [
        serialize_base(
            kb,
            assigned=True,
            assignment_enabled=bool(link.enabled),
        )
        for kb, link in rows
    ]


def _preview_around(text: str, needle: str, *, width: int = 140) -> str:
    lower = text.lower()
    idx = lower.find(needle.lower())
    if idx < 0:
        snippet = text.strip().replace("\n", " ")
        return snippet[:width] + ("…" if len(snippet) > width else "")
    start = max(0, idx - width // 3)
    end = min(len(text), idx + len(needle) + width // 2)
    snippet = text[start:end].replace("\n", " ").strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


async def keyword_test_search(
    db: AsyncSession, knowledge_base_id: int, query: str
) -> dict[str, Any]:
    """Phase 2 operator test search — keyword over names and text sources.

    Not production RAG. Does not embed, retrieve semantically, or call XiaoZhi.
    """
    from .knowledge_storage import TEXT_SOURCE_TYPES, read_text_preview

    q = (query or "").strip()
    terms = [re.sub(r"[^a-z0-9-]+", "", t) for t in re.split(r"\s+", q.lower())]
    terms = [t for t in terms if len(t) > 1]
    payload = {
        "mode": "keyword",
        "retrievalConfigured": False,
        "query": q,
        "list": [],
        "total": 0,
        "message": None,
    }
    if not terms:
        payload["message"] = "Enter a question to run Phase 2 test search."
        return payload

    rows = await load_sources(db, knowledge_base_id)
    hits: list[dict[str, Any]] = []
    for source, topic in rows:
        haystacks: list[tuple[str, float]] = [
            (source.name or "", 3.0),
            (source.original_filename or "", 2.0),
            (source.description or "", 1.5),
        ]
        body = ""
        if source.source_type in TEXT_SOURCE_TYPES:
            body = read_text_preview(source.storage_path)
            if body:
                haystacks.append((body, 1.0))
        previews: list[tuple[float, str]] = []
        score = 0.0
        for text, weight in haystacks:
            blob = text.lower()
            hits_here = sum(1 for term in terms if term in blob)
            if hits_here == 0:
                continue
            score += weight * hits_here
            previews.append((weight, _preview_around(text, next(t for t in terms if t in blob))))
        if score <= 0:
            continue
        matched_text = min(previews, key=lambda item: item[0])[1]
        hits.append(
            {
                "sourceId": source.id,
                "source": source.name,
                "sourceType": source.source_type,
                "topic": topic.title if topic is not None else None,
                "topicKey": topic.topic_key if topic is not None else None,
                "matchedText": matched_text,
                "score": round(score, 2),
                "revelTag": topic.revel_tag if topic is not None else None,
            }
        )
    hits.sort(key=lambda row: (-row["score"], row["source"].lower()))
    payload["list"] = hits
    payload["total"] = len(hits)
    if not hits:
        payload["message"] = (
            "No keyword matches. Retrieval service not configured — "
            "this is Phase 2 test search, not production RAG."
        )
    return payload
