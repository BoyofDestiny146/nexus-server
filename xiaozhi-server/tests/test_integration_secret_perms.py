"""integration-secret-key must match existing API-readable secret ownership.

cc-secrets is mounted :ro, so gen-secrets / Orin must set uid/gid/mode on the
volume. alpine root:root 0600 is not readable after the API `gosu appuser`.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "deploy/scripts"
GEN = SCRIPTS / "gen-secrets.sh"
ALIGN = SCRIPTS / "align_secret_perms.sh"
COMPOSE = ROOT / "deploy/docker-compose.yml"
API_ENTRY = ROOT / "api/docker-entrypoint.sh"
API_DOCKERFILE = ROOT / "api/Dockerfile"


def _run_align(secrets_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(ALIGN), str(secrets_dir)],
        check=True,
        capture_output=True,
        text=True,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_gen_secrets_clones_perms_and_never_prints_or_overwrites_fernet():
    text = GEN.read_text()
    assert "align_secret_perms.sh" in text
    assert "integration-secret-key" in text
    assert "api-client-key" in text
    assert "api-internal-token" in text
    # Must not recreate the Orin failure mode as the only permission step.
    assert "chmod 600 /s/integration-secret-key" not in text
    assert "cat /s/integration-secret-key" not in text
    assert "cat \"$DEST\"" not in text
    # Documented read-back is api-client-key only, never the Fernet file.
    assert "cat /s/api-client-key" in text


def test_align_helper_is_busybox_safe_and_not_world_writable():
    text = ALIGN.read_text()
    assert "stat -c '%u:%g'" in text
    assert "stat -c '%a'" in text
    assert not any(ln.strip().startswith("chmod --reference") for ln in text.splitlines())
    assert "chmod o-w" in text
    assert "api-client-key" in text
    assert "api-internal-token" in text
    assert "api-jwt-secret" in text
    assert "[[ " not in text
    assert "cat /s/integration-secret-key" not in text
    assert "echo \"$(cat" not in text
    assert "head -c 32 /dev/urandom" in text


def test_compose_secrets_stay_read_only_and_api_still_drops_to_appuser():
    compose = COMPOSE.read_text()
    assert "cc-secrets:/run/secrets:ro" in compose
    assert 'CC_INTEGRATION_SECRET_KEY_FILE: "/run/secrets/integration-secret-key"' in compose
    entry = API_ENTRY.read_text()
    assert "gosu appuser" in entry
    df = API_DOCKERFILE.read_text()
    assert "\nUSER appuser" not in df
    # Entrypoint must not try to chmod the :ro secrets mount.
    assert "/run/secrets" not in entry


def test_align_creates_key_from_api_client_key_uid_gid_mode(tmp_path: Path):
    ref = tmp_path / "api-client-key"
    ref.write_text("client-key-placeholder")
    ref.chmod(0o640)
    result = _run_align(tmp_path)
    dest = tmp_path / "integration-secret-key"
    assert dest.is_file()
    assert dest.stat().st_size > 0
    body = dest.read_text()
    assert body.strip()
    assert "\n" not in body
    assert body not in result.stdout
    assert body not in result.stderr
    assert _mode(dest) == 0o640
    assert dest.stat().st_uid == ref.stat().st_uid
    assert dest.stat().st_gid == ref.stat().st_gid
    assert not (_mode(dest) & stat.S_IWOTH)
    assert "created" in result.stdout
    assert "uid=" in result.stdout
    assert "mode=640" in result.stdout


def test_align_keeps_nonempty_key_and_reapplies_perms(tmp_path: Path):
    ref = tmp_path / "api-client-key"
    ref.write_text("client-key-placeholder")
    ref.chmod(0o644)
    dest = tmp_path / "integration-secret-key"
    existing = "KEEP_EXISTING_FERNET_KEY_MATERIAL_XXXXXXX=="
    dest.write_text(existing)
    dest.chmod(0o600)
    result = _run_align(tmp_path)
    assert dest.read_text() == existing
    assert existing not in result.stdout
    assert existing not in result.stderr
    assert "keep" in result.stdout
    assert _mode(dest) == 0o644
    assert dest.stat().st_uid == ref.stat().st_uid
    assert dest.stat().st_gid == ref.stat().st_gid
    # Second run is still keep (preserve across "deploys").
    again = _run_align(tmp_path)
    assert dest.read_text() == existing
    assert "keep" in again.stdout
    assert existing not in again.stdout


def test_align_falls_back_to_api_internal_token(tmp_path: Path):
    ref = tmp_path / "api-internal-token"
    ref.write_text("internal-token-placeholder")
    ref.chmod(0o640)
    dest = tmp_path / "integration-secret-key"
    dest.write_text("already-present-key")
    dest.chmod(0o600)
    _run_align(tmp_path)
    assert dest.read_text() == "already-present-key"
    assert _mode(dest) == 0o640


def test_align_strips_world_write_from_cloned_mode(tmp_path: Path):
    ref = tmp_path / "api-client-key"
    ref.write_text("client-key-placeholder")
    ref.chmod(0o666)
    dest = tmp_path / "integration-secret-key"
    dest.write_text("already-present-key")
    dest.chmod(0o600)
    result = _run_align(tmp_path)
    mode = _mode(dest)
    assert not (mode & stat.S_IWOTH)
    assert dest.read_text() == "already-present-key"
    # 0666 cloned then o-w -> 0664 (world-read may remain; world-write must not).
    assert mode == 0o664
    assert "mode=664" in result.stdout


def test_align_does_not_overwrite_empty_named_other_secrets(tmp_path: Path):
    """Only integration-secret-key is created; sibling secrets are left alone."""
    ref = tmp_path / "api-jwt-secret"
    ref.write_text("jwt-placeholder")
    ref.chmod(0o600)
    mqtt = tmp_path / "mqtt-signature-key"
    mqtt.write_text("mqtt-keep")
    _run_align(tmp_path)
    assert mqtt.read_text() == "mqtt-keep"
    assert (tmp_path / "integration-secret-key").is_file()
