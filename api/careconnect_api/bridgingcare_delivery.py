"""Dedicated Bridging Care assessment outbox.

Separate from CareConnect ``assessment_delivery``. Local persist is required
before a job is inserted. HTTP is off unless execute is explicitly enabled.
Retries reuse the persisted assessment and the frozen job snapshot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, inspect as sa_inspect, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .assessment_delivery import assessment_eligible_for_delivery
from .assessment_engine.schedule import TRIGGER_SCHEDULED
from .bridgingcare_mapping import resolve_verified_mapping
from .bridgingcare_payload import dump_payload, required_payload_fields, serialize_bridgingcare_assessment
from .db import async_session_factory
from .models import (
    AiMedicalAssessment,
    CcBridgingcareDeliveryAttempt,
    CcBridgingcareDeliveryJob,
)
from .settings import settings

log = logging.getLogger("bridgingcare_delivery")

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DELIVERED = "delivered"
STATUS_SKIPPED = "skipped"
STATUS_DEAD_LETTER = "dead_letter"

ERROR_EXECUTE_DISABLED = "execute_disabled"
ERROR_NO_URL = "no_ingest_url"
ERROR_MAPPING = "mapping_unverified"
ERROR_INELIGIBLE = "ineligible"
ERROR_TIMEOUT = "timeout"
ERROR_NETWORK = "network"
ERROR_HTTP_4XX = "http_4xx"
ERROR_HTTP_429 = "http_429"
ERROR_HTTP_5XX = "http_5xx"
ERROR_AUTH = "auth"
ERROR_SCHEMA = "schema_unavailable"

DESTINATION = "bridgingcare"
STAGING_INGEST_URL = (
    "https://llkzju8pfa.execute-api.us-east-2.amazonaws.com"
    "/staging/registration/integrations/nexus/assessments"
)
IDEMPOTENCY_PREFIX = "bc-assess-"
JOB_TABLE = "cc_bridgingcare_delivery_job"
MAP_TABLE = "cc_bridgingcare_companion_map"
ATTEMPT_TABLE = "cc_bridgingcare_delivery_attempt"

_job_guard = asyncio.Lock()
_jobs_held: set[int] = set()


def reset_bridgingcare_runtime_for_tests() -> None:
    _jobs_held.clear()


def idempotency_key_for(assessment_id: int) -> str:
    return f"{IDEMPOTENCY_PREFIX}{int(assessment_id)}"


def bridgingcare_execute_enabled() -> bool:
    raw = os.environ.get("BRIDGINGCARE_EXECUTE_ENABLED")
    if raw is None:
        raw = os.environ.get("CC_BRIDGINGCARE_EXECUTE_ENABLED")
    if raw is None:
        raw = str(getattr(settings, "bridgingcare_execute_enabled", False))
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _ingest_url() -> str:
    return (getattr(settings, "bridgingcare_ingest_url", "") or "").strip().rstrip("/")


def _hostname(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or "").lower().rstrip(".")


def _api_key() -> str | None:
    path = getattr(settings, "bridgingcare_api_key_file", None)
    if path is None:
        return None
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return None
    value = p.read_text().strip()
    return value or None


async def _sqlite_next_id(db: AsyncSession, model: type) -> int | None:
    conn = await db.connection()
    if conn.dialect.name != "sqlite":
        return None
    nxt = (await db.execute(select(func.max(model.id)))).scalar()
    return int(nxt or 0) + 1


async def bridgingcare_tables_present(db: AsyncSession) -> bool:
    conn = await db.connection()

    def _check(sync_conn) -> bool:
        insp = sa_inspect(sync_conn)
        return insp.has_table(JOB_TABLE) and insp.has_table(MAP_TABLE)

    return bool(await conn.run_sync(_check))


def _naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.replace(tzinfo=None)


def assessment_after_activation(
    assessment: AiMedicalAssessment, verified_at: datetime | None
) -> bool:
    """True only when the row was generated at or after mapping activation.

    Historical assessments (created before the companion map is inserted)
    stay local. Reconcile / startup must not backfill them.
    """
    generated = _naive(getattr(assessment, "generated_at", None))
    cutoff = _naive(verified_at)
    if generated is None or cutoff is None:
        return False
    return generated >= cutoff


def scheduled_and_eligible(assessment: AiMedicalAssessment | None) -> bool:
    if assessment is None:
        return False
    if (assessment.trigger_type or "").strip() != TRIGGER_SCHEDULED:
        return False
    return assessment_eligible_for_delivery(assessment)


async def enqueue_bridgingcare_after_persist(
    db: AsyncSession,
    agent_id: str,
    assessment: AiMedicalAssessment,
) -> CcBridgingcareDeliveryJob | None:
    """Insert an outbox row after the assessment commit. Never POSTs."""
    if not scheduled_and_eligible(assessment):
        return None
    if not await bridgingcare_tables_present(db):
        log.info("bridgingcare defer assessment=%s reason=schema_unavailable", assessment.id)
        return None
    existing = (
        await db.execute(
            select(CcBridgingcareDeliveryJob).where(
                CcBridgingcareDeliveryJob.assessment_id == assessment.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    mapping = await resolve_verified_mapping(db, agent_id)
    if mapping is None:
        log.info("bridgingcare hold assessment=%s reason=mapping_unverified", assessment.id)
        return None
    if not assessment_after_activation(assessment, mapping.verified_at):
        log.info("bridgingcare hold assessment=%s reason=pre_activation", assessment.id)
        return None

    payload = serialize_bridgingcare_assessment(
        companion_id=mapping.companion_id,
        account_id=mapping.account_id,
        client_id=mapping.client_id,
        assessment=assessment,
    )
    if not required_payload_fields(payload):
        log.info("bridgingcare hold assessment=%s reason=payload", assessment.id)
        return None
    now = datetime.now()
    dest = _ingest_url()
    fields: dict[str, Any] = {
        "assessment_id": assessment.id,
        "agent_id": agent_id,
        "companion_id": mapping.companion_id,
        "account_id": mapping.account_id,
        "client_id": mapping.client_id,
        "destination": DESTINATION,
        "dest_host": _hostname(dest) or None,
        "payload_json": dump_payload(payload),
        "idempotency_key": idempotency_key_for(int(assessment.id)),
        "status": STATUS_PENDING,
        "attempt_count": 0,
        "next_retry_at": now,
        "created_at": now,
        "updated_at": now,
    }
    sqlite_id = await _sqlite_next_id(db, CcBridgingcareDeliveryJob)
    if sqlite_id is not None:
        fields["id"] = sqlite_id
    job = CcBridgingcareDeliveryJob(**fields)
    try:
        db.add(job)
        await db.flush()
        await db.refresh(job)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return (
            await db.execute(
                select(CcBridgingcareDeliveryJob).where(
                    CcBridgingcareDeliveryJob.assessment_id == assessment.id
                )
            )
        ).scalar_one_or_none()
    return job


async def reconcile_missing_bridgingcare_jobs(db: AsyncSession) -> int:
    """Create jobs for post-activation scheduled assessments missing an outbox row.

    Does not enqueue assessments generated before the verified mapping.
    Applying migration 030 or inserting the map must not backfill history.
    """
    if not await bridgingcare_tables_present(db):
        return 0
    queued = (
        await db.execute(select(CcBridgingcareDeliveryJob.assessment_id))
    ).scalars().all()
    queued_ids = {int(i) for i in queued}
    rows = (
        await db.execute(
            select(AiMedicalAssessment).where(
                AiMedicalAssessment.trigger_type == TRIGGER_SCHEDULED
            )
        )
    ).scalars().all()
    created = 0
    for row in rows:
        if int(row.id) in queued_ids:
            continue
        if not assessment_eligible_for_delivery(row):
            continue
        job = await enqueue_bridgingcare_after_persist(db, row.agent_id, row)
        if job is not None and int(job.assessment_id) not in queued_ids:
            created += 1
            queued_ids.add(int(job.assessment_id))
    return created


async def _record_attempt(
    db: AsyncSession,
    job: CcBridgingcareDeliveryJob,
    *,
    success: bool,
    http_status: int | None,
    error_category: str | None,
    dest_host: str | None,
) -> None:
    now = datetime.now()
    fields = {
        "job_id": job.id,
        "assessment_id": job.assessment_id,
        "attempted_at": now,
        "http_status": http_status,
        "success": 1 if success else 0,
        "attempt_number": int(job.attempt_count),
        "destination": job.destination,
        "dest_host": dest_host or job.dest_host,
        "error_category": error_category,
    }
    sqlite_id = await _sqlite_next_id(db, CcBridgingcareDeliveryAttempt)
    if sqlite_id is not None:
        fields["id"] = sqlite_id
    db.add(CcBridgingcareDeliveryAttempt(**fields))


def _classify_http(status: int) -> str:
    if status in (401, 403):
        return ERROR_AUTH
    if status == 429:
        return ERROR_HTTP_429
    if 400 <= status < 500:
        return ERROR_HTTP_4XX
    return ERROR_HTTP_5XX


async def _post_snapshot(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    dest = _ingest_url()
    if not dest:
        return None, ERROR_NO_URL
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = _api_key()
    if key:
        headers["X-Api-Key"] = key
    timeout = float(getattr(settings, "bridgingcare_timeout_s", 5.0) or 5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(dest, json=payload, headers=headers)
    except httpx.TimeoutException:
        return None, ERROR_TIMEOUT
    except httpx.HTTPError:
        return None, ERROR_NETWORK
    if 200 <= resp.status_code < 300:
        return resp.status_code, None
    return resp.status_code, _classify_http(resp.status_code)


async def deliver_job(db: AsyncSession, job_id: int) -> bool:
    """Attempt one frozen snapshot. Never regenerates an assessment."""
    async with _job_guard:
        if job_id in _jobs_held:
            return False
        _jobs_held.add(job_id)
    try:
        job = await db.get(CcBridgingcareDeliveryJob, job_id)
        if job is None or job.status != STATUS_PENDING:
            return False
        if job.delivered_at is not None:
            return False
        assessment = await db.get(AiMedicalAssessment, job.assessment_id)
        if assessment is None or not scheduled_and_eligible(assessment):
            job.status = STATUS_SKIPPED
            job.last_error_category = ERROR_INELIGIBLE
            job.updated_at = datetime.now()
            await db.commit()
            return False

        if not bridgingcare_execute_enabled():
            await _record_attempt(
                db,
                job,
                success=False,
                http_status=None,
                error_category=ERROR_EXECUTE_DISABLED,
                dest_host=job.dest_host,
            )
            job.last_error_category = ERROR_EXECUTE_DISABLED
            job.next_retry_at = datetime.now() + timedelta(seconds=60)
            job.updated_at = datetime.now()
            await db.commit()
            log.info(
                "bridgingcare skip job=%s assessment=%s reason=execute_disabled",
                job.id,
                job.assessment_id,
            )
            return False

        dest = _ingest_url()
        if not dest:
            await _record_attempt(
                db,
                job,
                success=False,
                http_status=None,
                error_category=ERROR_NO_URL,
                dest_host=job.dest_host,
            )
            job.last_error_category = ERROR_NO_URL
            job.next_retry_at = datetime.now() + timedelta(seconds=60)
            job.updated_at = datetime.now()
            await db.commit()
            return False

        try:
            payload = json.loads(job.payload_json)
        except (TypeError, ValueError):
            payload = None
        if not isinstance(payload, dict) or not required_payload_fields(payload):
            job.status = STATUS_DEAD_LETTER
            job.last_error_category = ERROR_SCHEMA
            job.updated_at = datetime.now()
            await db.commit()
            return False

        job.attempt_count = int(job.attempt_count or 0) + 1
        job.status = STATUS_IN_PROGRESS
        job.updated_at = datetime.now()
        await db.commit()

        http_status, error = await _post_snapshot(payload)
        dest_host = _hostname(dest) or job.dest_host
        if error is None:
            await _record_attempt(
                db,
                job,
                success=True,
                http_status=http_status,
                error_category=None,
                dest_host=dest_host,
            )
            job.status = STATUS_DELIVERED
            job.delivered_at = datetime.now()
            job.last_http_status = http_status
            job.last_error_category = None
            job.next_retry_at = None
            job.updated_at = datetime.now()
            await db.commit()
            log.info(
                "bridgingcare delivered job=%s assessment=%s companion=%s http=%s",
                job.id,
                job.assessment_id,
                job.companion_id,
                http_status,
            )
            return True

        await _record_attempt(
            db,
            job,
            success=False,
            http_status=http_status,
            error_category=error,
            dest_host=dest_host,
        )
        max_attempts = max(1, int(getattr(settings, "bridgingcare_max_attempts", 8)))
        retryable = error in {
            ERROR_TIMEOUT,
            ERROR_NETWORK,
            ERROR_HTTP_5XX,
            ERROR_HTTP_429,
        }
        if not retryable or job.attempt_count >= max_attempts:
            job.status = STATUS_DEAD_LETTER
            job.next_retry_at = None
        else:
            job.status = STATUS_PENDING
            job.next_retry_at = datetime.now() + timedelta(
                seconds=max(15.0, float(getattr(settings, "bridgingcare_backoff_base_s", 30.0)))
                * job.attempt_count
            )
        job.last_http_status = http_status
        job.last_error_category = error
        job.updated_at = datetime.now()
        await db.commit()
        return False
    finally:
        async with _job_guard:
            _jobs_held.discard(job_id)


async def tick_bridgingcare_deliveries() -> dict[str, int]:
    claimed = 0
    delivered = 0
    async with async_session_factory() as db:
        if not await bridgingcare_tables_present(db):
            return {"claimed": 0, "delivered": 0, "reconciled": 0}
        reconciled = await reconcile_missing_bridgingcare_jobs(db)
        now = datetime.now()
        rows = (
            await db.execute(
                select(CcBridgingcareDeliveryJob.id).where(
                    CcBridgingcareDeliveryJob.status == STATUS_PENDING,
                    or_(
                        CcBridgingcareDeliveryJob.next_retry_at.is_(None),
                        CcBridgingcareDeliveryJob.next_retry_at <= now,
                    ),
                )
            )
        ).scalars().all()
        for job_id in rows:
            claimed += 1
            if await deliver_job(db, int(job_id)):
                delivered += 1
    return {"claimed": claimed, "delivered": delivered, "reconciled": reconciled}
