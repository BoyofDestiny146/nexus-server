"""Assessment delivery destination stored on ``ai_agent.profile_json``.

v1 exposes only CareConnect. Canonical Nexus/CareConnect persistence always
happens after a successful assessment and is not gated by this config.

Outbound CareConnect transmission is requested when the stored destination
is CareConnect (the v1 default). An explicit non-CareConnect destination
does not enqueue or POST. Connection validity, self-host skip, and
eligibility are enforced by ``assessment_delivery``.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..client_profile import dump_profile, load_profile, merge_profile
from ..envelope import APIException
from ..models import AiAgent

DELIVERY_JSON_KEY = "assessmentDelivery"

DESTINATION_CARECONNECT = "careconnect"

# UI-exposed destinations. Do not add labels here until the destination is real.
UI_DESTINATIONS: tuple[tuple[str, str], ...] = (
    (DESTINATION_CARECONNECT, "CareConnect"),
)

# Reserved for a later mapping from destination → outbound transport.
# Not accepted or returned by the v1 API.
_FUTURE_DESTINATIONS = frozenset({"partner_api", "emr", "webhook", "none"})

_EXPOSED = frozenset(dest for dest, _label in UI_DESTINATIONS)


def default_delivery() -> dict[str, Any]:
    return {"destination": DESTINATION_CARECONNECT}


def parse_delivery(raw: Any) -> dict[str, Any]:
    """Resolve stored delivery. Unknown / future values fall back to CareConnect."""
    fallback = default_delivery()
    if not isinstance(raw, dict):
        return fallback
    dest = str(raw.get("destination") or "").strip().lower()
    if dest in _EXPOSED:
        return {"destination": dest}
    return fallback


def parse_delivery_put(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise APIException(400, "assessmentDelivery is required")
    dest = str(raw.get("destination") or "").strip().lower()
    if dest not in _EXPOSED:
        raise APIException(400, "unsupported assessment delivery destination")
    return {"destination": dest}


def apply_assessment_delivery(agent: AiAgent, delivery: Mapping[str, Any]) -> None:
    existing = load_profile(agent.profile_json)
    prev = existing.get(DELIVERY_JSON_KEY)
    combined = dict(prev) if isinstance(prev, dict) else {}
    combined.update(dict(delivery))
    merged = merge_profile(existing, {DELIVERY_JSON_KEY: combined})
    agent.profile_json = dump_profile(merged)


def resolved_delivery(profile_json: Any) -> dict[str, Any]:
    loaded = load_profile(profile_json)
    return parse_delivery(loaded.get(DELIVERY_JSON_KEY))


def delivery_view(profile_json: Any) -> dict[str, Any]:
    return {
        "assessmentDelivery": resolved_delivery(profile_json),
        "deliveryChoices": [
            {"destination": dest, "label": label} for dest, label in UI_DESTINATIONS
        ],
    }


def portal_persist_enabled(_delivery: Mapping[str, Any] | None = None) -> bool:
    """Every assessment is stored in Nexus/CareConnect regardless of destination."""
    return True


def outbound_careconnect_requested(
    delivery: Mapping[str, Any] | None = None,
    *,
    profile_json: Any = None,
) -> bool:
    """True when outbound CareConnect delivery is requested.

    Missing/invalid stored config defaults to CareConnect (v1). An explicit
    destination other than ``careconnect`` (including reserved ``none``) does
    not transmit. Portal persist is unchanged.
    """
    raw: Any = delivery
    if profile_json is not None:
        loaded = load_profile(profile_json)
        raw = loaded.get(DELIVERY_JSON_KEY)
    if not isinstance(raw, dict) or raw.get("destination") in (None, ""):
        return True
    dest = str(raw.get("destination") or "").strip().lower()
    return dest == DESTINATION_CARECONNECT


def outbound_partner_push_follows_existing_config(
    delivery: Mapping[str, Any] | None = None,
) -> bool:
    """True when the delivery setting asks for outbound CareConnect."""
    return outbound_careconnect_requested(delivery)
