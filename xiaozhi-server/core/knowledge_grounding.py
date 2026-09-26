"""Watch-time Nexus Knowledge grounding for XiaoZhi.

Fail-open: any lookup error leaves the existing persona/system prompt alone.
Does not execute Revel. Does not scan assistant output.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable
from urllib.parse import quote

SKIP_PHRASES = frozenset(
    {
        "hello",
        "hi",
        "hey",
        "thanks",
        "thank you",
        "thankyou",
        "goodbye",
        "good bye",
        "bye",
        "yes",
        "no",
        "ok",
        "okay",
        "sure",
        "yep",
        "yup",
        "yeah",
        "nah",
        "please",
        "sorry",
    }
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WORD = re.compile(r"\S+")

GROUNDING_PREAMBLE = (
    "Use the following authorized Nexus Knowledge when relevant.\n"
    "Rules:\n"
    "- Treat the supplied Knowledge as authoritative for this product/client context.\n"
    "- Do not invent facts not present in it.\n"
    "- If the Knowledge does not answer the question, answer normally or say the "
    "available Knowledge does not contain that detail.\n"
    "- Do not mention internal retrieval mechanics, Qdrant, embeddings, chunks, "
    "or system architecture to the user.\n"
    "- Do not claim a source says something that is not in the provided excerpts.\n"
    "- Do not speak source, slide, or citation names unless the user asks.\n"
    "\nAUTHORIZED NEXUS KNOWLEDGE:"
)


def knowledge_enabled(environ: dict[str, str] | None = None) -> bool:
    import os

    env = environ if environ is not None else os.environ
    raw = (env.get("CC_XIAOZHI_KNOWLEDGE_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def env_float(name: str, default: float, *, environ: dict[str, str] | None = None) -> float:
    import os

    env = environ if environ is not None else os.environ
    try:
        return float((env.get(name) or str(default)).strip())
    except (TypeError, ValueError):
        return default


def env_int(name: str, default: int, *, environ: dict[str, str] | None = None) -> int:
    import os

    env = environ if environ is not None else os.environ
    try:
        return int(float((env.get(name) or str(default)).strip()))
    except (TypeError, ValueError):
        return default


_REFERENTIAL = re.compile(
    r"\b(it|that|this|those|them|they|he|she)\b|\bhow long\b|\bwhat about\b",
    re.IGNORECASE,
)


def should_retrieve(text: str, *, enabled: bool) -> bool:
    if not enabled:
        return False
    raw = (text or "").strip()
    if not raw:
        return False
    normalized = _NON_ALNUM.sub(" ", raw.lower()).strip()
    if normalized in SKIP_PHRASES:
        return False
    compact = normalized.replace(" ", "")
    if len(compact) < 6:
        return False
    return True


def expand_query(text: str, recent_user: str | None = None) -> str:
    """Current utterance first. Only prepend a prior turn when this one is referential."""
    query = (text or "").strip()
    prior = (recent_user or "").strip()
    if not query or not prior:
        return query
    compact = _NON_ALNUM.sub(" ", query.lower()).strip()
    if len(compact) > 80:
        return query
    if _REFERENTIAL.search(query):
        return f"{prior}\n{query}"[:500]
    return query


def clip_chars(text: str, limit: int) -> str:
    body = (text or "").strip()
    if limit <= 0 or len(body) <= limit:
        return body
    cut = body[:limit].rstrip()
    if limit < len(body) and body[limit : limit + 1].isalnum() and cut[-1:].isalnum():
        space = cut.rfind(" ")
        if space > limit // 2:
            cut = cut[:space].rstrip()
    return cut


def format_grounding_section(
    payload: dict[str, Any] | None,
    *,
    max_results: int = 3,
    max_chars: int = 6000,
) -> str:
    if not payload:
        return ""
    grounded = payload.get("grounded") if isinstance(payload.get("grounded"), dict) else None
    items = list((grounded or {}).get("context") or [])
    if not items:
        items = list(payload.get("results") or [])
    if not items:
        return ""
    take = max(1, int(max_results or 3))
    budget = max(200, int(max_chars or 6000))
    preamble = GROUNDING_PREAMBLE
    remaining = budget - len(preamble)
    blocks: list[str] = []
    for index, row in enumerate(items[:take], start=1):
        citation = (row.get("citation") or row.get("sourceName") or "Source").strip()
        text = (row.get("text") or "").strip()
        if not text:
            continue
        header = f"[{index}] {citation}"
        overhead = len(header) + 2
        if remaining - overhead < 40:
            break
        body = clip_chars(text, remaining - overhead)
        if not body:
            break
        block = f"{header}\n{body}"
        blocks.append(block)
        remaining -= len(block) + 2
    if not blocks:
        return ""
    return preamble + "\n" + "\n\n".join(blocks)


def apply_grounding(messages: list[dict[str, Any]], section: str) -> list[dict[str, Any]]:
    """Append grounding to a copy of the system message. Does not mutate persona storage."""
    if not section:
        return messages
    out = [dict(m) for m in messages]
    for row in out:
        if row.get("role") == "system":
            row["content"] = ((row.get("content") or "").rstrip() + "\n\n" + section).strip()
            return out
    out.insert(0, {"role": "system", "content": section})
    return out


def encode_mac_path(mac: str) -> str:
    return quote((mac or "").strip(), safe=":")


SearchFn = Callable[..., dict[str, Any] | None]


def build_knowledge_section(
    *,
    mac: str,
    query: str,
    search: SearchFn,
    enabled: bool = True,
    max_results: int = 3,
    max_chars: int = 6000,
    recent_user: str | None = None,
    logger=None,
) -> tuple[str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "mac": mac,
        "client": None,
        "bases": 0,
        "results": 0,
        "duration_ms": 0,
        "grounding_applied": False,
        "revel_execute": False,
        "skipped": None,
        "context": [],
        "query": (query or "").strip(),
    }
    if not should_retrieve(query, enabled=enabled):
        meta["skipped"] = "disabled" if not enabled else "trivial"
        if logger is not None:
            logger.debug(
                f"knowledge lookup skipped mac={mac} grounding_applied=false reason={meta['skipped']}"
            )
        return "", meta
    retrieval_query = expand_query(query, recent_user)
    meta["query"] = retrieval_query
    started = time.monotonic()
    payload = None
    try:
        payload = search(mac, retrieval_query, limit=max_results)
    except Exception as exc:
        meta["duration_ms"] = int((time.monotonic() - started) * 1000)
        meta["skipped"] = "search_error"
        if logger is not None:
            logger.warning(
                f"knowledge lookup mac={mac} duration_ms={meta['duration_ms']} "
                f"grounding_applied=false error={exc}"
            )
        return "", meta
    meta["duration_ms"] = int((time.monotonic() - started) * 1000)
    if not payload:
        meta["skipped"] = "empty_or_error"
        if logger is not None:
            logger.info(
                f"knowledge lookup mac={mac} client=None bases=0 results=0 "
                f"duration_ms={meta['duration_ms']} grounding_applied=false"
            )
        return "", meta
    meta["client"] = payload.get("clientId")
    bases = payload.get("knowledgeBases") or payload.get("knowledgeBaseIds") or []
    meta["bases"] = len(bases)
    grounded = payload.get("grounded") if isinstance(payload.get("grounded"), dict) else {}
    context = list(grounded.get("context") or [])
    meta["results"] = len(context)
    meta["context"] = [
        {
            "chunkId": row.get("chunkId"),
            "knowledgeBaseId": row.get("knowledgeBaseId"),
            "sourceName": row.get("sourceName"),
            "citation": row.get("citation"),
            "topic": row.get("topic"),
            "score": row.get("score"),
            "revelTag": row.get("revelTag"),
            "revelAutoTrigger": bool(row.get("revelAutoTrigger")),
        }
        for row in context[:max_results]
    ]
    section = format_grounding_section(payload, max_results=max_results, max_chars=max_chars)
    meta["grounding_applied"] = bool(section)
    if not section:
        meta["skipped"] = "no_results"
    if logger is not None:
        logger.info(
            f"knowledge lookup mac={mac} client={meta['client']} bases={meta['bases']} "
            f"results={meta['results']} duration_ms={meta['duration_ms']} "
            f"grounding_applied={meta['grounding_applied']} revel_execute=false"
        )
    return section, meta


def ground_turn_messages(
    *,
    mac: str,
    query: str,
    messages: list[dict[str, Any]],
    search: SearchFn,
    enabled: bool = True,
    max_results: int = 3,
    max_chars: int = 6000,
    recent_user: str | None = None,
    logger=None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fail-open grounding for one LLM call. Does not mutate stored persona."""
    try:
        section, meta = build_knowledge_section(
            mac=mac,
            query=query,
            search=search,
            enabled=enabled,
            max_results=max_results,
            max_chars=max_chars,
            recent_user=recent_user,
            logger=logger,
        )
        if not section:
            return messages, meta
        return apply_grounding(messages, section), meta
    except Exception as exc:
        if logger is not None:
            logger.warning(f"knowledge grounding failed (non-fatal): {exc}")
        return messages, {
            "grounding_applied": False,
            "skipped": "error",
            "revel_execute": False,
        }
