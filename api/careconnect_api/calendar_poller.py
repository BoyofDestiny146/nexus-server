"""APScheduler job: fetch connected Google Calendars and speak due timed events.

One broken calendar never aborts the rest. All-day DATE events are listed in
upcoming metadata but never generate Watcher TTS. Delivery de-dup lives in
``cc_client_integration.metadata_json`` (no new table). A successfully spoken
occurrence also writes one ``ai_agent_chat_history`` system_event row
(chat_type=3) for assessment — never CLIENT or CAREGIVER.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .chat_events import notify_timeline_best_effort, persist_google_calendar_timeline
from .db import async_session_factory
from .gcal_ical import (
    CalendarError,
    CalendarOccurrence,
    calendar_host,
    dedup_key,
    eligible_for_speech,
    fetch_ics,
    is_due,
    next_and_upcoming,
    parse_ics,
    spoken_text,
)
from .integration_crypto import decrypt_secret
from .models import AiDevice, ClientIntegration
from .settings import settings
from .xiaozhi_control import notify_xiaozhi_speak


log = logging.getLogger("calendar_poller")

PROVIDER = "google_calendar"
SpeakFn = Callable[..., Awaitable[dict[str, Any]]]
FetchFn = Callable[..., Awaitable[str]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_meta(row: ClientIntegration) -> dict[str, Any]:
    try:
        data = json.loads(row.metadata_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def dump_meta(row: ClientIntegration, meta: dict[str, Any]) -> None:
    row.metadata_json = json.dumps(meta)


def prune_fired(fired: Iterable[str], now: datetime) -> list[str]:
    retain = timedelta(days=settings.gcal_fired_retain_days)
    cutoff = now - retain
    kept: list[str] = []
    for key in fired:
        if not isinstance(key, str):
            continue
        parts = key.split("|")
        if len(parts) < 2:
            continue
        try:
            start = datetime.fromisoformat(parts[1])
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if start >= cutoff:
                kept.append(key)
        except Exception:
            continue
    return kept[-500:]


async def bound_watchers(db: AsyncSession, agent_id: str) -> list[AiDevice]:
    rows = (
        await db.execute(select(AiDevice).where(AiDevice.agent_id == agent_id))
    ).scalars().all()
    return list(rows)


async def deliver_occurrence(
    db: AsyncSession,
    agent_id: str,
    occ: CalendarOccurrence,
    *,
    speak_fn: SpeakFn | None = None,
) -> bool:
    """Speak to every currently bound Watcher with a live socket.

    Returns True only when at least one Watcher actually spoke. Offline
    devices are skipped; the occurrence is not marked fired so a reconnect
    inside the due window can still deliver. Stale hours-later replay is
    prevented by ``is_due``, not by this function.
    """
    text = spoken_text(occ)
    if not text:
        return False
    devices = await bound_watchers(db, agent_id)
    if not devices:
        log.info(
            "calendar speak skipped agent=%s reason=no_bound_watcher uid=%s",
            agent_id,
            occ.uid,
        )
        return False

    speak = speak_fn or notify_xiaozhi_speak
    spoken_any = False
    for dev in devices:
        result = await speak(mac=dev.mac_address, device_id=dev.id, text=text)
        n = 0
        try:
            n = int((result or {}).get("spoken") or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            spoken_any = True
    if not spoken_any:
        log.info(
            "calendar speak skipped agent=%s reason=watcher_offline uid=%s",
            agent_id,
            occ.uid,
        )
    return spoken_any


async def poll_one(
    db: AsyncSession,
    row: ClientIntegration,
    *,
    now: datetime | None = None,
    fetch_fn: FetchFn | None = None,
    speak_fn: SpeakFn | None = None,
) -> dict[str, Any]:
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    meta = load_meta(row)
    stats = {"agentId": row.agent_id, "delivered": 0, "skipped": 0, "error": None}

    if not row.secret_enc:
        log.warning("google calendar missing secret agent=%s", row.agent_id)
        stats["error"] = "missing_secret"
        return stats

    try:
        url = decrypt_secret(row.secret_enc)
    except Exception:
        log.warning("google calendar decrypt failed agent=%s", row.agent_id)
        stats["error"] = "decrypt_failed"
        return stats

    fetch = fetch_fn or fetch_ics
    try:
        ics = await fetch(url)
        occurrences = parse_ics(ics, now=now)
    except (CalendarError, Exception) as exc:
        err = type(exc).__name__ if not isinstance(exc, CalendarError) else exc.code
        meta["lastSyncError"] = err
        if "calendarHost" not in meta:
            host = calendar_host(url)
            if host:
                meta["calendarHost"] = host
        dump_meta(row, meta)
        await db.commit()
        log.warning("google calendar fetch failed agent=%s err=%s", row.agent_id, err)
        stats["error"] = err
        return stats

    nxt, upcoming = next_and_upcoming(occurrences, now, limit=3)
    fired = prune_fired(meta.get("fired") or [], now)
    fired_set = set(fired)
    delivered = 0
    skipped = 0
    pending_notifies: list[dict[str, Any]] = []

    for occ in occurrences:
        if not eligible_for_speech(occ):
            continue
        if not is_due(occ, now):
            continue
        key = dedup_key(row.agent_id, occ)
        if key in fired_set:
            skipped += 1
            continue
        ok = await deliver_occurrence(db, row.agent_id, occ, speak_fn=speak_fn)
        if ok:
            payload = await persist_google_calendar_timeline(
                db, agent_id=row.agent_id, occ=occ, delivered_at=now
            )
            if payload:
                pending_notifies.append(payload)
            fired.append(key)
            fired_set.add(key)
            delivered += 1
        else:
            skipped += 1

    meta["lastSuccessfulSync"] = now.isoformat()
    meta["lastSyncError"] = None
    meta["nextEvent"] = nxt
    meta["upcoming"] = upcoming
    meta["fired"] = prune_fired(fired, now)
    host = calendar_host(url)
    if host:
        meta["calendarHost"] = host
    dump_meta(row, meta)
    await db.commit()
    for payload in pending_notifies:
        await notify_timeline_best_effort(payload)
    stats["delivered"] = delivered
    stats["skipped"] = skipped
    return stats


async def poll_google_calendars(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    now: datetime | None = None,
    fetch_fn: FetchFn | None = None,
    speak_fn: SpeakFn | None = None,
) -> dict[str, Any]:
    """Walk every connected google_calendar row. Never raises to the scheduler."""
    factory = session_factory or async_session_factory
    summary = {"processed": 0, "delivered": 0, "errors": 0}
    async with factory() as db:
        try:
            rows = (
                await db.execute(
                    select(ClientIntegration).where(
                        ClientIntegration.provider == PROVIDER,
                        ClientIntegration.status == "connected",
                    )
                )
            ).scalars().all()
        except Exception as exc:
            log.warning("google calendar list failed err=%s", type(exc).__name__)
            return summary
        for row in rows:
            summary["processed"] += 1
            agent_id = getattr(row, "agent_id", None) or "?"
            try:
                stats = await poll_one(
                    db, row, now=now, fetch_fn=fetch_fn, speak_fn=speak_fn
                )
                summary["delivered"] += int(stats.get("delivered") or 0)
                if stats.get("error"):
                    summary["errors"] += 1
            except Exception as exc:
                summary["errors"] += 1
                log.warning(
                    "google calendar poll skipped agent=%s err=%s",
                    agent_id,
                    type(exc).__name__,
                )
                try:
                    await db.rollback()
                except Exception:
                    pass
    return summary
