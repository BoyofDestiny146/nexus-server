"""Lock the API image fix for the voice_config.json PermissionError."""
from pathlib import Path

API = Path(__file__).resolve().parents[1]


def test_dockerfile_chmods_shared_voice_volume():
    text = (API / "Dockerfile").read_text()
    assert "gosu" in text
    assert "docker-entrypoint.sh" in text
    assert "\nUSER appuser" not in text
    assert 'ENTRYPOINT ["/docker-entrypoint.sh"]' in text


def test_entrypoint_makes_data_writable():
    text = (API / "docker-entrypoint.sh").read_text()
    assert "chmod 0777 /data" in text
    assert "voice_config.json" in text
    assert "gosu appuser" in text
