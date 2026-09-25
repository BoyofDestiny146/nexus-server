"""Deterministic Revel command-prefix matching. Keep in sync with
``api/careconnect_api/revel_match.py``. XiaoZhi only strips the bot name;
phrase matching stays on CareConnect API.
"""
from __future__ import annotations

import json
from typing import Any

_NAME_FOLLOW = set(",.:;!-")


def utterance_from_asr_text(text: str | None) -> str:
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


def next_chat_text(
    *,
    prefix_matched: bool,
    remainder: str,
    original_text: str,
    command: dict[str, Any] | None,
) -> tuple[str, str]:
    """Return ``(mode, text)``.

    mode:
      * ``original`` — existing conversation with the original transcript
      * ``remainder`` — conversation with bot-name stripped
      * ``revel`` — allowlisted display command; do not send to Qwen
    """
    if not prefix_matched:
        return "original", original_text
    if not remainder:
        return "original", original_text
    if command and command.get("matched"):
        return "revel", remainder
    return "remainder", remainder
