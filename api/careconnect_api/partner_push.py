"""Best-effort outbound CareConnect ingest push.

Outbound HTTP runs only when ``CC_CARECONNECT_INGEST_URL`` is set to a host
that is not this Nexus/CareConnect stack. Redis is pub/sub only; there is no
retry queue. ``ai_medical_assessment`` remains the source of truth.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .integration_crypto import decrypt_secret
from .models import AiMedicalAssessment, ClientIntegration
from .partner_payload import serialize_client_assessment_payload
from .settings import settings


log = logging.getLogger("partner_push")

PROVIDER_CARECONNECT = "careconnect"

# Hosts that are this deployment, never an external CareConnect receiver.
_SELF_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "api",
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


async def push_assessment_best_effort(
    db: AsyncSession,
    agent_id: str,
    assessment: AiMedicalAssessment,
) -> bool:
    """POST the v1 payload to an external receiver only. Never raises."""
    dest = (settings.careconnect_ingest_url or "").strip().rstrip("/")
    if not dest:
        return False
    public_id = "-"
    try:
        row = await _careconnect_row(db, agent_id)
        if row is None or (row.status or "connected") != "connected" or not row.public_id:
            return False
        public_id = row.public_id
        if is_self_push_url(dest):
            log.info("careconnect self-push skipped public_id=%s", public_id)
            return False
        secret = _secret_for_push(row)
        if not secret:
            log.info("careconnect push skipped public_id=%s reason=no-secret", public_id)
            return False
        payload: dict[str, Any] = serialize_client_assessment_payload(
            public_client_id=public_id,
            assessment=assessment,
        )
        timeout = httpx.Timeout(settings.careconnect_push_timeout_s)
        headers = {
            "X-Client-Id": public_id,
            "X-Client-Secret": secret,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(dest, json=payload, headers=headers)
        try:
            body = resp.json()
        except Exception:
            body = None
        ok = (
            resp.status_code == 200
            and isinstance(body, dict)
            and body.get("code") == 0
        )
        if ok:
            log.info("careconnect push ok public_id=%s", public_id)
            return True
        log.warning(
            "careconnect push failed public_id=%s status=%s code=%s",
            public_id,
            resp.status_code,
            body.get("code") if isinstance(body, dict) else None,
        )
        return False
    except Exception as exc:
        log.warning(
            "careconnect push failed public_id=%s err=%s",
            public_id,
            type(exc).__name__,
        )
        return False
