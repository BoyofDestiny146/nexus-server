"""Allowlisted Revel voice-command evaluation. Never mutates Revel."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ClientIntegration
from .revel_config import EXECUTE_ENABLED, load_meta
from .revel_match import match_enabled_action

log = logging.getLogger("revel_command")

PROVIDER_REVEL = "revel"


async def evaluate_voice_command(
    db: AsyncSession,
    *,
    agent_id: str,
    utterance: str,
    remainder: str,
) -> dict[str, Any]:
    """Match remainder against this client's enabled phrases only.

    Does not decrypt the API key. Does not call Revel. Voice may not supply
    a tag, device id, URL, or GraphQL.
    """
    _ = utterance  # retained for later timeline; unused until execute is enabled
    row = (
        await db.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == PROVIDER_REVEL,
            )
        )
    ).scalar_one_or_none()
    if row is None or (row.status or "") != "connected":
        return {"matched": False, "executed": False, "reason": "not_connected"}

    meta = load_meta(row)
    action = match_enabled_action(remainder, meta.get("actions"))
    if action is None:
        return {"matched": False, "executed": False, "reason": "no_phrase_match"}

    intent = str(action.get("intent") or "")
    tag = action.get("revelTag")
    device_id = meta.get("deviceId")
    device_name = meta.get("deviceName")
    log.info(
        "revel command matched agent=%s intent=%s execute=%s",
        agent_id,
        intent,
        EXECUTE_ENABLED,
    )
    if not tag or not device_id:
        return {
            "matched": True,
            "executed": False,
            "executeEnabled": EXECUTE_ENABLED,
            "intent": intent,
            "deviceName": device_name,
            "reason": "not_configured",
        }
    if not EXECUTE_ENABLED:
        return {
            "matched": True,
            "executed": False,
            "executeEnabled": False,
            "intent": intent,
            "deviceName": device_name,
            "reason": "execute_not_enabled",
        }
    return {
        "matched": True,
        "executed": False,
        "executeEnabled": True,
        "intent": intent,
        "deviceName": device_name,
        "reason": "execute_not_enabled",
    }
