"""Internal Revel display hooks for later calendar, sensor, and care events.

These functions are the only intended callers besides ``POST /api/internal/revel/display``.
They never accept GraphQL, device commands, table IDs, or row IDs. Calendar and
sensor pipelines must import from here later — they are not wired yet.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .revel_display import (
    RETURN_HOME,
    SHOW_APPOINTMENT_REMINDER,
    SHOW_CARE_ALERT,
    SHOW_HOME,
    SHOW_MEDICATION_REMINDER,
    SHOW_SENSOR_ALERT,
    apply_display_state,
)

TEST_INTENT = SHOW_HOME
TEST_FIELDS = ("title", "message", "imageUrl", "screen", "priority", "expiresAt", "updatedAt")


async def request_home(
    db: AsyncSession,
    *,
    agent_id: str,
    title: str | None = None,
    message: str | None = None,
    image_url: str | None = None,
    priority: Any = None,
    expires_at: str | None = None,
    source: str | None = "internal",
    tag: str | None = None,
) -> dict[str, Any]:
    return await apply_display_state(
        db,
        agent_id=agent_id,
        intent=SHOW_HOME,
        title=title,
        message=message,
        image_url=image_url,
        priority=priority,
        expires_at=expires_at,
        source=source,
        tag=tag,
    )


async def request_return_home(
    db: AsyncSession,
    *,
    agent_id: str,
    title: str | None = None,
    message: str | None = None,
    image_url: str | None = None,
    priority: Any = None,
    expires_at: str | None = None,
    source: str | None = "internal",
    tag: str | None = None,
) -> dict[str, Any]:
    """Map RETURN_HOME onto screen=home. expiresAt is stored for a future scheduler."""
    return await apply_display_state(
        db,
        agent_id=agent_id,
        intent=RETURN_HOME,
        title=title,
        message=message,
        image_url=image_url,
        priority=priority,
        expires_at=expires_at,
        source=source,
        tag=tag,
    )


async def request_appointment_reminder(
    db: AsyncSession,
    *,
    agent_id: str,
    title: str | None = None,
    message: str | None = None,
    image_url: str | None = None,
    priority: Any = None,
    expires_at: str | None = None,
    source: str | None = "calendar",
    tag: str | None = None,
) -> dict[str, Any]:
    """Boundary for Google Calendar. The calendar poller does not call this yet."""
    return await apply_display_state(
        db,
        agent_id=agent_id,
        intent=SHOW_APPOINTMENT_REMINDER,
        title=title,
        message=message,
        image_url=image_url,
        priority=priority,
        expires_at=expires_at,
        source=source or "calendar",
        tag=tag,
    )


async def request_medication_reminder(
    db: AsyncSession,
    *,
    agent_id: str,
    title: str | None = None,
    message: str | None = None,
    image_url: str | None = None,
    priority: Any = None,
    expires_at: str | None = None,
    source: str | None = "internal",
    tag: str | None = None,
) -> dict[str, Any]:
    return await apply_display_state(
        db,
        agent_id=agent_id,
        intent=SHOW_MEDICATION_REMINDER,
        title=title,
        message=message,
        image_url=image_url,
        priority=priority,
        expires_at=expires_at,
        source=source,
        tag=tag,
    )


async def request_care_alert(
    db: AsyncSession,
    *,
    agent_id: str,
    title: str | None = None,
    message: str | None = None,
    image_url: str | None = None,
    priority: Any = None,
    expires_at: str | None = None,
    source: str | None = "sensor",
    tag: str | None = None,
) -> dict[str, Any]:
    """Boundary for future CareConnect / sensor care alerts. Not auto-triggered."""
    return await apply_display_state(
        db,
        agent_id=agent_id,
        intent=SHOW_CARE_ALERT,
        title=title,
        message=message,
        image_url=image_url,
        priority=priority,
        expires_at=expires_at,
        source=source or "sensor",
        tag=tag,
    )


async def request_sensor_alert(
    db: AsyncSession,
    *,
    agent_id: str,
    title: str | None = None,
    message: str | None = None,
    image_url: str | None = None,
    priority: Any = None,
    expires_at: str | None = None,
    source: str | None = "sensor",
    tag: str | None = None,
) -> dict[str, Any]:
    """Boundary for future sensor events. Not auto-triggered."""
    return await apply_display_state(
        db,
        agent_id=agent_id,
        intent=SHOW_SENSOR_ALERT,
        title=title,
        message=message,
        image_url=image_url,
        priority=priority,
        expires_at=expires_at,
        source=source or "sensor",
        tag=tag,
    )


async def apply_test_display(
    db: AsyncSession,
    *,
    agent_id: str,
    title: str | None = None,
    message: str | None = None,
    image_url: str | None = None,
    priority: Any = None,
    expires_at: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """First-screen dry-run helper. Uses SHOW_HOME; screen cannot be chosen by the caller."""
    return await request_home(
        db,
        agent_id=agent_id,
        title=title,
        message=message,
        image_url=image_url,
        priority=priority,
        expires_at=expires_at,
        source="internal",
        tag=tag,
    )
