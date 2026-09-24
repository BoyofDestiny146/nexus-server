"""Daily medical-risk triage runner.

Port of the Java :class:`MedicalAssessmentServiceImpl` (per-agent compute)
plus :class:`MedicalAssessmentScheduler` (walk every agent). One row per
``(agent_id, for_date)`` is appended to ``ai_medical_assessment``.

Public surface:

* :func:`run_for_agent` — compute + persist for a single agent. Used by both
  the scheduler tick and the ``/regenerate`` endpoint.
* :func:`run_for_all`  — walk every ``ai_agent`` row. Per-agent failures are
  logged and skipped; the batch never aborts halfway.

The Ollama HTTP shape uses the **/api/chat** endpoint with ``format=json`` and
``stream=false`` (the Python target runs against a vanilla Ollama server, not
the OpenAI-compat shim the Java code used). The structured-JSON parsing,
risk-level whitelist, and parse-error fallback all match the Java behavior.

One intentional deviation from the Java original: the Java code skipped agents
with zero messages in the window. The Python port writes a row anyway with
``confidence=0.0`` and ``source_msg_count=0`` so the dashboard still has a
clear "no data" data point per agent per day.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chat_events import CHAT_TYPE_CAREGIVER, CHAT_TYPE_SYSTEM, calendar_dialogue_line
from ..db import async_session_factory
from ..models import AiAgent, AiAgentChatHistory, AiMedicalAssessment
from ..settings import settings


log = logging.getLogger("triage")

ALLOWED_LEVELS = {"low", "moderate", "elevated", "urgent"}
_PROMPT_PATH = Path(__file__).parent / "prompt.md"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")


class TriageResult:
    """Carrier for the four LLM output fields. Mirrors the Java inner class."""

    __slots__ = ("risk_level", "confidence", "concerns", "recommendations")

    def __init__(
        self,
        risk_level: str,
        confidence: float,
        concerns: list[str] | None,
        recommendations: list[str] | None,
    ):
        self.risk_level = risk_level
        self.confidence = confidence
        self.concerns = concerns or []
        self.recommendations = recommendations or []

    @classmethod
    def parse_error(cls, reason: str) -> "TriageResult":
        return cls("low", 0.0, ["parse_error", reason], [])


# ---------- helpers ----------

def _render_dialogue(messages: list[AiAgentChatHistory]) -> str:
    """Format chat history compactly.

    chat_type 1 = client, 2 = caregiver voice, 3 = system_event (calendar
    reminders are labelled ``calendar_reminder``). A calendar row is a
    spoken reminder, not evidence the medication/task was completed.
    """
    parts: list[str] = []
    for m in messages:
        if m.chat_type == CHAT_TYPE_SYSTEM:
            role = "calendar_reminder"
            text = calendar_dialogue_line(m.content)
        elif m.chat_type == CHAT_TYPE_CAREGIVER:
            role = "caregiver"
            text = (m.content or "").strip()
        else:
            role = "client"
            text = (m.content or "").strip()
        if not text:
            continue
        parts.append(f"{role}: {text}\n")
    return "".join(parts)


def _coerce_string_list(o: Any) -> list[str]:
    """Coerce arbitrary JSON value into a clean list[str]."""
    if isinstance(o, list):
        out: list[str] = []
        for item in o:
            if item is None:
                continue
            s = str(item)
            if s.strip():
                out.append(s)
        return out
    if isinstance(o, str) and o.strip():
        return [o]
    return []


def _parse_llm_json(raw: str | None) -> TriageResult:
    """Parse the LLM's JSON reply. On any failure returns a parse-error placeholder."""
    if not raw:
        return TriageResult.parse_error("empty_content")
    trimmed = raw.strip()
    first = trimmed.find("{")
    last = trimmed.rfind("}")
    if first < 0 or last <= first:
        log.warning("LLM reply had no JSON object: %s", trimmed)
        return TriageResult.parse_error("no_json_object")
    try:
        parsed = json.loads(trimmed[first : last + 1])
    except json.JSONDecodeError as exc:
        log.warning("JSON parse threw %s: %s", type(exc).__name__, exc)
        return TriageResult.parse_error(f"exception:{type(exc).__name__}")

    if not isinstance(parsed, dict):
        return TriageResult.parse_error("null_parse")

    level = str(parsed.get("risk_level", "low")).lower().strip()
    # The calibration block in prompt.md uses the word "high" colloquially;
    # alias it to the schema value "urgent" so a calibrated reply doesn't
    # silently fall back to "low".
    if level == "high":
        level = "urgent"
    if level not in ALLOWED_LEVELS:
        log.warning("LLM returned invalid risk_level '%s'", level)
        level = "low"

    c = parsed.get("confidence", 0.5)
    try:
        confidence = float(c)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    return TriageResult(
        risk_level=level,
        confidence=confidence,
        concerns=_coerce_string_list(parsed.get("concerns")),
        recommendations=_coerce_string_list(parsed.get("recommendations")),
    )


async def _invoke_llm(dialogue: str) -> TriageResult:
    """POST the dialogue to Ollama /api/chat and parse the structured-JSON reply."""
    body = {
        "model": settings.ollama_triage_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Caregiver-client dialogue from the last 24 hours follows "
                    "(including any calendar_reminder system events). "
                    "Return ONLY a JSON object as specified. This is a triage signal, "
                    "NOT a diagnosis.\n\n" + dialogue
                ),
            },
        ],
        "format": "json",
        "stream": False,
        "options": {"num_predict": 300, "temperature": 0.2},
    }
    async with httpx.AsyncClient(timeout=settings.ollama_timeout_s) as client:
        resp = await client.post(settings.ollama_url + "/api/chat", json=body)
    resp.raise_for_status()
    payload = resp.json()
    message = payload.get("message") or {}
    content = message.get("content")
    if content is None:
        raise RuntimeError("Ollama response had no message.content")
    return _parse_llm_json(str(content))


# ---------- public entry points ----------

async def run_for_agent(
    db: AsyncSession,
    agent_id: str,
    for_date: date | None = None,
) -> AiMedicalAssessment | None:
    """Compute and persist one assessment row for the given agent.

    The ``for_date`` window covers ``(for_date - 1 day, for_date]`` server-local;
    that matches the original 24h-rolling window used by the Java scheduler.
    Returns the inserted ORM row (refreshed so ``id`` and ``generated_at`` are
    populated), or ``None`` if the agent doesn't exist.
    """
    if for_date is None:
        for_date = date.today()

    agent = await db.get(AiAgent, agent_id)
    if agent is None:
        log.warning("triage: agent %s not found", agent_id)
        return None

    # Calendar-day window with a 1-day safety lookback so the manual
    # "regenerate" button captures both same-day testing AND the previous
    # calendar day that the daily 02:00 cron is meant to assess. Previously
    # this was a strict 24h-rolling window anchored to time.max(for_date),
    # which silently skipped any conversation row from before the prior
    # midnight and almost always produced an empty-window low-risk fallback.
    window_start = datetime.combine(for_date - timedelta(days=1), time.min)
    window_end = datetime.combine(for_date, time.max)

    log.info("triage: agent=%s window=[%s, %s]", agent_id, window_start, window_end)

    rows = (
        await db.execute(
            select(AiAgentChatHistory)
            .where(
                AiAgentChatHistory.agent_id == agent_id,
                AiAgentChatHistory.created_at >= window_start,
                AiAgentChatHistory.created_at <= window_end,
            )
            .order_by(AiAgentChatHistory.id.asc())
            .limit(settings.triage_max_messages)
        )
    ).scalars().all()

    log.info("triage: agent=%s rows_fetched=%d", agent_id, len(rows))

    if not rows:
        # Java skipped these; we write an explicit zero-confidence "no data" row so
        # the dashboard still has a per-day datapoint.
        log.warning(
            "triage: zero messages — defaulting to risk_level=low for agent %s",
            agent_id,
        )
        result = TriageResult("low", 0.0, [], [])
        source_count = 0
    else:
        dialogue = _render_dialogue(rows)
        try:
            result = await _invoke_llm(dialogue)
        except Exception as exc:  # noqa: BLE001 — every LLM failure becomes a parse-error row
            log.warning(
                "triage: LLM call failed for agent %s on %s: %s",
                agent_id, for_date, exc,
            )
            result = TriageResult.parse_error(f"llm_call_failed: {type(exc).__name__}")
        source_count = len(rows)
        log.info(
            "triage: agent=%s risk=%s confidence=%.2f source_count=%d",
            agent_id, result.risk_level, result.confidence, source_count,
        )

    row = AiMedicalAssessment(
        agent_id=agent_id,
        for_date=for_date,
        risk_level=result.risk_level,
        confidence=Decimal(f"{result.confidence:.3f}"),
        concerns_json=json.dumps(result.concerns, ensure_ascii=False),
        recommendations_json=json.dumps(result.recommendations, ensure_ascii=False),
        source_msg_count=source_count,
        llm_model=settings.ollama_triage_model,
        generated_at=datetime.now(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    try:
        from ..partner_push import push_assessment_best_effort

        await push_assessment_best_effort(db, agent_id, row)
    except Exception:
        log.warning("careconnect push raised after persist")
    return row


async def run_for_all() -> dict[str, int]:
    """Scheduler tick — walk every ai_agent row and produce one assessment each.

    Per-agent exceptions are caught and logged; the loop continues. Each agent
    runs in its own short-lived session so a failed agent's transaction can't
    poison the next one.
    """
    for_date = date.today()
    log.info("triage: daily run starting for %s", for_date)

    async with async_session_factory() as session:
        agent_ids = (await session.execute(select(AiAgent.id))).scalars().all()

    processed = 0
    errors = 0
    for agent_id in agent_ids:
        try:
            async with async_session_factory() as session:
                await run_for_agent(session, agent_id, for_date)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.warning("triage: assessment for agent %s failed: %s", agent_id, exc)

    log.info(
        "triage: daily run finished — agents_processed=%d errors=%d",
        processed, errors,
    )
    return {"agents_processed": processed, "errors": errors}
