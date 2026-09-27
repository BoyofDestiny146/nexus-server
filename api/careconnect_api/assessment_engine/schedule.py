"""Per-client assessment schedule stored on ``ai_agent.profile_json``.

No cron strings. Intervals are minutes. Care & Wellness defaults to every
24 hours; Sales and unimplemented profiles stay manual.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chat_events import (
    CHAT_TYPE_CAREGIVER,
    CHAT_TYPE_CLIENT,
    CHAT_TYPE_SYSTEM,
    GCAL_MARKER,
    REVEL_MARKER,
)
from ..client_profile import dump_profile, load_profile, merge_profile
from ..envelope import APIException
from ..models import AiAgent, AiAgentChatHistory, AiMedicalAssessment, CcAssessmentResult
from .profiles import CARE_WELLNESS_ID, SALES_PRODUCT_ID
from .storage import resolved_definition

SCHEDULE_JSON_KEY = "assessmentSchedule"

MODE_MANUAL = "manual"
MODE_INTERVAL = "interval"

TRIGGER_MANUAL = "manual"
TRIGGER_SCHEDULED = "scheduled"
TRIGGER_ESCALATION = "escalation_phrase"

# Minutes. UI labels live in INTERVAL_CHOICES — no free-form cron.
INTERVAL_MINUTES: tuple[int, ...] = (60, 120, 240, 480, 720, 1440, 4320, 10080)

INTERVAL_CHOICES: tuple[tuple[int, str], ...] = (
    (60, "Every hour"),
    (120, "Every 2 hours"),
    (240, "Every 4 hours"),
    (480, "Every 8 hours"),
    (720, "Every 12 hours"),
    (1440, "Every 24 hours"),
    (4320, "Every 3 days"),
    (10080, "Weekly"),
)

DEFAULT_CARE_INTERVAL_MINUTES = 1440


def schedule_supported(profile_id: str) -> bool:
    return profile_id == CARE_WELLNESS_ID


def default_schedule(profile_id: str) -> dict[str, Any]:
    if schedule_supported(profile_id):
        return {
            "enabled": True,
            "mode": MODE_INTERVAL,
            "intervalMinutes": DEFAULT_CARE_INTERVAL_MINUTES,
            "onlyIfNewData": True,
        }
    return {
        "enabled": False,
        "mode": MODE_MANUAL,
        "intervalMinutes": None,
        "onlyIfNewData": True,
    }


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return default


def parse_schedule(raw: Any, *, profile_id: str) -> dict[str, Any]:
    """Resolve a stored schedule. Invalid values fall back to the profile default."""
    fallback = default_schedule(profile_id)
    if not schedule_supported(profile_id):
        return dict(fallback)
    data = raw
    if isinstance(raw, str) and raw.strip():
        return fallback
    if not isinstance(data, dict):
        return fallback
    mode = str(data.get("mode") or "").strip().lower()
    enabled = _as_bool(data.get("enabled"), fallback["enabled"])
    only_if_new = _as_bool(data.get("onlyIfNewData"), True)
    interval = data.get("intervalMinutes")
    try:
        interval_int = int(interval) if interval is not None and str(interval).strip() != "" else None
    except (TypeError, ValueError):
        interval_int = None
    if mode == MODE_MANUAL or enabled is False:
        return {
            "enabled": False,
            "mode": MODE_MANUAL,
            "intervalMinutes": None,
            "onlyIfNewData": only_if_new,
        }
    if interval_int not in INTERVAL_MINUTES:
        interval_int = DEFAULT_CARE_INTERVAL_MINUTES
    return {
        "enabled": True,
        "mode": MODE_INTERVAL,
        "intervalMinutes": interval_int,
        "onlyIfNewData": only_if_new,
    }


def parse_schedule_put(raw: Any, *, profile_id: str) -> dict[str, Any]:
    """Validate a client-supplied schedule. Rejects cron strings and unknown intervals."""
    if not schedule_supported(profile_id):
        raise APIException(400, "scheduled assessments are not available for this profile")
    if not isinstance(raw, dict):
        raise APIException(400, "assessmentSchedule is required")
    if any(k in raw for k in ("cron", "cronExpression", "crontab")):
        raise APIException(400, "cron strings are not supported")
    mode = str(raw.get("mode") or "").strip().lower()
    enabled = _as_bool(raw.get("enabled"), True)
    only_if_new = _as_bool(raw.get("onlyIfNewData"), True)
    if mode == MODE_MANUAL or enabled is False:
        return {
            "enabled": False,
            "mode": MODE_MANUAL,
            "intervalMinutes": None,
            "onlyIfNewData": only_if_new,
        }
    interval = raw.get("intervalMinutes")
    try:
        interval_int = int(interval) if interval is not None and str(interval).strip() != "" else None
    except (TypeError, ValueError):
        interval_int = None
    if interval_int not in INTERVAL_MINUTES:
        raise APIException(400, "unsupported assessment interval")
    return {
        "enabled": True,
        "mode": MODE_INTERVAL,
        "intervalMinutes": interval_int,
        "onlyIfNewData": only_if_new,
    }


def stored_schedule_value(profile: Mapping[str, Any] | None) -> Any:
    if not profile:
        return None
    return profile.get(SCHEDULE_JSON_KEY)


def resolved_schedule(profile_json: Any) -> dict[str, Any]:
    loaded = load_profile(profile_json)
    profile_id = resolved_definition(profile_json).id
    return parse_schedule(stored_schedule_value(loaded), profile_id=profile_id)


def apply_assessment_schedule(agent: AiAgent, schedule: Mapping[str, Any]) -> None:
    existing = load_profile(agent.profile_json)
    merged = merge_profile(existing, {SCHEDULE_JSON_KEY: dict(schedule)})
    agent.profile_json = dump_profile(merged)


def next_due_at(last: datetime | None, interval_minutes: int, now: datetime) -> datetime:
    if last is None:
        return now
    return last + timedelta(minutes=int(interval_minutes))


def is_due(last: datetime | None, interval_minutes: int, now: datetime) -> bool:
    return next_due_at(last, interval_minutes, now) <= now


async def latest_care_generated_at(db: AsyncSession, agent_id: str) -> datetime | None:
    row = (
        await db.execute(
            select(AiMedicalAssessment.generated_at)
            .where(AiMedicalAssessment.agent_id == agent_id)
            .order_by(
                AiMedicalAssessment.generated_at.desc(),
                AiMedicalAssessment.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


async def latest_sales_generated_at(db: AsyncSession, agent_id: str) -> datetime | None:
    row = (
        await db.execute(
            select(CcAssessmentResult.generated_at)
            .where(
                CcAssessmentResult.agent_id == agent_id,
                CcAssessmentResult.profile_id == SALES_PRODUCT_ID,
            )
            .order_by(
                CcAssessmentResult.generated_at.desc(),
                CcAssessmentResult.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


def _is_meaningful_system_event(content: str | None) -> bool:
    text = content or ""
    return REVEL_MARKER in text or GCAL_MARKER in text


async def has_meaningful_new_data(
    db: AsyncSession,
    agent_id: str,
    since: datetime | None,
) -> bool:
    """True when assessment inputs arrived after ``since``.

    Care v1: client/caregiver chat, Revel context/display events, calendar
    reminders already stored in discussion. No invented sensor inputs.
    """
    if since is None:
        return True
    rows = (
        await db.execute(
            select(
                AiAgentChatHistory.chat_type,
                AiAgentChatHistory.content,
            )
            .where(
                AiAgentChatHistory.agent_id == agent_id,
                AiAgentChatHistory.created_at > since,
            )
            .order_by(AiAgentChatHistory.id.asc())
            .limit(200)
        )
    ).all()
    for chat_type, content in rows:
        if chat_type in (CHAT_TYPE_CLIENT, CHAT_TYPE_CAREGIVER):
            return True
        if chat_type == CHAT_TYPE_SYSTEM and _is_meaningful_system_event(content):
            return True
    return False


SKIP_UNSUPPORTED = "unsupported_profile"
SKIP_MANUAL = "manual"
SKIP_NOT_DUE = "not_due"
SKIP_NO_NEW_DATA = "no_new_data"
SKIP_LOCKED = "locked"
ACTION_RUN = "run"


async def evaluate_due(
    db: AsyncSession,
    agent: AiAgent,
    now: datetime,
) -> dict[str, Any]:
    profile_id = resolved_definition(agent.profile_json).id
    schedule = parse_schedule(stored_schedule_value(load_profile(agent.profile_json)), profile_id=profile_id)
    last = await latest_care_generated_at(db, agent.id)
    if not schedule_supported(profile_id):
        return {
            "action": SKIP_UNSUPPORTED,
            "profileId": profile_id,
            "schedule": schedule,
            "lastAssessmentAt": last,
            "nextAssessmentAt": None,
        }
    if not schedule["enabled"] or schedule["mode"] == MODE_MANUAL:
        return {
            "action": SKIP_MANUAL,
            "profileId": profile_id,
            "schedule": schedule,
            "lastAssessmentAt": last,
            "nextAssessmentAt": None,
        }
    interval = int(schedule["intervalMinutes"])
    due_at = next_due_at(last, interval, now)
    if due_at > now:
        return {
            "action": SKIP_NOT_DUE,
            "profileId": profile_id,
            "schedule": schedule,
            "lastAssessmentAt": last,
            "nextAssessmentAt": due_at,
        }
    if schedule["onlyIfNewData"] and not await has_meaningful_new_data(db, agent.id, last):
        return {
            "action": SKIP_NO_NEW_DATA,
            "profileId": profile_id,
            "schedule": schedule,
            "lastAssessmentAt": last,
            "nextAssessmentAt": due_at,
        }
    return {
        "action": ACTION_RUN,
        "profileId": profile_id,
        "schedule": schedule,
        "lastAssessmentAt": last,
        "nextAssessmentAt": due_at,
        "scheduledDueAt": due_at,
    }


async def schedule_view(db: AsyncSession, agent: AiAgent, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    profile_id = resolved_definition(agent.profile_json).id
    schedule = parse_schedule(stored_schedule_value(load_profile(agent.profile_json)), profile_id=profile_id)
    supported = schedule_supported(profile_id)
    if profile_id == SALES_PRODUCT_ID:
        last = await latest_sales_generated_at(db, agent.id)
    else:
        last = await latest_care_generated_at(db, agent.id)
    next_at = None
    if supported and schedule["enabled"] and schedule["intervalMinutes"]:
        next_at = next_due_at(last, int(schedule["intervalMinutes"]), now)
    return {
        "assessmentSchedule": schedule,
        "scheduleSupported": supported,
        "intervalChoices": [
            {"intervalMinutes": minutes, "label": label} for minutes, label in INTERVAL_CHOICES
        ],
        "lastAssessmentAt": last,
        "nextAssessmentAt": next_at if supported and schedule["enabled"] else None,
    }
