"""Best-effort assessment.updated publish after a successful persist."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..pubsub import publish_assessment_updated

log = logging.getLogger("assessment_notify")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def publish_assessment_run(
    agent_id: str,
    *,
    profile_id: str,
    generated_at: Any,
) -> None:
    """Slim envelope only — the open client page refetches latest over HTTP."""
    try:
        await publish_assessment_updated(
            agent_id,
            {
                "agentId": agent_id,
                "profileId": profile_id,
                "generatedAt": _iso(generated_at),
            },
        )
    except Exception:
        log.warning("assessment.updated publish failed agent=%s", agent_id)
