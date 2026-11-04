"""Ask Sam session ownership, completed turns, and Yes/No feedback.

The browser cannot choose a Nexus client. Library sessions store no account,
device, or resident. Turns are written only after a completed answer.
cc_virtual_agent_message is not used.

Retention is 30 days from created_at. sam_session_retention_cutoff documents
the future cleanup and does not delete rows.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .bridgingcare_mapping import resolve_verified_client_by_account
from .envelope import APIException
from .models import CcSamSession, CcSamSessionTurn, CcVirtualAgentBinding

log = logging.getLogger("sam-session")

SAM_SESSION_RETENTION_DAYS = 30
_LIBRARY = "library"
_ACCOUNT = "account_device"
_SUBJECT_MAX = 255
_ID_MAX = 64


@dataclass(frozen=True)
class SessionClaim:
    owner_subject: str
    context_kind: str
    account_id: str | None
    registered_device_id: str | None
    session_id: str | None


def sam_session_retention_cutoff(now: datetime | None = None) -> datetime:
    """Return the timestamp a future cleanup would use. Does not delete."""
    current = now or datetime.now(timezone.utc).replace(tzinfo=None)
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    return current - timedelta(days=SAM_SESSION_RETENTION_DAYS)


def feedback_summary(helpful: int, not_helpful: int) -> dict[str, int | None]:
    votes = helpful + not_helpful
    percent = round(helpful * 100 / votes) if votes >= 10 else None
    return {"helpful": helpful, "notHelpful": not_helpful, "votes": votes, "percent": percent}


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def session_claim_from_body(body: dict[str, Any]) -> SessionClaim | None:
    """Read the Lambda's session claim. A missing session means do not persist.

    nexusClientId and clientId are ignored. Library claims drop account and device.
    """
    raw = body.get("session")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise APIException(400, "invalid session")
    subject = _text(raw.get("ownerSubject"), _SUBJECT_MAX)
    if subject == "":
        raise APIException(400, "invalid session")
    kind = _text(raw.get("contextKind"), 32)
    session_id = _text(raw.get("sessionId"), _ID_MAX)
    if kind == _LIBRARY:
        return SessionClaim(subject, _LIBRARY, None, None, session_id or None)
    if kind != _ACCOUNT:
        raise APIException(400, "invalid session")
    account_id = _text(raw.get("accountId"), _ID_MAX)
    device_id = _text(raw.get("registeredDeviceId"), _ID_MAX)
    if account_id == "" or device_id == "":
        raise APIException(400, "invalid session")
    return SessionClaim(subject, _ACCOUNT, account_id, device_id, session_id or None)


def _same_owner(row: CcSamSession, claim: SessionClaim, virtual_agent_id: str) -> bool:
    return (
        row.virtual_agent_id == virtual_agent_id
        and row.owner_subject == claim.owner_subject
        and row.context_kind == claim.context_kind
        and row.account_id == claim.account_id
        and row.registered_device_id == claim.registered_device_id
    )


async def require_owned_session(
    db: AsyncSession,
    claim: SessionClaim,
    virtual_agent_id: str,
) -> None:
    """Reject a supplied session id that is missing or owned by someone else.

    A new dialog has no session id and is not rejected here.
    """
    if not claim.session_id:
        return
    row = await db.get(CcSamSession, claim.session_id)
    if row is None or not _same_owner(row, claim, virtual_agent_id):
        raise APIException(403, "session is not available")


async def verified_nexus_client_id(
    db: AsyncSession,
    claim: SessionClaim,
    virtual_agent_id: str,
) -> str | None:
    """Client for a new session, or None when ownership is not verified.

    A Bridging Care account map can name the client. A single binding to
    that same client confirms the configured agent serves it. A binding
    without that verified account, or more than one binding, does not
    choose a client.
    """
    if claim.context_kind != _ACCOUNT or not claim.account_id:
        return None
    client_id = await resolve_verified_client_by_account(db, claim.account_id)
    if client_id is None:
        return None
    bound = (
        await db.execute(
            select(CcVirtualAgentBinding.client_id).where(
                CcVirtualAgentBinding.virtual_agent_id == virtual_agent_id
            )
        )
    ).scalars().all()
    if len(bound) != 1 or bound[0] != client_id:
        return None
    return client_id


async def record_completed_turn(
    db: AsyncSession,
    claim: SessionClaim,
    virtual_agent_id: str,
    question: str,
    answer: str,
    follow_up: str | None,
) -> str:
    """Insert one completed turn. The session id is server-generated."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if claim.session_id:
        session = await db.get(CcSamSession, claim.session_id)
        if session is None or not _same_owner(session, claim, virtual_agent_id):
            raise APIException(403, "session is not available")
    else:
        session = CcSamSession(
            session_id=uuid.uuid4().hex,
            virtual_agent_id=virtual_agent_id,
            owner_subject=claim.owner_subject,
            context_kind=claim.context_kind,
            account_id=claim.account_id,
            registered_device_id=claim.registered_device_id,
            nexus_client_id=await verified_nexus_client_id(db, claim, virtual_agent_id),
            answer_count=0,
            helpful=None,
            feedback_at=None,
            created_at=now,
        )
        db.add(session)
        await db.flush()
    asked = question.strip()[:2000]
    if asked == "" or answer.strip() == "":
        raise APIException(400, "invalid session")
    db.add(CcSamSessionTurn(
        session_id=session.session_id,
        created_at=now,
        question=asked,
        answer=answer,
        follow_up=follow_up.strip() if isinstance(follow_up, str) and follow_up.strip() else None,
    ))
    session.answer_count = int(session.answer_count or 0) + 1
    await db.commit()
    return session.session_id


async def record_feedback(
    db: AsyncSession,
    claim: SessionClaim,
    helpful: bool,
) -> None:
    """Record Yes or No for the owning subject. A repeat replaces the vote."""
    if not claim.session_id:
        raise APIException(400, "invalid session")
    session = await db.get(CcSamSession, claim.session_id)
    if session is None or session.owner_subject != claim.owner_subject:
        raise APIException(403, "session is not available")
    if (
        session.context_kind != claim.context_kind
        or session.account_id != claim.account_id
        or session.registered_device_id != claim.registered_device_id
    ):
        raise APIException(403, "session is not available")
    if int(session.answer_count or 0) < 1:
        raise APIException(403, "session is not available")
    session.helpful = 1 if helpful else 0
    session.feedback_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()


def _helpful(value: int | None) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


def _client_review_scope(requested_id: str):
    """Sessions that belong to this client.

    A client id matches nexus_client_id. Older rows that used the client
    record as the agent match only while no other client owns them.
    """
    return or_(
        CcSamSession.nexus_client_id == requested_id,
        and_(
            CcSamSession.virtual_agent_id == requested_id,
            CcSamSession.nexus_client_id.is_(None),
        ),
    )


def _agent_review_scope(agent_id: str, allowed_client_ids: list[str] | None):
    """Sessions handled by this agent that this administrator may see.

    Unowned sessions stay with the agent. A client-owned session is included
    only when that client is in the administrator's scope. Root passes no
    list and can see every client of the agent.
    """
    handled = CcSamSession.virtual_agent_id == agent_id
    unowned = CcSamSession.nexus_client_id.is_(None)
    if allowed_client_ids is None:
        return handled
    if not allowed_client_ids:
        return and_(handled, unowned)
    return and_(
        handled,
        or_(unowned, CcSamSession.nexus_client_id.in_(allowed_client_ids)),
    )


async def review_sam_sessions(
    db: AsyncSession,
    requested_id: str,
    *,
    view: str = "client",
    allowed_client_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Read one explicit review. Client and agent views are not inferred."""
    if view == "agent":
        scope = _agent_review_scope(requested_id, allowed_client_ids)
    elif view == "client":
        scope = _client_review_scope(requested_id)
    else:
        raise APIException(400, "invalid review")
    helpful = int((
        await db.scalar(
            select(func.count()).select_from(CcSamSession).where(
                scope,
                CcSamSession.helpful == 1,
            )
        )
    ) or 0)
    not_helpful = int((
        await db.scalar(
            select(func.count()).select_from(CcSamSession).where(
                scope,
                CcSamSession.helpful == 0,
            )
        )
    ) or 0)
    rows = (
        await db.execute(
            select(CcSamSession)
            .where(scope)
            .order_by(CcSamSession.created_at.desc())
            .limit(100)
        )
    ).scalars().all()
    ids = [row.session_id for row in rows]
    turns = (
        await db.execute(
            select(CcSamSessionTurn)
            .where(CcSamSessionTurn.session_id.in_(ids))
            .order_by(CcSamSessionTurn.id.asc())
        )
    ).scalars().all() if ids else []
    grouped: dict[str, list[dict[str, Any]]] = {item: [] for item in ids}
    for turn in turns:
        grouped.setdefault(turn.session_id, []).append({
            "question": turn.question,
            "answer": turn.answer,
            "followUp": turn.follow_up,
            "createdAt": _iso(turn.created_at),
        })
    return {
        "summary": feedback_summary(helpful, not_helpful),
        "sessions": [
            {
                "sessionId": row.session_id,
                "createdAt": _iso(row.created_at),
                "contextKind": row.context_kind,
                "accountId": row.account_id,
                "registeredDeviceId": row.registered_device_id,
                "nexusClientId": row.nexus_client_id,
                "answerCount": int(row.answer_count or 0),
                "helpful": _helpful(row.helpful),
                "feedbackAt": _iso(row.feedback_at),
                "turns": grouped.get(row.session_id, []),
            }
            for row in rows
        ],
    }
