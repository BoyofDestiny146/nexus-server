"""Machine-to-machine CareConnect client credentials (Nx- id + bcrypt secret).

Separate from dashboard JWT login and from the Watcher X-API-Key. Missing,
unknown, and wrong secrets all return the same envelope so callers cannot
tell whether an Nx- id exists.
"""
from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import hash_password, verify_password
from .db import get_db
from .envelope import APIException
from .models import ClientIntegration


PROVIDER_CARECONNECT = "careconnect"
AUTH_FAIL_MSG = "invalid or missing client credentials"

# One bcrypt round-trip on unknown ids so timing does not leak existence.
_DUMMY_HASH = hash_password("timing-dummy")


async def require_careconnect_client(
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
    x_client_secret: str | None = Header(default=None, alias="X-Client-Secret"),
    db: AsyncSession = Depends(get_db),
) -> ClientIntegration:
    """Lookup ``cc_client_integration`` by public Nx- id and verify the secret.

    Never logs the secret. Never returns whether the id existed.
    """
    public_id = (x_client_id or "").strip()
    secret = x_client_secret if x_client_secret is not None else ""

    row: ClientIntegration | None = None
    if public_id:
        row = (
            await db.execute(
                select(ClientIntegration).where(
                    ClientIntegration.provider == PROVIDER_CARECONNECT,
                    ClientIntegration.public_id == public_id,
                )
            )
        ).scalar_one_or_none()

    stored_hash = (
        row.secret_hash
        if row is not None
        and (row.status or "connected") == "connected"
        and row.secret_hash
        else _DUMMY_HASH
    )
    ok = verify_password(secret, stored_hash)
    if not public_id or not secret or row is None or not ok:
        raise APIException(401, AUTH_FAIL_MSG)
    return row
