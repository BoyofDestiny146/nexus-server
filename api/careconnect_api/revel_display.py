"""Phase 2C controlled Revel display state + skipped/live write path.

``REVEL_EXECUTE_ENABLED`` (not the keyword ``EXECUTE_ENABLED`` flag) gates
the REST PUT. Default is false: the payload is built and a SKIPPED timeline
row is recorded, with zero Revel writes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from .chat_events import persist_revel_timeline, notify_timeline_best_effort
from .envelope import APIException
from .models import ClientIntegration
from .revel_client import RevelMutationDisabled, _safe_revel_error
from .revel_config import load_meta
from .revel_player_map import cache_control_row, resolve_player_map
from .revel_status import result_for_attempt
from .revel_write import (
    bind_column_keys,
    build_update_payload,
    configured_control_table_id,
    fetch_control_table,
    revel_execute_enabled,
    revel_puts_attempted,
    select_control_row,
    update_data_table_row,
    write_plan,
)

log = logging.getLogger("revel_display")

PROVIDER_REVEL = "revel"

SHOW_HOME = "SHOW_HOME"
SHOW_APPOINTMENT_REMINDER = "SHOW_APPOINTMENT_REMINDER"
SHOW_MEDICATION_REMINDER = "SHOW_MEDICATION_REMINDER"
SHOW_CARE_ALERT = "SHOW_CARE_ALERT"
SHOW_SENSOR_ALERT = "SHOW_SENSOR_ALERT"
RETURN_HOME = "RETURN_HOME"

DISPLAY_INTENTS = (
    SHOW_HOME,
    SHOW_APPOINTMENT_REMINDER,
    SHOW_MEDICATION_REMINDER,
    SHOW_CARE_ALERT,
    SHOW_SENSOR_ALERT,
    RETURN_HOME,
)

SCREEN_HOME = "home"
SCREEN_APPOINTMENT = "appointment"
SCREEN_MEDICATION = "medication"
SCREEN_CARE_ALERT = "care_alert"
SCREEN_SENSOR_ALERT = "sensor_alert"

ALLOWED_SCREENS = (
    SCREEN_HOME,
    SCREEN_APPOINTMENT,
    SCREEN_MEDICATION,
    SCREEN_CARE_ALERT,
    SCREEN_SENSOR_ALERT,
)

INTENT_TO_SCREEN: dict[str, str] = {
    SHOW_HOME: SCREEN_HOME,
    RETURN_HOME: SCREEN_HOME,
    SHOW_APPOINTMENT_REMINDER: SCREEN_APPOINTMENT,
    SHOW_MEDICATION_REMINDER: SCREEN_MEDICATION,
    SHOW_CARE_ALERT: SCREEN_CARE_ALERT,
    SHOW_SENSOR_ALERT: SCREEN_SENSOR_ALERT,
}

_DEFAULT_TITLES: dict[str, str] = {
    SHOW_HOME: "Home",
    RETURN_HOME: "Home",
    SHOW_APPOINTMENT_REMINDER: "Upcoming Appointment",
    SHOW_MEDICATION_REMINDER: "Medication Reminder",
    SHOW_CARE_ALERT: "Care Alert",
    SHOW_SENSOR_ALERT: "Sensor Alert",
}

TITLE_MAX = 120
MESSAGE_MAX = 500
IMAGE_URL_MAX = 2048
PRIORITY_MIN = 0
PRIORITY_MAX = 100
DEFAULT_PRIORITY = 50
EXPIRES_MAX_DAYS = 7
ALLOWED_SOURCES = frozenset(
    {"calendar", "knowledge", "voice", "internal", "caregiver", "sensor"}
)
REASON_EXECUTE_DISABLED = "Revel execution disabled"

_FORBIDDEN_REQUEST_KEYS = frozenset(
    {
        "screen",
        "revelDeviceId",
        "revel_device_id",
        "deviceId",
        "device_id",
        "graphql",
        "query",
        "mutation",
        "command",
        "commands",
        "apiKey",
        "api_key",
        "tableId",
        "table_id",
        "controlTableId",
        "rowId",
        "row_id",
        "controlRowId",
        "data",
        "fields",
        "executeEnabled",
        "revelExecuteEnabled",
        "eTag",
        "If-Match",
    }
)


def screen_for_intent(intent: str) -> str:
    """Map a V1 display intent onto an allowlisted screen. Raises on unknown."""
    key = (intent or "").strip()
    screen = INTENT_TO_SCREEN.get(key)
    if screen is None:
        raise APIException(400, "unsupported Revel display intent")
    return screen


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise APIException(400, "expiresAt must be a valid ISO-8601 timestamp") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def validate_image_url(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if len(text) > IMAGE_URL_MAX:
        raise APIException(400, "imageUrl is too long")
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise APIException(400, "imageUrl must be an https URL without credentials")
    host = (parsed.hostname or "").casefold()
    if host in {"localhost", "127.0.0.1", "::1"}:
        raise APIException(400, "imageUrl must be an https URL without credentials")
    return text


def validate_expiration(raw: str | None, *, now: datetime | None = None) -> str | None:
    if raw is None or not str(raw).strip():
        return None
    dt = _parse_iso(str(raw))
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    if dt <= stamp:
        raise APIException(400, "expiresAt must be in the future")
    if dt > stamp + timedelta(days=EXPIRES_MAX_DAYS):
        raise APIException(400, "expiresAt is too far in the future")
    return _iso(dt)


def validate_source(raw: str | None) -> str:
    text = (raw or "internal").strip().casefold()
    if text not in ALLOWED_SOURCES:
        raise APIException(400, "unsupported display source")
    return text


def _validate_priority(raw: Any) -> int:
    if raw is None:
        return DEFAULT_PRIORITY
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise APIException(400, "priority must be an integer") from exc
    if value < PRIORITY_MIN or value > PRIORITY_MAX:
        raise APIException(400, "priority must be between 0 and 100")
    return value


def reject_forbidden_fields(payload: dict[str, Any] | None) -> None:
    if not payload:
        return
    for key in payload:
        if key in _FORBIDDEN_REQUEST_KEYS:
            raise APIException(400, f"{key} cannot be supplied by the caller")


def build_display_state(
    *,
    device_key: str,
    revel_device_id: str,
    intent: str,
    title: str | None = None,
    message: str | None = None,
    image_url: str | None = None,
    priority: Any = None,
    expires_at: str | None = None,
    source: str | None = None,
    created_at: datetime | None = None,
    caller_screen: str | None = None,
) -> dict[str, Any]:
    """Normalize and validate. Screen is derived from intent only."""
    screen = screen_for_intent(intent)
    if caller_screen is not None and str(caller_screen).strip() != screen:
        raise APIException(400, "screen is derived from intent and cannot be overridden")
    heading = (title if title is not None else _DEFAULT_TITLES[intent]).strip()
    if not heading:
        raise APIException(400, "title is required")
    if len(heading) > TITLE_MAX:
        raise APIException(400, "title is too long")
    body = (message or "").strip()
    if len(body) > MESSAGE_MAX:
        raise APIException(400, "message is too long")
    created = created_at or datetime.now(timezone.utc)
    return {
        "deviceKey": device_key,
        "revelDeviceId": revel_device_id,
        "intent": intent,
        "screen": screen,
        "title": heading,
        "message": body,
        "imageUrl": validate_image_url(image_url),
        "priority": _validate_priority(priority),
        "expiresAt": validate_expiration(expires_at, now=created),
        "createdAt": _iso(created),
        "source": validate_source(source),
    }


async def _connected_revel(db: AsyncSession, agent_id: str) -> ClientIntegration | None:
    from sqlalchemy import select

    return (
        await db.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == PROVIDER_REVEL,
            )
        )
    ).scalar_one_or_none()


def _outcome(
    *,
    result: str,
    reason: str,
    state: dict[str, Any] | None,
    plan: dict[str, Any] | None = None,
    table_id: str | None = None,
    row_id: str | None = None,
    executed: bool = False,
    missing: list[str] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": result in {"skipped", "sent"},
        "executed": executed,
        "executeEnabled": revel_execute_enabled(),
        "result": result,
        "reason": reason,
        "displayState": state,
        "controlTableId": table_id,
        "controlRowId": row_id,
        "write": plan,
    }
    if missing:
        out["missingColumns"] = missing
    return out


async def apply_display_state(
    db: AsyncSession,
    *,
    agent_id: str,
    intent: str,
    device_key: str | None = None,
    title: str | None = None,
    message: str | None = None,
    image_url: str | None = None,
    priority: Any = None,
    expires_at: str | None = None,
    source: str | None = None,
    caller_screen: str | None = None,
    tag: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate, resolve player/table/row, build PUT body, write only if enabled."""
    reject_forbidden_fields(extra)
    screen = screen_for_intent(intent)
    if caller_screen is not None and str(caller_screen).strip() and str(caller_screen).strip() != screen:
        raise APIException(400, "screen is derived from intent and cannot be overridden")

    heading = (title if title is not None else _DEFAULT_TITLES.get(intent, "")).strip()
    if heading and len(heading) > TITLE_MAX:
        raise APIException(400, "title is too long")
    if message is not None and len(message.strip()) > MESSAGE_MAX:
        raise APIException(400, "message is too long")
    validate_image_url(image_url)
    validate_expiration(expires_at)
    validate_source(source)
    _validate_priority(priority)

    row = await _connected_revel(db, agent_id)
    if row is None or (row.status or "") != "connected":
        raise APIException(400, "Revel integration is not connected")
    meta = load_meta(row)
    selected_id = str(meta.get("deviceId") or "").strip() or None
    mapped = await resolve_player_map(
        db,
        agent_id,
        device_key=device_key,
        selected_device_id=selected_id,
    )
    if mapped is None:
        await _record(
            db,
            agent_id=agent_id,
            intent=intent,
            screen=screen,
            device_key=device_key,
            device_name=str(meta.get("deviceName") or "") or None,
            revel_device_id=None,
            tag=tag,
            requested=heading or intent,
            executed=False,
            reason="unmapped_player",
        )
        return _outcome(result="failed", reason="unmapped_player", state=None)

    state = build_display_state(
        device_key=mapped.device_key,
        revel_device_id=mapped.revel_device_id,
        intent=intent,
        title=title,
        message=message,
        image_url=image_url,
        priority=priority,
        expires_at=expires_at,
        source=source,
        caller_screen=caller_screen,
    )

    table_id = configured_control_table_id()
    if not table_id:
        await _record(
            db,
            agent_id=agent_id,
            intent=state["intent"],
            screen=state["screen"],
            device_key=state["deviceKey"],
            device_name=mapped.revel_device_name,
            revel_device_id=state["revelDeviceId"],
            tag=tag,
            requested=state["title"] or intent,
            executed=False,
            reason="control_table_not_configured",
        )
        return _outcome(
            result="failed",
            reason="control_table_not_configured",
            state=state,
        )

    try:
        fetched = await fetch_control_table()
    except APIException as exc:
        await _record(
            db,
            agent_id=agent_id,
            intent=state["intent"],
            screen=state["screen"],
            device_key=state["deviceKey"],
            device_name=mapped.revel_device_name,
            revel_device_id=state["revelDeviceId"],
            tag=tag,
            requested=state["title"] or intent,
            executed=False,
            reason="revel_failed",
            error=str(exc.msg),
            control_table_id=table_id,
        )
        return _outcome(result="failed", reason="revel_failed", state=state, table_id=table_id)

    table = fetched.get("table") if isinstance(fetched.get("table"), dict) else {}
    binding = bind_column_keys(table.get("columns") if isinstance(table, dict) else [])
    missing = list(binding.get("missingRequired") or [])
    if missing:
        await _record(
            db,
            agent_id=agent_id,
            intent=state["intent"],
            screen=state["screen"],
            device_key=state["deviceKey"],
            device_name=mapped.revel_device_name,
            revel_device_id=state["revelDeviceId"],
            tag=tag,
            requested=state["title"] or intent,
            executed=False,
            reason="missing_control_columns",
            control_table_id=table_id,
        )
        return _outcome(
            result="failed",
            reason="missing_control_columns",
            state=state,
            table_id=table_id,
            missing=missing,
        )

    device_col = binding["mapping"]["device_key"]
    picked = select_control_row(
        fetched.get("rows") if isinstance(fetched.get("rows"), list) else [],
        device_key=state["deviceKey"],
        device_key_column=device_col,
    )
    if not picked.get("ok"):
        reason = str(picked.get("reason") or "control_row_not_found")
        await _record(
            db,
            agent_id=agent_id,
            intent=state["intent"],
            screen=state["screen"],
            device_key=state["deviceKey"],
            device_name=mapped.revel_device_name,
            revel_device_id=state["revelDeviceId"],
            tag=tag,
            requested=state["title"] or intent,
            executed=False,
            reason=reason,
            control_table_id=table_id,
        )
        return _outcome(result="failed", reason=reason, state=state, table_id=table_id)

    control_row = picked["row"]
    row_id = str(control_row.get("id") or "")
    payload = build_update_payload(display_state=state, binding=binding)
    plan = write_plan(table_id=table_id, row_id=row_id, payload=payload)
    await cache_control_row(db, mapped, table_id=table_id, row_id=row_id)

    if not revel_execute_enabled():
        await _record(
            db,
            agent_id=agent_id,
            intent=state["intent"],
            screen=state["screen"],
            device_key=state["deviceKey"],
            device_name=mapped.revel_device_name,
            revel_device_id=state["revelDeviceId"],
            tag=tag,
            requested=state["title"] or state["message"] or intent,
            executed=False,
            reason=REASON_EXECUTE_DISABLED,
            control_table_id=table_id,
            control_row_id=row_id,
        )
        log.info(
            "revel display skipped agent=%s intent=%s screen=%s table=%s row=%s puts=%s",
            agent_id,
            state["intent"],
            state["screen"],
            table_id,
            row_id,
            revel_puts_attempted,
        )
        return _outcome(
            result="skipped",
            reason=REASON_EXECUTE_DISABLED,
            state=state,
            plan=plan,
            table_id=table_id,
            row_id=row_id,
        )

    try:
        await update_data_table_row(table_id=table_id, row_id=row_id, payload=payload)
    except RevelMutationDisabled:
        await _record(
            db,
            agent_id=agent_id,
            intent=state["intent"],
            screen=state["screen"],
            device_key=state["deviceKey"],
            device_name=mapped.revel_device_name,
            revel_device_id=state["revelDeviceId"],
            tag=tag,
            requested=state["title"] or intent,
            executed=False,
            reason=REASON_EXECUTE_DISABLED,
            control_table_id=table_id,
            control_row_id=row_id,
        )
        return _outcome(
            result="skipped",
            reason=REASON_EXECUTE_DISABLED,
            state=state,
            plan=plan,
            table_id=table_id,
            row_id=row_id,
        )
    except APIException as exc:
        why = "revel_failed"
        data = exc.data if isinstance(exc.data, dict) else {}
        if data.get("reason") == "timeout":
            why = "timeout"
        elif data.get("reason") == "malformed":
            why = "malformed_response"
        elif data.get("reason") == "http_error":
            why = "http_error"
        await _record(
            db,
            agent_id=agent_id,
            intent=state["intent"],
            screen=state["screen"],
            device_key=state["deviceKey"],
            device_name=mapped.revel_device_name,
            revel_device_id=state["revelDeviceId"],
            tag=tag,
            requested=state["title"] or intent,
            executed=False,
            reason=why,
            error=str(exc.msg),
            control_table_id=table_id,
            control_row_id=row_id,
        )
        return _outcome(
            result="failed",
            reason=why,
            state=state,
            plan=plan,
            table_id=table_id,
            row_id=row_id,
        )

    await _record(
        db,
        agent_id=agent_id,
        intent=state["intent"],
        screen=state["screen"],
        device_key=state["deviceKey"],
        device_name=mapped.revel_device_name,
        revel_device_id=state["revelDeviceId"],
        tag=tag,
        requested=state["title"] or intent,
        executed=True,
        reason=None,
        control_table_id=table_id,
        control_row_id=row_id,
    )
    log.info(
        "revel display sent agent=%s intent=%s screen=%s table=%s row=%s puts=%s",
        agent_id,
        state["intent"],
        state["screen"],
        table_id,
        row_id,
        revel_puts_attempted,
    )
    return _outcome(
        result="sent",
        reason="sent",
        state=state,
        plan=plan,
        table_id=table_id,
        row_id=row_id,
        executed=True,
    )


async def _record(
    db: AsyncSession,
    *,
    agent_id: str,
    intent: str,
    screen: str,
    device_key: str | None,
    device_name: str | None,
    revel_device_id: str | None,
    tag: str | None,
    requested: str,
    executed: bool,
    reason: str | None,
    error: str | None = None,
    control_table_id: str | None = None,
    control_row_id: str | None = None,
) -> None:
    result = result_for_attempt(executed=executed, reason=reason)
    safe_error = _safe_revel_error(error) if result == "failed" else ""
    try:
        payload = await persist_revel_timeline(
            db,
            agent_id=agent_id,
            requested=requested or "",
            intent=intent or "",
            device_name=device_name or "",
            result=result,
            delivered_at=datetime.now(timezone.utc),
            tag=tag,
            device_key=device_key,
            revel_device_id=revel_device_id,
            screen=screen,
            error=safe_error or None,
            summary=requested or None,
            reason=reason,
            control_table_id=control_table_id,
            control_row_id=control_row_id,
        )
        await db.commit()
        await notify_timeline_best_effort(payload)
    except Exception:
        log.warning("revel display timeline persist failed agent=%s", agent_id, exc_info=True)
        try:
            await db.rollback()
        except Exception:
            pass
