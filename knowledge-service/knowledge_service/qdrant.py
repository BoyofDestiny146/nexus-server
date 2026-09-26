"""Qdrant REST client. Collection nexus_knowledge. No gRPC."""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import HTTPException

from .settings import settings

log = logging.getLogger("knowledge-service")

PAYLOAD_INDEXES = (
    ("knowledgeBaseId", "integer"),
    ("sourceId", "integer"),
    ("topicId", "integer"),
    ("enabled", "bool"),
    ("clientId", "keyword"),
)


def _url(path: str) -> str:
    return f"{settings.qdrant_url.rstrip('/')}{path}"


async def _request(method: str, path: str, *, json: Any = None) -> dict[str, Any]:
    timeout = httpx.Timeout(settings.qdrant_timeout_s)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.request(method, _url(path), json=json)
        if r.status_code >= 400:
            detail = r.text[:400]
            raise HTTPException(
                status_code=502,
                detail=f"Qdrant {method} {path} HTTP {r.status_code}: {detail}",
            )
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return {}


async def ensure_collection() -> None:
    name = settings.qdrant_collection
    existing = None
    timeout = httpx.Timeout(settings.qdrant_timeout_s)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(_url(f"/collections/{name}"))
        if r.status_code == 200:
            existing = r.json()
        elif r.status_code not in {404, 400}:
            raise HTTPException(status_code=502, detail=f"Qdrant collection probe HTTP {r.status_code}")
    if existing is None:
        await _request(
            "PUT",
            f"/collections/{name}",
            json={
                "vectors": {"size": settings.vector_size, "distance": "Cosine"},
            },
        )
        log.info("created Qdrant collection %s size=%s", name, settings.vector_size)
    for field, schema in PAYLOAD_INDEXES:
        try:
            await _request(
                "PUT",
                f"/collections/{name}/index",
                json={"field_name": field, "field_schema": schema},
            )
        except HTTPException as exc:
            if "already exists" not in str(exc.detail).lower():
                log.warning("payload index %s: %s", field, exc.detail)


async def upsert_points(points: list[dict[str, Any]]) -> int:
    if not points:
        return 0
    await _request(
        "PUT",
        f"/collections/{settings.qdrant_collection}/points?wait=true",
        json={"points": points},
    )
    return len(points)


async def delete_by_source(source_id: int) -> None:
    await _request(
        "POST",
        f"/collections/{settings.qdrant_collection}/points/delete?wait=true",
        json={"filter": {"must": [{"key": "sourceId", "match": {"value": int(source_id)}}]}},
    )


async def set_source_enabled(source_id: int, enabled: bool) -> None:
    await _request(
        "POST",
        f"/collections/{settings.qdrant_collection}/points/payload?wait=true",
        json={
            "payload": {"enabled": bool(enabled)},
            "filter": {"must": [{"key": "sourceId", "match": {"value": int(source_id)}}]},
        },
    )


def scoped_filter(
    knowledge_base_ids: list[int],
    *,
    enabled_only: bool = True,
    source_ids: list[int] | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    ids = [int(x) for x in knowledge_base_ids]
    if not ids:
        raise HTTPException(
            status_code=400,
            detail="knowledgeBaseIds is required; retrieval is never global",
        )
    must: list[dict[str, Any]] = [
        {"key": "knowledgeBaseId", "match": {"any": ids}},
    ]
    if enabled_only:
        must.append({"key": "enabled", "match": {"value": True}})
    if source_ids:
        must.append({"key": "sourceId", "match": {"any": [int(x) for x in source_ids]}})
    if client_id:
        must.append({"key": "clientId", "match": {"value": client_id}})
    return {"must": must}


async def search_vectors(
    vector: list[float],
    knowledge_base_ids: list[int],
    *,
    limit: int = 5,
    enabled_only: bool = True,
    source_ids: list[int] | None = None,
    client_id: str | None = None,
) -> list[dict[str, Any]]:
    body = {
        "vector": vector,
        "limit": max(1, min(int(limit), 50)),
        "with_payload": True,
        "filter": scoped_filter(
            knowledge_base_ids,
            enabled_only=enabled_only,
            source_ids=source_ids,
            client_id=client_id,
        ),
    }
    data = await _request(
        "POST",
        f"/collections/{settings.qdrant_collection}/points/search",
        json=body,
    )
    return list(data.get("result") or [])
