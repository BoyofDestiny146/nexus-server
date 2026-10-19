"""Serialize a persisted Care & Wellness row for Bridging Care ingest.

Matches the deployed Bridging Care R60C receiver contract. Field names are
exact: assessmentId (not externalAssessmentId), generatedAt, forDate, and
assessment.riskLevel / confidence (0–1) / concerns / recommendations.
No extra fields.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .models import AiMedicalAssessment
from .partner_payload import SCHEMA_VERSION, _parse_string_list
from .settings import settings

SOURCE = "nexus"
RISK_LEVELS = ("low", "moderate", "elevated", "urgent")
TOP_LEVEL_KEYS = (
    "schemaVersion",
    "assessmentId",
    "source",
    "companionId",
    "accountId",
    "clientId",
    "generatedAt",
    "forDate",
    "assessment",
)
ASSESSMENT_KEYS = ("riskLevel", "confidence", "concerns", "recommendations")
_UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_COMPANION = re.compile(r"^Nx-[A-Za-z0-9]+$")


def _as_utc_z(dt: datetime, tz: ZoneInfo) -> str:
    if dt.tzinfo is None:
        local = dt.replace(tzinfo=tz)
    else:
        local = dt
    utc = local.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _risk_level(raw: str | None) -> str | None:
    token = str(raw or "").strip().lower()
    if token in RISK_LEVELS:
        return token
    return None


def _confidence_unit(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return round(max(0.0, min(1.0, value)), 3)


def serialize_bridgingcare_assessment(
    *,
    companion_id: str,
    account_id: str,
    client_id: str,
    assessment: AiMedicalAssessment,
    tz_name: str | None = None,
) -> dict[str, Any]:
    """Map the persisted assessment. Never copies Nexus client or org ids.

    ``assessmentId`` is the persisted ``ai_medical_assessment.id``.
    Retries must reuse that id; they must not generate a new assessment.
    """
    tz = ZoneInfo(tz_name or settings.tz)
    generated = assessment.generated_at
    for_date = assessment.for_date
    if generated is not None:
        generated_at = _as_utc_z(generated, tz)
    elif for_date is not None:
        day = for_date if isinstance(for_date, date) else date.fromisoformat(str(for_date))
        generated_at = _as_utc_z(datetime.combine(day, datetime.min.time()), tz)
    else:
        generated_at = _as_utc_z(datetime.now(), tz)

    if for_date is None:
        for_date_s = generated_at[:10]
    elif isinstance(for_date, date):
        for_date_s = for_date.isoformat()
    else:
        for_date_s = str(for_date)[:10]

    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "assessmentId": str(int(assessment.id)),
        "source": SOURCE,
        "companionId": companion_id,
        "accountId": account_id,
        "clientId": client_id,
        "generatedAt": generated_at,
        "forDate": for_date_s,
        "assessment": {
            "riskLevel": _risk_level(assessment.risk_level),
            "confidence": _confidence_unit(assessment.confidence),
            "concerns": _parse_string_list(assessment.concerns_json),
            "recommendations": _parse_string_list(assessment.recommendations_json),
        },
    }
    return payload


def dump_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def validate_bridgingcare_payload(payload: Any) -> list[str]:
    """Return contract violations. Empty list means the payload is sendable."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload is not an object"]
    extra = set(payload) - set(TOP_LEVEL_KEYS)
    missing = [key for key in TOP_LEVEL_KEYS if key not in payload]
    if extra:
        errors.append("unknown fields: " + ",".join(sorted(extra)))
    if missing:
        errors.append("missing fields: " + ",".join(missing))
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        errors.append("schemaVersion")
    if payload.get("source") != SOURCE:
        errors.append("source")
    assessment_id = payload.get("assessmentId")
    if not isinstance(assessment_id, str) or not assessment_id.strip():
        errors.append("assessmentId")
    if not _COMPANION.fullmatch(str(payload.get("companionId") or "")):
        errors.append("companionId")
    if not _UUID.fullmatch(str(payload.get("accountId") or "")):
        errors.append("accountId")
    if not _UUID.fullmatch(str(payload.get("clientId") or "")):
        errors.append("clientId")
    if not _UTC_Z.fullmatch(str(payload.get("generatedAt") or "")):
        errors.append("generatedAt")
    if not _DATE.fullmatch(str(payload.get("forDate") or "")):
        errors.append("forDate")
    inner = payload.get("assessment")
    if not isinstance(inner, dict):
        errors.append("assessment")
        return errors
    extra_inner = set(inner) - set(ASSESSMENT_KEYS)
    missing_inner = [key for key in ASSESSMENT_KEYS if key not in inner]
    if extra_inner:
        errors.append("unknown assessment fields: " + ",".join(sorted(extra_inner)))
    if missing_inner:
        errors.append("missing assessment fields: " + ",".join(missing_inner))
    if inner.get("riskLevel") not in RISK_LEVELS:
        errors.append("riskLevel")
    confidence = inner.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        errors.append("confidence")
    elif not 0.0 <= float(confidence) <= 1.0:
        errors.append("confidence")
    if not isinstance(inner.get("concerns"), list) or not all(
        isinstance(item, str) for item in inner.get("concerns") or []
    ):
        errors.append("concerns")
    if not isinstance(inner.get("recommendations"), list) or not all(
        isinstance(item, str) for item in inner.get("recommendations") or []
    ):
        errors.append("recommendations")
    return errors


def required_payload_fields(payload: dict[str, Any]) -> bool:
    return not validate_bridgingcare_payload(payload)
