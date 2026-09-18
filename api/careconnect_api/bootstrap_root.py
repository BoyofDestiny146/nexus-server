"""Bootstrap admin accounts on first boot. Idempotent — re-running is safe.

Two admin accounts are always created on first boot:
  username: admin1 / admin2  (both super_admin=2, root tier)
  passwords: read from env CC_ADMIN1_PASSWORD / CC_ADMIN2_PASSWORD, or from
             secret files ~/.config/careconnect/admin1-password /
             ~/.config/careconnect/admin2-password (auto-generated if absent).

The interactive CLI path (python -m careconnect_api.bootstrap_root) is still
available for manual one-off admin management.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import ROLE_ADMIN, ROLE_ROOT, hash_password
from .db import async_session_factory
from .models import SysUser
from .settings import CFG_DIR, _read_or_create_secret, settings


log = logging.getLogger("bootstrap")


def _next_user_id() -> int:
    """Snowflake-ish ID matching the format the existing rows use (19-digit)."""
    import time
    import secrets
    # 13-digit ms timestamp + 6 random digits = 19 digits, fits BIGINT.
    return int(f"{int(time.time() * 1000)}{secrets.randbelow(1_000_000):06d}")


async def upsert_root(db: AsyncSession, username: str, password: str) -> tuple[int, str]:
    """Returns (user_id, action) where action is 'created', 'promoted', or 'updated'."""
    existing = (
        await db.execute(select(SysUser).where(SysUser.username == username))
    ).scalar_one_or_none()

    pwhash = hash_password(password)

    if existing is None:
        new_id = _next_user_id()
        db.add(SysUser(
            id=new_id,
            username=username,
            password=pwhash,
            super_admin=ROLE_ROOT,
            status=1,
        ))
        await db.commit()
        return new_id, "created"

    was_root = (existing.super_admin or 0) == ROLE_ROOT
    existing.password = pwhash
    existing.super_admin = ROLE_ROOT
    existing.status = 1
    await db.commit()
    return existing.id, ("updated" if was_root else "promoted")


def _resolve_admin_password(env_var: str, secret_file_name: str) -> str:
    """Resolve an admin password: env var wins, then secret file (auto-generated)."""
    from_env = os.environ.get(env_var, "").strip()
    if from_env:
        return from_env
    path = CFG_DIR / secret_file_name
    return _read_or_create_secret(path, generator=lambda: _gen_password())


def _gen_password() -> str:
    """Generate a strong random password: 24 chars, URL-safe alphabet."""
    import secrets as _secrets
    return _secrets.token_urlsafe(18)


async def seed_two_admins(db: AsyncSession) -> None:
    """Create (or update) the two default admin accounts on first boot.

    Usernames: admin1, admin2 — both get ROLE_ROOT (super_admin=2).
    Passwords from: CC_ADMIN1_PASSWORD / CC_ADMIN2_PASSWORD env vars,
    falling back to secret files ~/.config/careconnect/admin1-password /
    ~/.config/careconnect/admin2-password (auto-generated on first call).

    Idempotent: if rows already exist, their passwords are refreshed and role
    is re-asserted as ROLE_ROOT.
    """
    accounts = [
        ("admin1", _resolve_admin_password("CC_ADMIN1_PASSWORD", "admin1-password")),
        ("admin2", _resolve_admin_password("CC_ADMIN2_PASSWORD", "admin2-password")),
    ]
    for username, password in accounts:
        uid, action = await upsert_root(db, username, password)
        log.info("bootstrap admin %s: id=%s action=%s", username, uid, action)


async def demote_other_roots(db: AsyncSession, keep_user_id: int) -> int:
    """Ensure exactly one root admin: any other super_admin=2 rows get
    demoted to super_admin=1 (admin)."""
    others = (
        await db.execute(
            select(SysUser).where(
                SysUser.super_admin == ROLE_ROOT,
                SysUser.id != keep_user_id,
            )
        )
    ).scalars().all()
    for u in others:
        u.super_admin = ROLE_ADMIN
    if others:
        await db.commit()
    return len(others)


async def amain() -> int:
    p = argparse.ArgumentParser(description="Bootstrap careconnect root admin (super_admin=2).")
    p.add_argument("--username", help="Root admin username (will prompt if omitted)")
    p.add_argument("--password", help="Root admin password (will prompt if omitted)")
    p.add_argument(
        "--no-demote-others",
        action="store_true",
        help="Skip demoting any other existing root admins (default: demote them to admin).",
    )
    p.add_argument(
        "--seed-defaults",
        action="store_true",
        help="Create the two default admin accounts (admin1/admin2) from env/secret-files.",
    )
    args = p.parse_args()

    if args.seed_defaults:
        async with async_session_factory() as db:
            await seed_two_admins(db)
        print("  default admin accounts seeded (admin1, admin2)")
        return 0

    username = args.username or input("Root username: ").strip()
    if not username:
        print("ERROR: username required", file=sys.stderr)
        return 2

    password = args.password
    if not password:
        password = getpass.getpass("Root password: ")
        confirm = getpass.getpass("Confirm:        ")
        if password != confirm:
            print("ERROR: passwords don't match", file=sys.stderr)
            return 2
    if not password or len(password) < 6:
        print("ERROR: password must be at least 6 characters", file=sys.stderr)
        return 2

    async with async_session_factory() as db:
        user_id, action = await upsert_root(db, username, password)
        print(f"  root admin {action}: id={user_id}  username={username!r}")

        if not args.no_demote_others:
            n = await demote_other_roots(db, user_id)
            if n:
                print(f"  demoted {n} other root admin(s) to admin (super_admin=1)")

    print()
    print(f"  done. Log in as {username!r}.")
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
