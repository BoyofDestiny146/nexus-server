"""Internal endpoints — called by other careconnect services (the bridge),
not by browser clients.

Auth is a shared secret in the ``X-Internal-Token`` header that matches
``settings.internal_token`` (auto-generated to
``~/.config/careconnect/api-internal-token`` on first API boot, mode 0600).
The bridge reads the same file. Anything mounted under ``/api/internal/``
should require this header — these endpoints will end up exposed on the
same host:port as the dashboard API, and we don't want a stray fetch from
the browser to be able to fan out fake chat turns.

Endpoints
---------

* ``POST /api/internal/notify/chat-turn`` — bridge → API hand-off after each
  ``ai_agent_chat_history`` insert. Validates the token, normalizes the
  payload, publishes onto the agent's Redis chat channel. Fire-and-forget
  from the bridge's POV (it uses a 500ms timeout), so we keep the work here
  trivial — no DB writes, no LLM calls.
* ``GET /api/internal/revel/devices`` — Nexus-system Revel discovery
  (read-only GraphQL device list). Requires ``X-Internal-Token``. Does not
  accept GraphQL from the caller and does not write to Revel.
* ``GET /api/internal/revel/datatables`` — read-only Data Table list.
* ``GET /api/internal/revel/datatables/{id}`` — read-only table schema + rows.
* ``POST /api/internal/revel/display`` — validate a V1 display-state intent,
  resolve the stored player mapping, and record a skipped timeline event
  while ``EXECUTE_ENABLED`` is false. Does not write to Revel.
* ``GET /api/internal/revel/status/{agent_id}`` — read-only Revel observability
  for a client discussion. Same payload as ``GET /api/agent/{id}/revel/status``.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..envelope import APIException
from ..knowledge_retrieval import device_knowledge_search
from ..knowledge_revel import evaluate_knowledge_revel
from ..pubsub import publish_chat_turn
from ..revel_command import evaluate_voice_command
from ..revel_datatables import get_data_table, list_data_tables
from ..revel_display import apply_display_state
from ..revel_signage import list_signage_devices
from ..revel_status import revel_status_for_agent
from ..settings import settings


log = logging.getLogger("internal")

router = APIRouter(prefix="/internal", tags=["internal"])


# ---------- auth dep ----------

async def require_internal_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """Constant-time-ish header check against ``settings.internal_token``.

    ``settings.internal_token`` is auto-created on first read, so this
    dependency also doubles as a "make sure the secret file exists" gate at
    request time (in addition to the eager read in ``main.lifespan``).
    """
    expected = settings.internal_token
    if not x_internal_token or x_internal_token != expected:
        raise APIException(401, "invalid or missing X-Internal-Token")


# ---------- request model ----------

class ChatTurnNotify(BaseModel):
    """Mirror of the bridge's ``insert_chat_turn`` arguments. ``createdAt``
    is optional — if the bridge doesn't supply one we stamp it server-side
    so subscribers always see a millisecond timestamp."""

    agentId: str = Field(min_length=1, max_length=64)
    sessionId: str = Field(min_length=1, max_length=64)
    chatType: int = Field(ge=1, le=3)  # 1=client, 2=caregiver, 3=system_event
    content: str = Field(min_length=1)
    macAddress: str = Field(default="", max_length=64)
    createdAt: int | None = None  # epoch ms; filled in if absent
    id: int | None = None  # DB row id so the live WS push dedups vs polled rows


# ---------- endpoints ----------

@router.post("/notify/chat-turn", response_model=None, dependencies=[Depends(require_internal_token)])
async def notify_chat_turn(payload: ChatTurnNotify) -> dict[str, Any]:
    """Publish a chat-turn envelope onto ``cc:agent:{agentId}:chat``.

    Returned ``subscribers`` is the count from Redis ``PUBLISH`` (0 when no
    dashboard tab is currently open against the agent — normal). The
    bridge ignores this; it's surfaced for ad-hoc curl smoke tests.
    """
    # Truncate defensively — DB column is varchar(1024) and the bridge
    # already truncates before insert, but we don't want a misbehaving
    # caller to push a 50 MB string through pubsub.
    content = payload.content[:1024]

    created_at = payload.createdAt if payload.createdAt is not None else int(time.time() * 1000)
    msg_payload = {
        "agentId": payload.agentId,
        "sessionId": payload.sessionId,
        "chatType": payload.chatType,
        "content": content,
        "macAddress": payload.macAddress,
        "createdAt": created_at,
        "id": payload.id,
    }

    try:
        subscribers = await publish_chat_turn(payload.agentId, msg_payload)
    except Exception as exc:
        # Pub/sub is best-effort — log and surface a 500-ish envelope so the
        # bridge's warning log captures the failure.
        log.exception("publish_chat_turn failed for agent=%s", payload.agentId)
        raise APIException(500, f"publish failed: {exc}") from exc

    log.info(
        "notify chat-turn agent=%s session=%s type=%d subs=%d len=%d",
        payload.agentId, payload.sessionId, payload.chatType, subscribers, len(content),
    )
    return {"published": True, "subscribers": int(subscribers)}


class RevelCommandIn(BaseModel):
    agentId: str = Field(min_length=1, max_length=64)
    utterance: str = Field(default="", max_length=1024)
    remainder: str = Field(min_length=1, max_length=1024)


@router.get("/revel/devices", response_model=None, dependencies=[Depends(require_internal_token)])
async def revel_devices() -> dict[str, Any]:
    """Phase 1 read-only Revel player discovery.

    Uses the allowlisted GraphQL ``device`` query and the Nexus-system API key
    from ``REVEL_API_KEY_FILE``. Callers cannot supply GraphQL, command names,
    or device mutations.
    """
    devices = await list_signage_devices()
    return {"ok": True, "devices": devices}


@router.get(
    "/revel/datatables",
    response_model=None,
    dependencies=[Depends(require_internal_token)],
)
async def revel_datatables(
    pageSize: int | None = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Read-only Data Table list. Does not mutate Revel."""
    return await list_data_tables(page_size=pageSize)


@router.get(
    "/revel/datatables/{table_id}",
    response_model=None,
    dependencies=[Depends(require_internal_token)],
)
async def revel_datatable(
    table_id: str,
    pageSize: int | None = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    """Read-only table definition + rows. Does not mutate Revel."""
    return await get_data_table(table_id, page_size=pageSize)


class RevelDisplayIn(BaseModel):
    """Internal display-state request. Screen and Revel device id are server-side."""

    model_config = ConfigDict(extra="forbid")

    agentId: str = Field(min_length=1, max_length=64)
    deviceKey: str | None = Field(default=None, max_length=128)
    intent: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=500)
    message: str | None = Field(default=None, max_length=2000)
    imageUrl: str | None = Field(default=None, max_length=2048)
    priority: int | None = Field(default=None, ge=0, le=100)
    expiresAt: str | None = Field(default=None, max_length=64)
    source: str | None = Field(default=None, max_length=32)
    tag: str | None = Field(default=None, max_length=128)


@router.post("/revel/display", response_model=None, dependencies=[Depends(require_internal_token)])
async def revel_display(
    payload: RevelDisplayIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Validate V1 display state and skip the Revel write while execute is off."""
    result = await apply_display_state(
        db,
        agent_id=payload.agentId,
        intent=payload.intent,
        device_key=payload.deviceKey,
        title=payload.title,
        message=payload.message,
        image_url=payload.imageUrl,
        priority=payload.priority,
        expires_at=payload.expiresAt,
        source=payload.source,
        tag=payload.tag,
        extra=payload.model_dump(),
    )
    log.info(
        "revel display agent=%s intent=%s result=%s reason=%s executed=%s",
        payload.agentId,
        payload.intent,
        result.get("result"),
        result.get("reason"),
        result.get("executed"),
    )
    return result


@router.get(
    "/revel/status/{agent_id}",
    response_model=None,
    dependencies=[Depends(require_internal_token)],
)
async def internal_revel_status(
    agent_id: str,
    topicId: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Read-only discussion Revel status. Does not write to Revel."""
    return await revel_status_for_agent(db, agent_id, topic_id=topicId)


@router.post("/revel/command", response_model=None, dependencies=[Depends(require_internal_token)])
async def revel_command(
    payload: RevelCommandIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Allowlisted Revel phrase match. Does not mutate Revel in V1."""
    result = await evaluate_voice_command(
        db,
        agent_id=payload.agentId,
        utterance=payload.utterance,
        remainder=payload.remainder,
    )
    log.info(
        "revel command agent=%s matched=%s executed=%s reason=%s",
        payload.agentId,
        result.get("matched"),
        result.get("executed"),
        result.get("reason"),
    )
    return result


class DeviceKnowledgeSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=50)


@router.post(
    "/device/{mac}/knowledge-search",
    response_model=None,
    dependencies=[Depends(require_internal_token)],
)
async def internal_device_knowledge_search(
    mac: str,
    payload: DeviceKnowledgeSearchIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Scoped retrieval for a Watcher. XiaoZhi may call this with X-Internal-Token
    when CC_XIAOZHI_KNOWLEDGE_ENABLED is on. Authorization stays server-side.

    Device MAC → bound client Knowledge Bases → enabled sources only.
    Never searches all Knowledge Bases. Does not execute Revel.
    """
    result = await device_knowledge_search(db, mac, payload.query, payload.limit)
    log.info(
        "device knowledge-search mac=%s client=%s bases=%s hits=%s revel_execute=false",
        result.get("deviceMac"),
        result.get("clientId"),
        len(result.get("knowledgeBaseIds") or []),
        len(result.get("results") or []),
    )
    return result


class DeviceKnowledgeRevelIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    alreadyExecutedTag: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=5, ge=1, le=50)


@router.post(
    "/device/{mac}/knowledge-revel",
    response_model=None,
    dependencies=[Depends(require_internal_token)],
)
async def internal_device_knowledge_revel(
    mac: str,
    payload: DeviceKnowledgeRevelIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Structured Knowledge → Revel decision. Default deny. Does not scan text.

    Independent of keyword POST /internal/revel/command. Does not mutate Revel
    while EXECUTE_ENABLED is false.
    """
    result = await evaluate_knowledge_revel(
        db,
        mac,
        payload.query,
        already_executed_tag=payload.alreadyExecutedTag,
        limit=payload.limit,
    )
    return result
