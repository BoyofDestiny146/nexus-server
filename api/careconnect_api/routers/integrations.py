"""Per-client partner integrations (CareConnect, Revel; Directed Logic is UI-only).

Mounted at /api by main.py. Identity belongs to ``ai_agent``, not a Watcher.

Endpoints
---------
GET    /api/agent/{agentId}/integrations
POST   /api/agent/{agentId}/integrations/careconnect
POST   /api/agent/{agentId}/integrations/careconnect/rotate
DELETE /api/agent/{agentId}/integrations/careconnect
PUT    /api/agent/{agentId}/integrations/revel
DELETE /api/agent/{agentId}/integrations/revel

Secrets never go in logs. CareConnect plaintext is returned only on
create/rotate. Revel plaintext is never returned after save.
"""
from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, get_current_user, hash_password
from ..db import get_db
from ..envelope import APIException
from ..integration_crypto import encrypt_secret, secret_hint
from ..models import AiAgent, ClientIntegration
from ..rbac import assert_can_access_agent
from ..settings import settings


log = logging.getLogger("integrations")

router = APIRouter(prefix="/agent", tags=["integrations"])

PROVIDER_CARECONNECT = "careconnect"
PROVIDER_REVEL = "revel"
PROVIDER_DIRECTED_LOGIC = "directed_logic"

_NX_ALPHABET = string.ascii_uppercase + string.digits
_PUBLIC_ID_ATTEMPTS = 12


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_public_id() -> str:
    return "Nx-" + "".join(secrets.choice(_NX_ALPHABET) for _ in range(9))


def new_careconnect_secret() -> str:
    return secrets.token_urlsafe(32)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


async def _require_agent(db: AsyncSession, agent_id: str) -> AiAgent:
    agent = (
        await db.execute(select(AiAgent).where(AiAgent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise APIException(404, f"agent {agent_id} not found")
    return agent


async def _get_row(
    db: AsyncSession, agent_id: str, provider: str
) -> ClientIntegration | None:
    return (
        await db.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == provider,
            )
        )
    ).scalar_one_or_none()


async def _public_id_taken(db: AsyncSession, public_id: str) -> bool:
    row = (
        await db.execute(
            select(ClientIntegration.id).where(ClientIntegration.public_id == public_id)
        )
    ).scalar_one_or_none()
    return row is not None


async def _allocate_public_id(db: AsyncSession) -> str:
    for _ in range(_PUBLIC_ID_ATTEMPTS):
        candidate = new_public_id()
        if not await _public_id_taken(db, candidate):
            return candidate
    raise APIException(500, "could not allocate a unique integration id")


def _careconnect_connection_urls() -> dict[str, str]:
    return {
        "portal": settings.portal_base_url.rstrip("/"),
        "assessmentEndpoint": settings.careconnect_assessment_url,
    }


def _careconnect_public(row: ClientIntegration) -> dict[str, Any]:
    out = {
        "provider": PROVIDER_CARECONNECT,
        "label": "CareConnect",
        "status": row.status or "connected",
        "connected": True,
        "publicId": row.public_id,
        "secretMasked": True,
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
    }
    out.update(_careconnect_connection_urls())
    return out


def _revel_public(row: ClientIntegration) -> dict[str, Any]:
    hint = row.secret_hint or ""
    masked = ("•" * 12 + hint) if hint else ("•" * 12)
    return {
        "provider": PROVIDER_REVEL,
        "label": "Revel",
        "status": row.status or "connected",
        "connected": True,
        "secretHint": hint,
        "maskedKey": masked,
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
    }


def _empty_careconnect() -> dict[str, Any]:
    out = {
        "provider": PROVIDER_CARECONNECT,
        "label": "CareConnect",
        "status": "disconnected",
        "connected": False,
        "publicId": None,
        "secretMasked": True,
    }
    out.update(_careconnect_connection_urls())
    return out


def _empty_revel() -> dict[str, Any]:
    return {
        "provider": PROVIDER_REVEL,
        "label": "Revel",
        "status": "disconnected",
        "connected": False,
        "secretHint": None,
        "maskedKey": None,
    }


def _directed_logic() -> dict[str, Any]:
    return {
        "provider": PROVIDER_DIRECTED_LOGIC,
        "label": "Directed Logic",
        "status": "coming_soon",
        "connected": False,
        "comingSoon": True,
    }


def _assert_no_secret_fields(payload: dict[str, Any]) -> None:
    """Defense in depth for public GET shapes."""
    banned = ("secret", "apiKey", "api_key", "secretHash", "secret_hash", "secretEnc", "secret_enc")
    for key in banned:
        if key in payload and key != "secretMasked":
            payload.pop(key, None)


class RevelUpsert(BaseModel):
    apiKey: str = Field(min_length=8, max_length=512)


@router.get("/{agent_id}/integrations", response_model=None)
async def list_integrations(
    agent_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await assert_can_access_agent(db, user, agent_id)
    await _require_agent(db, agent_id)

    cc = await _get_row(db, agent_id, PROVIDER_CARECONNECT)
    revel = await _get_row(db, agent_id, PROVIDER_REVEL)
    items = [
        _careconnect_public(cc) if cc is not None else _empty_careconnect(),
        _revel_public(revel) if revel is not None else _empty_revel(),
        _directed_logic(),
    ]
    for item in items:
        _assert_no_secret_fields(item)
    return {"list": items}


@router.post("/{agent_id}/integrations/careconnect", response_model=None)
async def create_careconnect(
    agent_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await assert_can_access_agent(db, user, agent_id)
    await _require_agent(db, agent_id)

    existing = await _get_row(db, agent_id, PROVIDER_CARECONNECT)
    if existing is not None:
        raise APIException(
            409,
            "CareConnect integration already exists",
            data={"publicId": existing.public_id},
        )

    secret = new_careconnect_secret()
    last_error: Exception | None = None
    row: ClientIntegration | None = None
    for _ in range(_PUBLIC_ID_ATTEMPTS):
        public_id = await _allocate_public_id(db)
        row = ClientIntegration(
            agent_id=agent_id,
            provider=PROVIDER_CARECONNECT,
            public_id=public_id,
            secret_hash=hash_password(secret),
            secret_enc=encrypt_secret(secret),
            secret_hint=secret_hint(secret),
            status="connected",
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(row)
        try:
            await db.commit()
            await db.refresh(row)
            last_error = None
            break
        except IntegrityError as exc:
            last_error = exc
            await db.rollback()
            row = None
            if await _get_row(db, agent_id, PROVIDER_CARECONNECT) is not None:
                raise APIException(
                    409,
                    "CareConnect integration already exists",
                )
    if row is None:
        raise APIException(500, "could not allocate a unique integration id") from last_error

    public_id = row.public_id

    log.info("careconnect integration created agent=%s public_id=%s", agent_id, public_id)
    out = _careconnect_public(row)
    out["secret"] = secret
    out["secretOnce"] = True
    return out


@router.post("/{agent_id}/integrations/careconnect/rotate", response_model=None)
async def rotate_careconnect(
    agent_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await assert_can_access_agent(db, user, agent_id)
    row = await _get_row(db, agent_id, PROVIDER_CARECONNECT)
    if row is None:
        raise APIException(404, "CareConnect integration is not connected")

    secret = new_careconnect_secret()
    row.secret_hash = hash_password(secret)
    row.secret_enc = encrypt_secret(secret)
    row.secret_hint = secret_hint(secret)
    row.updated_at = _now()
    try:
        await db.commit()
        await db.refresh(row)
    except Exception:
        await db.rollback()
        raise

    log.info("careconnect secret rotated agent=%s public_id=%s", agent_id, row.public_id)
    out = _careconnect_public(row)
    out["secret"] = secret
    out["secretOnce"] = True
    return out


@router.delete("/{agent_id}/integrations/careconnect", response_model=None)
async def delete_careconnect(
    agent_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await assert_can_access_agent(db, user, agent_id)
    row = await _get_row(db, agent_id, PROVIDER_CARECONNECT)
    if row is None:
        raise APIException(404, "CareConnect integration is not connected")
    public_id = row.public_id
    try:
        await db.execute(
            delete(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == PROVIDER_CARECONNECT,
            )
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    log.info("careconnect integration disconnected agent=%s public_id=%s", agent_id, public_id)
    return {
        "provider": PROVIDER_CARECONNECT,
        "disconnected": True,
        "clientPreserved": True,
        "devicesPreserved": True,
    }


@router.put("/{agent_id}/integrations/revel", response_model=None)
async def upsert_revel(
    agent_id: str,
    payload: RevelUpsert,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await assert_can_access_agent(db, user, agent_id)
    await _require_agent(db, agent_id)

    api_key = payload.apiKey.strip()
    if not api_key:
        raise APIException(400, "apiKey is required")

    ciphertext = encrypt_secret(api_key)
    hint = secret_hint(api_key)
    existing = await _get_row(db, agent_id, PROVIDER_REVEL)
    now = _now()
    if existing is None:
        row = ClientIntegration(
            agent_id=agent_id,
            provider=PROVIDER_REVEL,
            public_id=None,
            secret_hash=None,
            secret_enc=ciphertext,
            secret_hint=hint,
            status="connected",
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        replaced = False
    else:
        existing.secret_enc = ciphertext
        existing.secret_hint = hint
        existing.status = "connected"
        existing.updated_at = now
        row = existing
        replaced = True
    try:
        await db.commit()
        await db.refresh(row)
    except Exception:
        await db.rollback()
        raise

    log.info("revel integration %s agent=%s", "replaced" if replaced else "created", agent_id)
    out = _revel_public(row)
    out["replaced"] = replaced
    return out


@router.delete("/{agent_id}/integrations/revel", response_model=None)
async def delete_revel(
    agent_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await assert_can_access_agent(db, user, agent_id)
    row = await _get_row(db, agent_id, PROVIDER_REVEL)
    if row is None:
        raise APIException(404, "Revel integration is not connected")
    try:
        await db.execute(
            delete(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == PROVIDER_REVEL,
            )
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    log.info("revel integration disconnected agent=%s", agent_id)
    return {
        "provider": PROVIDER_REVEL,
        "disconnected": True,
        "clientPreserved": True,
        "devicesPreserved": True,
    }
