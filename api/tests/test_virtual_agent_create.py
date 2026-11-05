"""Create unbound virtual agents via POST /api/agent/virtual.

Does not rewrite Sam. Does not auto-bind. Uses Portal AI Agents org.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import AiAgent, AiDevice, CcVirtualAgentBinding, SysUser
from careconnect_api.routers.virtual_agent_binding import PORTAL_AI_AGENTS_ORGANIZATION_ID

SAM_ID = "1c46373751d84d29a1aab382fd65501f"
BILL_ID = "billbob000000000000000000000001"
CARE_ORG = "ff6ab2bac2b147c080bce9ecbfd0d49e"


@pytest_asyncio.fixture(scope="function")
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    _ = client
    db_session.add(
        SysUser(id=1, username="admin1", password=hash_password("unused"), super_admin=ROLE_ROOT, status=1)
    )
    await db_session.commit()
    token, _expire = issue_token(1, "admin1", ROLE_ROOT)
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _agent(agent_id: str, name: str, org: str | None, bot: str | None = None) -> AiAgent:
    return AiAgent(
        id=agent_id,
        agent_name=name,
        bot_name=bot if bot is not None else name,
        organization_id=org,
        chat_history_conf=0,
    )


async def _seed_sam_and_client(db_session: AsyncSession) -> None:
    db_session.add_all([
        _agent(SAM_ID, "Portal-US", PORTAL_AI_AGENTS_ORGANIZATION_ID, "Sam Virtual Portal Agent"),
        _agent(BILL_ID, "Bill Bob", CARE_ORG),
    ])
    await db_session.commit()


@pytest.mark.asyncio
async def test_create_virtual_agent_is_unbound_and_listed(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    await _seed_sam_and_client(db_session)
    created = await client.post(
        "/api/agent/virtual",
        json={"name": "  Ops Assistant  "},
        headers=_auth(admin_token),
    )
    body = created.json()
    assert body["code"] == 0, body
    data = body["data"]
    agent_id = data["id"]
    assert agent_id
    assert data["agentId"] == agent_id
    assert data["agentName"] == "Ops Assistant"
    assert data["botName"] == "Ops Assistant"
    assert data["organizationId"] == PORTAL_AI_AGENTS_ORGANIZATION_ID
    assert data["deviceCount"] == 0
    assert data["boundClientId"] is None

    db_session.expire_all()
    row = await db_session.get(AiAgent, agent_id)
    assert row is not None
    assert row.agent_name == "Ops Assistant"
    assert row.bot_name == "Ops Assistant"
    assert row.organization_id == PORTAL_AI_AGENTS_ORGANIZATION_ID
    devices = (
        await db_session.execute(select(func.count()).select_from(AiDevice).where(AiDevice.agent_id == agent_id))
    ).scalar_one()
    assert int(devices) == 0
    bindings = (
        await db_session.execute(select(func.count()).select_from(CcVirtualAgentBinding))
    ).scalar_one()
    assert int(bindings) == 0

    listed = (await client.get("/api/agent/virtual", headers=_auth(admin_token))).json()
    assert listed["code"] == 0, listed
    ids = {item["id"] for item in listed["data"]}
    assert agent_id in ids
    assert SAM_ID in ids


@pytest.mark.asyncio
async def test_create_does_not_change_sam_and_rejects_blank_name(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    await _seed_sam_and_client(db_session)
    blank = await client.post(
        "/api/agent/virtual",
        json={"name": "   "},
        headers=_auth(admin_token),
    )
    assert blank.json()["code"] == 400

    created = (
        await client.post(
            "/api/agent/virtual",
            json={"name": "Night Desk"},
            headers=_auth(admin_token),
        )
    ).json()
    assert created["code"] == 0, created

    db_session.expire_all()
    sam = await db_session.get(AiAgent, SAM_ID)
    assert sam is not None
    assert sam.id == SAM_ID
    assert sam.agent_name == "Portal-US"
    assert sam.bot_name == "Sam Virtual Portal Agent"
    assert sam.organization_id == PORTAL_AI_AGENTS_ORGANIZATION_ID


@pytest.mark.asyncio
async def test_created_virtual_agent_can_bind_later_without_auto_bind(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    await _seed_sam_and_client(db_session)
    created = (
        await client.post(
            "/api/agent/virtual",
            json={"name": "Floor Guide"},
            headers=_auth(admin_token),
        )
    ).json()
    assert created["code"] == 0, created
    va_id = created["data"]["id"]

    before = (
        await db_session.execute(select(func.count()).select_from(CcVirtualAgentBinding))
    ).scalar_one()
    assert int(before) == 0

    bound = (
        await client.post(
            f"/api/agent/{BILL_ID}/virtual-agents",
            json={"virtualAgentId": va_id},
            headers=_auth(admin_token),
        )
    ).json()
    assert bound["code"] == 0, bound
    assert bound["data"]["virtualAgentId"] == va_id
    assert bound["data"]["clientId"] == BILL_ID

    after = (
        await db_session.execute(select(func.count()).select_from(CcVirtualAgentBinding))
    ).scalar_one()
    assert int(after) == 1
