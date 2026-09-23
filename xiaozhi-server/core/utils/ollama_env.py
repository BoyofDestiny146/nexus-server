"""Ollama URL / model resolution from env with YAML fallback.

Used by the conversational LLM, embeddings, and local vision providers so a
Dockerized xiaozhi-server can reach host Ollama via OLLAMA_BASE_URL without
editing the xiaozhi-data YAML volume.
"""
from __future__ import annotations

import os

# Conversational model stays resident. Vision + embeddings use a short TTL so
# they can unload after idle camera / RAG calls (see deploy/jetson).
CHAT_KEEP_ALIVE = -1
ON_DEMAND_KEEP_ALIVE = "5m"


def resolve_ollama_model(config_model, environ=None):
    """Pick the conversational Ollama model: CC_LLM_MODEL > YAML model_name."""
    env = os.environ if environ is None else environ
    val = (env.get("CC_LLM_MODEL") or "").strip()
    if val:
        return val, "CC_LLM_MODEL"
    yaml_model = (config_model or "").strip() if isinstance(config_model, str) else None
    return yaml_model or None, "yaml"


def resolve_ollama_base_url(config_url, environ=None):
    """Pick the Ollama host: OLLAMA_BASE_URL > YAML base_url."""
    env = os.environ if environ is None else environ
    val = (env.get("OLLAMA_BASE_URL") or "").strip()
    if val:
        return val.rstrip("/"), "OLLAMA_BASE_URL"
    yaml_url = (config_url or "").strip() if isinstance(config_url, str) else ""
    return (yaml_url or "http://localhost:11434").rstrip("/"), "yaml"


def ollama_v1_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def looks_like_local_ollama(url: str | None) -> bool:
    u = (url or "").lower()
    return "11434" in u or "localhost" in u or "127.0.0.1" in u or "host-gateway" in u
