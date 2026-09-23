"""voice_config.json round-trip including optional device volume."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_vc(tmp_path, monkeypatch):
    cfg = tmp_path / "voice_config.json"
    monkeypatch.setenv("CC_VOICE_CONFIG", str(cfg))
    import config.voice_config as vc

    return importlib.reload(vc), cfg


def test_volume_roundtrip(tmp_path, monkeypatch):
    vc, cfg = _load_vc(tmp_path, monkeypatch)
    vc.set_voice("AA:BB:CC:DD:EE:77", voice="kokoro:af_heart", speed="normal", volume=70)
    got = vc.get_voice("aa:bb:cc:dd:ee:77")
    assert got["voice"] == "kokoro:af_heart"
    assert got["speed"] == "normal"
    assert got["volume"] == 70
    assert vc.get_device_volume("aa:bb:cc:dd:ee:77") == 70
    data = json.loads(cfg.read_text())
    assert data["devices"]["aa:bb:cc:dd:ee:77"]["volume"] == 70


def test_unset_volume_keeps_mcp_fallback(tmp_path, monkeypatch):
    vc, _cfg = _load_vc(tmp_path, monkeypatch)
    vc.set_voice("aa:bb:cc:dd:ee:77", voice="kokoro:af_heart")
    got = vc.get_voice("aa:bb:cc:dd:ee:77")
    assert "volume" not in got or got.get("volume") is None
    assert vc.get_device_volume("aa:bb:cc:dd:ee:77") == 95


def test_volume_clamped(tmp_path, monkeypatch):
    vc, _cfg = _load_vc(tmp_path, monkeypatch)
    vc.set_voice("aa:bb:cc:dd:ee:77", volume=140)
    assert vc.get_device_volume("aa:bb:cc:dd:ee:77") == 100
    vc.set_voice("aa:bb:cc:dd:ee:77", volume=-3)
    assert vc.get_device_volume("aa:bb:cc:dd:ee:77") == 0
