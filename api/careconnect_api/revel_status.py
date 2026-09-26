"""Read-only Revel observability for a client discussion.

Status text and last-event results are derived from stored configuration and
system timeline rows. Callers cannot supply GraphQL, command names, or results.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .chat_events import (
    CHAT_TYPE_SYSTEM,
    SOURCE_REVEL,
    parse_revel_timeline,
    persist_revel_timeline,
    notify_timeline_best_effort,
    normalize_revel_result,
)
from .knowledge import slugify
from .models import (
    AiAgentChatHistory,
    ClientIntegration,
    ClientKnowledgeBase,
    KnowledgeBase,
    KnowledgeTopic,
)
from .revel_client import _safe_revel_error
from .revel_config import load_meta
from .revel_player_map import get_player_map, list_player_maps
from .revel_write import revel_execute_enabled

log = logging.getLogger("revel_status")

PROVIDER_REVEL = "revel"

_SECRET_KEYS = (
    "apiKey",
    "api_key",
    "secret",
    "secret_enc",
    "registrationKey",
    "registration_key",
    "token",
    "X-RevelDigital-ApiKey",
    "X-Internal-Token",
)
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _tag(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:128]


def device_key_for(device_name: str | None, device_id: str | None = None) -> str | None:
    """Stable key from the configured display name. Does not invent a player."""
    slug = slugify(device_name or "", max_len=128)
    if slug:
        return slug
    did = (device_id or "").strip()
    if did and _DEVICE_ID_RE.match(did):
        return did
    return None


def header_text(
    *,
    mode: str,
    tag: str | None,
    device_name: str | None,
) -> str:
    """Server-generated badge copy. The UI must display this string as-is."""
    if mode == "enabled":
        return (
            f"Revel: ENABLED | Tag: {tag or '—'} | Display: {device_name or '—'}"
        )
    if mode == "manual":
        return f"Revel: MANUAL | Tag: {tag or '—'}"
    return "Revel: OFF"


def _player_status(meta: dict[str, Any], device_id: str | None) -> str:
    if not device_id:
        return "unknown"
    for raw in meta.get("discoveredDevices") or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("id") or "").strip() != device_id:
            continue
        online = raw.get("isOnline")
        if online is True:
            return "online"
        if online is False:
            return "offline"
        return "unknown"
    return "unknown"


def _public_event(parsed: dict[str, Any] | None) -> dict[str, Any] | None:
    if not parsed:
        return None
    error = _safe_revel_error(parsed.get("error") or "") or None
    out = {
        "eventType": parsed.get("event_type") or "revel_display",
        "tag": parsed.get("tag") or None,
        "deviceKey": parsed.get("device_key") or None,
        "revelDeviceId": parsed.get("revel_device_id") or None,
        "revelDeviceName": parsed.get("revel_device_name") or parsed.get("deviceName") or None,
        "intent": parsed.get("intent") or None,
        "screen": parsed.get("screen") or None,
        "controlTableId": parsed.get("control_table_id") or None,
        "controlRowId": parsed.get("control_row_id") or None,
        "result": normalize_revel_result(str(parsed.get("result") or "")),
        "reason": parsed.get("reason") or None,
        "error": error,
        "summary": (parsed.get("summary") or parsed.get("requested") or None) or None,
        "createdAt": parsed.get("created_at") or parsed.get("delivered_at") or None,
    }
    for key in _SECRET_KEYS:
        out.pop(key, None)
    if out.get("revelDeviceId") == "":
        out["revelDeviceId"] = None
    if out.get("deviceKey") == "":
        out["deviceKey"] = None
    if out.get("tag") == "":
        out["tag"] = None
    if out.get("screen") == "":
        out["screen"] = None
    if out.get("reason") == "":
        out["reason"] = None
    if out.get("controlTableId") == "":
        out["controlTableId"] = None
    if out.get("controlRowId") == "":
        out["controlRowId"] = None
    return out


def _strip_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip_secrets(v)
            for k, v in value.items()
            if str(k) not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    return value


async def _assigned_topics(
    db: AsyncSession, agent_id: str
) -> list[KnowledgeTopic]:
    rows = (
        await db.execute(
            select(KnowledgeTopic)
            .join(KnowledgeBase, KnowledgeBase.id == KnowledgeTopic.knowledge_base_id)
            .join(
                ClientKnowledgeBase,
                ClientKnowledgeBase.knowledge_base_id == KnowledgeBase.id,
            )
            .where(
                ClientKnowledgeBase.agent_id == agent_id,
                ClientKnowledgeBase.enabled == 1,
                KnowledgeBase.enabled == 1,
                KnowledgeTopic.enabled == 1,
            )
            .order_by(KnowledgeTopic.sort_order.asc(), KnowledgeTopic.id.asc())
        )
    ).scalars().all()
    return list(rows)


async def latest_revel_event(
    db: AsyncSession, agent_id: str
) -> dict[str, Any] | None:
    """Newest system_event Revel row. Client/caregiver text cannot spoof this."""
    rows = (
        await db.execute(
            select(AiAgentChatHistory)
            .where(
                AiAgentChatHistory.agent_id == agent_id,
                AiAgentChatHistory.chat_type == CHAT_TYPE_SYSTEM,
            )
            .order_by(AiAgentChatHistory.id.desc())
            .limit(50)
        )
    ).scalars().all()
    for row in rows:
        parsed = parse_revel_timeline(row.content)
        if parsed is None:
            continue
        public = _public_event(parsed)
        if public and not public.get("createdAt") and row.created_at is not None:
            stamp = row.created_at
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            public["createdAt"] = stamp.isoformat()
        return public
    return None


def _pick_topic(
    topics: list[KnowledgeTopic],
    *,
    topic_id: int | None,
    last_tag: str | None,
) -> KnowledgeTopic | None:
    if topic_id is not None:
        for topic in topics:
            if int(topic.id) == int(topic_id):
                return topic
        return None
    tagged = [t for t in topics if _tag(t.revel_tag)]
    if last_tag:
        for topic in tagged:
            if _tag(topic.revel_tag) == last_tag and topic.revel_auto_trigger:
                return topic
        for topic in tagged:
            if _tag(topic.revel_tag) == last_tag:
                return topic
    auto = [t for t in tagged if t.revel_auto_trigger]
    if auto:
        return auto[0]
    if tagged:
        return tagged[0]
    return None


async def revel_status_for_agent(
    db: AsyncSession,
    agent_id: str,
    *,
    topic_id: int | None = None,
) -> dict[str, Any]:
    """Normalized discussion Revel status. Never includes API keys."""
    row = (
        await db.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == PROVIDER_REVEL,
            )
        )
    ).scalar_one_or_none()
    connected = row is not None and (row.status or "") == "connected"
    meta = load_meta(row) if row is not None else {}
    device_id = str(meta.get("deviceId") or "").strip() or None
    if device_id and not _DEVICE_ID_RE.match(device_id):
        device_id = None
    device_name = str(meta.get("deviceName") or "").strip() or None
    mapped = None
    if device_id:
        mapped = await get_player_map(db, agent_id, revel_device_id=device_id)
    if mapped is None:
        maps = await list_player_maps(db, agent_id)
        if len(maps) == 1:
            mapped = maps[0]
    if mapped is not None:
        device_id = mapped.revel_device_id
        device_name = mapped.revel_device_name or device_name
        device_key = mapped.device_key
    else:
        device_key = device_key_for(device_name, device_id)

    last_event = await latest_revel_event(db, agent_id)
    topics = await _assigned_topics(db, agent_id)
    topic = _pick_topic(
        topics,
        topic_id=topic_id,
        last_tag=_tag((last_event or {}).get("tag")),
    )
    tag = _tag(topic.revel_tag) if topic is not None else ""
    auto_trigger = bool(topic.revel_auto_trigger) if topic is not None else False

    if not connected:
        mode = "off"
    elif not tag:
        mode = "off"
    elif auto_trigger:
        mode = "enabled"
    else:
        mode = "manual"

    device = None
    if device_id or device_name:
        device = {
            "id": device_id,
            "name": device_name,
            "status": _player_status(meta, device_id) if device_id else "unknown",
        }

    payload = {
        "ok": True,
        "enabled": mode == "enabled",
        "mode": mode,
        "autoTrigger": auto_trigger,
        "tag": tag or None,
        "deviceKey": device_key,
        "device": device,
        "lastEvent": last_event,
        "header": header_text(mode=mode, tag=tag or None, device_name=device_name),
        "provider": SOURCE_REVEL,
        "revelExecuteEnabled": revel_execute_enabled(),
    }
    log.info(
        "revel status agent=%s mode=%s tag_set=%s auto_trigger=%s device_set=%s "
        "player_status=%s last_result=%s",
        agent_id,
        mode,
        bool(tag),
        auto_trigger,
        bool(device_id),
        (device or {}).get("status"),
        (last_event or {}).get("result"),
    )
    return _strip_secrets(payload)


def result_for_attempt(*, executed: bool, reason: str | None) -> str:
    if executed:
        return "sent"
    why = (reason or "").strip()
    if why in {"feature_disabled", "auto_trigger_false", "action_disabled"}:
        return "disabled"
    if why in {
        "unmapped_player",
        "revel_failed",
        "control_row_not_found",
        "ambiguous_control_row",
        "missing_control_columns",
        "control_table_not_configured",
        "timeout",
        "http_error",
        "malformed_response",
    } or "fail" in why.casefold():
        return "failed"
    return "skipped"


async def record_revel_attempt(
    db: AsyncSession,
    *,
    agent_id: str,
    intent: str | None,
    tag: str | None,
    device_id: str | None = None,
    device_name: str | None = None,
    requested: str | None = None,
    executed: bool = False,
    reason: str | None = None,
    error: str | None = None,
    device_key: str | None = None,
    screen: str | None = None,
) -> dict[str, Any] | None:
    """Persist a system timeline event for a real backend attempt. Fail-open."""
    if not agent_id:
        return None
    result = result_for_attempt(executed=executed, reason=reason)
    safe_error = _safe_revel_error(error) if result == "failed" else ""
    stored_key = device_key
    if not stored_key and device_id:
        mapped = await get_player_map(db, agent_id, revel_device_id=device_id)
        if mapped is not None:
            stored_key = mapped.device_key
            device_name = device_name or mapped.revel_device_name
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
            device_key=stored_key or device_key_for(device_name, device_id),
            revel_device_id=device_id,
            screen=screen,
            error=safe_error or None,
            summary=requested or None,
        )
        await db.commit()
        await notify_timeline_best_effort(payload)
        return payload
    except Exception:
        log.warning("revel timeline persist failed agent=%s", agent_id, exc_info=True)
        try:
            await db.rollback()
        except Exception:
            pass
        return None
