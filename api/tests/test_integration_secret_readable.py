"""The API runtime user must be able to open the Fernet key file.

Production path is /run/secrets/integration-secret-key (cc-secrets volume,
mounted :ro). This test opens a key the same way _load_fernet does, and when
the production path is present (API container) it opens that file too.
Never prints the key.
"""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet

from careconnect_api.integration_crypto import (
    _load_fernet,
    encrypt_secret,
    reset_fernet_cache,
)
from careconnect_api import settings as settings_mod


_PROD_PATH = Path("/run/secrets/integration-secret-key")


def _open_key_without_logging(path: Path) -> bytes:
    """Proof the current process can open the file. Do not return via logs."""
    assert os.access(path, os.R_OK), f"not readable by uid={os.getuid()} path={path}"
    fd = os.open(path, os.O_RDONLY)
    try:
        data = os.read(fd, 4096)
    finally:
        os.close(fd)
    assert data.strip(), f"empty integration key at {path}"
    # Validate it is a Fernet key without echoing it.
    Fernet(data.strip())
    return data.strip()


def test_current_user_can_open_and_load_integration_secret_key(
    tmp_path, monkeypatch
):
    key_path = tmp_path / "secrets" / "integration-secret-key"
    key_path.parent.mkdir()
    raw = Fernet.generate_key()
    key_path.write_bytes(raw)
    key_path.chmod(0o640)

    loaded = _open_key_without_logging(key_path)
    assert loaded == raw.strip()
    assert os.stat(key_path).st_uid == os.getuid()

    monkeypatch.setattr(settings_mod.settings, "integration_secret_key_file", key_path)
    reset_fernet_cache()
    try:
        token = encrypt_secret("probe-only")
        assert token
        # encrypt_secret goes through _load_fernet which Path.read_text()s the file.
        f = _load_fernet()
        assert f.decrypt(token.encode("ascii")).decode("utf-8") == "probe-only"
    finally:
        reset_fernet_cache()


def test_production_run_secrets_path_openable_when_present():
    """In the API container this is the real file appuser must read."""
    crypto = (
        Path(__file__).resolve().parents[1]
        / "careconnect_api"
        / "integration_crypto.py"
    ).read_text()
    assert 'Path("/run/secrets/integration-secret-key")' in crypto
    if not _PROD_PATH.exists():
        return
    # Running inside the API (or an Orin exec -u appuser). Must not be a
    # root-only smoke test: the point is the intended runtime user.
    _open_key_without_logging(_PROD_PATH)
