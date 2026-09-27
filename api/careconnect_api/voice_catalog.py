"""Normalize the TTS voice catalog for the dashboard.

Existing voice ids are preserved. Missing category metadata lands in
``other`` rather than being dropped. Category keys are stable so Child /
Regional / Specialty can be added later without a UI redesign.
"""
from __future__ import annotations

from typing import Any

VOICE_CATEGORIES = ("female", "male", "child", "regional", "specialty", "other")

# Known keepers + male additions. Ids must never change.
_KNOWN: dict[str, dict[str, str]] = {
    "kokoro:af_heart": {
        "category": "female",
        "language": "en",
        "locale": "en-US",
        "displayName": "Hazel — Heart",
    },
    "edge:en-US-AvaNeural": {
        "category": "female",
        "language": "en",
        "locale": "en-US",
        "displayName": "Ava",
    },
    "edge:en-US-JennyNeural": {
        "category": "female",
        "language": "en",
        "locale": "en-US",
        "displayName": "Jenny",
    },
    "edge:en-US-EmmaNeural": {
        "category": "female",
        "language": "en",
        "locale": "en-US",
        "displayName": "Emma",
    },
    "piper:en_US-hfc_female-medium": {
        "category": "female",
        "language": "en",
        "locale": "en-US",
        "displayName": "Clara",
    },
    "edge:en-US-GuyNeural": {
        "category": "male",
        "language": "en",
        "locale": "en-US",
        "displayName": "Guy",
    },
    "edge:en-US-AndrewNeural": {
        "category": "male",
        "language": "en",
        "locale": "en-US",
        "displayName": "Andrew",
    },
    "kokoro:am_adam": {
        "category": "male",
        "language": "en",
        "locale": "en-US",
        "displayName": "Adam",
    },
}


def infer_category(voice_id: str, label: str = "") -> str:
    known = _KNOWN.get(voice_id)
    if known:
        return known["category"]
    blob = f"{voice_id} {label}".lower()
    if any(tok in blob for tok in ("female", "woman", ":af_", "hfc_female")):
        return "female"
    if any(tok in blob for tok in ("male", "man", ":am_", "guy", "andrew", "ryan")):
        return "male"
    if "child" in blob or "kid" in blob:
        return "child"
    return "other"


def normalize_voice_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    voice_id = str(raw["id"])
    label = str(raw.get("label") or raw.get("displayName") or voice_id)
    known = _KNOWN.get(voice_id, {})
    engine = str(raw.get("engine") or (voice_id.split(":", 1)[0] if ":" in voice_id else "unknown"))
    category = str(raw.get("category") or known.get("category") or infer_category(voice_id, label)).lower()
    if category not in VOICE_CATEGORIES:
        category = "other"
    enabled = raw.get("enabled")
    if enabled is None:
        enabled = True
    return {
        "id": voice_id,
        "label": label,
        "displayName": str(raw.get("displayName") or known.get("displayName") or label),
        "provider": str(raw.get("provider") or engine),
        "category": category,
        "language": str(raw.get("language") or known.get("language") or "en"),
        "locale": str(raw.get("locale") or known.get("locale") or "en-US"),
        "engine": engine,
        "recommended": bool(raw.get("recommended")),
        "enabled": bool(enabled),
        "local": bool(raw.get("local", engine != "edge")),
    }


def normalize_voice_catalog(catalog: dict[str, Any] | None) -> dict[str, Any]:
    """Return catalog with normalized voice rows. Unknown extra keys pass through."""
    src = dict(catalog or {})
    voices = []
    seen: set[str] = set()
    for raw in src.get("voices") or []:
        row = normalize_voice_entry(raw)
        if row is None or row["id"] in seen:
            continue
        seen.add(row["id"])
        voices.append(row)
    src["voices"] = voices
    src["categories"] = list(VOICE_CATEGORIES)
    return src
