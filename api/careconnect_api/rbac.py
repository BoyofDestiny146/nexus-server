"""Per-admin client scoping helpers. Root sees all; admins see only the
agent_ids granted in cc_admin_client_access."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import CurrentUser
from .envelope import APIException
from .models import AdminClientAccess


async def scoped_agent_ids(db: AsyncSession, user: CurrentUser) -> list[str] | None:
    """Return the list of agent_ids the user is allowed to see, or None for
    'no scope filter' (root sees everything)."""
    if user.is_root:
        return None
    rows = (
        await db.execute(
            select(AdminClientAccess.agent_id).where(AdminClientAccess.admin_user_id == user.id)
        )
    ).scalars().all()
    return list(rows)


async def assert_can_access_agent(db: AsyncSession, user: CurrentUser, agent_id: str) -> None:
    """Raise 403 if the user can't see this agent."""
    if user.is_root:
        return
    granted = (
        await db.execute(
            select(AdminClientAccess.agent_id).where(
                AdminClientAccess.admin_user_id == user.id,
                AdminClientAccess.agent_id == agent_id,
            )
        )
    ).scalar_one_or_none()
    if granted is None:
        raise APIException(403, "this client is not in your scope")
