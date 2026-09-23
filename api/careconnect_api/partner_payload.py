"""Reusable partner payloads for CareConnect (and later Revel / Calendar / push).

Outbound push is not implemented. Routes and future push jobs must call
``build_client_assessment_payload`` instead of assembling JSON inline.

The payload never includes ``ai_agent.id``, Watcher MAC, integration row ids,
bcrypt hashes, or Revel ciphertext.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AiMedicalAssessment
from .settings import settings


SCHEMA_VERSION = "1.0"

# Dashboard RiskBadge labels. Stored values are lowercase whitelist tokens.
LEVEL_LABELS = {
    "low": "Low",
    "moderate": "Moderate",
    "elevated": "Elevated",
    "urgent": "Urgent",
}


def _parse_string_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    if isinstance(value, str) and value.strip():
        return [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _localize(dt: datetime, tz: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _confidence_percent(raw: Any) -> int | None:
    """Stored 0.0–1.0 fraction → integer percent. None if unavailable."""
    if raw is None:
        return None
    try:
        fraction = float(raw)
    except (TypeError, ValueError):
        return None
    return int(round(max(0.0, min(1.0, fraction)) * 100))


def _level_label(raw: str | None) -> str | None:
    if raw is None:
        return None
    token = str(raw).strip().lower()
    if not token:
        return None
    return LEVEL_LABELS.get(token)


def serialize_client_assessment_payload(
    *,
    public_client_id: str,
    assessment: AiMedicalAssessment | None,
    tz_name: str | None = None,
) -> dict[str, Any]:
    """Pure mapper. Safe to reuse from GET, later push, Revel, Calendar jobs."""
    tz = ZoneInfo(tz_name or settings.tz)
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "clientId": public_client_id,
        "date": None,
        "time": None,
        "timestamp": None,
        "assessment": None,
        "recommendations": [],
    }
    if assessment is None:
        return payload

    generated = assessment.generated_at
    for_date = assessment.for_date
    if generated is not None:
        local = _localize(generated, tz)
    elif for_date is not None:
        day = for_date if isinstance(for_date, date) else date.fromisoformat(str(for_date))
        local = datetime.combine(day, time.min, tzinfo=tz)
    else:
        local = None

    if local is not None:
        payload["date"] = local.date().isoformat()
        payload["time"] = local.strftime("%H:%M:%S")
        payload["timestamp"] = local.isoformat(timespec="seconds")

    level = _level_label(assessment.risk_level)
    confidence = _confidence_percent(assessment.confidence)
    if level is not None or confidence is not None:
        payload["assessment"] = {
            "level": level,
            "confidence": confidence,
        }

    payload["recommendations"] = _parse_string_list(assessment.recommendations_json)
    return payload


async def _latest_assessment(
    db: AsyncSession, agent_id: str
) -> AiMedicalAssessment | None:
    return (
        await db.execute(
            select(AiMedicalAssessment)
            .where(AiMedicalAssessment.agent_id == agent_id)
            .order_by(
                AiMedicalAssessment.for_date.desc(),
                AiMedicalAssessment.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def build_client_assessment_payload(
    db: AsyncSession,
    agent_id: str,
    public_client_id: str,
) -> dict[str, Any]:
    """Latest assessment for ``agent_id``, keyed by the CareConnect Nx- id.

    ``agent_id`` is used only to load rows. It is never copied into the payload.
    """
    row = await _latest_assessment(db, agent_id)
    return serialize_client_assessment_payload(
        public_client_id=public_client_id,
        assessment=row,
    )
