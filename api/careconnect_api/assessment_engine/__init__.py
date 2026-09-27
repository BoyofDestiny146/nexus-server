"""Nexus Assessment Engine — profile registry and thin runner wrap.

Phase 1: Care & Wellness is the only implemented profile. Execution always
delegates to :func:`careconnect_api.triage.runner.run_for_agent` without
changing prompt, window, model, or persistence.
"""
from .engine import assess_agent
from .profiles import (
    CARE_WELLNESS_ID,
    AssessmentProfileDefinition,
    definition_for,
    list_assessment_profiles,
    parse_selectable_profile_id,
    public_profile_dict,
    resolve_assessment_profile_id,
)

__all__ = [
    "CARE_WELLNESS_ID",
    "AssessmentProfileDefinition",
    "assess_agent",
    "definition_for",
    "list_assessment_profiles",
    "parse_selectable_profile_id",
    "public_profile_dict",
    "resolve_assessment_profile_id",
]
