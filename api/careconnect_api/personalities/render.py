"""Render personality templates without mutating stored library text."""
from __future__ import annotations

import re
from typing import Any, Mapping

from .safety import NEXUS_SAFETY_LAYER
from .seeds import ASSISTANT_NAME_TOKEN
from ..client_profile import _ENGLISH_ONLY_PIN

_TOKEN_RE = re.compile(r"\{\{\s*assistant_name\s*\}\}", re.I)

DEFAULT_ASSISTANT_NAME = "Nexus"
LEGACY_PERSONALITY_ID = "legacy_custom_persona"


def resolve_assistant_name(bot_name: str | None) -> str:
    name = (bot_name or "").strip()
    return name or DEFAULT_ASSISTANT_NAME


def apply_assistant_name(template: str, assistant_name: str) -> str:
    """Substitute ``{{assistant_name}}``. Does not write back to storage."""
    safe = (assistant_name or DEFAULT_ASSISTANT_NAME).strip() or DEFAULT_ASSISTANT_NAME
    return _TOKEN_RE.sub(safe, template or "")


def compose_personality_prompt(
    *,
    client_name: str,
    assistant_name: str,
    personality_name: str | None,
    template: str,
    profile: Mapping[str, Any],
) -> str:
    """Safety + English pin + rendered personality + client guardrails."""
    rendered = apply_assistant_name(template, assistant_name).strip()
    parts: list[str] = [
        _ENGLISH_ONLY_PIN,
        NEXUS_SAFETY_LAYER.strip(),
    ]
    heading = personality_name.strip() if personality_name else "Agent Personality"
    parts.append(f"# Agent Personality: {heading}\nYour spoken name is {assistant_name}.\n\n{rendered}")

    ctx_lines = [f"The person you are speaking with is named {client_name}."]
    condition = str(profile.get("condition") or "").strip()
    if condition:
        ctx_lines.append(f"Context note: {condition}")
    tags = profile.get("tags") or []
    if tags:
        ctx_lines.append("Tags: " + ", ".join(str(t).strip() for t in tags if str(t).strip()))
    parts.append("# Client context\n" + "\n".join(ctx_lines))

    phrases = profile.get("escalationPhrases") or []
    if phrases:
        listed = "\n".join(f"- {p.strip()}" for p in phrases if str(p).strip())
        parts.append(
            "# Escalation triggers\n"
            "If they mention any of the following, gently direct them to press the "
            "device button or get in-person help:\n"
            f"{listed}"
        )
    topics = profile.get("topicsToAvoid") or []
    if topics:
        listed = "\n".join(f"- {t.strip()}" for t in topics if str(t).strip())
        parts.append(
            "# Topics to avoid\n"
            "Steer away from these topics; do not volunteer them:\n"
            f"{listed}"
        )
    return "\n\n".join(parts) + "\n"
