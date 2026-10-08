"""Durable CareConnect assessment delivery queue, worker, and audit.

Local persist is the primary invariant: a generated Care & Wellness row is
committed even when the 027 queue tables are missing or a job insert fails.
Generation-time delivery intent is snapshotted on
``ai_medical_assessment.delivery_intent_json`` (no secrets). HTTP runs only
after persist commit.

When 027 exists, ``enqueue_careconnect_delivery`` stores a job in a SAVEPOINT
inside the caller's transaction. Failure does not roll back the assessment.
``reconcile_missing_delivery_jobs`` creates jobs for eligible intent rows
that still lack a queue row (idempotent on unique ``assessment_id``).

Rows without an intent snapshot are never retroactively queued. Sales,
parse-error, empty, destination-off, and self-host intents are not eligible.
Send-time still refuses a live ``public_id`` that does not match the snapshot.

Never log API secrets or sensitive assessment payloads.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, inspect as sa_inspect, or_, select, update
from sqlalchemy.exc import IntegrityError, ProgrammingError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from .assessment_engine.delivery import (
    DESTINATION_CARECONNECT,
    outbound_careconnect_requested,
)
from .db import async_session_factory
from .models import (
    AiAgent,
    AiMedicalAssessment,
    CcAssessmentDeliveryAttempt,
    CcAssessmentDeliveryJob,
)
from .partner_payload import serialize_client_assessment_payload
from .partner_push import (
    _careconnect_row,
    _hostname,
    _secret_for_push,
    is_self_push_url,
    post_careconnect_ingest,
)
from .pubsub import get_redis
from .settings import settings

log = logging.getLogger("assessment_delivery")

_job_guard = asyncio.Lock()
_jobs_held: set[int] = set()
_schema_ready: bool | None = None
_schema_next_check: datetime | None = None
_schema_missing_logged = False

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DELIVERED = "delivered"
STATUS_SKIPPED = "skipped"
STATUS_DEAD_LETTER = "dead_letter"

ERROR_TIMEOUT = "timeout"
ERROR_NETWORK = "network"
ERROR_HTTP_4XX = "http_4xx"
ERROR_HTTP_429 = "http_429"
ERROR_HTTP_5XX = "http_5xx"
ERROR_AUTH = "auth"
ERROR_SELF_HOST = "self_host"
ERROR_NO_INGEST_URL = "no_ingest_url"
ERROR_NOT_CONNECTED = "not_connected"
ERROR_NO_SECRET = "no_secret"
ERROR_DESTINATION = "destination_disabled"
ERROR_INELIGIBLE = "ineligible"
ERROR_ENVELOPE = "envelope_error"
ERROR_CONFLICT = "conflict"
ERROR_CLIENT_MISMATCH = "client_id_mismatch"
ERROR_SCHEMA = "schema_unavailable"

PARSE_ERROR_TOKEN = "parse_error"
IDEMPOTENCY_PREFIX = "cc-assess-"
JOB_LOCK_PREFIX = "cc:delivery:job:"
DELIVERY_JOB_TABLE = "cc_assessment_delivery_job"
DELIVERY_ATTEMPT_TABLE = "cc_assessment_delivery_attempt"
INTENT_VERSION = 1
REASON_OK = "ok"
REASON_INELIGIBLE = "ineligible"
REASON_DESTINATION = "destination_disabled"
REASON_NOT_CONNECTED = "not_connected"
REASON_NO_INGEST_URL = "no_ingest_url"
REASON_SELF_HOST = "self_host"


class DeliveryEnqueueError(Exception):
    """Queue insert failed. The assessment row must still be committed."""


def idempotency_key_for(assessment_id: int) -> str:
    return f"{IDEMPOTENCY_PREFIX}{int(assessment_id)}"


def reset_delivery_runtime_for_tests() -> None:
    """Clear schema cache and in-process locks (unit tests only)."""
    global _schema_ready, _schema_next_check, _schema_missing_logged
    _schema_ready = None
    _schema_next_check = None
    _schema_missing_logged = False
    _jobs_held.clear()


def _concerns_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    if isinstance(value, str) and value.strip():
        return [value]
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def parse_delivery_intent(raw: str | None) -> dict[str, Any] | None:
    if not raw or not str(raw).strip():
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    return value


def dump_delivery_intent(intent: dict[str, Any]) -> str:
    return json.dumps(intent, ensure_ascii=False, separators=(",", ":"))


def intent_requests_enqueue(intent: dict[str, Any] | None) -> bool:
    if not intent:
        return False
    if not bool(intent.get("eligible")):
        return False
    public_id = str(intent.get("publicClientId") or "").strip()
    destination = str(intent.get("destination") or "").strip().lower()
    return bool(public_id) and destination == DESTINATION_CARECONNECT


async def snapshot_delivery_intent(
    db: AsyncSession,
    agent_id: str,
    assessment: AiMedicalAssessment,
) -> dict[str, Any]:
    """Record outbound eligibility and client identity. Never stores secrets."""
    intent: dict[str, Any] = {
        "version": INTENT_VERSION,
        "destination": DESTINATION_CARECONNECT,
        "publicClientId": None,
        "eligible": False,
        "reason": REASON_INELIGIBLE,
    }
    agent = await db.get(AiAgent, agent_id)
    dest_requested = bool(
        agent is not None and outbound_careconnect_requested(profile_json=agent.profile_json)
    )
    if not dest_requested:
        intent["destination"] = "none"
        intent["reason"] = REASON_DESTINATION
        return intent

    integration = await _careconnect_row(db, agent_id)
    connected = (
        integration is not None
        and (integration.status or "connected") == "connected"
        and bool(integration.public_id)
    )
    if connected:
        intent["publicClientId"] = integration.public_id

    if not assessment_eligible_for_delivery(assessment):
        intent["reason"] = REASON_INELIGIBLE
        return intent
    if not connected:
        intent["reason"] = REASON_NOT_CONNECTED
        return intent

    dest = _ingest_dest()
    if not dest:
        intent["reason"] = REASON_NO_INGEST_URL
        return intent
    if is_self_push_url(dest):
        intent["reason"] = REASON_SELF_HOST
        return intent

    intent["eligible"] = True
    intent["reason"] = REASON_OK
    return intent


def assessment_eligible_for_delivery(row: AiMedicalAssessment | None) -> bool:
    """Valid completed Care & Wellness rows only.

    Excludes empty/no-data (zero source messages), LLM parse-error rows,
    and incomplete records. Sales never uses ``AiMedicalAssessment``.
    """
    if row is None or row.id is None or not row.agent_id:
        return False
    if not (row.risk_level or "").strip():
        return False
    if int(row.source_msg_count or 0) <= 0:
        return False
    concerns = _concerns_list(row.concerns_json)
    if any(str(item).startswith(PARSE_ERROR_TOKEN) for item in concerns):
        return False
    return True


def _ingest_dest() -> str:
    return (settings.careconnect_ingest_url or "").strip().rstrip("/")


def _schema_retry_s() -> int:
    return max(15, int(getattr(settings, "assessment_delivery_schema_retry_s", 60)))


async def _sqlite_next_id(db: AsyncSession, model: type) -> int | None:
    conn = await db.connection()
    if conn.dialect.name != "sqlite":
        return None
    nxt = (await db.execute(select(func.max(model.id)))).scalar()
    return int(nxt or 0) + 1


async def delivery_tables_present(db: AsyncSession) -> bool:
    """True when migration 027 tables exist. Uses catalog lookup, not SELECT *."""
    conn = await db.connection()

    def _check(sync_conn) -> bool:
        insp = sa_inspect(sync_conn)
        return insp.has_table(DELIVERY_JOB_TABLE) and insp.has_table(DELIVERY_ATTEMPT_TABLE)

    return bool(await conn.run_sync(_check))


async def delivery_schema_is_ready(
    session_factory: Callable[[], Any] | None = None,
    db: AsyncSession | None = None,
) -> bool:
    """Cached migration-readiness. Re-probes after retry interval without a process restart."""
    global _schema_ready, _schema_next_check, _schema_missing_logged
    now = datetime.now()
    if _schema_ready is True:
        return True
    if (
        _schema_ready is False
        and _schema_next_check is not None
        and now < _schema_next_check
    ):
        return False

    present = False
    if db is not None:
        present = await delivery_tables_present(db)
    else:
        factory = session_factory or async_session_factory
        async with factory() as session:
            present = await delivery_tables_present(session)

    if present:
        if _schema_ready is False:
            log.info("assessment delivery schema ready; worker resuming")
        _schema_ready = True
        _schema_next_check = None
        _schema_missing_logged = False
        return True

    _schema_ready = False
    _schema_next_check = now + timedelta(seconds=_schema_retry_s())
    if not _schema_missing_logged:
        log.warning(
            "assessment delivery inactive: apply api/migrations/027_cc_assessment_delivery.sql"
        )
        _schema_missing_logged = True
    return False


async def _live_enqueue_public_id(
    db: AsyncSession,
    agent_id: str,
    assessment: AiMedicalAssessment,
) -> str | None:
    """Live policy for rows without a generation-time intent snapshot."""
    if not assessment_eligible_for_delivery(assessment):
        log.info("delivery skip assessment=%s reason=ineligible", getattr(assessment, "id", None))
        return None

    agent = await db.get(AiAgent, agent_id)
    if agent is None:
        return None
    if not outbound_careconnect_requested(profile_json=agent.profile_json):
        log.info("delivery skip assessment=%s reason=destination", assessment.id)
        return None

    row = await _careconnect_row(db, agent_id)
    if row is None or (row.status or "connected") != "connected" or not row.public_id:
        log.info("delivery skip assessment=%s reason=not_connected", assessment.id)
        return None

    dest = _ingest_dest()
    if not dest:
        log.info("delivery skip assessment=%s reason=no_ingest_url", assessment.id)
        return None
    if is_self_push_url(dest):
        log.info("careconnect self-push skipped public_id=%s", row.public_id)
        return None
    return str(row.public_id)


async def enqueue_careconnect_delivery(
    db: AsyncSession,
    agent_id: str,
    assessment: AiMedicalAssessment,
) -> CcAssessmentDeliveryJob | None:
    """Insert a pending job in the caller's transaction. Does not commit or POST.

    A generation-time intent on the assessment, when present, is authoritative:
    ineligible snapshots are never queued; eligible snapshots freeze
    ``publicClientId``. Callers without a snapshot use live policy.

    Returns the job when outbound delivery is required and stored.
    Returns None when policy says do not transmit, or when 027 tables are
    missing (deferred to reconcile). Raises DeliveryEnqueueError when a job
    was required and the insert failed; the assessment must still be committed.
    Duplicate assessment_id is success (returns the existing row).
    """
    intent = parse_delivery_intent(getattr(assessment, "delivery_intent_json", None))
    if intent is not None:
        if not intent_requests_enqueue(intent):
            log.info(
                "delivery skip assessment=%s reason=%s",
                getattr(assessment, "id", None),
                intent.get("reason") or "intent",
            )
            return None
        if not assessment_eligible_for_delivery(assessment):
            log.info("delivery skip assessment=%s reason=ineligible", assessment.id)
            return None
        public_id = str(intent.get("publicClientId") or "").strip()
    else:
        public_id = await _live_enqueue_public_id(db, agent_id, assessment) or ""
    if not public_id:
        return None

    if not await delivery_tables_present(db):
        log.info("delivery defer assessment=%s reason=schema_unavailable", assessment.id)
        return None

    existing = (
        await db.execute(
            select(CcAssessmentDeliveryJob).where(
                CcAssessmentDeliveryJob.assessment_id == assessment.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    dest = _ingest_dest()
    payload = serialize_client_assessment_payload(
        public_client_id=public_id,
        assessment=assessment,
    )
    now = datetime.now()
    fields: dict[str, Any] = {
        "assessment_id": assessment.id,
        "agent_id": agent_id,
        "public_client_id": public_id,
        "destination": DESTINATION_CARECONNECT,
        "dest_host": _hostname(dest) or None,
        "payload_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "idempotency_key": idempotency_key_for(int(assessment.id)),
        "status": STATUS_PENDING,
        "attempt_count": 0,
        "next_retry_at": now,
        "created_at": now,
        "updated_at": now,
    }
    sqlite_id = await _sqlite_next_id(db, CcAssessmentDeliveryJob)
    if sqlite_id is not None:
        fields["id"] = sqlite_id
    job = CcAssessmentDeliveryJob(**fields)
    try:
        async with db.begin_nested():
            db.add(job)
            await db.flush()
            await db.refresh(job)
    except IntegrityError:
        found = (
            await db.execute(
                select(CcAssessmentDeliveryJob).where(
                    CcAssessmentDeliveryJob.assessment_id == assessment.id
                )
            )
        ).scalar_one_or_none()
        if found is not None:
            return found
        raise DeliveryEnqueueError(
            f"delivery enqueue conflict assessment={assessment.id}"
        ) from None
    except (ProgrammingError, OperationalError) as exc:
        raise DeliveryEnqueueError(
            f"delivery enqueue failed assessment={assessment.id}"
        ) from exc
    log.info(
        "delivery enqueued assessment=%s job=%s dest_host=%s",
        assessment.id,
        job.id,
        job.dest_host,
    )
    return job


async def reconcile_missing_delivery_jobs(
    db: AsyncSession,
    limit: int = 50,
) -> int:
    """Create queue jobs for eligible intent rows that still lack one.

    NULL / missing / ineligible intents are never queued. Unique
    ``assessment_id`` makes this idempotent. No-op when 027 is absent.
    """
    if not await delivery_tables_present(db):
        return 0
    rows = (
        await db.execute(
            select(AiMedicalAssessment)
            .outerjoin(
                CcAssessmentDeliveryJob,
                CcAssessmentDeliveryJob.assessment_id == AiMedicalAssessment.id,
            )
            .where(
                AiMedicalAssessment.delivery_intent_json.is_not(None),
                CcAssessmentDeliveryJob.id.is_(None),
            )
            .order_by(AiMedicalAssessment.id.asc())
            .limit(max(1, int(limit)))
        )
    ).scalars().all()
    created = 0
    for row in rows:
        intent = parse_delivery_intent(row.delivery_intent_json)
        if not intent_requests_enqueue(intent):
            continue
        if not assessment_eligible_for_delivery(row):
            continue
        try:
            job = await enqueue_careconnect_delivery(db, row.agent_id, row)
        except DeliveryEnqueueError:
            log.warning("delivery reconcile enqueue failed assessment=%s", row.id)
            continue
        if job is not None:
            created += 1
    await db.commit()
    if created:
        log.info("delivery reconcile created=%s", created)
    return created


def _backoff_seconds(attempt_count: int) -> float:
    base = max(1.0, float(settings.assessment_delivery_backoff_base_s))
    exp = max(0, int(attempt_count) - 1)
    return min(3600.0, base * (2 ** exp))


def _classify_http(status: int | None, envelope_code: Any) -> tuple[bool, str, bool]:
    """Return (success, error_category, retryable).

    Success is only a 2xx HTTP status with envelope code 0 or no envelope
    (plain 200/201/202 from a receiver that does not use {code,msg,data}).
    HTTP 409 and envelope code 409 are conflicts for investigation, not
    success and not retried. 429/5xx/timeout remain retryable. The in-tree
    CareConnect ingest API has no documented duplicate-409 contract.
    """
    if status is None:
        return False, ERROR_NETWORK, True
    if status == 409:
        return False, ERROR_CONFLICT, False
    if status == 429:
        return False, ERROR_HTTP_429, True
    if status in (401, 403):
        return False, ERROR_AUTH, True
    if status == 408:
        return False, ERROR_TIMEOUT, True
    if 200 <= status < 300:
        if envelope_code in (0, None):
            return True, "", False
        if envelope_code == 409:
            return False, ERROR_CONFLICT, False
        return False, ERROR_ENVELOPE, True
    if 400 <= status < 500:
        return False, ERROR_HTTP_4XX, False
    if status >= 500:
        return False, ERROR_HTTP_5XX, True
    return False, ERROR_NETWORK, True


async def _record_attempt(
    db: AsyncSession,
    job: CcAssessmentDeliveryJob,
    *,
    success: bool,
    http_status: int | None,
    error_category: str | None,
    dest_host: str | None,
) -> None:
    now = datetime.now()
    sqlite_id = await _sqlite_next_id(db, CcAssessmentDeliveryAttempt)
    fields: dict[str, Any] = {
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
    if sqlite_id is not None:
        fields["id"] = sqlite_id
    db.add(CcAssessmentDeliveryAttempt(**fields))


async def _try_job_lock(job_id: int) -> bool:
    async with _job_guard:
        if job_id in _jobs_held:
            return False
        _jobs_held.add(job_id)
    try:
        redis = await get_redis()
        ok = await redis.set(
            f"{JOB_LOCK_PREFIX}{job_id}",
            "1",
            nx=True,
            ex=max(30, int(settings.assessment_delivery_lease_s)),
        )
        if not ok:
            async with _job_guard:
                _jobs_held.discard(job_id)
            return False
    except Exception:
        log.debug("delivery lock: redis unavailable job=%s", job_id)
    return True


async def _release_job_lock(job_id: int) -> None:
    async with _job_guard:
        _jobs_held.discard(job_id)
    try:
        redis = await get_redis()
        await redis.delete(f"{JOB_LOCK_PREFIX}{job_id}")
    except Exception:
        log.debug("delivery lock: redis release skipped job=%s", job_id)


async def _claim_job(db: AsyncSession, job_id: int) -> CcAssessmentDeliveryJob | None:
    now = datetime.now()
    lease = now + timedelta(seconds=max(30, int(settings.assessment_delivery_lease_s)))
    result = await db.execute(
        update(CcAssessmentDeliveryJob)
        .where(
            CcAssessmentDeliveryJob.id == job_id,
            CcAssessmentDeliveryJob.status == STATUS_PENDING,
            or_(
                CcAssessmentDeliveryJob.next_retry_at.is_(None),
                CcAssessmentDeliveryJob.next_retry_at <= now,
            ),
            or_(
                CcAssessmentDeliveryJob.lease_expires_at.is_(None),
                CcAssessmentDeliveryJob.lease_expires_at <= now,
            ),
        )
        .values(
            status=STATUS_IN_PROGRESS,
            lease_expires_at=lease,
            updated_at=now,
        )
        .returning(CcAssessmentDeliveryJob.id)
    )
    claimed_id = result.scalar_one_or_none()
    if claimed_id is None:
        await db.rollback()
        return None
    await db.commit()
    return await db.get(CcAssessmentDeliveryJob, claimed_id)


async def _finish_job(
    db: AsyncSession,
    job: CcAssessmentDeliveryJob,
    *,
    status: str,
    http_status: int | None,
    error_category: str | None,
    delivered: bool,
) -> None:
    now = datetime.now()
    job.status = status
    job.last_http_status = http_status
    job.last_error_category = error_category
    job.lease_expires_at = None
    job.updated_at = now
    if delivered:
        job.delivered_at = now
        job.next_retry_at = None
    elif status == STATUS_PENDING:
        job.next_retry_at = now + timedelta(seconds=_backoff_seconds(job.attempt_count))
    else:
        job.next_retry_at = None
    await db.commit()


async def deliver_job(db: AsyncSession, job_id: int) -> bool:
    """Claim and attempt one job. Timeout does not mean the receiver missed it."""
    locked = await _try_job_lock(job_id)
    if not locked:
        return False
    try:
        job = await _claim_job(db, job_id)
        if job is None:
            return False
        job.attempt_count = int(job.attempt_count or 0) + 1
        job.updated_at = datetime.now()
        await db.commit()
        await db.refresh(job)

        dest = _ingest_dest()
        dest_host = _hostname(dest) if dest else job.dest_host
        max_attempts = max(1, int(settings.assessment_delivery_max_attempts))

        async def _fail(category: str, *, retryable: bool, http_status: int | None = None) -> bool:
            await _record_attempt(
                db,
                job,
                success=False,
                http_status=http_status,
                error_category=category,
                dest_host=dest_host,
            )
            if not retryable:
                next_status = (
                    STATUS_SKIPPED
                    if category in (ERROR_SELF_HOST, ERROR_DESTINATION, ERROR_INELIGIBLE)
                    else STATUS_DEAD_LETTER
                )
            elif job.attempt_count >= max_attempts:
                next_status = STATUS_DEAD_LETTER
            else:
                next_status = STATUS_PENDING
            await _finish_job(
                db,
                job,
                status=next_status,
                http_status=http_status,
                error_category=category,
                delivered=False,
            )
            log.info(
                "delivery attempt job=%s assessment=%s success=0 category=%s http=%s attempts=%s",
                job.id,
                job.assessment_id,
                category,
                http_status,
                job.attempt_count,
            )
            return False

        if not dest:
            return await _fail(ERROR_NO_INGEST_URL, retryable=True)
        if is_self_push_url(dest):
            log.info(
                "careconnect self-push skipped public_id=%s",
                job.public_client_id,
            )
            return await _fail(ERROR_SELF_HOST, retryable=False)

        agent = await db.get(AiAgent, job.agent_id)
        if agent is None or not outbound_careconnect_requested(profile_json=agent.profile_json):
            return await _fail(ERROR_DESTINATION, retryable=False)

        assessment = await db.get(AiMedicalAssessment, job.assessment_id)
        if not assessment_eligible_for_delivery(assessment):
            return await _fail(ERROR_INELIGIBLE, retryable=False)

        integration = await _careconnect_row(db, job.agent_id)
        if (
            integration is None
            or (integration.status or "connected") != "connected"
            or not integration.public_id
        ):
            return await _fail(ERROR_NOT_CONNECTED, retryable=True)
        if integration.public_id != job.public_client_id:
            log.warning(
                "delivery client mismatch job=%s assessment=%s",
                job.id,
                job.assessment_id,
            )
            return await _fail(ERROR_CLIENT_MISMATCH, retryable=False)
        secret = _secret_for_push(integration)
        if not secret:
            return await _fail(ERROR_NO_SECRET, retryable=True)

        try:
            payload = json.loads(job.payload_json)
        except (ValueError, TypeError):
            payload = serialize_client_assessment_payload(
                public_client_id=job.public_client_id,
                assessment=assessment,
            )

        try:
            result = await post_careconnect_ingest(
                dest,
                payload=payload,
                public_id=job.public_client_id,
                secret=secret,
                idempotency_key=job.idempotency_key,
            )
        except Exception as exc:
            name = type(exc).__name__
            category = ERROR_TIMEOUT if "timeout" in name.lower() or "Timeout" in name else ERROR_NETWORK
            return await _fail(category, retryable=True)

        success, category, retryable = _classify_http(result.status_code, result.envelope_code)
        if success:
            await _record_attempt(
                db,
                job,
                success=True,
                http_status=result.status_code,
                error_category=None,
                dest_host=dest_host,
            )
            await _finish_job(
                db,
                job,
                status=STATUS_DELIVERED,
                http_status=result.status_code,
                error_category=None,
                delivered=True,
            )
            log.info(
                "careconnect push ok public_id=%s job=%s assessment=%s http=%s",
                job.public_client_id,
                job.id,
                job.assessment_id,
                result.status_code,
            )
            return True
        return await _fail(category, retryable=retryable, http_status=result.status_code)
    finally:
        await _release_job_lock(job_id)


async def tick_assessment_deliveries(
    limit: int = 10,
    session_factory: Callable[[], Any] | None = None,
) -> dict[str, int]:
    """Bounded worker tick. Inactive until migration 027 tables exist."""
    factory = session_factory or async_session_factory
    if not await delivery_schema_is_ready(session_factory=factory):
        return {"due": 0, "delivered": 0, "failed": 0, "inactive": 1, "reconciled": 0}

    reconciled = 0
    try:
        async with factory() as session:
            reconciled = await reconcile_missing_delivery_jobs(
                session, limit=max(1, int(limit))
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("delivery reconcile raised err=%s", type(exc).__name__)

    now = datetime.now()
    delivered = 0
    failed = 0
    async with factory() as session:
        await session.execute(
            update(CcAssessmentDeliveryJob)
            .where(
                CcAssessmentDeliveryJob.status == STATUS_IN_PROGRESS,
                CcAssessmentDeliveryJob.lease_expires_at.is_not(None),
                CcAssessmentDeliveryJob.lease_expires_at <= now,
            )
            .values(status=STATUS_PENDING, lease_expires_at=None, updated_at=now)
        )
        await session.commit()
        due = (
            await session.execute(
                select(CcAssessmentDeliveryJob.id)
                .where(
                    CcAssessmentDeliveryJob.status == STATUS_PENDING,
                    or_(
                        CcAssessmentDeliveryJob.next_retry_at.is_(None),
                        CcAssessmentDeliveryJob.next_retry_at <= now,
                    ),
                )
                .order_by(CcAssessmentDeliveryJob.id.asc())
                .limit(max(1, int(limit)))
            )
        ).scalars().all()

    for job_id in due:
        try:
            async with factory() as session:
                ok = await deliver_job(session, int(job_id))
            if ok:
                delivered += 1
            else:
                failed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("delivery tick job=%s err=%s", job_id, type(exc).__name__)

    log.info(
        "assessment delivery tick due=%d delivered=%d failed=%d reconciled=%d",
        len(due),
        delivered,
        failed,
        reconciled,
    )
    return {
        "due": len(due),
        "delivered": delivered,
        "failed": failed,
        "inactive": 0,
        "reconciled": reconciled,
    }


async def kick_assessment_delivery(db: AsyncSession, assessment_id: int) -> bool:
    """Best-effort immediate HTTP attempt after persist commit. Never raises."""
    try:
        if not await delivery_tables_present(db):
            return False
        job = (
            await db.execute(
                select(CcAssessmentDeliveryJob).where(
                    CcAssessmentDeliveryJob.assessment_id == assessment_id
                )
            )
        ).scalar_one_or_none()
        if job is None or job.status != STATUS_PENDING:
            return False
        return await deliver_job(db, job.id)
    except Exception:
        log.warning("delivery kick raised assessment=%s", assessment_id)
        return False
