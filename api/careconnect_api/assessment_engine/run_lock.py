"""In-process + Redis locks so one client cannot double-run an assessment."""
from __future__ import annotations

import asyncio
import logging

from ..pubsub import get_redis

log = logging.getLogger("assessment_lock")

_guard = asyncio.Lock()
_held: set[str] = set()
_escalation_seen: set[str] = set()

LOCK_TTL_S = 600
ESCALATION_TTL_S = 86_400


def _lock_key(agent_id: str) -> str:
    return f"cc:assessment:lock:{agent_id}"


def _escalation_key(agent_id: str, message_id: int | str) -> str:
    return f"cc:assessment:escalation:{agent_id}:{message_id}"


async def try_acquire_agent_lock(agent_id: str, ttl_s: int = LOCK_TTL_S) -> bool:
    async with _guard:
        if agent_id in _held:
            return False
        _held.add(agent_id)
    try:
        redis = await get_redis()
        ok = await redis.set(_lock_key(agent_id), "1", nx=True, ex=max(30, int(ttl_s)))
        if not ok:
            async with _guard:
                _held.discard(agent_id)
            return False
    except Exception:
        log.debug("assessment lock: redis unavailable, using in-process lock agent=%s", agent_id)
    return True


async def release_agent_lock(agent_id: str) -> None:
    async with _guard:
        _held.discard(agent_id)
    try:
        redis = await get_redis()
        await redis.delete(_lock_key(agent_id))
    except Exception:
        log.debug("assessment lock: redis release skipped agent=%s", agent_id)


async def claim_escalation_message(agent_id: str, message_id: int | str) -> bool:
    """Return True once per (agent, chat-history row). Prevents duplicate runs."""
    key = f"{agent_id}:{message_id}"
    async with _guard:
        if key in _escalation_seen:
            return False
        _escalation_seen.add(key)
    try:
        redis = await get_redis()
        ok = await redis.set(
            _escalation_key(agent_id, message_id),
            "1",
            nx=True,
            ex=ESCALATION_TTL_S,
        )
        if not ok:
            return False
    except Exception:
        log.debug("escalation claim: redis unavailable agent=%s message=%s", agent_id, message_id)
    return True


async def release_escalation_message(agent_id: str, message_id: int | str) -> None:
    """Allow the same chat row to trigger again after a failed persist."""
    key = f"{agent_id}:{message_id}"
    async with _guard:
        _escalation_seen.discard(key)
    try:
        redis = await get_redis()
        await redis.delete(_escalation_key(agent_id, message_id))
    except Exception:
        log.debug("escalation release skipped agent=%s message=%s", agent_id, message_id)


def reset_locks_for_tests() -> None:
    _held.clear()
    _escalation_seen.clear()
