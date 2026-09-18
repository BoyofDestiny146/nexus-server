"""JWT issuance/verification + bcrypt password helpers + current-user dep."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .envelope import APIException
from .models import SysUser
from .settings import settings


# Roles encoded in sys_user.super_admin (we reuse the existing column as a 3-tier flag)
ROLE_VIEWER = 0  # reserved
ROLE_ADMIN = 1
ROLE_ROOT = 2

ROLE_NAMES = {ROLE_ADMIN: "admin", ROLE_ROOT: "root", ROLE_VIEWER: "viewer"}


@dataclass
class CurrentUser:
    id: int
    username: str
    role: int

    @property
    def is_root(self) -> bool:
        return self.role == ROLE_ROOT

    @property
    def is_admin(self) -> bool:
        return self.role >= ROLE_ADMIN

    @property
    def role_name(self) -> str:
        return ROLE_NAMES.get(self.role, "unknown")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def issue_token(user_id: int, username: str, role: int) -> tuple[str, datetime]:
    """Return (token, expiry). HS256."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=settings.jwt_ttl_hours)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expire


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """Dependency: require a valid Bearer JWT, return the user."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise APIException(401, "missing bearer token")
    token = authorization.split(None, 1)[1].strip()

    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise APIException(401, "token expired")
    except jwt.PyJWTError as exc:
        raise APIException(401, f"invalid token: {exc}")

    user_id = int(payload["sub"])
    user = (await db.execute(select(SysUser).where(SysUser.id == user_id))).scalar_one_or_none()
    if user is None:
        raise APIException(401, "user not found")

    return CurrentUser(id=user.id, username=user.username, role=int(user.super_admin or 0))


def require_root(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_root:
        raise APIException(403, "root admin required")
    return user
