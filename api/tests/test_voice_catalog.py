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

CONFIRMED_MALE_IDS = [
    "edge:en-US-GuyNeural",
    "edge:en-US-AndrewNeural",
]

DISABLED_MALE_IDS = [
    "kokoro:am_adam",
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


def _catalog_line(src: str, voice_id: str) -> str:
    for line in src.splitlines():
        if f'"{voice_id}"' in line and '"engine"' in line:
            return line
    raise AssertionError(f"catalog line missing for {voice_id}")


def test_tts_script_preserves_keeper_ids_and_adds_male():
    src = (Path(__file__).resolve().parents[2] / "scripts" / "careconnect_tts_server.py").read_text()
    for voice_id in KEEPER_IDS:
        assert voice_id in src
    for voice_id in CONFIRMED_MALE_IDS + DISABLED_MALE_IDS:
        assert voice_id in src
    assert '"category": "male"' in src
    assert "_runtime_voices" in src
    assert infer_category("edge:en-US-GuyNeural") == "male"
    assert infer_category("kokoro:af_heart") == "female"
    assert '"enabled": True' in _catalog_line(src, "edge:en-US-GuyNeural")
    assert '"enabled": True' in _catalog_line(src, "edge:en-US-AndrewNeural")
    assert '"enabled": False' in _catalog_line(src, "kokoro:am_adam")


def test_orin_confirmed_males_are_selectable_and_adam_stays_disabled():
    voices = [
        {"id": "kokoro:af_heart", "engine": "kokoro", "enabled": True, "category": "female"},
        {"id": "kokoro:am_adam", "engine": "kokoro", "enabled": True, "category": "male"},
        {"id": "edge:en-US-GuyNeural", "engine": "edge", "enabled": True, "category": "male"},
        {"id": "edge:en-US-AndrewNeural", "engine": "edge", "enabled": True, "category": "male"},
        {"id": "edge:en-US-AvaNeural", "engine": "edge", "enabled": True, "category": "female"},
    ]
    gated = apply_runtime_voice_availability(
        voices,
        kokoro_voice_names={"af_heart", "am_adam"},
        edge_available=True,
    )
    by_id = {v["id"]: v for v in gated}
    assert list(by_id) == [v["id"] for v in voices]
    assert by_id["kokoro:af_heart"]["enabled"] is True
    assert by_id["edge:en-US-AvaNeural"]["enabled"] is True
    assert by_id["edge:en-US-GuyNeural"]["enabled"] is True
    assert by_id["edge:en-US-AndrewNeural"]["enabled"] is True
    assert by_id["kokoro:am_adam"]["enabled"] is False
    assert RUNTIME_GATED_VOICE_IDS == frozenset(DISABLED_MALE_IDS)


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
