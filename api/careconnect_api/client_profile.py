"""Structured Create/Edit client profile stored on ai_agent.profile_json.

Create Client currently collects DOB, age, condition, tags, escalation
phrases, topics to avoid, and persona override — but only agent_name and a
baked system_prompt were persisted. This module is the round-trip for those
wizard fields without a competing data model.

profile_json is not a credential. Unknown keys are preserved on merge so
Edit cannot erase fields it does not understand.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

PROFILE_KEYS = (
    "dob",
    "age",
    "condition",
    "tags",
    "escalationPhrases",
    "topicsToAvoid",
    "personaOverride",
)

_ENGLISH_ONLY_PIN = (
    "# ENGLISH-ONLY (ABSOLUTE RULE)\n"
    "ALWAYS reply in English. Never reply in Chinese, Japanese, Korean, or any "
    "other language or script, even if the user writes in another language. "
    "Do not use emoji or non-Latin characters. This rule overrides everything "
    "below."
)


def empty_profile() -> dict[str, Any]:
    return {
        "dob": None,
        "age": None,
        "condition": None,
        "tags": [],
        "escalationPhrases": [],
        "topicsToAvoid": [],
        "personaOverride": None,
    }


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def load_profile(raw: Any) -> dict[str, Any]:
    out = empty_profile()
    data: Any = raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return out
    if not isinstance(data, dict):
        return out
    if "dob" in data:
        dob = data.get("dob")
        out["dob"] = str(dob).strip()[:32] if dob else None
    if "age" in data:
        try:
            age = data.get("age")
            out["age"] = int(age) if age is not None and str(age).strip() != "" else None
        except (TypeError, ValueError):
            out["age"] = None
    if "condition" in data:
        cond = data.get("condition")
        out["condition"] = str(cond).strip() if cond else None
    out["tags"] = _as_str_list(data.get("tags"))
    out["escalationPhrases"] = _as_str_list(data.get("escalationPhrases"))
    out["topicsToAvoid"] = _as_str_list(data.get("topicsToAvoid"))
    if "personaOverride" in data:
        persona = data.get("personaOverride")
        out["personaOverride"] = str(persona).strip() if persona else None
    for key, value in data.items():
        if key not in PROFILE_KEYS:
            out[key] = value
    return out


def dump_profile(profile: Mapping[str, Any]) -> str:
    return json.dumps(load_profile(dict(profile)), ensure_ascii=False, separators=(",", ":"))


def profile_is_empty(profile: Mapping[str, Any]) -> bool:
    return not (
        profile.get("dob")
        or profile.get("age") is not None
        or profile.get("condition")
        or profile.get("tags")
        or profile.get("escalationPhrases")
        or profile.get("topicsToAvoid")
        or profile.get("personaOverride")
    )


def merge_profile(existing: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    out = load_profile(dict(existing))
    for key, value in patch.items():
        if key in PROFILE_KEYS:
            if key in ("tags", "escalationPhrases", "topicsToAvoid"):
                out[key] = _as_str_list(value)
            elif key == "age":
                try:
                    out[key] = int(value) if value is not None and str(value).strip() != "" else None
                except (TypeError, ValueError):
                    out[key] = None
            elif key == "dob":
                out[key] = str(value).strip()[:32] if value else None
            elif key in ("condition", "personaOverride"):
                text = str(value).strip() if value else None
                out[key] = text or None
        elif key not in PROFILE_KEYS:
            out[key] = value
    return out


def profile_from_onboard(req: Any) -> dict[str, Any]:
    return load_profile(
        {
            "dob": getattr(req, "dob", None),
            "age": getattr(req, "age", None),
            "condition": getattr(req, "condition", None),
            "tags": getattr(req, "tags", None) or [],
            "escalationPhrases": getattr(req, "escalationPhrases", None) or [],
            "topicsToAvoid": getattr(req, "topicsToAvoid", None) or [],
            "personaOverride": getattr(req, "personaOverride", None),
        }
    )


def _bullet_lines(block: str) -> list[str]:
    items: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return items


def parse_profile_from_prompt(prompt: str | None) -> dict[str, Any]:
    """Best-effort recovery of wizard fields from a stored system_prompt.

    Used only when profile_json is empty (clients created before structured
    storage). Hand-edited prompts that do not match the wizard format are
    preserved as personaOverride so Edit cannot destroy them.
    """
    out = empty_profile()
    text = (prompt or "").strip()
    if not text:
        return out

    if "# Operator override" in text:
        rest = text.split("# Operator override", 1)[1]
        lines = rest.splitlines()
        body: list[str] = []
        started = False
        for line in lines[1:]:
            if not started and (not line.strip() or line.startswith("# Client:")):
                continue
            started = True
            body.append(line)
        out["personaOverride"] = "\n".join(body).strip() or None
        return out

    generated = "# Client context" in text or "# Caregiver framing" in text
    if not generated:
        body = text
        pin = _ENGLISH_ONLY_PIN.strip()
        if body.startswith(pin):
            body = body[len(pin):].strip()
        out["personaOverride"] = body or None
        return out

    ctx = re.search(
        r"# Client context\n(.*?)(?=\n# |\Z)",
        text,
        re.S,
    )
    if ctx:
        block = ctx.group(1)
        clinical = re.search(r"Clinical context:\s*(.+)", block)
        if clinical:
            out["condition"] = clinical.group(1).strip() or None
        tags = re.search(r"Tags:\s*(.+)", block)
        if tags:
            out["tags"] = [t.strip() for t in tags.group(1).split(",") if t.strip()]

    esc = re.search(
        r"# Escalation triggers\n(.*?)(?=\n# |\Z)",
        text,
        re.S,
    )
    if esc:
        out["escalationPhrases"] = _bullet_lines(esc.group(1))

    avoid = re.search(
        r"# Topics to avoid\n(.*?)(?=\n# |\Z)",
        text,
        re.S,
    )
    if avoid:
        out["topicsToAvoid"] = _bullet_lines(avoid.group(1))
    return out


def effective_profile(profile_json: Any, system_prompt: str | None) -> dict[str, Any]:
    stored = load_profile(profile_json)
    parsed = parse_profile_from_prompt(system_prompt)
    if profile_is_empty(stored):
        return parsed
    # Live system_prompt may have been hand-edited (old Edit Client) after
    # structured JSON was stored. Keep that persona text instead of dropping it.
    if not stored.get("personaOverride") and parsed.get("personaOverride"):
        stored["personaOverride"] = parsed["personaOverride"]
    return stored


def public_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    loaded = load_profile(dict(profile))
    return {key: loaded.get(key) for key in PROFILE_KEYS}


def build_system_prompt(*, name: str, profile: Mapping[str, Any]) -> str:
    """Compose ai_agent.system_prompt from wizard fields (same as Create Client)."""
    loaded = load_profile(dict(profile))
    override = (loaded.get("personaOverride") or "").strip()
    if override:
        return (
            f"{_ENGLISH_ONLY_PIN}\n\n"
            "# Operator override (custom persona)\n"
            f"# Client: {name}\n\n"
            f"{override}\n"
        )

    parts: list[str] = [_ENGLISH_ONLY_PIN]
    ctx_lines = [f"Client name: {name}."]
    condition = (loaded.get("condition") or "").strip()
    if condition:
        ctx_lines.append(f"Clinical context: {condition}")
    tags = loaded.get("tags") or []
    if tags:
        ctx_lines.append("Tags: " + ", ".join(t.strip() for t in tags if t.strip()))
    parts.append("# Client context\n" + "\n".join(ctx_lines))

    phrases = loaded.get("escalationPhrases") or []
    if phrases:
        listed = "\n".join(f"- {p.strip()}" for p in phrases if p.strip())
        parts.append(
            "# Escalation triggers\n"
            "If the client mentions any of the following, raise the assessment "
            "risk_level and surface a clear concern in your reply:\n"
            f"{listed}"
        )

    topics = loaded.get("topicsToAvoid") or []
    if topics:
        listed = "\n".join(f"- {t.strip()}" for t in topics if t.strip())
        parts.append(
            "# Topics to avoid\n"
            "Steer the conversation away from these topics; do not volunteer them:\n"
            f"{listed}"
        )

    parts.append(
        "# Caregiver framing\n"
        "You are a soothing, plain-language companion for an elderly client. "
        "Keep replies short. Confirm what you heard before recommending action. "
        "If anything indicates a medical emergency, recommend calling for help."
    )
    return "\n\n".join(parts) + "\n"
