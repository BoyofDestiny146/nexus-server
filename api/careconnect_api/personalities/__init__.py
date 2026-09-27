"""Agent Personality library."""
from .render import (
    LEGACY_PERSONALITY_ID,
    apply_assistant_name,
    compose_personality_prompt,
    resolve_assistant_name,
)
from .seeds import DEFAULT_PERSONALITY_ID, WITTY_TECH_SIDEKICK_ID
from .store import (
    catalog_personality,
    default_personality_id,
    duplicate_personality,
    get_personality,
    list_personalities,
    personality_mapping,
    public_personality,
    seed_system_personalities,
)

__all__ = [
    "DEFAULT_PERSONALITY_ID",
    "LEGACY_PERSONALITY_ID",
    "WITTY_TECH_SIDEKICK_ID",
    "apply_assistant_name",
    "catalog_personality",
    "compose_personality_prompt",
    "default_personality_id",
    "duplicate_personality",
    "get_personality",
    "list_personalities",
    "public_personality",
    "resolve_assistant_name",
    "seed_system_personalities",
]
