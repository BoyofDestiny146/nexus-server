"""Strict Sales & Product Guide payload: validate, coerce, never invent medical fields."""
from __future__ import annotations

import json
from typing import Any

INTEREST_LEVELS = ("low", "medium", "high")
ARRAY_FIELDS = (
    "productsDiscussed",
    "customerNeeds",
    "questions",
    "objections",
    "recommendedNextTopics",
    "followUp",
)
MAX_ITEMS = 8
MAX_ITEM_CHARS = 160
MAX_SUMMARY_CHARS = 600

_MEDICAL_KEYS = frozenset(
    {
        "risk_level",
        "riskLevel",
        "confidence",
        "concerns",
        "recommendations",
        "concernsJson",
        "recommendationsJson",
    }
)

_INTEREST_ALIAS = {
    "low": "low",
    "medium": "medium",
    "med": "medium",
    "moderate": "medium",
    "high": "high",
}


def empty_sales_payload() -> dict[str, Any]:
    return {
        "interestLevel": "low",
        "productsDiscussed": [],
        "customerNeeds": [],
        "questions": [],
        "objections": [],
        "recommendedNextTopics": [],
        "followUp": [],
        "summary": "",
    }


def _clean_item(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    if not text:
        return None
    return text[:MAX_ITEM_CHARS]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        item = _clean_item(raw)
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= MAX_ITEMS:
            break
    return out


def coerce_interest_level(value: Any) -> str:
    if not isinstance(value, str):
        return "low"
    mapped = _INTEREST_ALIAS.get(value.strip().lower())
    return mapped if mapped in INTEREST_LEVELS else "low"


def sanitize_sales_payload(raw: Any) -> dict[str, Any]:
    """Turn arbitrary model JSON into the sales contract. Medical keys dropped."""
    out = empty_sales_payload()
    if not isinstance(raw, dict):
        return out
    out["interestLevel"] = coerce_interest_level(raw.get("interestLevel") or raw.get("interest_level"))
    for field in ARRAY_FIELDS:
        snake = "".join(["_" + c.lower() if c.isupper() else c for c in field]).lstrip("_")
        out[field] = _string_list(raw.get(field, raw.get(snake)))
    summary = raw.get("summary")
    if isinstance(summary, str):
        out["summary"] = " ".join(summary.split()).strip()[:MAX_SUMMARY_CHARS]
    return out


def parse_sales_llm_json(raw: str | None) -> dict[str, Any]:
    """Parse LLM text to a sales payload. Malformed input degrades to empty/low."""
    if not raw or not str(raw).strip():
        return empty_sales_payload()
    text = str(raw).strip()
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last <= first:
        return empty_sales_payload()
    try:
        parsed = json.loads(text[first : last + 1])
    except (ValueError, TypeError, json.JSONDecodeError):
        return empty_sales_payload()
    return sanitize_sales_payload(parsed)


def sales_payload_has_medical_fields(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in _MEDICAL_KEYS)
