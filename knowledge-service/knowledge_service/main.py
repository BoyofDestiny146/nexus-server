"""Internal retrieval HTTP API. Not exposed through Caddy."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import __version__
from .embed import embed_texts
from .extract import chunks_from_units, extract_file
from .qdrant import delete_by_source, ensure_collection, search_vectors, set_source_enabled, upsert_points
from .settings import settings

log = logging.getLogger("knowledge-service")

app = FastAPI(title="careconnect knowledge-service", version=__version__, docs_url=None, redoc_url=None)


class ExtractIn(BaseModel):
    storagePath: str
    sourceType: str


class IndexChunkIn(BaseModel):
    chunkId: int
    text: str
    knowledgeBaseId: int
    sourceId: int
    topicId: int | None = None
    sourceType: str
    filename: str = ""
    enabled: bool = True
    pageNumber: int | None = None
    slideNumber: int | None = None
    revelTag: str | None = None
    revelAutoTrigger: bool | None = None
    clientId: str | None = None


class ChunkIn(BaseModel):
    units: list[dict[str, Any]] = Field(default_factory=list)


class EmbedIn(BaseModel):
    texts: list[str] = Field(default_factory=list)


class IndexIn(BaseModel):
    chunks: list[IndexChunkIn] = Field(default_factory=list)


class UpsertIn(BaseModel):
    chunks: list[IndexChunkIn] = Field(default_factory=list)
    embeddings: list[list[float]] = Field(default_factory=list)


class SearchIn(BaseModel):
    query: str
    knowledgeBaseIds: list[int] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=50)
    enabledOnly: bool = True
    sourceIds: list[int] | None = None
    clientId: str | None = None


class SourceRef(BaseModel):
    sourceId: int


class SourceEnabledIn(BaseModel):
    sourceId: int
    enabled: bool


def chunk_payload(row: IndexChunkIn) -> dict[str, Any]:
    return {
        "knowledgeBaseId": row.knowledgeBaseId,
        "sourceId": row.sourceId,
        "topicId": row.topicId,
        "chunkId": row.chunkId,
        "sourceType": row.sourceType,
        "filename": row.filename,
        "enabled": bool(row.enabled),
        "pageNumber": row.pageNumber,
        "slideNumber": row.slideNumber,
        "revelTag": row.revelTag,
        "revelAutoTrigger": row.revelAutoTrigger,
        "clientId": row.clientId,
    }


@app.on_event("startup")
async def _startup() -> None:
    try:
        await ensure_collection()
    except Exception as exc:
        log.warning("Qdrant collection ensure failed at boot: %s", exc)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "careconnect-knowledge",
        "version": __version__,
        "collection": settings.qdrant_collection,
        "embedModel": settings.ollama_embed_model,
        "vectorSize": settings.vector_size,
    }


@app.post("/v1/extract")
def extract(payload: ExtractIn) -> dict[str, Any]:
    return extract_file(payload.sourceType, payload.storagePath)


@app.post("/v1/chunk")
def chunk(payload: ChunkIn) -> dict[str, Any]:
    return chunks_from_units(list(payload.units or []))


@app.post("/v1/embed")
async def embed(payload: EmbedIn) -> dict[str, Any]:
    vectors = await embed_texts(list(payload.texts or []))
    return {"embeddings": vectors, "count": len(vectors)}


@app.post("/v1/upsert")
async def upsert(payload: UpsertIn) -> dict[str, Any]:
    if not payload.chunks:
        return {"indexed": 0}
    if len(payload.embeddings) != len(payload.chunks):
        raise HTTPException(status_code=400, detail="embeddings length must match chunks")
    await ensure_collection()
    points = []
    for row, vector in zip(payload.chunks, payload.embeddings, strict=True):
        points.append({"id": int(row.chunkId), "vector": vector, "payload": chunk_payload(row)})
    count = await upsert_points(points)
    return {"indexed": count}


@app.post("/v1/index")
async def index_chunks(payload: IndexIn) -> dict[str, Any]:
    if not payload.chunks:
        return {"indexed": 0}
    await ensure_collection()
    source_ids = {row.sourceId for row in payload.chunks}
    for source_id in source_ids:
        await delete_by_source(source_id)
    vectors = await embed_texts([row.text for row in payload.chunks])
    points = []
    for row, vector in zip(payload.chunks, vectors, strict=True):
        points.append({"id": int(row.chunkId), "vector": vector, "payload": chunk_payload(row)})
    count = await upsert_points(points)
    return {"indexed": count}


@app.post("/v1/search")
async def search(payload: SearchIn) -> dict[str, Any]:
    ids = [int(x) for x in payload.knowledgeBaseIds if x is not None]
    if not ids:
        raise HTTPException(
            status_code=400,
            detail="knowledgeBaseIds is required; retrieval is never global",
        )
    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    vectors = await embed_texts([query])
    hits = await search_vectors(
        vectors[0],
        ids,
        limit=payload.limit,
        enabled_only=payload.enabledOnly,
        source_ids=payload.sourceIds,
        client_id=payload.clientId,
    )
    results = []
    for hit in hits:
        body = hit.get("payload") or {}
        results.append(
            {
                "chunkId": body.get("chunkId") if body.get("chunkId") is not None else hit.get("id"),
                "score": hit.get("score"),
                "payload": body,
            }
        )
    return {"query": query, "results": results}


@app.post("/v1/delete-source")
async def delete_source(payload: SourceRef) -> dict[str, Any]:
    await delete_by_source(payload.sourceId)
    return {"deleted": True, "sourceId": payload.sourceId}


@app.post("/v1/set-enabled")
async def set_enabled(payload: SourceEnabledIn) -> dict[str, Any]:
    await set_source_enabled(payload.sourceId, payload.enabled)
    return {"sourceId": payload.sourceId, "enabled": payload.enabled}
