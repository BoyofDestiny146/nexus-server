"""Deterministic Guardrails escalation-phrase matching for CLIENT text only.

The Watcher LLM still sees phrases in the system prompt. This matcher is the
Nexus Assessment Engine hook: one Care & Wellness run per matching CLIENT
chat-history row, never caregiver/system/Revel text.
"""
from __future__ import annotations

from typing import Any, Iterable

from ..chat_events import GCAL_MARKER, REVEL_MARKER


def normalize_match_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\u00a0", " ")
    return " ".join(text.lower().split())


def configured_phrases(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item is None:
            continue
        phrase = str(item).strip()
        key = normalize_match_text(phrase)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(phrase)
    return out


def matching_escalation_phrases(content: Any, phrases: Iterable[str]) -> list[str]:
    hay = normalize_match_text(content)
    if not hay:
        return []
    hits: list[str] = []
    for phrase in phrases:
        needle = normalize_match_text(phrase)
        if needle and needle in hay:
            hits.append(phrase)
    return hits


def is_client_originated_text(chat_type: int | None, content: Any = None) -> bool:
    """True only for Watcher CLIENT turns (chat_type=1).

    Revel/calendar markers in the body are treated as system metadata even if
    a caller mis-tags the row — they must not fire escalation.
    """
    if chat_type != 1:
        return False
    text = "" if content is None else str(content)
    if REVEL_MARKER in text or GCAL_MARKER in text:
        return False
    return True
