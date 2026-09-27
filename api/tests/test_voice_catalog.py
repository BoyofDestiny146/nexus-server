"""Voice catalog categories preserve existing ids."""
from __future__ import annotations

from pathlib import Path

from careconnect_api.voice_catalog import (
    RUNTIME_GATED_VOICE_IDS,
    apply_runtime_voice_availability,
    infer_category,
    normalize_voice_catalog,
)


KEEPER_IDS = [
    "kokoro:af_heart",
    "edge:en-US-AvaNeural",
    "edge:en-US-JennyNeural",
    "edge:en-US-EmmaNeural",
    "piper:en_US-hfc_female-medium",
]

GATED_MALE_IDS = [
    "kokoro:am_adam",
    "edge:en-US-GuyNeural",
    "edge:en-US-AndrewNeural",
]


def test_keepers_stay_female_and_ids_unchanged():
    catalog = normalize_voice_catalog(
        {
            "default": "kokoro:af_heart",
            "voices": [
                {"id": "kokoro:af_heart", "label": "Hazel", "engine": "kokoro", "local": True, "recommended": True},
                {"id": "edge:en-US-AvaNeural", "label": "Ava", "engine": "edge", "local": False, "recommended": True},
                {"id": "mystery:unknown", "label": "Mystery", "engine": "mystery"},
            ],
        }
    )
    ids = [v["id"] for v in catalog["voices"]]
    assert ids[0] == "kokoro:af_heart"
    assert ids[1] == "edge:en-US-AvaNeural"
    by_id = {v["id"]: v for v in catalog["voices"]}
    assert by_id["kokoro:af_heart"]["category"] == "female"
    assert by_id["edge:en-US-AvaNeural"]["category"] == "female"
    assert by_id["mystery:unknown"]["category"] == "other"
    assert by_id["kokoro:af_heart"]["displayName"]
    assert catalog["categories"] == ["female", "male", "child", "regional", "specialty", "other"]


def test_tts_script_preserves_keeper_ids_and_adds_male():
    src = (Path(__file__).resolve().parents[2] / "scripts" / "careconnect_tts_server.py").read_text()
    for voice_id in KEEPER_IDS:
        assert voice_id in src
    for voice_id in GATED_MALE_IDS:
        assert voice_id in src
    assert '"category": "male"' in src
    assert "_runtime_voices" in src
    assert infer_category("edge:en-US-GuyNeural") == "male"
    assert infer_category("kokoro:af_heart") == "female"


def test_gated_males_stay_in_catalog_but_are_disabled_until_runtime_confirms():
    voices = [
        {"id": "kokoro:af_heart", "engine": "kokoro", "enabled": True, "category": "female"},
        {"id": "kokoro:am_adam", "engine": "kokoro", "enabled": True, "category": "male"},
        {"id": "edge:en-US-GuyNeural", "engine": "edge", "enabled": True, "category": "male"},
        {"id": "edge:en-US-AndrewNeural", "engine": "edge", "enabled": True, "category": "male"},
        {"id": "edge:en-US-AvaNeural", "engine": "edge", "enabled": True, "category": "female"},
    ]
    unconfirmed = apply_runtime_voice_availability(voices)
    by_id = {v["id"]: v for v in unconfirmed}
    assert list(by_id) == [v["id"] for v in voices]
    assert by_id["kokoro:af_heart"]["enabled"] is True
    assert by_id["edge:en-US-AvaNeural"]["enabled"] is True
    for vid in GATED_MALE_IDS:
        assert by_id[vid]["enabled"] is False

    confirmed = apply_runtime_voice_availability(
        voices,
        kokoro_voice_names={"af_heart", "am_adam"},
        edge_available=True,
    )
    confirmed_by_id = {v["id"]: v for v in confirmed}
    assert confirmed_by_id["kokoro:am_adam"]["enabled"] is True
    assert confirmed_by_id["edge:en-US-GuyNeural"]["enabled"] is True
    assert confirmed_by_id["edge:en-US-AndrewNeural"]["enabled"] is True

    kokoro_missing_adam = apply_runtime_voice_availability(
        voices,
        kokoro_voice_names={"af_heart"},
        edge_available=True,
    )
    assert {v["id"]: v["enabled"] for v in kokoro_missing_adam}["kokoro:am_adam"] is False
    assert RUNTIME_GATED_VOICE_IDS == frozenset(GATED_MALE_IDS)


def test_normalize_preserves_disabled_flag_and_does_not_drop_ids():
    catalog = normalize_voice_catalog(
        {
            "default": "kokoro:af_heart",
            "voices": [
                {"id": "kokoro:af_heart", "label": "Hazel", "engine": "kokoro", "enabled": True},
                {"id": "kokoro:am_adam", "label": "Adam", "engine": "kokoro", "category": "male", "enabled": False},
            ],
        }
    )
    by_id = {v["id"]: v for v in catalog["voices"]}
    assert by_id["kokoro:am_adam"]["enabled"] is False
    assert by_id["kokoro:am_adam"]["category"] == "male"
    assert by_id["kokoro:af_heart"]["enabled"] is True
