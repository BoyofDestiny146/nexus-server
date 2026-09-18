"""Tests for two-admin bootstrap (Task 2)."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, verify_password
from careconnect_api.models import SysUser


@pytest.mark.asyncio
async def test_seed_two_admins_creates_both(db_session: AsyncSession):
    """seed_two_admins should create admin1 and admin2 with correct roles."""
    from careconnect_api.bootstrap_root import seed_two_admins

    await seed_two_admins(db_session)

    users = (
        await db_session.execute(
            select(SysUser).where(SysUser.super_admin == ROLE_ROOT)
        )
    ).scalars().all()

    usernames = {u.username for u in users}
    assert "admin1" in usernames
    assert "admin2" in usernames


@pytest.mark.asyncio
async def test_seed_two_admins_passwords_from_env(db_session: AsyncSession):
    """Passwords should come from CC_ADMIN1_PASSWORD / CC_ADMIN2_PASSWORD env vars."""
    from careconnect_api.bootstrap_root import seed_two_admins

    await seed_two_admins(db_session)

    user1 = (
        await db_session.execute(select(SysUser).where(SysUser.username == "admin1"))
    ).scalar_one_or_none()
    user2 = (
        await db_session.execute(select(SysUser).where(SysUser.username == "admin2"))
    ).scalar_one_or_none()

    assert user1 is not None
    assert user2 is not None

    # Passwords must match what the env vars have (set by conftest).
    assert verify_password("AdminPassword1!", user1.password)
    assert verify_password("AdminPassword2!", user2.password)


@pytest.mark.asyncio
async def test_seed_two_admins_idempotent(db_session: AsyncSession):
    """Running seed_two_admins twice should not create duplicate rows."""
    from careconnect_api.bootstrap_root import seed_two_admins

    await seed_two_admins(db_session)
    await seed_two_admins(db_session)

    count = len(
        (
            await db_session.execute(
                select(SysUser).where(SysUser.username.in_(["admin1", "admin2"]))
            )
        ).scalars().all()
    )
    assert count == 2


@pytest.mark.asyncio
async def test_seed_two_admins_both_active(db_session: AsyncSession):
    from careconnect_api.bootstrap_root import seed_two_admins

    await seed_two_admins(db_session)

    users = (
        await db_session.execute(
            select(SysUser).where(SysUser.username.in_(["admin1", "admin2"]))
        )
    ).scalars().all()

    for u in users:
        assert u.status == 1, f"user {u.username} should be active (status=1)"
        assert u.super_admin == ROLE_ROOT, f"user {u.username} should be ROLE_ROOT"
