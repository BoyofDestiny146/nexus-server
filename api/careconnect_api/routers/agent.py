"""Agent (client) read endpoints — Python port of the Java AgentController.

Mounted at /api/agent/* by main.py. Five read-only endpoints:

    GET /agent/list                                  — current user's agents
    GET /agent/all?page=&limit=                      — root-only paged list of all agents
    GET /agent/{agentId}                             — single agent details
    GET /agent/{agentId}/sessions?page=&limit=       — paged session list
    GET /agent/{agentId}/chat-history/{sessionId}    — messages in one session

Field names are camelCase to match the AgentDTO/AgentInfoVO/AgentChatSessionDTO/
AgentChatHistoryDTO shapes the existing Vue dashboard already consumes (Spring
auto-camelCases its DTOs; we mirror that here so the frontend can swap
baseURLs in Phase 4 without touching client code).

RBAC:
- Root sees everything. /agent/all is gated by Depends(require_root).
- Scoped admins see only the agent_ids granted in cc_admin_client_access.
  Per-agent endpoints call assert_can_access_agent which raises 403.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CurrentUser, get_current_user, require_root
from ..db import get_db
from ..envelope import APIException
from ..models import AiAgent, AiAgentChatHistory, AiDevice, AiMedicalAssessment
from ..rbac import assert_can_access_agent, scoped_agent_ids


router = APIRouter(prefix="/agent", tags=["agent"])


# ---------- response builders ----------

def _agent_summary(
    agent: AiAgent,
    *,
    device_count: int = 0,
    last_connected_at: datetime | None = None,
    risk_level: str | None = None,
) -> dict[str, Any]:
    """Shape used by /agent/list — matches dashboard's AgentSummary type."""
    return {
        "id": agent.id,
        "agentName": agent.agent_name,
        "agentCode": agent.agent_code,
        "asrModelId": agent.asr_model_id,
        "vadModelId": agent.vad_model_id,
        "llmModelId": agent.llm_model_id,
        "vllmModelId": agent.vllm_model_id,
        "ttsModelId": agent.tts_model_id,
        "ttsVoiceId": agent.tts_voice_id,
        "memModelId": agent.mem_model_id,
        "intentModelId": agent.intent_model_id,
        "systemPrompt": agent.system_prompt,
        "langCode": agent.lang_code,
        "language": agent.language,
        "sort": agent.sort,
        "creator": agent.creator,
        "createdAt": agent.created_at,
        "updater": agent.updater,
        "updatedAt": agent.updated_at,
        "deviceCount": device_count,
        "lastConnectedAt": last_connected_at,
        # careconnect extension: latest risk level so the dashboard can render
        # a risk dot per card without an N+1 fetch.
        "riskLevel": risk_level,
    }


def _agent_info(agent: AiAgent) -> dict[str, Any]:
    """Shape used by /agent/{id} — matches Java's AgentInfoVO (extends AgentEntity)."""
    return {
        "id": agent.id,
        "userId": agent.user_id,
        "agentCode": agent.agent_code,
        "agentName": agent.agent_name,
        "asrModelId": agent.asr_model_id,
        "vadModelId": agent.vad_model_id,
        "llmModelId": agent.llm_model_id,
        "vllmModelId": agent.vllm_model_id,
        "ttsModelId": agent.tts_model_id,
        "ttsVoiceId": agent.tts_voice_id,
        "memModelId": agent.mem_model_id,
        "intentModelId": agent.intent_model_id,
        "chatHistoryConf": agent.chat_history_conf,
        "systemPrompt": agent.system_prompt,
        "summaryMemory": agent.summary_memory,
        "langCode": agent.lang_code,
        "language": agent.language,
        "sort": agent.sort,
        "creator": agent.creator,
        "createdAt": agent.created_at,
        "updater": agent.updater,
        "updatedAt": agent.updated_at,
        # AgentInfoVO carries a `functions` plugin list. We don't have an
        # ai_agent_plugin_mapping ORM model yet; ship empty for now so the
        # dashboard's null-checks still pass.
        "functions": [],
    }


# ---------- helpers ----------

async def _device_counts_by_agent(
    db: AsyncSession, agent_ids: list[str]
) -> dict[str, int]:
    if not agent_ids:
        return {}
    rows = (
        await db.execute(
            select(AiDevice.agent_id, func.count(AiDevice.id))
            .where(AiDevice.agent_id.in_(agent_ids))
            .group_by(AiDevice.agent_id)
        )
    ).all()
    return {row[0]: row[1] for row in rows}


async def _last_connected_by_agent(
    db: AsyncSession, agent_ids: list[str]
) -> dict[str, datetime]:
    if not agent_ids:
        return {}
    rows = (
        await db.execute(
            select(AiDevice.agent_id, func.max(AiDevice.last_connected_at))
            .where(AiDevice.agent_id.in_(agent_ids))
            .group_by(AiDevice.agent_id)
        )
    ).all()
    return {row[0]: row[1] for row in rows if row[1] is not None}


async def _latest_risk_by_agent(
    db: AsyncSession, agent_ids: list[str]
) -> dict[str, str]:
    """Latest assessment risk_level per agent. Picked by max(generated_at) so
    the dashboard's risk dots reflect the most recent triage run."""
    if not agent_ids:
        return {}
    # Subquery: per-agent newest generated_at
    newest = (
        select(
            AiMedicalAssessment.agent_id.label("agent_id"),
            func.max(AiMedicalAssessment.generated_at).label("g"),
        )
        .where(AiMedicalAssessment.agent_id.in_(agent_ids))
        .group_by(AiMedicalAssessment.agent_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(AiMedicalAssessment.agent_id, AiMedicalAssessment.risk_level)
            .join(
                newest,
                (AiMedicalAssessment.agent_id == newest.c.agent_id)
                & (AiMedicalAssessment.generated_at == newest.c.g),
            )
        )
    ).all()
    return {row[0]: row[1] for row in rows}


# ---------- endpoints ----------

@router.get("/list", response_model=None)
async def list_agents(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Agents the current user can see (root: all; admin: scoped)."""
    scope = await scoped_agent_ids(db, user)

    stmt = select(AiAgent).order_by(AiAgent.sort.asc(), AiAgent.created_at.desc())
    if scope is not None:
        if not scope:
            return []
        stmt = stmt.where(AiAgent.id.in_(scope))

    agents = (await db.execute(stmt)).scalars().all()
    if not agents:
        return []

    ids = [a.id for a in agents]
    device_counts = await _device_counts_by_agent(db, ids)
    last_connected = await _last_connected_by_agent(db, ids)
    latest_risk = await _latest_risk_by_agent(db, ids)

    return [
        _agent_summary(
            a,
            device_count=device_counts.get(a.id, 0),
            last_connected_at=last_connected.get(a.id),
            risk_level=latest_risk.get(a.id),
        )
        for a in agents
    ]


@router.get("/all", response_model=None)
async def list_all_agents(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    _root: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Root-only paged list of every agent. Java equivalent: /agent/all."""
    total = (await db.execute(select(func.count(AiAgent.id)))).scalar_one()

    offset = (page - 1) * limit
    rows = (
        await db.execute(
            select(AiAgent)
            .order_by(AiAgent.agent_name.asc(), AiAgent.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()

    return {
        "list": [_agent_info(a) for a in rows],
        "total": int(total or 0),
        "page": page,
        "limit": limit,
    }


@router.get("/{agent_id}", response_model=None)
async def get_agent(
    agent_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Full detail for a single agent. RBAC-checked."""
    await assert_can_access_agent(db, user, agent_id)
    agent = (
        await db.execute(select(AiAgent).where(AiAgent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise APIException(404, "agent not found")
    return _agent_info(agent)


@router.get("/{agent_id}/sessions", response_model=None)
async def list_sessions(
    agent_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(30, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Paged list of sessions for one agent.

    Sessions are derived by grouping ai_agent_chat_history rows by session_id,
    using max(created_at) as the session timestamp (Java does the same).
    """
    await assert_can_access_agent(db, user, agent_id)

    # Total distinct sessions
    total_stmt = select(func.count(func.distinct(AiAgentChatHistory.session_id))).where(
        AiAgentChatHistory.agent_id == agent_id,
        AiAgentChatHistory.session_id.is_not(None),
    )
    total = (await db.execute(total_stmt)).scalar_one()

    offset = (page - 1) * limit
    rows = (
        await db.execute(
            select(
                AiAgentChatHistory.session_id,
                func.max(AiAgentChatHistory.created_at).label("created_at"),
                func.count().label("chat_count"),
            )
            .where(
                AiAgentChatHistory.agent_id == agent_id,
                AiAgentChatHistory.session_id.is_not(None),
            )
            .group_by(AiAgentChatHistory.session_id)
            .order_by(func.max(AiAgentChatHistory.created_at).desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()

    items = [
        {
            "sessionId": r[0],
            "agentId": agent_id,
            "createdAt": r[1],
            # Both `chatCount` (Java DTO field) and `messageCount` (dashboard
            # type) so either client speaks the same JSON.
            "chatCount": int(r[2]),
            "messageCount": int(r[2]),
        }
        for r in rows
    ]

    return {
        "list": items,
        "total": int(total or 0),
        "page": page,
        "limit": limit,
    }


@router.get("/{agent_id}/chat-history/{session_id}", response_model=None)
async def get_chat_history(
    agent_id: str,
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """All messages for one (agent, session), ordered by id ASC (insertion order)."""
    await assert_can_access_agent(db, user, agent_id)

    rows = (
        await db.execute(
            select(AiAgentChatHistory)
            .where(
                AiAgentChatHistory.agent_id == agent_id,
                AiAgentChatHistory.session_id == session_id,
            )
            .order_by(AiAgentChatHistory.id.asc())
        )
    ).scalars().all()

    return [
        {
            "id": r.id,
            "chatType": r.chat_type,
            "content": r.content,
            "audioId": r.audio_id,
            "macAddress": r.mac_address,
            "createdAt": r.created_at,
        }
        for r in rows
    ]
