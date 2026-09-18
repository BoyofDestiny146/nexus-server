"""Redis pub/sub for WebSocket fan-out.

Channels
--------
* ``cc:agent:{agent_id}:chat``       — new chat turns (user + assistant)
* ``cc:agent:{agent_id}:assessment`` — new triage assessments

The publisher side is called from the bridge → API notify endpoint
(:mod:`careconnect_api.routers.internal`); the subscriber side runs inside
the ``/ws/agent/{id}`` handler in :mod:`careconnect_api.routers.ws`.

A single ``redis.asyncio.Redis`` client is reused for the lifetime of the
process — its underlying connection pool is async-safe and the publisher
just reuses pooled connections. Subscribers (PubSub objects) are created
per-connection so each WebSocket gets its own duplex pipe.
"""
from __future__ import annotations

import json
import logging

import redis.asyncio as redis

from .settings import settings


log = logging.getLogger("pubsub")


_pool: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """Return the process-wide async Redis client, lazily connecting on first
    call. ``decode_responses=True`` so we get ``str`` back from ``get_message``
    instead of having to decode bytes at every callsite."""
    global _pool
    if _pool is None:
        _pool = redis.from_url(settings.redis_url, decode_responses=True)
    return _pool


async def close_redis() -> None:
    """Tear the client down on lifespan shutdown so uvicorn's reload doesn't
    leak file descriptors."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


def chat_channel(agent_id: str) -> str:
    return f"cc:agent:{agent_id}:chat"


def assessment_channel(agent_id: str) -> str:
    return f"cc:agent:{agent_id}:assessment"


async def publish_chat_turn(agent_id: str, payload: dict) -> int:
    """Publish a ``chat.turn`` envelope. Returns subscriber count from
    ``PUBLISH`` — useful for telemetry / smoke tests but never load-bearing
    (zero subscribers is a normal state)."""
    r = await get_redis()
    msg = json.dumps({"type": "chat.turn", "payload": payload})
    return await r.publish(chat_channel(agent_id), msg)


async def publish_assessment_updated(agent_id: str, payload: dict) -> int:
    """Publish an ``assessment.updated`` envelope. Wired now so the triage
    runner can call it without a second round-trip to add the helper later."""
    r = await get_redis()
    msg = json.dumps({"type": "assessment.updated", "payload": payload})
    return await r.publish(assessment_channel(agent_id), msg)
