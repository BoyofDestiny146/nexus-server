"""Admin user management endpoints — backs the dashboard's "Admins" page.

Mounted at /api/admin/users/* by main.py. All routes are root-only
(Depends(require_root)); only the single root admin can create / modify /
delete other admin accounts and manage their per-client scope.

Six endpoints:

    GET    /admin/users                            — list all admins (+ scope summary)
    POST   /admin/users                            — create a new admin
    PUT    /admin/users/{user_id}                  — update username/role/scope
    POST   /admin/users/{user_id}/reset-password   — set a new password
    DELETE /admin/users/{user_id}                  — soft delete (status=0)
    GET    /admin/users/{user_id}/scope            — list of granted agents

Business invariants enforced:
  * Username uniqueness (409 on collision).
  * Password >= 8 chars on create / reset.
  * role must be ROLE_ADMIN (1) or ROLE_ROOT (2).
  * **There is always exactly one root admin.** Promoting someone to root
    auto-demotes any other root rows to ROLE_ADMIN. Demoting the only
    remaining root is rejected. Deleting the only remaining root is rejected.
  * The current user cannot change their own role/permissions and cannot
    delete themselves (so root can't accidentally lock everyone out).
  * scopedAgentIds is ignored for root admins (they see every agent
    regardless of cc_admin_client_access rows).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    ROLE_ADMIN,
    ROLE_NAMES,
    ROLE_ROOT,
    CurrentUser,
    hash_password,
    require_root,
)
from ..bootstrap_root import _next_user_id, demote_other_roots
from ..db import get_db
from ..envelope import APIException
from ..models import AdminClientAccess, AiAgent, SysUser


router = APIRouter(prefix="/admin/users", tags=["admin-users"])


# ---------- request bodies ----------

class CreateAdminPayload(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=8, max_length=100)
    role: int  # 1 = admin, 2 = root
    scopedAgentIds: list[str] = Field(default_factory=list)


class UpdateAdminPayload(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=50)
    role: int | None = None
    scopedAgentIds: list[str] | None = None


class ResetPasswordPayload(BaseModel):
    password: str = Field(min_length=8, max_length=100)


# ---------- response shaping ----------

def _role_name(role: int) -> str:
    return ROLE_NAMES.get(role, "unknown")


async def _scope_for_users(
    db: AsyncSession, user_ids: list[int]
) -> dict[int, list[str]]:
    """{user_id: [agent_id, ...]} for any non-root admins. Root rows get an
    empty list since cc_admin_client_access doesn't apply to them."""
    if not user_ids:
        return {}
    rows = (
        await db.execute(
            select(AdminClientAccess.admin_user_id, AdminClientAccess.agent_id)
            .where(AdminClientAccess.admin_user_id.in_(user_ids))
        )
    ).all()
    out: dict[int, list[str]] = {uid: [] for uid in user_ids}
    for uid, aid in rows:
        out.setdefault(uid, []).append(aid)
    return out


def _admin_summary(user: SysUser, scope: list[str]) -> dict[str, Any]:
    role = int(user.super_admin or 0)
    # Root admins ignore the scope table — surface an empty list so the
    # dashboard doesn't show stale rows that have no effect.
    effective_scope = [] if role == ROLE_ROOT else scope
    return {
        "id": user.id,
        "username": user.username,
        "role": role,
        "roleName": _role_name(role),
        "status": int(user.status if user.status is not None else 1),
        "createDate": user.create_date,
        "scopedAgentCount": len(effective_scope),
        "scopedAgentIds": effective_scope,
    }


async def _load_summary(db: AsyncSession, user_id: int) -> dict[str, Any]:
    user = (
        await db.execute(select(SysUser).where(SysUser.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise APIException(404, "user not found")
    scope_map = await _scope_for_users(db, [user.id])
    return _admin_summary(user, scope_map.get(user.id, []))


# ---------- helpers ----------

async def _root_count(db: AsyncSession) -> int:
    return int(
        (
            await db.execute(
                select(func.count(SysUser.id)).where(
                    SysUser.super_admin == ROLE_ROOT,
                    # Only count active rows — soft-deleted accounts shouldn't
                    # keep the "only remaining root" gate locked.
                    SysUser.status == 1,
                )
            )
        ).scalar_one()
        or 0
    )


async def _existing_agent_ids(
    db: AsyncSession, agent_ids: list[str]
) -> list[str]:
    """Filter the input list to agent_ids that actually exist in ai_agent.
    De-duplicates while preserving order."""
    if not agent_ids:
        return []
    # Dedup but keep stable order so the eventual scope list is predictable.
    seen: set[str] = set()
    deduped: list[str] = []
    for aid in agent_ids:
        if aid not in seen:
            seen.add(aid)
            deduped.append(aid)

    rows = (
        await db.execute(select(AiAgent.id).where(AiAgent.id.in_(deduped)))
    ).scalars().all()
    found = set(rows)
    return [aid for aid in deduped if aid in found]


async def _replace_scope(
    db: AsyncSession,
    *,
    admin_user_id: int,
    agent_ids: list[str],
    granted_by: int,
) -> None:
    """Wipe + reinsert this admin's scope rows. Caller is responsible for
    filtering agent_ids to those that exist in ai_agent."""
    await db.execute(
        delete(AdminClientAccess).where(
            AdminClientAccess.admin_user_id == admin_user_id
        )
    )
    for aid in agent_ids:
        db.add(
            AdminClientAccess(
                admin_user_id=admin_user_id,
                agent_id=aid,
                granted_by=granted_by,
            )
        )


# ---------- endpoints ----------

@router.get("", response_model=None)
async def list_admins(
    _root: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """All sys_user rows that are admin-tier (super_admin >= 1), newest first.

    Soft-deleted rows (status=0) are included so the root admin can see who
    has been disabled — the dashboard is responsible for filtering or
    rendering them differently.
    """
    # MariaDB doesn't support NULLS LAST; emulate by ordering on
    # (create_date IS NULL, create_date DESC) so non-null dates come first.
    users = (
        await db.execute(
            select(SysUser)
            .where(SysUser.super_admin >= ROLE_ADMIN)
            .order_by(
                SysUser.create_date.is_(None),
                SysUser.create_date.desc(),
                SysUser.id.desc(),
            )
        )
    ).scalars().all()

    if not users:
        return []

    scope_map = await _scope_for_users(db, [u.id for u in users])
    return [_admin_summary(u, scope_map.get(u.id, [])) for u in users]


@router.post("", response_model=None)
async def create_admin(
    payload: CreateAdminPayload,
    root: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if payload.role not in (ROLE_ADMIN, ROLE_ROOT):
        raise APIException(400, "role must be 1 (admin) or 2 (root)")

    # Username uniqueness check — case-sensitive to match login lookup.
    existing = (
        await db.execute(
            select(SysUser.id).where(SysUser.username == payload.username)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise APIException(409, "username already exists")

    # Resolve scope (root ignores it).
    scope_ids: list[str] = []
    dropped: list[str] = []
    if payload.role == ROLE_ADMIN and payload.scopedAgentIds:
        scope_ids = await _existing_agent_ids(db, payload.scopedAgentIds)
        dropped = [
            aid for aid in payload.scopedAgentIds if aid not in set(scope_ids)
        ]

    new_id = _next_user_id()
    now = datetime.now()
    db.add(
        SysUser(
            id=new_id,
            username=payload.username,
            password=hash_password(payload.password),
            super_admin=payload.role,
            status=1,
            create_date=now,
            creator=root.id,
        )
    )

    if scope_ids:
        for aid in scope_ids:
            db.add(
                AdminClientAccess(
                    admin_user_id=new_id,
                    agent_id=aid,
                    granted_by=root.id,
                )
            )

    await db.commit()

    # Promoting to root means demoting all other roots — preserves the
    # single-root invariant (the same one bootstrap_root.py enforces at boot).
    if payload.role == ROLE_ROOT:
        await demote_other_roots(db, new_id)

    summary = await _load_summary(db, new_id)
    if dropped:
        # Surface the partial-failure as a meta hint without breaking the
        # success envelope — frontend shows it as a toast.
        summary["warnings"] = [
            f"dropped unknown agentId: {aid}" for aid in dropped
        ]
    return summary


@router.put("/{user_id}", response_model=None)
async def update_admin(
    user_id: int,
    payload: UpdateAdminPayload,
    root: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = (
        await db.execute(select(SysUser).where(SysUser.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise APIException(404, "user not found")

    is_self = user.id == root.id
    current_role = int(user.super_admin or 0)
    new_role = payload.role if payload.role is not None else current_role

    # Self-edit guard: the root cannot change their OWN role or scope. They
    # can still rename themselves (covered below) — that's why we don't reject
    # the whole request, only the dangerous fields.
    if is_self:
        if payload.role is not None and payload.role != current_role:
            raise APIException(
                403, "you cannot change your own role"
            )
        if payload.scopedAgentIds is not None:
            raise APIException(
                403, "you cannot change your own scope"
            )

    if payload.role is not None and payload.role not in (ROLE_ADMIN, ROLE_ROOT):
        raise APIException(400, "role must be 1 (admin) or 2 (root)")

    # Username uniqueness on rename.
    if payload.username is not None and payload.username != user.username:
        clash = (
            await db.execute(
                select(SysUser.id).where(
                    SysUser.username == payload.username,
                    SysUser.id != user.id,
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise APIException(409, "username already exists")
        user.username = payload.username

    # Demoting the only remaining root would lock the system out of admin
    # management entirely — refuse it. Promotion is fine; the demote-others
    # step below preserves the invariant on the other side.
    if (
        current_role == ROLE_ROOT
        and new_role != ROLE_ROOT
        and await _root_count(db) <= 1
    ):
        raise APIException(
            400, "cannot demote the only remaining root admin"
        )

    if payload.role is not None and payload.role != current_role:
        user.super_admin = payload.role
        user.updater = root.id
        user.update_date = datetime.now()

    # Scope changes only apply to non-root rows; if the resulting role is
    # root, blow away any leftover scope rows so the table doesn't lie.
    if payload.scopedAgentIds is not None:
        if new_role == ROLE_ROOT:
            await db.execute(
                delete(AdminClientAccess).where(
                    AdminClientAccess.admin_user_id == user.id
                )
            )
        else:
            scope_ids = await _existing_agent_ids(db, payload.scopedAgentIds)
            await _replace_scope(
                db,
                admin_user_id=user.id,
                agent_ids=scope_ids,
                granted_by=root.id,
            )
    elif payload.role is not None and new_role == ROLE_ROOT and current_role != ROLE_ROOT:
        # Promotion to root with no scope payload — clean up any stale rows so
        # the user.scope reads as empty (matching the "root sees all" rule).
        await db.execute(
            delete(AdminClientAccess).where(
                AdminClientAccess.admin_user_id == user.id
            )
        )

    await db.commit()

    # Now flush the single-root invariant if we just promoted someone.
    if new_role == ROLE_ROOT and current_role != ROLE_ROOT:
        await demote_other_roots(db, user.id)

    return await _load_summary(db, user.id)


@router.post("/{user_id}/reset-password", response_model=None)
async def reset_password(
    user_id: int,
    payload: ResetPasswordPayload,
    _root: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Set a new password. Self-reset is allowed (root resetting their own).

    Note: still gated on require_root, so an admin can't reset their own —
    only the root admin can reset anyone's password, including their own.
    """
    user = (
        await db.execute(select(SysUser).where(SysUser.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise APIException(404, "user not found")

    user.password = hash_password(payload.password)
    user.updater = _root.id
    user.update_date = datetime.now()
    await db.commit()

    return {"id": user.id, "username": user.username, "passwordReset": True}


@router.delete("/{user_id}", response_model=None)
async def delete_admin(
    user_id: int,
    root: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Soft delete: status=0. Hard-delete is intentionally not exposed —
    sys_user rows are referenced as creator/updater across the schema."""
    if user_id == root.id:
        raise APIException(403, "you cannot delete yourself")

    user = (
        await db.execute(select(SysUser).where(SysUser.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise APIException(404, "user not found")

    if int(user.super_admin or 0) == ROLE_ROOT and await _root_count(db) <= 1:
        raise APIException(
            400, "cannot delete the only remaining root admin"
        )

    user.status = 0
    user.updater = root.id
    user.update_date = datetime.now()

    # Strip scope rows on delete so a re-enabled account doesn't silently
    # inherit old client access. If you re-create the account by id you'll
    # need to re-grant.
    await db.execute(
        delete(AdminClientAccess).where(
            AdminClientAccess.admin_user_id == user.id
        )
    )

    await db.commit()
    return {"id": user.id, "deleted": True}


@router.get("/{user_id}/scope", response_model=None)
async def get_scope(
    user_id: int,
    _root: CurrentUser = Depends(require_root),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Detailed scope rows for one admin, joined with ai_agent for the name.

    Returns an empty list for root admins (their access isn't governed by
    cc_admin_client_access)."""
    user = (
        await db.execute(select(SysUser).where(SysUser.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise APIException(404, "user not found")

    if int(user.super_admin or 0) == ROLE_ROOT:
        return []

    rows = (
        await db.execute(
            select(
                AdminClientAccess.agent_id,
                AiAgent.agent_name,
                AdminClientAccess.granted_at,
                AdminClientAccess.granted_by,
            )
            .join(AiAgent, AiAgent.id == AdminClientAccess.agent_id, isouter=True)
            .where(AdminClientAccess.admin_user_id == user.id)
            .order_by(AdminClientAccess.granted_at.desc())
        )
    ).all()

    return [
        {
            "agentId": r[0],
            "agentName": r[1],
            "grantedAt": r[2],
            "grantedBy": r[3],
        }
        for r in rows
    ]
