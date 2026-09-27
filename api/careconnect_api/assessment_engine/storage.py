"""Read/write ``ai_agent.profile_json.assessmentProfile`` without a migration.

Unknown keys on profile_json are preserved. This module never rewrites
wizard fields (dob, tags, …) and never backfills missing assessmentProfile.
"""
from __future__ import annotations

from typing import Any

from ..client_profile import dump_profile, load_profile, merge_profile
from ..models import AiAgent
from .profiles import (
    PROFILE_JSON_KEY,
    AssessmentProfileDefinition,
    catalog_dicts,
    definition_for,
    public_profile_dict,
    stored_profile_value,
)


def stored_assessment_profile_value(profile_json: Any) -> Any:
    return stored_profile_value(load_profile(profile_json))


def resolved_definition(profile_json: Any) -> AssessmentProfileDefinition:
    return definition_for(stored_assessment_profile_value(profile_json))


def resolved_assessment_profile_dict(profile_json: Any) -> dict[str, Any]:
    return public_profile_dict(resolved_definition(profile_json))


def profile_payload(profile_json: Any) -> dict[str, Any]:
    return {
        "assessmentProfile": resolved_assessment_profile_dict(profile_json),
        "profiles": catalog_dicts(),
    }


def apply_assessment_profile(agent: AiAgent, profile_id: str) -> None:
    """Set ``assessmentProfile`` on profile_json; leave every other key intact."""
    existing = load_profile(agent.profile_json)
    merged = merge_profile(existing, {PROFILE_JSON_KEY: profile_id})
    agent.profile_json = dump_profile(merged)


def apply_assessment_schedule(agent: AiAgent, schedule: dict[str, Any]) -> None:
    """Set ``assessmentSchedule`` on profile_json; leave every other key intact."""
    existing = load_profile(agent.profile_json)
    merged = merge_profile(existing, {"assessmentSchedule": dict(schedule)})
    agent.profile_json = dump_profile(merged)
