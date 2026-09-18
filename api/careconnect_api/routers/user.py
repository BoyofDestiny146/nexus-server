"""User auth + profile endpoints.

Mounted at /api/user/* by main.py.

Phase 1 ships login + info + pub-config. The captcha endpoint is a no-op
stub (returns an empty image-id) so the existing Vue dashboard, which
still asks for one during the migration window, doesn't crash. The new
Next.js login won't call it at all.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    CurrentUser,
    ROLE_NAMES,
    get_current_user,
    issue_token,
    verify_password,
)
from ..db import get_db
from ..envelope import APIException
from ..models import SysUser


router = APIRouter(prefix="/user", tags=["user"])


class LoginPayload(BaseModel):
    username: str
    password: str
    # Legacy fields the existing Vue dashboard still sends — accepted but ignored.
    captcha: str | None = None
    captchaId: str | None = None


class TokenResponse(BaseModel):
    token: str
    expire: int       # seconds remaining
    expireAt: datetime
    clientHash: str = ""  # legacy field; the Vue client reads but ignores


class UserDetail(BaseModel):
    id: int
    username: str
    superAdmin: int   # 0|1|2 — see auth.ROLE_*
    role: str
    status: int = 1
    realName: str | None = None


@router.post("/login", response_model=None)
async def login(payload: LoginPayload, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    user = (
        await db.execute(select(SysUser).where(SysUser.username == payload.username))
    ).scalar_one_or_none()
    if user is None:
        raise APIException(401, "username or password is incorrect")
    if not verify_password(payload.password, user.password):
        raise APIException(401, "username or password is incorrect")
    if user.status is not None and user.status == 0:
        raise APIException(403, "account is disabled")

    role = int(user.super_admin or 0)
    token, expire = issue_token(user.id, user.username, role)

    # Match the existing dashboard's TokenDTO shape so we can swap baseURLs
    # without touching client code in Phase 4.
    return {
        "token": token,
        "expire": int((expire - datetime.now(tz=expire.tzinfo)).total_seconds()),
        "expireAt": expire,
        "clientHash": "",
    }


@router.get("/info", response_model=None)
async def info(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "superAdmin": user.role,
        "role": user.role_name,
        "status": 1,
    }


@router.get("/pub-config", response_model=None)
def pub_config() -> dict[str, Any]:
    """Public config the Vue dashboard reads to decide whether to show the
    register button etc. We always return a sane admin-only config."""
    return {
        "version": "careconnect-1.0",
        "name": "careconnect",
        "allowUserRegister": False,
        "enableMobileRegister": False,
        "mobileAreaList": [],
        "beianIcpNum": "",
        "beianGaNum": "",
    }


@router.get("/captcha", response_model=None)
def captcha_stub() -> dict[str, Any]:
    """No-op captcha. We dropped CAPTCHAs in the rewrite. The endpoint
    exists only because the un-rewritten Vue dashboard fetches it on
    mount; returning a stub avoids a 404 in the network tab during the
    migration window. The new Next.js login won't call this at all."""
    return {"captchaId": "noop", "img": ""}
