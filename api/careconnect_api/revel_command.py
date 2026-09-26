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


def _matched_action_result(action: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Shared keyword/tag command tail. Never mutates Revel."""
    intent = str(action.get("intent") or "")
    tag = action.get("revelTag")
    device_id = meta.get("deviceId")
    device_name = meta.get("deviceName")
    if not tag or not device_id:
        return {
            "matched": True,
            "executed": False,
            "executeEnabled": EXECUTE_ENABLED,
            "intent": intent,
            "revelTag": tag,
            "deviceName": device_name,
            "reason": "not_configured",
        }
    if not EXECUTE_ENABLED:
        return {
            "matched": True,
            "executed": False,
            "executeEnabled": False,
            "intent": intent,
            "revelTag": tag,
            "deviceName": device_name,
            "reason": "execute_not_enabled",
        }
    return {
        "matched": True,
        "executed": False,
        "executeEnabled": True,
        "intent": intent,
        "revelTag": tag,
        "deviceName": device_name,
        "reason": "execute_not_enabled",
    }


async def _connected_revel(db: AsyncSession, agent_id: str):
    return (
        await db.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == PROVIDER_REVEL,
            )
        )
    ).scalar_one_or_none()


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
    row = await _connected_revel(db, agent_id)
    if row is None or (row.status or "") != "connected":
        return {"matched": False, "executed": False, "reason": "not_connected"}

    meta = load_meta(row)
    action = match_enabled_action(remainder, meta.get("actions"))
    if action is None:
        return {"matched": False, "executed": False, "reason": "no_phrase_match"}

    log.info(
        "revel command matched agent=%s intent=%s execute=%s",
        agent_id,
        action.get("intent"),
        EXECUTE_ENABLED,
    )
    return _matched_action_result(action, meta)


async def evaluate_configured_tag(
    db: AsyncSession,
    *,
    agent_id: str,
    tag: str,
) -> dict[str, Any]:
    """Reuse the keyword command tail for an already-approved revelTag.

    Does not match phrases. Does not read document or assistant text.
    Does not call apply_device_tags.
    """
    target = (tag or "").strip()
    if not target:
        return {"matched": False, "executed": False, "reason": "missing_revel_tag"}
    row = await _connected_revel(db, agent_id)
    if row is None or (row.status or "") != "connected":
        return {"matched": False, "executed": False, "reason": "not_connected"}
    meta = load_meta(row)
    action = None
    for item in meta.get("actions") or []:
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            continue
        if isinstance(item.get("revelTag"), str) and item["revelTag"].strip() == target:
            action = item
            break
    if action is None:
        return {
            "matched": False,
            "executed": False,
            "reason": "no_matching_action",
            "revelTag": target,
        }
    log.info(
        "revel tag command agent=%s intent=%s tag=%s execute=%s",
        agent_id,
        action.get("intent"),
        target,
        EXECUTE_ENABLED,
    )
    return _matched_action_result(action, meta)
