"""Per-device Agent Personality override. NULL inherits the bound client."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .client_profile import build_system_prompt, empty_profile, load_profile
from .envelope import APIException
from .models import AiAgent, AiDevice
from .personalities.store import get_personality


async def effective_device_personality_id(db: AsyncSession, device: AiDevice) -> str | None:
    """Return the override id only when the library row exists and is active."""
    raw = (device.personality_id or "").strip() or None
    if not raw:
        return None
    row = await get_personality(db, raw)
    if row is None or not row.is_active:
        return None
    return row.id


def client_personality_id(agent: AiAgent | None) -> str | None:
    if agent is None:
        return None
    profile = load_profile(agent.profile_json)
    pid = str(profile.get("personalityId") or "").strip()
    return pid or None


async def assignment_view(db: AsyncSession, device: AiDevice) -> dict[str, Any]:
    agent = await db.get(AiAgent, device.agent_id) if device.agent_id else None
    stored = (device.personality_id or "").strip() or None
    effective = await effective_device_personality_id(db, device)
    unavailable = bool(stored and effective is None)
    source = "device" if effective else ("client" if agent else "unassigned")
    return {
        "personalityId": effective,
        "storedPersonalityId": stored,
        "clientPersonalityId": client_personality_id(agent),
        "source": source,
        "unbound": agent is None,
        "personalityUnavailable": unavailable,
    }


async def assign_device_personality(
    db: AsyncSession,
    device: AiDevice,
    personality_id: str | None,
) -> dict[str, Any]:
    raw = (personality_id or "").strip() or None
    if raw:
        row = await get_personality(db, raw)
        if row is None:
            raise APIException(400, "personality not found")
        if not row.is_active:
            raise APIException(400, "personality is inactive")
        device.personality_id = row.id
    else:
        device.personality_id = None
    await db.commit()
    await db.refresh(device)
    return await assignment_view(db, device)


async def resolve_session_persona(db: AsyncSession, mac: str) -> dict[str, Any] | None:
    """Compose the session prompt using device override or client snapshot.

    Reuses ``build_system_prompt`` / library templates. Inactive or deleted
    overrides inherit the bound client's stored ``system_prompt``.
    """
    from .watcher_device import get_watcher_device

    device = await get_watcher_device(db, mac)
    if device is None:
        return None
    agent = await db.get(AiAgent, device.agent_id) if device.agent_id else None
    override_id = await effective_device_personality_id(db, device)
    bot_name = (agent.bot_name if agent else None) or None
    agent_name = (agent.agent_name if agent else None) or None
    first_name = (agent_name or "").strip().split()[0] if agent_name else None

    prompt: str | None = None
    source = "unassigned"
    if override_id:
        row = await get_personality(db, override_id)
        personality = None
        if row is not None:
            personality = {"name": row.name, "prompt_template": row.prompt_template}
        profile = load_profile(agent.profile_json) if agent is not None else empty_profile()
        name = agent_name or (device.alias or "").strip() or "the person you are speaking with"
        prompt = build_system_prompt(
            name=name,
            profile=profile,
            assistant_name=bot_name,
            personality=personality,
        )
        source = "device"
    elif agent is not None and (agent.system_prompt or "").strip():
        prompt = agent.system_prompt
        source = "client"

    return {
        "agent_id": agent.id if agent is not None else None,
        "agent_name": agent_name,
        "system_prompt": prompt,
        "first_name": first_name,
        "bot_name": (bot_name or "").strip() or None,
        "personalityId": override_id,
        "personalitySource": source,
    }
