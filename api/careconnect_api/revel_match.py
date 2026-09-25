"""Deterministic Revel wake-prefix + phrase matching. No LLM. No network."""
from __future__ import annotations

import json
from typing import Any

# Punctuation allowed immediately after the bot name.
_NAME_FOLLOW = set(",.:;!-")
# Phrase matching: treat these as separators, keep apostrophes.
_PHRASE_STRIP = set(".,:;!?\"“”()[]{}")


def utterance_from_asr_text(text: str | None) -> str:
    """Plain transcript, or JSON ``{speaker, content}`` from ASR enhancement."""
    if not text:
        return ""
    raw = text.strip()
    if raw.startswith("{") and raw.endswith("}"):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return text
        if isinstance(data, dict) and isinstance(data.get("content"), str):
            return data["content"]
    return text


def strip_wake_prefix(utterance: str, bot_name: str | None) -> tuple[bool, str]:
    """Return ``(prefix_matched, remainder)``.

    Remainder keeps original casing and internal punctuation. The prefix must
    begin the utterance (after leading whitespace). A longer token that only
    starts with the name (``Bobby``) does not match.
    """
    if not utterance:
        return False, ""
    name = (bot_name or "").strip()
    if not name:
        return False, utterance
    raw = utterance.lstrip()
    nlen = len(name)
    if len(raw) < nlen:
        return False, utterance
    if raw[:nlen].casefold() != name.casefold():
        return False, utterance
    rest = raw[nlen:]
    if rest == "":
        return True, ""
    first = rest[0]
    if first.isspace() or first in _NAME_FOLLOW:
        i = 0
        while i < len(rest) and (rest[i].isspace() or rest[i] in _NAME_FOLLOW):
            i += 1
        return True, rest[i:]
    return False, utterance


def normalize_phrase(text: str | None) -> str:
    """Casefold, collapse whitespace, strip surrounding/separator punctuation."""
    if not text:
        return ""
    chars: list[str] = []
    for ch in text.casefold():
        if ch in {"'", "’"}:
            chars.append("'")
        elif ch.isalnum() or ch.isspace():
            chars.append(ch)
        elif ch in _PHRASE_STRIP:
            chars.append(" ")
        else:
            chars.append(" ")
    return " ".join("".join(chars).split())


def match_enabled_action(
    remainder: str,
    actions: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Exact normalized remainder vs enabled action phrases. First hit wins."""
    target = normalize_phrase(remainder)
    if not target:
        return None
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        if action.get("enabled") is False:
            continue
        phrases = action.get("phrases")
        if not isinstance(phrases, list):
            continue
        for phrase in phrases:
            if not isinstance(phrase, str):
                continue
            if normalize_phrase(phrase) == target:
                return action
    return None
