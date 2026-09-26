"""Ollama embeddings. Never pulls models."""
from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException

from .settings import settings

log = logging.getLogger("knowledge-service")

INSTALL_HINT = (
    "ollama pull nomic-embed-text"
    "  # ~274 MB, 768-d; already documented for the Jetson host in deploy/README.md"
)


class EmbedModelMissing(Exception):
    def __init__(self, model: str):
        self.model = model
        super().__init__(
            f"Embedding model {model!r} is not installed (~274 MB, 768-d). "
            f"On the Jetson host run: {INSTALL_HINT}. This service will not download it."
        )


def model_is_listed(names: list[str], target: str) -> bool:
    want = (target or "").strip()
    if not want:
        return False
    for raw in names:
        name = (raw or "").strip()
        if not name:
            continue
        if name == want or name.startswith(want + ":") or name.startswith(want + "-"):
            return True
    return False


async def listed_models(client: httpx.AsyncClient) -> list[str]:
    base = settings.ollama_url.rstrip("/")
    r = await client.get(f"{base}/api/tags")
    r.raise_for_status()
    return [m.get("name", "") for m in (r.json().get("models") or [])]


async def require_embed_model(client: httpx.AsyncClient) -> None:
    names = await listed_models(client)
    if not model_is_listed(names, settings.ollama_embed_model):
        raise EmbedModelMissing(settings.ollama_embed_model)


def _as_vectors(payload: dict) -> list[list[float]]:
    if isinstance(payload.get("embeddings"), list) and payload["embeddings"]:
        out = []
        for item in payload["embeddings"]:
            if not isinstance(item, list) or not item:
                raise HTTPException(status_code=502, detail="Ollama returned an empty embedding")
            out.append([float(x) for x in item])
        return out
    single = payload.get("embedding")
    if isinstance(single, list) and single:
        return [[float(x) for x in single]]
    raise HTTPException(status_code=502, detail="Ollama returned no embeddings")


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    timeout = httpx.Timeout(settings.ollama_timeout_s)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            await require_embed_model(client)
        except EmbedModelMissing as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Ollama unreachable at {settings.ollama_url}: {exc}",
            ) from exc

        vectors: list[list[float]] = []
        batch = max(1, int(settings.embed_batch_size))
        base = settings.ollama_url.rstrip("/")
        model = settings.ollama_embed_model
        for start in range(0, len(texts), batch):
            chunk = texts[start : start + batch]
            try:
                r = await client.post(
                    f"{base}/api/embed",
                    json={"model": model, "input": chunk, "keep_alive": "5m"},
                )
                if r.status_code == 404:
                    raise httpx.HTTPStatusError("embed endpoint missing", request=r.request, response=r)
                r.raise_for_status()
                got = _as_vectors(r.json())
                if len(got) != len(chunk):
                    raise HTTPException(status_code=502, detail="Ollama embed batch size mismatch")
                vectors.extend(got)
                continue
            except HTTPException:
                raise
            except Exception:
                log.info("Ollama /api/embed unavailable; falling back to /api/embeddings")
            for text in chunk:
                r = await client.post(
                    f"{base}/api/embeddings",
                    json={"model": model, "prompt": text, "keep_alive": "5m"},
                )
                try:
                    r.raise_for_status()
                except httpx.HTTPError as exc:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Ollama embeddings failed: {exc}",
                    ) from exc
                vectors.extend(_as_vectors(r.json()))
        for vec in vectors:
            if len(vec) != settings.vector_size:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"embedding dimension {len(vec)} does not match "
                        f"{settings.vector_size} (nomic-embed-text)"
                    ),
                )
        return vectors
