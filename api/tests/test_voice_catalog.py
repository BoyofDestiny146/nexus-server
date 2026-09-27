"""Voice catalog categories preserve existing ids."""
from __future__ import annotations

from pathlib import Path

from careconnect_api.voice_catalog import infer_category, normalize_voice_catalog


KEEPER_IDS = [
    "kokoro:af_heart",
    "edge:en-US-AvaNeural",
    "edge:en-US-JennyNeural",
    "edge:en-US-EmmaNeural",
    "piper:en_US-hfc_female-medium",
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
    assert "edge:en-US-GuyNeural" in src
    assert '"category": "male"' in src
    assert infer_category("edge:en-US-GuyNeural") == "male"
    assert infer_category("kokoro:af_heart") == "female"
