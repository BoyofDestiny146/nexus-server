"""Deterministic retrieval quality: content kind, rerank, dedupe, grounded context.

No extra ML model. Qdrant cosine remains the foundation. XiaoZhi is not called.
"""
from __future__ import annotations

import re
from typing import Any

CONTENT_KINDS = ("narrative", "specification", "telemetry", "table", "mixed")

# Live useful hits observed around 0.55–0.62 cosine. Floor stays below that.
DEFAULT_MIN_SCORE = 0.35
DEFAULT_CANDIDATE_LIMIT = 15
DEFAULT_RESULT_LIMIT = 5

_TOKEN = re.compile(r"[a-z0-9]+", re.I)
_KV = re.compile(r"\b[A-Za-z][A-Za-z0-9]{0,12}\s*=\s*[-+]?\d")
_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "for",
        "and",
        "or",
        "to",
        "in",
        "on",
        "is",
        "are",
        "was",
        "were",
        "be",
        "does",
        "do",
        "did",
        "how",
        "what",
        "why",
        "with",
        "from",
        "that",
        "this",
        "it",
        "its",
        "as",
        "by",
        "at",
        "into",
    }
)
_TELEMETRY_QUERY = re.compile(
    r"\b(reading|readings|telemetry|measurement|measurements|recorded|"
    r"test results?|sensor data|humidity values?|temperature values?|"
    r"battery values?)\b",
    re.I,
)
_EXPLANATORY_QUERY = re.compile(
    r"\b(how does|how do|how is|what is|what are|why|benefit|benefits|"
    r"overview|explain|purpose|work|works)\b",
    re.I,
)


def classify_content(text: str, *, hint: str | None = None) -> str:
    """Conservative numeric/key-value classifier. Never calls an LLM."""
    hinted = (hint or "").strip().lower()
    if hinted == "table":
        return "table"
    raw = text or ""
    tokens = re.findall(r"\S+", raw)
    n = max(len(tokens), 1)
    num_count = sum(1 for tok in tokens if re.search(r"\d", tok))
    num_ratio = num_count / n
    kv_count = len(_KV.findall(raw))
    if kv_count >= 3 and num_ratio >= 0.22:
        return "telemetry"
    if num_ratio >= 0.42 and num_count >= 6:
        return "telemetry"
    if raw.count("|") >= 3 and raw.count("\n") >= 1:
        return "table"
    if hinted in CONTENT_KINDS:
        return hinted
    spec = bool(
        re.search(
            r"\b(spec|specification|requirement|tolerance|voltage|ip[0-9]{2}|mhz|mm\b)\b",
            raw,
            re.I,
        )
    )
    if spec and num_ratio >= 0.12:
        return "specification"
    if num_ratio >= 0.20 and kv_count >= 1:
        return "mixed"
    if num_ratio < 0.18:
        return "narrative"
    return "mixed"


def query_terms(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN.finditer(text or "") if len(m.group(0)) >= 3 and m.group(0).lower() not in _STOP}


def query_intent(query: str) -> dict[str, bool]:
    q = query or ""
    telemetry = bool(_TELEMETRY_QUERY.search(q) or re.search(r"\b(rh|bat|temp)\s*=", q, re.I))
    explanatory = bool(_EXPLANATORY_QUERY.search(q))
    return {"telemetry": telemetry, "explanatory": explanatory}


def overlap(query: set[str], field: str | None) -> bool:
    if not query or not field:
        return False
    return bool(query & query_terms(field))


def fingerprint(text: str) -> str:
    return " ".join(_TOKEN.findall((text or "").lower()))


def too_similar(a: str, b: str) -> bool:
    fa, fb = fingerprint(a), fingerprint(b)
    if not fa or not fb:
        return False
    if fa == fb:
        return True
    prefix = 160
    if min(len(fa), len(fb)) > 80 and fa[:prefix] == fb[:prefix]:
        return True
    sa, sb = set(fa.split()), set(fb.split())
    union = len(sa | sb)
    if union == 0:
        return False
    return (len(sa & sb) / union) >= 0.85


def excerpt(text: str, query: str, width: int = 240) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    if len(body) <= width:
        return body
    terms = [t for t in query_terms(query) if t]
    low = body.lower()
    pos = -1
    for term in terms:
        found = low.find(term)
        if found >= 0:
            pos = found
            break
    if pos < 0:
        snippet = body[:width].rstrip()
        return snippet + ("…" if len(body) > width else "")
    start = max(0, pos - 60)
    snippet = body[start : start + width].strip()
    if start > 0:
        snippet = "…" + snippet
    if start + width < len(body):
        snippet = snippet.rstrip() + "…"
    return snippet


def citation_label(source_name: str | None, *, slide: int | None = None, page: int | None = None) -> str:
    name = (source_name or "Source").strip() or "Source"
    if slide is not None:
        return f"{name} — Slide {slide}"
    if page is not None:
        return f"{name} — Page {page}"
    return name


def rerank_hit(query: str, hit: dict[str, Any]) -> dict[str, Any]:
    intent = query_intent(query)
    qt = query_terms(query)
    vector = float(hit.get("vectorScore") if hit.get("vectorScore") is not None else hit.get("score") or 0)
    adj = 0.0
    reasons: list[str] = []
    kind = (hit.get("contentKind") or "mixed").lower()
    if overlap(qt, hit.get("topic")):
        adj += 0.08
        reasons.append("topic_overlap")
    if overlap(qt, hit.get("sectionTitle")):
        adj += 0.06
        reasons.append("title_overlap")
    if overlap(qt, hit.get("sourceName")):
        adj += 0.05
        reasons.append("source_overlap")
    if intent["telemetry"] and kind == "telemetry":
        adj += 0.07
        reasons.append("telemetry_query")
    if intent["explanatory"] and not intent["telemetry"] and kind in {"narrative", "specification"}:
        adj += 0.05
        reasons.append("explanatory_narrative")
    if intent["explanatory"] and not intent["telemetry"] and kind == "telemetry":
        adj -= 0.08
        reasons.append("telemetry_penalty")
    if intent["explanatory"] and not intent["telemetry"] and kind == "table":
        adj -= 0.03
        reasons.append("table_penalty")
    adj = max(-0.12, min(0.12, adj))
    out = dict(hit)
    out["vectorScore"] = round(vector, 4)
    out["rerankAdjustment"] = round(adj, 4)
    out["rerankReasons"] = reasons
    out["score"] = round(vector + adj, 4)
    return out


def dedupe_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for hit in hits:
        text = hit.get("text") or ""
        if any(too_similar(text, prev.get("text") or "") for prev in kept):
            continue
        kept.append(hit)
    return kept


def rank_results(
    query: str,
    hits: list[dict[str, Any]],
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> list[dict[str, Any]]:
    ranked = [rerank_hit(query, hit) for hit in hits]
    ranked.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
    filtered = [
        row
        for row in ranked
        if float(row.get("vectorScore") or 0) >= float(min_score)
    ]
    unique = dedupe_hits(filtered)
    out = unique[: max(1, int(limit))]
    for index, row in enumerate(out, start=1):
        row["rank"] = index
        row["excerpt"] = excerpt(row.get("text") or "", query)
    return out


def prepare_grounded_context(
    query: str,
    results: list[dict[str, Any]],
    *,
    knowledge_base_ids: list[int] | None = None,
    knowledge_bases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """LLM-ready grounded context. Does not call a model or execute Revel."""
    kb_list: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in knowledge_bases or []:
        try:
            kid = int(row.get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        if kid <= 0 or kid in seen:
            continue
        seen.add(kid)
        kb_list.append({"id": kid, "name": row.get("name")})
    for kid in knowledge_base_ids or []:
        try:
            value = int(kid)
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        kb_list.append({"id": value, "name": None})
    if not kb_list:
        for row in results:
            kid = row.get("knowledgeBaseId")
            if kid is None:
                continue
            value = int(kid)
            if value in seen:
                continue
            seen.add(value)
            kb_list.append({"id": value, "name": None})
    context: list[dict[str, Any]] = []
    for row in results:
        context.append(
            {
                "chunkId": row.get("chunkId"),
                "sourceId": row.get("sourceId"),
                "sourceName": row.get("sourceName"),
                "originalFilename": row.get("originalFilename"),
                "citation": citation_label(
                    row.get("sourceName"),
                    slide=row.get("slideNumber"),
                    page=row.get("pageNumber"),
                ),
                "topic": row.get("topic"),
                "text": row.get("text") or "",
                "score": row.get("score"),
                "revelTag": row.get("revelTag"),
                "revelAutoTrigger": bool(row.get("revelAutoTrigger")),
                "knowledgeBaseId": row.get("knowledgeBaseId"),
                "topicId": row.get("topicId"),
                "slideNumber": row.get("slideNumber"),
                "pageNumber": row.get("pageNumber"),
            }
        )
    return {"query": query, "knowledgeBases": kb_list, "context": context}
