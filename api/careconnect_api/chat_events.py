"""System-originated timeline events stored in ``ai_agent_chat_history``.

Existing convention (unchanged):
  chat_type 1 = CLIENT speech
  chat_type 2 = CAREGIVER / assistant speech

Added without a migration (column is an unconstrained SmallInteger):
  chat_type 3 = system_event — not a person. Google Calendar reminders use
  ``source = google_calendar`` inside a ``[[gcal]]`` JSON header.

Spoken text lives in the body after the first newline so it is not duplicated
in the JSON. The private iCal URL and Google credentials are never stored.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .gcal_ical import CalendarOccurrence, spoken_text
from .models import AiAgentChatHistory
from .pubsub import publish_chat_turn


log = logging.getLogger("chat_events")

CHAT_TYPE_CLIENT = 1
CHAT_TYPE_CAREGIVER = 2
CHAT_TYPE_SYSTEM = 3
SOURCE_GOOGLE_CALENDAR = "google_calendar"
GCAL_MARKER = "[[gcal]]"
GCAL_SESSION_FALLBACK = "google_calendar"
CONTENT_MAX = 1024

_FORBIDDEN_HEADER_KEYS = frozenset(
    {
        "url",
        "ical",
        "ical_url",
        "icalUrl",
        "secret",
        "secret_enc",
        "token",
        "credential",
        "credentials",
        "password",
    }
)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _naive_local(dt: datetime) -> datetime:
    """Match existing chat rows: naive timestamp in the datetime's wall clock."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def encode_gcal_timeline(
    spoken: str,
    *,
    event_uid: str,
    occurrence_start: str,
    title: str,
    delivered_at: str,
    provider: str = SOURCE_GOOGLE_CALENDAR,
) -> str:
    """Pack a system timeline row. ``spoken_text`` is the body, not the JSON."""
    header = {
        "provider": provider or SOURCE_GOOGLE_CALENDAR,
        "event_uid": (event_uid or "")[:200],
        "occurrence_start": occurrence_start or "",
        "title": (title or "")[:180],
        "delivered_at": delivered_at or "",
    }
    for key in _FORBIDDEN_HEADER_KEYS:
        header.pop(key, None)
    raw = json.dumps(header, separators=(",", ":"), ensure_ascii=False)
    prefix = f"{GCAL_MARKER}{raw}\n"
    body = (spoken or "").strip()
    budget = CONTENT_MAX - len(prefix)
    if budget < 0:
        # Pathological header; keep the marker and a truncated header.
        return (prefix[: CONTENT_MAX - 1] + "\n")[:CONTENT_MAX]
    return prefix + body[:budget]


def parse_gcal_timeline(content: str | None) -> dict[str, Any] | None:
    """Return header + spoken_text, or None if this is not a calendar row."""
    if not content:
        return None
    text = content.lstrip()
    if not text.startswith(GCAL_MARKER):
        return None
    rest = text[len(GCAL_MARKER) :]
    nl = rest.find("\n")
    if nl < 0:
        header_raw, spoken = rest, ""
    else:
        header_raw, spoken = rest[:nl], rest[nl + 1 :]
    try:
        header = json.loads(header_raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(header, dict):
        return None
    provider = str(header.get("provider") or SOURCE_GOOGLE_CALENDAR)
    if provider != SOURCE_GOOGLE_CALENDAR:
        return None
    return {
        "provider": provider,
        "event_uid": str(header.get("event_uid") or ""),
        "occurrence_start": str(header.get("occurrence_start") or ""),
        "title": str(header.get("title") or ""),
        "spoken_text": spoken,
        "delivered_at": str(header.get("delivered_at") or ""),
    }


def calendar_dialogue_line(content: str | None) -> str:
    """Compact line for triage: spoken text only, never the JSON header."""
    parsed = parse_gcal_timeline(content)
    if parsed is not None:
        spoken = (parsed.get("spoken_text") or "").strip()
        title = (parsed.get("title") or "").strip()
        return spoken or title
    return (content or "").strip()


async def latest_session_id(db: AsyncSession, agent_id: str) -> str:
    row = (
        await db.execute(
            select(AiAgentChatHistory.session_id)
            .where(
                AiAgentChatHistory.agent_id == agent_id,
                AiAgentChatHistory.session_id.is_not(None),
                AiAgentChatHistory.session_id != "",
            )
            .order_by(AiAgentChatHistory.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if isinstance(row, str) and row.strip():
        return row.strip()[:50]
    return GCAL_SESSION_FALLBACK


async def _sqlite_next_chat_id(db: AsyncSession) -> int | None:
    """SQLite BigInteger PKs are NOT NULL without AUTOINCREMENT; MariaDB is fine."""
    conn = await db.connection()
    if conn.dialect.name != "sqlite":
        return None
    nxt = (await db.execute(select(func.max(AiAgentChatHistory.id)))).scalar()
    return int(nxt or 0) + 1


async def persist_google_calendar_timeline(
    db: AsyncSession,
    *,
    agent_id: str,
    occ: CalendarOccurrence,
    delivered_at: datetime,
) -> dict[str, Any] | None:
    """Insert one system_event row. Caller commits. Does not mark tasks done."""
    spoken = spoken_text(occ)
    if not agent_id or not spoken:
        return None
    session_id = await latest_session_id(db, agent_id)
    delivered_iso = _iso(delivered_at)
    content = encode_gcal_timeline(
        spoken,
        event_uid=occ.uid,
        occurrence_start=_iso(occ.start),
        title=occ.title or "",
        delivered_at=delivered_iso,
    )
    stamp = _naive_local(delivered_at)
    fields: dict[str, Any] = {
        "mac_address": None,
        "agent_id": agent_id,
        "session_id": session_id,
        "chat_type": CHAT_TYPE_SYSTEM,
        "content": content,
        "created_at": stamp,
        "updated_at": stamp,
    }
    next_id = await _sqlite_next_chat_id(db)
    if next_id is not None:
        fields["id"] = next_id
    row = AiAgentChatHistory(**fields)
    db.add(row)
    await db.flush()
    if delivered_at.tzinfo is not None:
        created_ms = int(delivered_at.timestamp() * 1000)
    else:
        created_ms = int(stamp.replace(tzinfo=timezone.utc).timestamp() * 1000)
    log.info(
        "calendar timeline recorded agent=%s uid=%s chat_type=%s",
        agent_id,
        occ.uid,
        CHAT_TYPE_SYSTEM,
    )
    return {
        "id": row.id,
        "agentId": agent_id,
        "sessionId": session_id,
        "chatType": CHAT_TYPE_SYSTEM,
        "content": content,
        "macAddress": "",
        "createdAt": created_ms,
    }


async def notify_timeline_best_effort(payload: dict[str, Any] | None) -> None:
    """Live dashboard fan-out. Never raises; never required for persistence."""
    if not payload:
        return
    agent_id = payload.get("agentId")
    if not agent_id:
        return
    try:
        await publish_chat_turn(str(agent_id), payload)
    except Exception:
        log.debug(
            "calendar timeline notify skipped agent=%s",
            agent_id,
            exc_info=True,
        )
