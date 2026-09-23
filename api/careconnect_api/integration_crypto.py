"""Fernet (AES-128-CBC + HMAC, i.e. authenticated encryption) for Revel keys.

CareConnect inbound secrets are hashed with bcrypt (see auth.hash_password)
and never stored here. This module is only for credentials we must retrieve
later (Revel API key).
"""
from __future__ import annotations

import logging
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .envelope import APIException
from .settings import settings

log = logging.getLogger("integration_crypto")

_fernet: Fernet | None = None


def _load_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    raw = ""
    tried: list[str] = []
    for path in (
        settings.integration_secret_key_file,
        Path("/run/secrets/integration-secret-key"),
    ):
        tried.append(str(path))
        try:
            if path.exists():
                raw = path.read_text().strip()
                if raw:
                    break
        except OSError:
            continue
    if not raw:
        log.warning("integration encryption key file missing")
        raise APIException(500, "integration encryption key is not configured")
    try:
        _fernet = Fernet(raw.encode("utf-8"))
    except Exception:
        log.warning("integration encryption key file is not a valid Fernet key")
        raise APIException(500, "integration encryption key is invalid")
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    token = _load_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Server-side retrieve only. Never used in GET/list handlers."""
    try:
        return _load_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise APIException(500, "stored credential cannot be decrypted") from exc


def secret_hint(plaintext: str, n: int = 4) -> str:
    text = (plaintext or "").strip()
    if not text:
        return ""
    return text[-n:]


def reset_fernet_cache() -> None:
    """Tests only."""
    global _fernet
    _fernet = None
