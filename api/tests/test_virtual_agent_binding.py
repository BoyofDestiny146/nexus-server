"""Virtual-agent binding and client-scoped discussion reads.

Sam stays the existing Portal-US row. Binding does not create a device.
The Sam conversation route is not a writer.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ADMIN, ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import (
    AdminClientAccess,
    AiAgent,
    AiAgentChatHistory,
    AiDevice,
    CcSamSession,
    CcVirtualAgentBinding,
    CcVirtualAgentMessage,
    SysUser,
)
from careconnect_api.routers.virtual_agent_binding import PORTAL_AI_AGENTS_ORGANIZATION_ID

SAM_ID = "1c46373751d84d29a1aab382fd65501f"
BILL_ID = "billbob000000000000000000000001"
OTHER_ID = "otherclient00000000000000000001"
WATCHER_AGENT_ID = "portalwithdevice000000000000001"


@pytest_asyncio.fixture(scope="function")
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    _ = client
    db_session.add(SysUser(id=1, username="admin1", password=hash_password("unused"), super_admin=ROLE_ROOT, status=1))
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


async def _seed(db_session: AsyncSession) -> None:
    db_session.add_all([
        _agent(SAM_ID, "Portal-US", PORTAL_AI_AGENTS_ORGANIZATION_ID, "Sam Virtual Portal Agent"),
        _agent(BILL_ID, "Bill Bob", "ff6ab2bac2b147c080bce9ecbfd0d49e"),
        _agent(OTHER_ID, "Another Client", "ff6ab2bac2b147c080bce9ecbfd0d49e"),
        _agent(WATCHER_AGENT_ID, "Has Watcher", PORTAL_AI_AGENTS_ORGANIZATION_ID, "Not Virtual"),
        AiDevice(id="watcher-1", mac_address="80:45:6B:4D:5D:74", agent_id=WATCHER_AGENT_ID, alias="Betty"),
    ])
    await db_session.commit()


async def _bind(client: AsyncClient, token: str, client_id: str, virtual_agent_id: str):
    return await client.post(
        f"/api/agent/{client_id}/virtual-agents",
        json={"virtualAgentId": virtual_agent_id},
        headers=_auth(token),
    )


@pytest.mark.asyncio
async def test_sam_identity_is_unchanged_by_binding(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    await _seed(db_session)
    body = (await _bind(client, admin_token, BILL_ID, SAM_ID)).json()
    assert body["code"] == 0, body
    assert body["data"]["virtualAgentId"] == SAM_ID
    assert body["data"]["createdBy"] == 1

    db_session.expire_all()
    sam = await db_session.get(AiAgent, SAM_ID)
    assert sam is not None
    assert sam.id == SAM_ID
    assert sam.agent_name == "Portal-US"
    assert sam.bot_name == "Sam Virtual Portal Agent"
    assert sam.organization_id == PORTAL_AI_AGENTS_ORGANIZATION_ID
    devices = (
        await db_session.execute(select(func.count()).select_from(AiDevice).where(AiDevice.agent_id == SAM_ID))
    ).scalar_one()
    assert int(devices) == 0
    bindings = (
        await db_session.execute(select(func.count()).select_from(CcVirtualAgentBinding))
    ).scalar_one()
    assert int(bindings) == 1


@pytest.mark.asyncio
async def test_duplicate_self_and_physical_bindings_are_rejected(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    await _seed(db_session)
    first = (await _bind(client, admin_token, BILL_ID, SAM_ID)).json()
    assert first["code"] == 0, first
    duplicate = (await _bind(client, admin_token, BILL_ID, SAM_ID)).json()
    assert duplicate["code"] == 409

    itself = (await _bind(client, admin_token, SAM_ID, SAM_ID)).json()
    assert itself["code"] == 400

    physical = (await _bind(client, admin_token, BILL_ID, WATCHER_AGENT_ID)).json()
    assert physical["code"] == 400

    care_client = (await _bind(client, admin_token, SAM_ID, BILL_ID)).json()
    assert care_client["code"] == 400

    db_session.expire_all()
    rows = (await db_session.execute(select(CcVirtualAgentBinding))).scalars().all()
    assert [(row.client_id, row.virtual_agent_id) for row in rows] == [(BILL_ID, SAM_ID)]
    watcher = await db_session.get(AiDevice, "watcher-1")
    assert watcher is not None
    assert watcher.agent_id == WATCHER_AGENT_ID
    assert watcher.mac_address == "80:45:6B:4D:5D:74"


@pytest.mark.asyncio
async def test_unbind_keeps_stored_turns_and_rebind_shows_them(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    await _seed(db_session)
    assert (await _bind(client, admin_token, BILL_ID, SAM_ID)).json()["code"] == 0
    db_session.add(CcVirtualAgentMessage(
        client_id=BILL_ID,
        virtual_agent_id=SAM_ID,
        session_id="session-a",
        question="How do I configure an alarm?",
        answer="Use Manage Alarm.",
        follow_up="Which alarm?",
        created_at=datetime(2026, 11, 2, 18, 0, 0),
    ))
    await db_session.commit()

    removed = (
        await client.delete(f"/api/agent/{BILL_ID}/virtual-agents/{SAM_ID}", headers=_auth(admin_token))
    ).json()
    assert removed["code"] == 0, removed
    assert removed["data"]["removed"] is True
    hidden = (
        await client.get(f"/api/agent/{BILL_ID}/virtual-agents/{SAM_ID}/sessions", headers=_auth(admin_token))
    ).json()
    assert hidden["code"] == 404

    db_session.expire_all()
    stored = (await db_session.execute(select(CcVirtualAgentMessage))).scalars().all()
    assert len(stored) == 1

    assert (await _bind(client, admin_token, BILL_ID, SAM_ID)).json()["code"] == 0
    shown = (
        await client.get(f"/api/agent/{BILL_ID}/virtual-agents/{SAM_ID}/sessions", headers=_auth(admin_token))
    ).json()
    assert shown["code"] == 0, shown
    assert shown["data"]["total"] == 1
    assert shown["data"]["list"][0]["sessionId"] == "session-a"


@pytest.mark.asyncio
async def test_scoped_admin_cannot_cross_clients(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    await _seed(db_session)
    db_session.add(SysUser(id=2, username="scoped", password=hash_password("unused"), super_admin=ROLE_ADMIN, status=1))
    db_session.add(AdminClientAccess(admin_user_id=2, agent_id=BILL_ID, granted_by=1))
    await db_session.commit()
    token, _expire = issue_token(2, "scoped", ROLE_ADMIN)

    denied_list = (await client.get(f"/api/agent/{OTHER_ID}/virtual-agents", headers=_auth(token))).json()
    assert denied_list["code"] == 403

    denied_bind = (await _bind(client, token, BILL_ID, SAM_ID)).json()
    assert denied_bind["code"] == 403

    db_session.add(AdminClientAccess(admin_user_id=2, agent_id=SAM_ID, granted_by=1))
    await db_session.commit()
    allowed = (await _bind(client, token, BILL_ID, SAM_ID)).json()
    assert allowed["code"] == 0, allowed

    available = (await client.get("/api/agent/virtual", headers=_auth(token))).json()
    assert available["code"] == 0, available
    assert [item["id"] for item in available["data"]] == [SAM_ID]


@pytest.mark.asyncio
async def test_discussion_reads_are_client_scoped_and_ordered(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    await _seed(db_session)
    assert (await _bind(client, admin_token, BILL_ID, SAM_ID)).json()["code"] == 0
    assert (await _bind(client, admin_token, OTHER_ID, SAM_ID)).json()["code"] == 0
    empty = (
        await client.get(f"/api/agent/{BILL_ID}/virtual-agents/{SAM_ID}/sessions", headers=_auth(admin_token))
    ).json()
    assert empty["data"] == {"list": [], "total": 0}

    start = datetime(2026, 11, 2, 18, 0, 0)
    db_session.add_all([
        CcVirtualAgentMessage(
            client_id=BILL_ID, virtual_agent_id=SAM_ID, session_id="session-a",
            question="First question", answer="First answer", follow_up=None,
            created_at=start,
        ),
        CcVirtualAgentMessage(
            client_id=BILL_ID, virtual_agent_id=SAM_ID, session_id="session-a",
            question="Second question", answer="Second answer", follow_up="One follow-up",
            created_at=start + timedelta(minutes=1),
        ),
        CcVirtualAgentMessage(
            client_id=BILL_ID, virtual_agent_id=SAM_ID, session_id="session-b",
            question="Later question", answer="Later answer", follow_up=None,
            created_at=start + timedelta(minutes=5),
        ),
    ])
    await db_session.commit()

    sessions = (
        await client.get(f"/api/agent/{BILL_ID}/virtual-agents/{SAM_ID}/sessions", headers=_auth(admin_token))
    ).json()
    assert [item["sessionId"] for item in sessions["data"]["list"]] == ["session-b", "session-a"]
    assert sessions["data"]["list"][1]["messageCount"] == 2

    turns = (
        await client.get(
            f"/api/agent/{BILL_ID}/virtual-agents/{SAM_ID}/sessions/session-a",
            headers=_auth(admin_token),
        )
    ).json()
    assert [item["question"] for item in turns["data"]] == ["First question", "Second question"]
    assert turns["data"][1]["followUp"] == "One follow-up"
    assert all(item["clientId"] == BILL_ID and item["virtualAgentId"] == SAM_ID for item in turns["data"])

    other_sessions = (
        await client.get(f"/api/agent/{OTHER_ID}/virtual-agents/{SAM_ID}/sessions", headers=_auth(admin_token))
    ).json()
    assert other_sessions["data"]["total"] == 0
    other_turns = (
        await client.get(
            f"/api/agent/{OTHER_ID}/virtual-agents/{SAM_ID}/sessions/session-a",
            headers=_auth(admin_token),
        )
    ).json()
    assert other_turns["data"] == []


@pytest.mark.asyncio
async def test_deleting_a_client_removes_its_links_and_leaves_sam(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    await _seed(db_session)
    assert (await _bind(client, admin_token, BILL_ID, SAM_ID)).json()["code"] == 0
    db_session.add(CcVirtualAgentMessage(
        client_id=BILL_ID, virtual_agent_id=SAM_ID, session_id="session-a",
        question="Question", answer="Answer", follow_up=None,
    ))
    await db_session.commit()

    deleted = (await client.delete(f"/api/agent/{BILL_ID}", headers=_auth(admin_token))).json()
    assert deleted["code"] == 0, deleted

    db_session.expire_all()
    assert await db_session.get(AiAgent, BILL_ID) is None
    sam = await db_session.get(AiAgent, SAM_ID)
    assert sam is not None
    assert sam.agent_name == "Portal-US"
    assert (await db_session.execute(select(func.count()).select_from(CcVirtualAgentBinding))).scalar_one() == 0
    assert (await db_session.execute(select(func.count()).select_from(CcVirtualAgentMessage))).scalar_one() == 0
    watcher = await db_session.get(AiDevice, "watcher-1")
    assert watcher is not None
    assert watcher.agent_id == WATCHER_AGENT_ID


@pytest.mark.asyncio
async def test_sam_review_stays_inside_the_administrator_scope(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    await _seed(db_session)
    db_session.add(SysUser(id=2, username="scoped", password=hash_password("unused"), super_admin=ROLE_ADMIN, status=1))
    db_session.add(AdminClientAccess(admin_user_id=2, agent_id=BILL_ID, granted_by=1))
    await db_session.commit()
    token, _expire = issue_token(2, "scoped", ROLE_ADMIN)

    denied = (await client.get(f"/api/agent/{SAM_ID}/sam-review", headers=_auth(token))).json()
    assert denied["code"] == 403

    allowed = (await client.get(f"/api/agent/{BILL_ID}/sam-review", headers=_auth(token))).json()
    assert allowed["code"] == 0, allowed
    assert allowed["data"]["sessions"] == []

    root = (await client.get(f"/api/agent/{SAM_ID}/sam-review", headers=_auth(admin_token))).json()
    assert root["code"] == 0, root


@pytest.mark.asyncio
async def test_agent_review_query_filters_unauthorized_clients(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    await _seed(db_session)
    created = datetime(2026, 11, 3, 12, 0, 0)
    db_session.add_all([
        CcSamSession(
            session_id=f"legacyview{index:022d}",
            virtual_agent_id=SAM_ID,
            owner_subject=f"auth0|legacy-{index}",
            context_kind="library",
            nexus_client_id=None,
            answer_count=1,
            created_at=created,
        )
        for index in range(4)
    ])
    db_session.add_all([
        CcSamSession(
            session_id="billview0000000000000000000001",
            virtual_agent_id=SAM_ID,
            owner_subject="auth0|bill",
            context_kind="account_device",
            account_id="11111111-1111-4111-8111-111111111111",
            registered_device_id="band-a",
            nexus_client_id=BILL_ID,
            answer_count=1,
            helpful=1,
            created_at=created,
        ),
        CcSamSession(
            session_id="otherview000000000000000000001",
            virtual_agent_id=SAM_ID,
            owner_subject="auth0|other",
            context_kind="account_device",
            account_id="22222222-2222-4222-8222-222222222222",
            registered_device_id="band-b",
            nexus_client_id=OTHER_ID,
            answer_count=1,
            helpful=0,
            created_at=created,
        ),
    ])
    db_session.add(SysUser(id=3, username="agent-admin", password=hash_password("unused"), super_admin=ROLE_ADMIN, status=1))
    db_session.add(AdminClientAccess(admin_user_id=3, agent_id=SAM_ID, granted_by=1))
    db_session.add(AdminClientAccess(admin_user_id=3, agent_id=BILL_ID, granted_by=1))
    await db_session.commit()
    token, _expire = issue_token(3, "agent-admin", ROLE_ADMIN)
    headers = _auth(token)

    denied = (await client.get(f"/api/agent/{OTHER_ID}/sam-review?view=agent", headers=headers)).json()
    assert denied["code"] == 403

    client_view = (await client.get(f"/api/agent/{SAM_ID}/sam-review", headers=headers)).json()
    assert client_view["code"] == 0, client_view
    assert len(client_view["data"]["sessions"]) == 4
    assert all(item["nexusClientId"] is None for item in client_view["data"]["sessions"])

    agent_view = (await client.get(f"/api/agent/{SAM_ID}/sam-review?view=agent", headers=headers)).json()
    assert agent_view["code"] == 0, agent_view
    visible = {item["sessionId"] for item in agent_view["data"]["sessions"]}
    assert "billview0000000000000000000001" in visible
    assert "otherview000000000000000000001" not in visible
    assert len(visible) == 5
    assert agent_view["data"]["summary"]["notHelpful"] == 0

    bill = (await client.get(f"/api/agent/{BILL_ID}/sam-review", headers=headers)).json()
    assert [item["sessionId"] for item in bill["data"]["sessions"]] == ["billview0000000000000000000001"]

    root = (await client.get(
        f"/api/agent/{SAM_ID}/sam-review?view=agent", headers=_auth(admin_token),
    )).json()
    assert root["code"] == 0, root
    assert {item["sessionId"] for item in root["data"]["sessions"]} == visible | {"otherview000000000000000000001"}

    invalid = (await client.get(f"/api/agent/{SAM_ID}/sam-review?view=shared", headers=headers)).json()
    assert invalid["code"] == 400
    assert (await db_session.execute(select(func.count()).select_from(AiAgentChatHistory))).scalar_one() == 0


def test_sam_conversation_route_does_not_write_virtual_agent_messages():
    source = Path("/mnt/xiaozhi/nexus-server/api/careconnect_api/sam_conversation.py").read_text()
    route = Path("/mnt/xiaozhi/nexus-server/api/careconnect_api/routers/partner_bridgingcare.py").read_text()
    assert "CcVirtualAgentMessage" not in source
    assert "cc_virtual_agent_message" not in source
    assert "CcVirtualAgentMessage" not in route
    assert "cc_virtual_agent_message" not in route
