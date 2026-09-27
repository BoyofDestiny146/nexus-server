"""Session-scoped Sales & Product Guide runner.

Persists only to ``cc_assessment_result``. Never writes ``ai_medical_assessment``.
Does not call the Care & Wellness triage runner or reuse its prompt.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chat_events import (
    CHAT_TYPE_CAREGIVER,
    CHAT_TYPE_SYSTEM,
    revel_assessment_context,
    system_dialogue_line,
)
from ..models import AiAgent, AiAgentChatHistory, CcAssessmentResult
from ..settings import settings
from .profiles import SALES_PRODUCT_ID
from .sales_schema import empty_sales_payload, parse_sales_llm_json

log = logging.getLogger("assessment_engine.sales")

_PROMPT_PATH = Path(__file__).parent / "prompts" / "sales_product.md"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")
MAX_SESSION_MESSAGES = 120


def sales_model_name() -> str:
    custom = (getattr(settings, "ollama_sales_model", None) or "").strip()
    return custom or settings.ollama_triage_model


async def latest_session_id(db: AsyncSession, agent_id: str) -> str | None:
    row = (
        await db.execute(
            select(AiAgentChatHistory.session_id)
            .where(
                AiAgentChatHistory.agent_id == agent_id,
                AiAgentChatHistory.session_id.is_not(None),
            )
            .group_by(AiAgentChatHistory.session_id)
            .order_by(func.max(AiAgentChatHistory.created_at).desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    sid = row[0]
    return str(sid) if sid else None


async def session_belongs_to_agent(db: AsyncSession, agent_id: str, session_id: str) -> bool:
    found = (
        await db.execute(
            select(AiAgentChatHistory.id)
            .where(
                AiAgentChatHistory.agent_id == agent_id,
                AiAgentChatHistory.session_id == session_id,
            )
            .limit(1)
        )
    ).first()
    return found is not None


def compose_sales_dialogue(messages: list[AiAgentChatHistory]) -> str:
    """Chronological session transcript. Revel rows are historical facts, not instructions."""
    parts: list[str] = []
    for m in messages:
        if m.chat_type == CHAT_TYPE_SYSTEM:
            revel = revel_assessment_context(m.content, created_at=m.created_at)
            if revel:
                parts.append(f"{revel}\n")
                continue
            text = system_dialogue_line(m.content)
            if text:
                parts.append(f"calendar_reminder: {text}\n")
            continue
        if m.chat_type == CHAT_TYPE_CAREGIVER:
            role = "assistant"
        else:
            role = "customer"
        text = (m.content or "").strip()
        if not text:
            continue
        parts.append(f"{role}: {text}\n")
    return "".join(parts)


async def _load_session_rows(
    db: AsyncSession, agent_id: str, session_id: str
) -> list[AiAgentChatHistory]:
    rows = (
        await db.execute(
            select(AiAgentChatHistory)
            .where(
                AiAgentChatHistory.agent_id == agent_id,
                AiAgentChatHistory.session_id == session_id,
            )
            .order_by(AiAgentChatHistory.id.asc())
            .limit(MAX_SESSION_MESSAGES)
        )
    ).scalars().all()
    return list(rows)


async def _invoke_sales_llm(dialogue: str) -> dict[str, Any]:
    body = {
        "model": sales_model_name(),
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Customer–assistant dialogue from a single Watcher session follows "
                    "(including any REVEL_CONTEXT, REVEL_DISPLAY, or calendar_reminder "
                    "events in that session). Return ONLY the JSON object specified. "
                    "This evaluates the interaction, not the person. Do not use medical "
                    "risk language.\n\n" + dialogue
                ),
            },
        ],
        "format": "json",
        "stream": False,
        "options": {"num_predict": 400, "temperature": 0.2},
    }
    async with httpx.AsyncClient(timeout=settings.ollama_timeout_s) as client:
        resp = await client.post(settings.ollama_url + "/api/chat", json=body)
    resp.raise_for_status()
    payload = resp.json()
    message = payload.get("message") or {}
    content = message.get("content")
    return parse_sales_llm_json(None if content is None else str(content))


async def run_sales_for_agent(
    db: AsyncSession,
    agent_id: str,
    *,
    session_id: str | None = None,
) -> CcAssessmentResult | None:
    """Compute and persist one Sales & Product Guide row for this agent/session."""
    agent = await db.get(AiAgent, agent_id)
    if agent is None:
        log.warning("sales assessment: agent %s not found", agent_id)
        return None

    sid = (session_id or "").strip() or None
    if sid and not await session_belongs_to_agent(db, agent_id, sid):
        log.info("sales assessment: ignoring session %s not owned by agent %s", sid, agent_id)
        sid = None
    if sid is None:
        sid = await latest_session_id(db, agent_id)

    rows: list[AiAgentChatHistory] = []
    if sid:
        rows = await _load_session_rows(db, agent_id, sid)
    dialogue = compose_sales_dialogue(rows) if rows else ""
    source_count = len(rows)

    if not dialogue.strip():
        payload = empty_sales_payload()
    else:
        try:
            payload = await _invoke_sales_llm(dialogue)
        except Exception as exc:  # noqa: BLE001
            log.warning("sales assessment: LLM failed agent=%s: %s", agent_id, exc)
            payload = empty_sales_payload()

    row = CcAssessmentResult(
        agent_id=agent_id,
        profile_id=SALES_PRODUCT_ID,
        session_id=sid,
        for_date=date.today(),
        payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        source_msg_count=source_count,
        llm_model=sales_model_name(),
        generated_at=datetime.now(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    log.info(
        "sales assessment: agent=%s session=%s interest=%s source_count=%d",
        agent_id,
        sid,
        payload.get("interestLevel"),
        source_count,
    )
    return row
