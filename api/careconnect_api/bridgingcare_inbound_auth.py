"""Machine-to-machine Bridging Care inbound credentials (bcrypt hash file).

Dedicated inbound identity for the Bridging Care *server*. Not dashboard JWT,
not Watcher X-API-Key, not CareConnect ``cc_client_integration``, not the
outbound ingest ``X-Api-Key``. Missing, unknown, and wrong secrets all return
the same envelope so callers cannot tell whether the inbound id is configured.

Never logs the secret, the hash, or the outbound ingest key. Missing hash file
or empty client id fails closed (401). Does not create the hash file.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Header

from .auth import hash_password, verify_password
from .envelope import APIException
from .partner_auth import AUTH_FAIL_MSG
from .settings import settings


# One bcrypt round-trip on unknown ids so timing does not leak existence.
_DUMMY_HASH = hash_password("timing-dummy")


def _load_inbound_secret_hash() -> str | None:
    path = getattr(settings, "bridgingcare_inbound_secret_hash_file", None)
    if path is None:
        return None
    try:
        p = Path(path)
        if not p.is_file():
            return None
        value = p.read_text().strip()
    except (OSError, UnicodeError):
        return None
    return value or None


def require_bridgingcare_inbound(
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
    x_client_secret: str | None = Header(default=None, alias="X-Client-Secret"),
) -> str:
    """Verify the Bridging Care inbound pair against settings + hash file.

    Never queries ``cc_client_integration``. Never logs the secret.
    """
    public_id = (x_client_id or "").strip()
    secret = x_client_secret if x_client_secret is not None else ""

    configured_id = (getattr(settings, "bridgingcare_inbound_client_id", None) or "").strip()
    stored_hash = None
    if public_id and configured_id and public_id == configured_id:
        stored_hash = _load_inbound_secret_hash()

    compare_hash = stored_hash if stored_hash else _DUMMY_HASH
    ok = verify_password(secret, compare_hash)
    if (
        not public_id
        or not secret
        or not configured_id
        or public_id != configured_id
        or not stored_hash
        or not ok
    ):
        raise APIException(401, AUTH_FAIL_MSG)
    return public_id
