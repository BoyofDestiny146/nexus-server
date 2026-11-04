"""Authenticated Ollama catalog and a per-tag probe.

Discovery is the same ``GET /api/tags`` call the health hop uses. A successful
probe says the model can generate. It does not say BridgingCare is connected.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query

from ..agent_classification import tag_roles
from ..auth import CurrentUser, get_current_user
from ..envelope import APIException
from ..settings import settings
from .health import probe_selected_model, read_ollama_tags

router = APIRouter(prefix="/models", tags=["models"])


def _catalog(names: list[str]) -> dict[str, Any]:
    listed = []
    for raw in names:
        name = (raw or "").strip()
        if not name or any(item["name"] == name for item in listed):
            continue
        listed.append({"name": name, "roles": tag_roles(name)})
    return {
        "serverModel": (settings.llm_model or "").strip(),
        "triageModel": (settings.ollama_triage_model or "").strip(),
        "embeddingModel": (settings.ollama_embed_model or "").strip(),
        "models": listed,
    }


@router.get("/ollama", response_model=None)
async def list_ollama_models(
    _user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    timeout = settings.health_probe_timeout_s
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            reachable, names = await read_ollama_tags(client)
    except Exception as exc:
        raise APIException(503, "Ollama is unreachable") from exc
    if not reachable:
        raise APIException(503, "Ollama is unreachable")
    return _catalog(names)


@router.get("/ollama/verify", response_model=None)
async def verify_ollama_model(
    model: str = Query(min_length=1, max_length=128),
    _user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Probe the selected tag. Integration status is always reported separately."""
    timeout = settings.health_probe_timeout_s
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            result = await probe_selected_model(client, model)
    except Exception as exc:
        raise APIException(503, "Ollama is unreachable") from exc
    return {
        "model": model.strip(),
        "ollamaReachable": result["ollamaReachable"],
        "listed": result["listed"],
        "generationSucceeded": result["generationSucceeded"],
        "integrationConnected": False,
        "detail": result["detail"],
    }
