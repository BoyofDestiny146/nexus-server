"""Bind an existing virtual agent to a Nexus client and read stored discussions.

A virtual agent is an ai_agent in Portal AI Agents with no Watcher. Binding
stores that id. It does not copy the agent and it does not write ai_device.

Conversation routes only read cc_virtual_agent_message. The Sam conversation
endpoint does not write that table.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent_classification import (
    PORTAL_AI_AGENTS_ORGANIZATION_ID,
    binding_membership,
    is_virtual_agent,
)
from ..auth import CurrentUser, get_current_user
from ..db import get_db
from ..envelope import APIException
from ..models import AiAgent, AiDevice, CcVirtualAgentBinding, CcVirtualAgentMessage
from ..rbac import assert_can_access_agent, scoped_agent_ids
from ..sam_session import review_sam_sessions

router = APIRouter(prefix="/agent", tags=["virtual-agents"])


class BindVirtualAgentBody(BaseModel):
    virtualAgentId: str = Field(min_length=1, max_length=32)


def _public_virtual_agent(agent: AiAgent, device_count: int) -> dict[str, Any]:
    return {
        "id": agent.id,
        "agentName": agent.agent_name,
        "botName": agent.bot_name,
        "organizationId": agent.organization_id,
        "deviceCount": device_count,
    }


async def _device_counts(db: AsyncSession, agent_ids: list[str]) -> dict[str, int]:
    if not agent_ids:
        return {}
    rows = (
        await db.execute(
            select(AiDevice.agent_id, func.count())
            .where(AiDevice.agent_id.in_(agent_ids))
            .group_by(AiDevice.agent_id)
        )
    ).all()
    return {str(agent_id): int(count) for agent_id, count in rows if agent_id}


async def _require_client(db: AsyncSession, client_id: str) -> AiAgent:
    agent = await db.get(AiAgent, client_id)
    if agent is None:
        raise APIException(404, "client not found")
    return agent


async def _require_binding(
    db: AsyncSession, client_id: str, virtual_agent_id: str
) -> CcVirtualAgentBinding:
    row = await db.get(
        CcVirtualAgentBinding,
        {"client_id": client_id, "virtual_agent_id": virtual_agent_id},
    )
    if row is None:
        raise APIException(404, "virtual agent is not associated with this client")
    return row


@router.get("/virtual", response_model=None)
async def list_virtual_agents(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Virtual agents this administrator can access. Watchers are excluded."""
    rows = (
        await db.execute(
            select(AiAgent).where(AiAgent.organization_id == PORTAL_AI_AGENTS_ORGANIZATION_ID)
        )
    ).scalars().all()
    ids = [row.id for row in rows]
    counts = await _device_counts(db, ids)
    virtual_ids, client_ids = await binding_membership(db, ids)
    allowed = await scoped_agent_ids(db, user)
    visible: list[dict[str, Any]] = []
    for agent in rows:
        device_count = counts.get(agent.id, 0)
        if not is_virtual_agent(
            organization_id=agent.organization_id,
            device_count=device_count,
            bound_as_virtual_agent=agent.id in virtual_ids,
            bound_as_client=agent.id in client_ids,
        ):
            continue
        if allowed is not None and agent.id not in allowed:
            continue
        visible.append(_public_virtual_agent(agent, 0))
    visible.sort(key=lambda item: (item.get("agentName") or "", item["id"]))
    return visible


@router.get("/{client_id}/virtual-agents", response_model=None)
async def list_client_virtual_agents(
    client_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    await assert_can_access_agent(db, user, client_id)
    await _require_client(db, client_id)
    bindings = (
        await db.execute(
            select(CcVirtualAgentBinding)
            .where(CcVirtualAgentBinding.client_id == client_id)
            .order_by(CcVirtualAgentBinding.created_at.asc(), CcVirtualAgentBinding.virtual_agent_id.asc())
        )
    ).scalars().all()
    agents = {
        agent.id: agent
        for agent in (
            await db.execute(
                select(AiAgent).where(AiAgent.id.in_([row.virtual_agent_id for row in bindings] or [""]))
            )
        ).scalars().all()
    }
    listed: list[dict[str, Any]] = []
    for row in bindings:
        agent = agents.get(row.virtual_agent_id)
        listed.append({
            "clientId": row.client_id,
            "virtualAgentId": row.virtual_agent_id,
            "agentName": agent.agent_name if agent else None,
            "botName": agent.bot_name if agent else None,
            "organizationId": agent.organization_id if agent else None,
            "createdAt": row.created_at,
        })
    return listed


@router.post("/{client_id}/virtual-agents", response_model=None)
async def bind_virtual_agent(
    client_id: str,
    body: BindVirtualAgentBody,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    virtual_agent_id = body.virtualAgentId.strip()
    if virtual_agent_id == "":
        raise APIException(400, "virtual agent is required")
    if virtual_agent_id == client_id:
        raise APIException(400, "a client cannot be associated with itself")
    await assert_can_access_agent(db, user, client_id)
    await assert_can_access_agent(db, user, virtual_agent_id)
    await _require_client(db, client_id)
    virtual = await db.get(AiAgent, virtual_agent_id)
    if virtual is None:
        raise APIException(404, "virtual agent not found")
    device_count = (await _device_counts(db, [virtual_agent_id])).get(virtual_agent_id, 0)
    if virtual.organization_id != PORTAL_AI_AGENTS_ORGANIZATION_ID or device_count != 0:
        raise APIException(400, "only a virtual agent can be associated")
    row = CcVirtualAgentBinding(
        client_id=client_id,
        virtual_agent_id=virtual_agent_id,
        created_by=user.id,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIException(409, "virtual agent is already associated with this client")
    await db.refresh(row)
    return {
        "clientId": row.client_id,
        "virtualAgentId": row.virtual_agent_id,
        "createdAt": row.created_at,
        "createdBy": row.created_by,
    }


@router.delete("/{client_id}/virtual-agents/{virtual_agent_id}", response_model=None)
async def unbind_virtual_agent(
    client_id: str,
    virtual_agent_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await assert_can_access_agent(db, user, client_id)
    row = await _require_binding(db, client_id, virtual_agent_id)
    await db.delete(row)
    await db.commit()
    return {
        "clientId": client_id,
        "virtualAgentId": virtual_agent_id,
        "removed": True,
    }


@router.get("/{agent_id}/sam-review", response_model=None)
async def read_sam_conversation_review(
    agent_id: str,
    view: str = Query(default="client"),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Client review by default. view=agent is the Virtual Agent Control review."""
    if view not in ("client", "agent"):
        raise APIException(400, "invalid review")
    await assert_can_access_agent(db, user, agent_id)
    allowed = await scoped_agent_ids(db, user) if view == "agent" else None
    return await review_sam_sessions(
        db,
        agent_id,
        view=view,
        allowed_client_ids=allowed,
    )


@router.get("/{client_id}/virtual-agents/{virtual_agent_id}/sessions", response_model=None)
async def list_virtual_agent_sessions(
    client_id: str,
    virtual_agent_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await assert_can_access_agent(db, user, client_id)
    await _require_binding(db, client_id, virtual_agent_id)
    rows = (
        await db.execute(
            select(
                CcVirtualAgentMessage.session_id,
                func.max(CcVirtualAgentMessage.created_at).label("created_at"),
                func.count().label("message_count"),
            )
            .where(
                CcVirtualAgentMessage.client_id == client_id,
                CcVirtualAgentMessage.virtual_agent_id == virtual_agent_id,
            )
            .group_by(CcVirtualAgentMessage.session_id)
            .order_by(func.max(CcVirtualAgentMessage.created_at).desc())
        )
    ).all()
    items = [
        {
            "sessionId": session_id,
            "clientId": client_id,
            "virtualAgentId": virtual_agent_id,
            "createdAt": created_at,
            "messageCount": int(message_count),
        }
        for session_id, created_at, message_count in rows
    ]
    return {"list": items, "total": len(items)}


@router.get(
    "/{client_id}/virtual-agents/{virtual_agent_id}/sessions/{session_id}",
    response_model=None,
)
async def read_virtual_agent_session(
    client_id: str,
    virtual_agent_id: str,
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    await assert_can_access_agent(db, user, client_id)
    await _require_binding(db, client_id, virtual_agent_id)
    rows = (
        await db.execute(
            select(CcVirtualAgentMessage)
            .where(
                CcVirtualAgentMessage.client_id == client_id,
                CcVirtualAgentMessage.virtual_agent_id == virtual_agent_id,
                CcVirtualAgentMessage.session_id == session_id,
            )
            .order_by(CcVirtualAgentMessage.id.asc())
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "clientId": row.client_id,
            "virtualAgentId": row.virtual_agent_id,
            "sessionId": row.session_id,
            "createdAt": row.created_at,
            "question": row.question,
            "answer": row.answer,
            "followUp": row.follow_up,
        }
        for row in rows
    ]
