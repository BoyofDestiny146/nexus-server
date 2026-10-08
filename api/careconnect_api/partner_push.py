"""Best-effort outbound CareConnect ingest POST.

HTTP runs only when ``CC_CARECONNECT_INGEST_URL`` is an external host.
Queueing, retries, and audit live in ``assessment_delivery``. Redis is
pub/sub + job locks only. ``ai_medical_assessment`` remains the source of
truth for portal persist. Secrets are passed at POST time, never logged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .integration_crypto import decrypt_secret
from .models import AiMedicalAssessment, ClientIntegration
from .settings import settings


log = logging.getLogger("partner_push")

PROVIDER_CARECONNECT = "careconnect"

# Hosts that are this deployment, never an external CareConnect receiver.
# nexus.warehouse-13.biz is canonical; care.nexus.warehouse-13.biz is the
# legacy portal alias. Both must skip outbound push to avoid a loop.
_SELF_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "api",
    "nexus.warehouse-13.biz",
    "care.nexus.warehouse-13.biz",
}


def _hostname(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or "").lower().rstrip(".")


def is_self_push_url(dest: str, portal_base: str | None = None) -> bool:
    """True when dest is this local CareConnect/Nexus host."""
    dest_host = _hostname(dest)
    if not dest_host:
        return True
    if dest_host in _SELF_HOSTS:
        return True
    portal_host = _hostname(portal_base if portal_base is not None else settings.portal_base_url)
    return bool(portal_host) and dest_host == portal_host


def _secret_for_push(row: ClientIntegration) -> str | None:
    if not row.secret_enc:
        return None
    try:
        secret = decrypt_secret(row.secret_enc)
    except Exception:
        log.warning("careconnect push skipped public_id=%s reason=decrypt", row.public_id)
        return None
    return secret or None


async def _careconnect_row(db: AsyncSession, agent_id: str) -> ClientIntegration | None:
    return (
        await db.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == PROVIDER_CARECONNECT,
            )
        )
    ).scalar_one_or_none()


@dataclass
class IngestPostResult:
    status_code: int
    envelope_code: Any = None


async def post_careconnect_ingest(
    dest: str,
    *,
    payload: dict[str, Any],
    public_id: str,
    secret: str,
    idempotency_key: str | None = None,
) -> IngestPostResult:
    """POST the existing partner JSON with existing X-Client-* auth.

    Does not log the secret or payload. Caller classifies success/retry.
    """
    timeout = httpx.Timeout(settings.careconnect_push_timeout_s)
    headers = {
        "X-Client-Id": public_id,
        "X-Client-Secret": secret,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
        headers["X-Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(dest, json=payload, headers=headers)
    envelope_code: Any = None
    try:
        body = resp.json()
        if isinstance(body, dict):
            envelope_code = body.get("code")
    except Exception:
        envelope_code = None
    return IngestPostResult(status_code=resp.status_code, envelope_code=envelope_code)


async def push_assessment_best_effort(
    db: AsyncSession,
    agent_id: str,
    assessment: AiMedicalAssessment,
) -> bool:
    """Enqueue (idempotent) then attempt one delivery. Never raises.

    Portal persist is the caller's responsibility. This no longer POSTs
    without a durable queue row when transmission is required.
    """
    try:
        from .assessment_delivery import (
            enqueue_careconnect_delivery_safe,
            kick_assessment_delivery,
        )

        job = await enqueue_careconnect_delivery_safe(db, agent_id, assessment)
        if job is None:
            return False
        try:
            await db.commit()
        except Exception:
            pass
        return await kick_assessment_delivery(db, int(assessment.id))
    except Exception:
        log.warning("careconnect push raised after persist")
        return False
