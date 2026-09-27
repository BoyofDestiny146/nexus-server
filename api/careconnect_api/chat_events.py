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
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .gcal_ical import CalendarOccurrence, spoken_text
from .models import AiAgentChatHistory
from .pubsub import publish_chat_turn
from .revel_client import _safe_revel_error
from .revel_errors import normalize_reason, reason_label


log = logging.getLogger("chat_events")

CHAT_TYPE_CLIENT = 1
CHAT_TYPE_CAREGIVER = 2
CHAT_TYPE_SYSTEM = 3
SOURCE_GOOGLE_CALENDAR = "google_calendar"
GCAL_MARKER = "[[gcal]]"
REVEL_MARKER = "[[revel]]"
SOURCE_REVEL = "revel"
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
        "apiKey",
        "api_key",
        "registrationKey",
        "registration_key",
        "registrationKeyEnc",
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


REVEL_RESULTS = ("sent", "failed", "skipped", "disabled")
REVEL_EVENT_TYPE = "revel_display"
REVEL_CONTEXT_EVENT_TYPE = "revel_context"
REVEL_CONTEXT_TYPE = "REVEL_CONTEXT"


def normalize_revel_result(raw: str | None) -> str:
    """Allowlisted results only. Legacy ``delivered`` rows map to ``sent``."""
    value = (raw or "").strip().casefold()
    if value == "delivered":
        return "sent"
    if value in REVEL_RESULTS:
        return value
    return "failed"


def encode_revel_timeline(
    *,
    requested: str,
    intent: str,
    device_name: str,
    result: str,
    delivered_at: str,
    provider: str = SOURCE_REVEL,
    tag: str | None = None,
    device_key: str | None = None,
    revel_device_id: str | None = None,
    screen: str | None = None,
    error: str | None = None,
    summary: str | None = None,
    reason: str | None = None,
    control_table_id: str | None = None,
    control_row_id: str | None = None,
    event_type: str = REVEL_EVENT_TYPE,
) -> str:
    """Pack a display-action system row. No API keys, hosts, or GraphQL."""
    allowed_result = normalize_revel_result(result)
    reason_code = (normalize_reason(reason) or reason or "")[:80]
    header = {
        "provider": provider or SOURCE_REVEL,
        "event_type": (event_type or REVEL_EVENT_TYPE)[:32],
        "intent": (intent or "")[:64],
        "screen": (screen or "")[:32],
        "deviceName": (device_name or "")[:80],
        "revel_device_name": (device_name or "")[:80],
        "revel_device_id": (revel_device_id or "")[:128],
        "device_key": (device_key or "")[:128],
        "tag": (tag or "")[:128],
        "control_table_id": (control_table_id or "")[:128],
        "control_row_id": (control_row_id or "")[:128],
        "result": allowed_result,
        "reason": reason_code,
        "reason_label": (reason_label(reason_code) or "")[:80],
        "requested": (requested or "")[:180],
        "summary": (summary or requested or "")[:180],
        "error": _safe_revel_error(error or "")[:180],
        "delivered_at": delivered_at or "",
        "created_at": delivered_at or "",
    }
    for key in _FORBIDDEN_HEADER_KEYS:
        header.pop(key, None)
    raw = json.dumps(header, separators=(",", ":"), ensure_ascii=False)
    prefix = f"{REVEL_MARKER}{raw}\n"
    body = "REVEL DISPLAY EVENT"
    budget = CONTENT_MAX - len(prefix)
    if budget < 0:
        return (prefix[: CONTENT_MAX - 1] + "\n")[:CONTENT_MAX]
    return prefix + body[:budget]


def parse_revel_timeline(content: str | None) -> dict[str, Any] | None:
    if not content:
        return None
    text = content.lstrip()
    if not text.startswith(REVEL_MARKER):
        return None
    rest = text[len(REVEL_MARKER) :]
    nl = rest.find("\n")
    if nl < 0:
        header_raw, _body = rest, ""
    else:
        header_raw, _body = rest[:nl], rest[nl + 1 :]
    try:
        header = json.loads(header_raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(header, dict):
        return None
    provider = str(header.get("provider") or SOURCE_REVEL)
    if provider != SOURCE_REVEL:
        return None
    for key in _FORBIDDEN_HEADER_KEYS:
        header.pop(key, None)
    device_name = str(
        header.get("revel_device_name") or header.get("deviceName") or ""
    )
    created = str(header.get("created_at") or header.get("delivered_at") or "")
    auto_raw = header.get("auto_trigger")
    if auto_raw is None:
        auto_raw = header.get("autoTrigger")
    return {
        "provider": provider,
        "event_type": str(header.get("event_type") or REVEL_EVENT_TYPE),
        "type": str(header.get("type") or ""),
        "session_id": str(header.get("session_id") or header.get("sessionId") or ""),
        "intent": str(header.get("intent") or ""),
        "screen": str(header.get("screen") or ""),
        "deviceName": device_name,
        "revel_device_name": device_name,
        "revel_device_id": str(header.get("revel_device_id") or ""),
        "device_key": str(header.get("device_key") or ""),
        "tag": str(header.get("tag") or ""),
        "auto_trigger": _as_bool(auto_raw),
        "control_table_id": str(header.get("control_table_id") or ""),
        "control_row_id": str(header.get("control_row_id") or ""),
        "result": normalize_revel_result(str(header.get("result") or "")),
        "reason": str(header.get("reason") or ""),
        "reason_label": str(header.get("reason_label") or ""),
        "requested": str(header.get("requested") or ""),
        "summary": str(header.get("summary") or header.get("requested") or ""),
        "error": str(header.get("error") or ""),
        "delivered_at": created,
        "created_at": created,
    }


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value) and value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_revel_context_event(parsed: dict[str, Any] | None) -> bool:
    if not parsed:
        return False
    event_type = str(parsed.get("event_type") or "").strip().casefold()
    if event_type == REVEL_CONTEXT_EVENT_TYPE:
        return True
    return str(parsed.get("type") or "").strip().upper() == REVEL_CONTEXT_TYPE


def encode_revel_context_timeline(
    *,
    tag: str,
    auto_trigger: bool,
    delivered_at: str,
    session_id: str | None = None,
    device_name: str | None = None,
    provider: str = SOURCE_REVEL,
) -> str:
    """Pack a historical topic-transition system row. Not a display event."""
    header = {
        "provider": provider or SOURCE_REVEL,
        "event_type": REVEL_CONTEXT_EVENT_TYPE,
        "type": REVEL_CONTEXT_TYPE,
        "session_id": (session_id or "")[:50],
        "tag": (tag or "")[:128],
        "auto_trigger": bool(auto_trigger),
        "deviceName": (device_name or "")[:80],
        "revel_device_name": (device_name or "")[:80],
        "created_at": delivered_at or "",
        "delivered_at": delivered_at or "",
    }
    for key in _FORBIDDEN_HEADER_KEYS:
        header.pop(key, None)
    raw = json.dumps(header, separators=(",", ":"), ensure_ascii=False)
    prefix = f"{REVEL_MARKER}{raw}\n"
    body = "REVEL CONTEXT"
    budget = CONTENT_MAX - len(prefix)
    if budget < 0:
        return (prefix[: CONTENT_MAX - 1] + "\n")[:CONTENT_MAX]
    return prefix + body[:budget]


def should_record_revel_context(previous_tag: str | None, new_tag: str | None) -> bool:
    """True when this session's active revel_tag changed to a non-empty value."""
    nxt = (new_tag or "").strip()
    if not nxt:
        return False
    return (previous_tag or "").strip() != nxt


def resolved_revel_topic(hits: list[Any] | None) -> tuple[str, bool]:
    """First tagged knowledge hit. Does not scan document text."""
    for row in hits or []:
        if not isinstance(row, dict):
            continue
        tag = str(row.get("revelTag") or row.get("revel_tag") or "").strip()
        if tag:
            auto = row.get("revelAutoTrigger")
            if auto is None:
                auto = row.get("revel_auto_trigger")
            return tag[:128], _as_bool(auto)
    return "", False


def calendar_dialogue_line(content: str | None) -> str:
    """Compact line for triage: spoken text only, never the JSON header."""
    parsed = parse_gcal_timeline(content)
    if parsed is not None:
        spoken = (parsed.get("spoken_text") or "").strip()
        title = (parsed.get("title") or "").strip()
        return spoken or title
    return (content or "").strip()


_INJECTION_RE = re.compile(
    r"(?is)"
    r"(ignore\s+(all\s+)?(previous|prior)\s+(instructions|prompts))"
    r"|(you\s+are\s+(now|a))"
    r"|(system\s*prompt)"
    r"|(\[INST\])"
    r"|(\<\|)"
    r"|(`{3})"
)
_ROLE_PREFIX_RE = re.compile(r"(?i)\b(system|assistant|developer)\s*:")
_ASSESSMENT_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|x-reveldigital-apikey|"
    r"x-internal-token|bearer\s+\S+|graphql|mutation\b)"
)
_ASSESSMENT_ID_KEYS = frozenset(
    {
        "revel_device_id",
        "revelDeviceId",
        "control_table_id",
        "controlTableId",
        "control_row_id",
        "controlRowId",
        "device_key",
        "deviceKey",
    }
)


def _sanitize_context_value(raw: Any, *, max_len: int = 80) -> str:
    """Allowlisted field text. Strips injection, secrets, and role prefixes."""
    text = " ".join(str(raw or "").replace("\x00", " ").split())
    if not text:
        return ""
    if _INJECTION_RE.search(text) or _ROLE_PREFIX_RE.search(text):
        return ""
    if _ASSESSMENT_SECRET_RE.search(text):
        return ""
    return text[:max_len]


def revel_topic_assessment_context(
    *,
    tag: str | None,
    auto_trigger: bool | None = False,
    display: str | None = None,
) -> str | None:
    """Discussion metadata for analysis. Not a display event and not an instruction."""
    safe_tag = _sanitize_context_value(tag, max_len=64)
    if not safe_tag:
        return None
    lines = [
        "REVEL_CONTEXT",
        f"tag: {safe_tag}",
        f"auto_trigger: {'true' if auto_trigger else 'false'}",
    ]
    safe_display = _sanitize_context_value(display, max_len=80)
    if safe_display:
        lines.append(f"display: {safe_display}")
    return "\n".join(lines)


def revel_display_name(parsed: dict[str, Any]) -> str:
    """Human display id: mapped player name, else normalized screen. Never invented."""
    name = _sanitize_context_value(
        parsed.get("revel_device_name") or parsed.get("deviceName") or "",
        max_len=80,
    )
    if name:
        return name
    return _sanitize_context_value(parsed.get("screen") or "", max_len=32)


def revel_assessment_context(
    content: str | None,
    *,
    created_at: datetime | None = None,
) -> str | None:
    """Normalized Revel block for Nexus analysis. Historical, not instructions."""
    parsed = parse_revel_timeline(content)
    if parsed is None:
        return None
    if is_revel_context_event(parsed):
        display = revel_display_name(parsed)
        return revel_topic_assessment_context(
            tag=parsed.get("tag"),
            auto_trigger=bool(parsed.get("auto_trigger")),
            display=display or None,
        )
    for key in _ASSESSMENT_ID_KEYS:
        parsed.pop(key, None)
    ts = str(parsed.get("created_at") or parsed.get("delivered_at") or "")
    if not ts and created_at is not None:
        if created_at.tzinfo is None:
            ts = created_at.replace(tzinfo=timezone.utc).isoformat()
        else:
            ts = created_at.isoformat()
    fields = (
        ("timestamp", _sanitize_context_value(ts, max_len=40)),
        ("tag", _sanitize_context_value(parsed.get("tag"), max_len=64)),
        ("display", revel_display_name(parsed)),
        ("intent", _sanitize_context_value(parsed.get("intent"), max_len=64)),
        ("screen", _sanitize_context_value(parsed.get("screen"), max_len=32)),
        ("result", _sanitize_context_value(parsed.get("result"), max_len=16)),
        ("reason", _sanitize_context_value(parsed.get("reason"), max_len=80)),
    )
    lines = ["REVEL_DISPLAY"]
    for key, value in fields:
        if value:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def system_dialogue_line(content: str | None) -> str:
    """Triage line for chat_type=3. Never includes JSON headers or secrets."""
    revel = revel_assessment_context(content)
    if revel is not None:
        return revel
    return calendar_dialogue_line(content)


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


async def persist_revel_timeline(
    db: AsyncSession,
    *,
    agent_id: str,
    requested: str,
    intent: str,
    device_name: str,
    result: str,
    delivered_at: datetime,
    tag: str | None = None,
    device_key: str | None = None,
    revel_device_id: str | None = None,
    screen: str | None = None,
    error: str | None = None,
    summary: str | None = None,
    reason: str | None = None,
    control_table_id: str | None = None,
    control_row_id: str | None = None,
) -> dict[str, Any] | None:
    """Insert one display-action system_event row. Caller commits."""
    if not agent_id:
        return None
    session_id = await latest_session_id(db, agent_id)
    delivered_iso = _iso(delivered_at)
    content = encode_revel_timeline(
        requested=requested,
        intent=intent,
        device_name=device_name,
        result=result,
        delivered_at=delivered_iso,
        tag=tag,
        device_key=device_key,
        revel_device_id=revel_device_id,
        screen=screen,
        error=error,
        summary=summary,
        reason=reason,
        control_table_id=control_table_id,
        control_row_id=control_row_id,
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
        "revel timeline recorded agent=%s intent=%s result=%s chat_type=%s",
        agent_id,
        intent,
        normalize_revel_result(result),
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


async def last_session_revel_context_tag(
    db: AsyncSession,
    *,
    agent_id: str,
    session_id: str,
) -> str | None:
    """Most recent persisted REVEL CONTEXT tag for this session only."""
    if not agent_id or not session_id:
        return None
    rows = (
        await db.execute(
            select(AiAgentChatHistory)
            .where(
                AiAgentChatHistory.agent_id == agent_id,
                AiAgentChatHistory.session_id == session_id,
                AiAgentChatHistory.chat_type == CHAT_TYPE_SYSTEM,
            )
            .order_by(AiAgentChatHistory.id.desc())
            .limit(80)
        )
    ).scalars().all()
    for row in rows:
        parsed = parse_revel_timeline(row.content)
        if not is_revel_context_event(parsed):
            continue
        tag = str((parsed or {}).get("tag") or "").strip()
        if tag:
            return tag
    return None


async def persist_revel_context_event(
    db: AsyncSession,
    *,
    agent_id: str,
    session_id: str,
    tag: str,
    auto_trigger: bool,
    delivered_at: datetime,
    device_name: str | None = None,
) -> dict[str, Any] | None:
    """Insert one topic-transition system_event. Caller commits. No Revel write."""
    if not agent_id or not tag.strip():
        return None
    sid = (session_id or "").strip()[:50] or await latest_session_id(db, agent_id)
    delivered_iso = _iso(delivered_at)
    content = encode_revel_context_timeline(
        tag=tag.strip(),
        auto_trigger=bool(auto_trigger),
        delivered_at=delivered_iso,
        session_id=sid,
        device_name=device_name,
    )
    stamp = _naive_local(delivered_at)
    fields: dict[str, Any] = {
        "mac_address": None,
        "agent_id": agent_id,
        "session_id": sid,
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
        "revel context recorded agent=%s session=%s tag=%s chat_type=%s",
        agent_id,
        sid,
        tag.strip(),
        CHAT_TYPE_SYSTEM,
    )
    return {
        "id": row.id,
        "agentId": agent_id,
        "sessionId": sid,
        "chatType": CHAT_TYPE_SYSTEM,
        "content": content,
        "macAddress": "",
        "createdAt": created_ms,
    }


async def persist_revel_context_if_changed(
    db: AsyncSession,
    *,
    agent_id: str,
    session_id: str | None,
    tag: str | None,
    auto_trigger: bool = False,
    device_name: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any] | None:
    """Record a session-scoped topic transition when the revel_tag changes."""
    nxt = (tag or "").strip()
    if not agent_id or not nxt:
        return None
    sid = (session_id or "").strip()[:50] or await latest_session_id(db, agent_id)
    previous = await last_session_revel_context_tag(
        db, agent_id=agent_id, session_id=sid
    )
    if not should_record_revel_context(previous, nxt):
        return None
    when = at or datetime.now(timezone.utc)
    return await persist_revel_context_event(
        db,
        agent_id=agent_id,
        session_id=sid,
        tag=nxt,
        auto_trigger=bool(auto_trigger),
        delivered_at=when,
        device_name=device_name,
    )


async def record_revel_context_transition(
    db: AsyncSession,
    *,
    agent_id: str,
    session_id: str | None,
    tag: str | None,
    auto_trigger: bool = False,
    device_name: str | None = None,
) -> dict[str, Any] | None:
    """Commit a context transition if the session tag changed. Fail-open."""
    try:
        payload = await persist_revel_context_if_changed(
            db,
            agent_id=agent_id,
            session_id=session_id,
            tag=tag,
            auto_trigger=auto_trigger,
            device_name=device_name,
        )
        if payload is None:
            return None
        await db.commit()
        await notify_timeline_best_effort(payload)
        return payload
    except Exception:
        log.warning("revel context persist failed agent=%s", agent_id, exc_info=True)
        try:
            await db.rollback()
        except Exception:
            pass
        return None


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
