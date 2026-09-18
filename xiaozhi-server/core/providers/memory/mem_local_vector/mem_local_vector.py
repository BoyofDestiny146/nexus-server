"""
Local-vector RAG memory provider for xiaozhi-server.

Backs each `role_id` (Watcher MAC / agent_id) with its own Chroma collection
and embeds dialogue snippets via the local Ollama `nomic-embed-text` model.

Designed for:
  - per-patient memory key (`role_id` = device_id)
  - 100% local (Ollama + ChromaDB persistent on disk)
  - graceful degrade: any failure -> empty context, never blocks the chat path
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..base import MemoryProviderBase, logger

TAG = __name__

DEFAULT_PERSIST_PATH = os.path.expanduser("~/.local/share/careconnect/chroma")
DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_TOP_K = 3
SNIPPET_MIN_CHARS = 40
SNIPPET_MAX_CHARS = 400
EMBED_TIMEOUT_SEC = 3.0


def _sanitize_role_id(role_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(role_id))[:60] or "default"


def _snippet_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _format_snippet(user_text: str, asst_text: str) -> str:
    user_text = (user_text or "").strip()
    asst_text = (asst_text or "").strip()
    snippet = f"USER: {user_text}\nCARE: {asst_text}"
    if len(snippet) > SNIPPET_MAX_CHARS:
        # keep both halves balanced
        half = (SNIPPET_MAX_CHARS - 16) // 2
        snippet = (
            f"USER: {user_text[:half].strip()}\nCARE: {asst_text[:half].strip()}"
        )
    return snippet


def _pair_dialogue(msgs) -> List[Tuple[int, str, str]]:
    """Walk a Dialogue list, yield (turn_idx, user_text, assistant_text) tuples
    by pairing each user with the next assistant. Skips system / tool msgs."""
    pairs: List[Tuple[int, str, str]] = []
    pending_user: Optional[str] = None
    turn_idx = 0
    for m in msgs:
        role = getattr(m, "role", None)
        content = getattr(m, "content", None) or ""
        if role == "user" and content.strip():
            pending_user = content.strip()
        elif role == "assistant" and content.strip() and pending_user is not None:
            pairs.append((turn_idx, pending_user, content.strip()))
            pending_user = None
            turn_idx += 1
    return pairs


class MemoryProvider(MemoryProviderBase):
    def __init__(self, config, summary_memory=None):
        super().__init__(config)
        cfg = config or {}
        self.persist_path = os.path.expanduser(
            cfg.get("path", DEFAULT_PERSIST_PATH)
        )
        self.embed_model = cfg.get("embedding_model", DEFAULT_EMBED_MODEL)
        self.ollama_base = cfg.get("ollama_base_url", DEFAULT_OLLAMA_URL).rstrip("/")
        try:
            self.top_k = int(cfg.get("top_k", DEFAULT_TOP_K))
        except (TypeError, ValueError):
            self.top_k = DEFAULT_TOP_K

        self._chroma = None
        self._collection = None
        try:
            os.makedirs(self.persist_path, exist_ok=True)
            import chromadb  # lazy import so xiaozhi-server still boots if not installed

            self._chroma = chromadb.PersistentClient(path=self.persist_path)
            logger.bind(tag=TAG).info(
                f"mem_local_vector ready: path={self.persist_path}, "
                f"model={self.embed_model}, top_k={self.top_k}"
            )
        except Exception as e:
            logger.bind(tag=TAG).error(
                f"mem_local_vector init failed (chroma): {e}. "
                f"Provider will no-op until restored."
            )

    # ------------------------------------------------------------------ init

    def init_memory(self, role_id, llm, **kwargs):
        super().init_memory(role_id, llm, **kwargs)
        if self._chroma is None:
            return
        try:
            safe = _sanitize_role_id(role_id)
            self._collection = self._chroma.get_or_create_collection(
                name=f"agent_{safe}",
                metadata={"hnsw:space": "cosine"},
            )
            logger.bind(tag=TAG).info(
                f"mem_local_vector collection: agent_{safe} "
                f"(count={self._collection.count()})"
            )
        except Exception as e:
            logger.bind(tag=TAG).error(
                f"mem_local_vector get_or_create_collection failed: {e}"
            )
            self._collection = None

    # -------------------------------------------------------------- embedding

    def _embed(self, text: str, timeout: float = EMBED_TIMEOUT_SEC) -> Optional[List[float]]:
        if not text or not text.strip():
            return None
        try:
            r = requests.post(
                f"{self.ollama_base}/api/embeddings",
                json={"model": self.embed_model, "prompt": text},
                timeout=timeout,
            )
            r.raise_for_status()
            data = r.json()
            emb = data.get("embedding")
            if not emb:
                logger.bind(tag=TAG).warning(
                    f"empty embedding from ollama for text len={len(text)}"
                )
                return None
            return emb
        except Exception as e:
            logger.bind(tag=TAG).warning(f"embed call failed: {e}")
            return None

    # --------------------------------------------------------------- save_*

    async def save_memory(self, msgs):
        if self._collection is None:
            logger.bind(tag=TAG).debug("mem_local_vector: no collection, skip save")
            return None
        if not msgs or len(msgs) < 2:
            return None

        pairs = _pair_dialogue(msgs)
        if not pairs:
            return None

        ts_now = int(time.time())
        ids: List[str] = []
        docs: List[str] = []
        embs: List[List[float]] = []
        metas: List[Dict[str, Any]] = []
        for turn_idx, u, a in pairs:
            snippet = _format_snippet(u, a)
            if len(snippet) < SNIPPET_MIN_CHARS:
                continue
            sid = _snippet_id(snippet)
            emb = self._embed(snippet)
            if emb is None:
                continue
            ids.append(sid)
            docs.append(snippet)
            embs.append(emb)
            metas.append(
                {
                    "turn_idx": int(turn_idx),
                    "ts": ts_now,
                    "role_user": (u or "")[:500],
                    "role_assistant": (a or "")[:500],
                }
            )

        if not ids:
            return None

        try:
            # upsert is idempotent on id collision
            self._collection.upsert(
                ids=ids, documents=docs, embeddings=embs, metadatas=metas
            )
            logger.bind(tag=TAG).info(
                f"mem_local_vector saved {len(ids)} snippets for role={self.role_id}"
            )
        except Exception as e:
            logger.bind(tag=TAG).error(f"chroma upsert failed: {e}")
            return None

        return len(ids)

    # --------------------------------------------------------------- query

    async def query_memory(self, query: str) -> str:
        if self._collection is None:
            return ""
        if not query or not query.strip():
            return ""
        try:
            count = self._collection.count()
        except Exception:
            count = 0
        if count == 0:
            return ""

        emb = self._embed(query)
        if emb is None:
            return ""

        try:
            res = self._collection.query(
                query_embeddings=[emb],
                n_results=min(self.top_k, count),
            )
        except Exception as e:
            logger.bind(tag=TAG).warning(f"chroma query failed: {e}")
            return ""

        metas_list = (res.get("metadatas") or [[]])[0] or []
        if not metas_list:
            return ""

        lines: List[str] = []
        for meta in metas_list:
            if not isinstance(meta, dict):
                continue
            ts = meta.get("ts")
            try:
                date_s = (
                    datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
                    if ts
                    else "earlier"
                )
            except Exception:
                date_s = "earlier"
            u = (meta.get("role_user") or "").strip().replace("\n", " ")
            a = (meta.get("role_assistant") or "").strip().replace("\n", " ")
            if not u and not a:
                continue
            lines.append(
                f"On {date_s}, the patient said: '{u}'. Care replied: '{a}'."
            )

        return "\n".join(lines)
