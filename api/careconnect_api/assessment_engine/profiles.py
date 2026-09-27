"""Assessment Profile registry — single source of truth for ids and names.

Phase 2 registers four profiles. ``care_wellness`` and ``sales_product`` are
implemented. The remaining two appear in the dashboard as Coming soon and
cannot become the active engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..envelope import APIException

CARE_WELLNESS_ID = "care_wellness"
SALES_PRODUCT_ID = "sales_product"
INFORMATION_KIOSK_ID = "information_kiosk"
OPERATIONS_STAFF_ID = "operations_staff"

PROFILE_JSON_KEY = "assessmentProfile"


@dataclass(frozen=True)
class AssessmentProfileDefinition:
    id: str
    displayName: str
    implemented: bool
    description: str


ASSESSMENT_PROFILES: tuple[AssessmentProfileDefinition, ...] = (
    AssessmentProfileDefinition(
        id=CARE_WELLNESS_ID,
        displayName="Care & Wellness",
        implemented=True,
        description=(
            "Conversation triage for care and wellness: risk level, confidence, "
            "concerns, and recommendations from recent dialogue."
        ),
    ),
    AssessmentProfileDefinition(
        id=SALES_PRODUCT_ID,
        displayName="Sales & Product Guide",
        implemented=True,
        description=(
            "Session-scoped sales and product-guide assessment: interest, "
            "products discussed, needs, questions, objections, and follow-up."
        ),
    ),
    AssessmentProfileDefinition(
        id=INFORMATION_KIOSK_ID,
        displayName="Information Kiosk",
        implemented=False,
        description="Public information kiosk assessment. Coming soon.",
    ),
    AssessmentProfileDefinition(
        id=OPERATIONS_STAFF_ID,
        displayName="Operations & Staff Assistant",
        implemented=False,
        description="Operations and staff assistant assessment. Coming soon.",
    ),
)

_BY_ID: dict[str, AssessmentProfileDefinition] = {p.id: p for p in ASSESSMENT_PROFILES}


def list_assessment_profiles() -> list[AssessmentProfileDefinition]:
    return list(ASSESSMENT_PROFILES)


def get_profile(profile_id: str) -> AssessmentProfileDefinition | None:
    return _BY_ID.get(profile_id)


def public_profile_dict(defn: AssessmentProfileDefinition) -> dict[str, Any]:
    return {
        "id": defn.id,
        "displayName": defn.displayName,
        "implemented": defn.implemented,
        "description": defn.description,
    }


def catalog_dicts() -> list[dict[str, Any]]:
    return [public_profile_dict(p) for p in ASSESSMENT_PROFILES]


def resolve_assessment_profile_id(value: Any) -> str:
    """Map a stored/requested value to the profile that may actually run.

    Missing, empty, unknown, and unimplemented ids all resolve to
    ``care_wellness`` so a client cannot sit on an engine that does not exist.
    """
    if not isinstance(value, str):
        return CARE_WELLNESS_ID
    candidate = value.strip()
    if not candidate:
        return CARE_WELLNESS_ID
    defn = _BY_ID.get(candidate)
    if defn is None or not defn.implemented:
        return CARE_WELLNESS_ID
    return defn.id


def definition_for(value: Any) -> AssessmentProfileDefinition:
    return _BY_ID[resolve_assessment_profile_id(value)]


def parse_selectable_profile_id(value: Any) -> str:
    """Validate a client-supplied id for persistence.

    Arbitrary strings are rejected. Unimplemented registry ids are rejected
    so they cannot be stored as the active profile.
    """
    if not isinstance(value, str) or not value.strip():
        raise APIException(400, "assessmentProfile is required")
    candidate = value.strip()
    defn = _BY_ID.get(candidate)
    if defn is None:
        raise APIException(400, "unknown assessment profile")
    if not defn.implemented:
        raise APIException(400, "assessment profile is not available yet")
    return defn.id


def stored_profile_value(profile: Mapping[str, Any] | None) -> Any:
    if not profile:
        return None
    return profile.get(PROFILE_JSON_KEY)
