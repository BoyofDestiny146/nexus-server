"""Verified Nexus client → Bridging Care companion / account / client.

``ai_agent.id`` is the Nexus *client* (assessment owner). Bill Bob's live
id is ``795e977e58784702a3b7e309889fbc61``. Assessments stay on that row.

Betty is the Bridging Care *companion* (``Nx-C97S1X9PS``), never an
``ai_agent`` and never an assessment owner. Organization id and CareConnect
public client id are never accepted as Bridging Care account or client UUIDs.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .envelope import APIException
from .models import AiAgent, CcBridgingcareCompanionMap

NEXUS_AGENT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
COMPANION_ID_RE = re.compile(r"^Nx-[A-Za-z0-9]+$")

# Live Orin Nexus client (assessment owner). Not inserted by this module.
BILL_BOB_DISPLAY_NAME = "Bill Bob"
BILL_BOB_AGENT_ID = "795e977e58784702a3b7e309889fbc61"
BILL_BOB_ORGANIZATION_ID = "ff6ab2bac2b147c080bce9ecbfd0d49e"

# Bridging Care companion Betty. Never a Nexus client / ai_agent.id.
BETTY_COMPANION_DISPLAY_NAME = "Betty"
BETTY_COMPANION_ID = "Nx-C97S1X9PS"
BETTY_ACCOUNT_ID = "46c9f2fb-f405-4cdb-b88a-8f20e20a9bb0"
BETTY_CLIENT_ID = "1fb8c2e2-3f29-4ec5-8626-9affbe4e1f2c"


def is_nexus_agent_id(value: str | None) -> bool:
    return bool(value and NEXUS_AGENT_ID_RE.fullmatch(str(value).strip()))


def is_companion_registration_id(value: str | None) -> bool:
    return bool(value and COMPANION_ID_RE.fullmatch(str(value).strip()))


def is_uuid(value: str | None) -> bool:
    raw = (value or "").strip()
    if not raw:
        return False
    try:
        uuid.UUID(raw)
    except (TypeError, ValueError):
        return False
    return True


def assert_expected_name_is_client(*, expected_agent_name: str | None, companion_id: str) -> None:
    """Refuse using the companion's display name as the Nexus client name."""
    want = (expected_agent_name or "").strip()
    if not want:
        return
    if companion_id.strip() == BETTY_COMPANION_ID and want == BETTY_COMPANION_DISPLAY_NAME:
        raise APIException(
            400,
            "expected client name must be the Nexus client (Bill Bob), not companion Betty",
        )


def assert_distinct_identities(
    *,
    agent_id: str,
    companion_id: str,
    account_id: str,
    client_id: str,
    organization_id: str | None = None,
    careconnect_public_id: str | None = None,
) -> None:
    """Refuse known identity collisions before a mapping is stored."""
    if not is_nexus_agent_id(agent_id):
        raise APIException(400, "nexus client id is not a 32-character hex ai_agent.id")
    if not is_companion_registration_id(companion_id):
        raise APIException(400, "companion id is not a Bridging Care registration identifier")
    if agent_id == companion_id:
        raise APIException(400, "companion id must not be the Nexus client id")
    if not is_uuid(account_id):
        raise APIException(400, "account id is not a Bridging Care account UUID")
    if not is_uuid(client_id):
        raise APIException(400, "client id is not a Bridging Care client UUID")
    if account_id == client_id:
        raise APIException(400, "account id and client id must be different")
    org = (organization_id or "").strip()
    if org and org == account_id:
        raise APIException(400, "organization id is not a Bridging Care account id")
    cc_public = (careconnect_public_id or "").strip()
    if cc_public and cc_public == client_id:
        raise APIException(400, "CareConnect public client id is not a Bridging Care client UUID")
    if cc_public and cc_public == companion_id:
        raise APIException(400, "CareConnect public client id is not a Bridging Care companion id")


def agent_name_matches(agent: AiAgent, expected: str | None) -> bool:
    """Compare ``ai_agent.agent_name`` (Nexus client display name)."""
    if not expected or not str(expected).strip():
        return True
    name = (agent.agent_name or "").strip()
    want = expected.strip()
    return name == want or name.startswith(f"{want} ")


async def lookup_agents_named(db: AsyncSession, name: str) -> list[AiAgent]:
    rows = (
        await db.execute(
            select(AiAgent).where(AiAgent.agent_name == name).order_by(AiAgent.id.asc())
        )
    ).scalars().all()
    return list(rows)


async def resolve_verified_mapping(
    db: AsyncSession, agent_id: str
) -> CcBridgingcareCompanionMap | None:
    """Return the frozen mapping only when the Nexus client still matches."""
    raw = (agent_id or "").strip()
    if not is_nexus_agent_id(raw):
        return None
    row = (
        await db.execute(
            select(CcBridgingcareCompanionMap).where(
                CcBridgingcareCompanionMap.agent_id == raw
            )
        )
    ).scalar_one_or_none()
    if row is None or row.verified_at is None:
        return None
    agent = await db.get(AiAgent, raw)
    if agent is None:
        return None
    if not agent_name_matches(agent, row.expected_agent_name):
        return None
    try:
        assert_distinct_identities(
            agent_id=row.agent_id,
            companion_id=row.companion_id,
            account_id=row.account_id,
            client_id=row.client_id,
            organization_id=getattr(agent, "organization_id", None),
        )
        assert_expected_name_is_client(
            expected_agent_name=row.expected_agent_name,
            companion_id=row.companion_id,
        )
    except APIException:
        return None
    return row


async def bind_verified_mapping(
    db: AsyncSession,
    *,
    agent_id: str,
    companion_id: str,
    account_id: str,
    client_id: str,
    expected_agent_name: str | None,
    careconnect_public_id: str | None = None,
) -> CcBridgingcareCompanionMap:
    """Explicit operator bind. Never auto-creates from org or CareConnect ids."""
    agent = await db.get(AiAgent, (agent_id or "").strip())
    if agent is None:
        raise APIException(404, "nexus client not found")
    assert_distinct_identities(
        agent_id=agent.id,
        companion_id=companion_id,
        account_id=account_id,
        client_id=client_id,
        organization_id=getattr(agent, "organization_id", None),
        careconnect_public_id=careconnect_public_id,
    )
    assert_expected_name_is_client(
        expected_agent_name=expected_agent_name,
        companion_id=companion_id,
    )
    if expected_agent_name and not agent_name_matches(agent, expected_agent_name):
        raise APIException(400, "client name does not match the verified Nexus client")
    existing = (
        await db.execute(
            select(CcBridgingcareCompanionMap).where(
                CcBridgingcareCompanionMap.agent_id == agent.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        same = (
            existing.companion_id == companion_id.strip()
            and existing.account_id == account_id.strip()
            and existing.client_id == client_id.strip()
        )
        if same:
            return existing
        raise APIException(409, "client already has a verified Bridging Care mapping")
    now = datetime.now()
    row = CcBridgingcareCompanionMap(
        agent_id=agent.id,
        companion_id=companion_id.strip(),
        account_id=account_id.strip(),
        client_id=client_id.strip(),
        expected_agent_name=(expected_agent_name or "").strip() or None,
        verified_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


def mapping_view(row: CcBridgingcareCompanionMap) -> dict[str, Any]:
    return {
        "agentId": row.agent_id,
        "companionId": row.companion_id,
        "accountId": row.account_id,
        "clientId": row.client_id,
        "expectedAgentName": row.expected_agent_name,
        "verifiedAt": row.verified_at.isoformat(timespec="seconds") if row.verified_at else None,
    }
