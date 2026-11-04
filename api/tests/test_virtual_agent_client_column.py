"""Virtual Agents roster Client column uses the bound care client.

boundClientId / boundClientName come from cc_virtual_agent_binding via
/agent/list. They must not repeat the virtual agent's own agent_name.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import AiAgent, CcVirtualAgentBinding, SysUser
from careconnect_api.agent_classification import PORTAL_AI_AGENTS_ORGANIZATION_ID

VA_ID = "va000000000000000000000000000001"
CLIENT_ID = "client00000000000000000000000001"


@pytest_asyncio.fixture(scope="function")
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    _ = client
    db_session.add(SysUser(id=1, username="admin1", password=hash_password("unused"), super_admin=ROLE_ROOT, status=1))
    await db_session.commit()
    token, _expire = issue_token(1, "admin1", ROLE_ROOT)
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_agent_list_exposes_bound_care_client_not_va_agent_name(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    db_session.add_all([
        AiAgent(
            id=VA_ID,
            agent_name="Sam",
            bot_name="Sam Virtual Portal Agent",
            organization_id=PORTAL_AI_AGENTS_ORGANIZATION_ID,
            chat_history_conf=0,
        ),
        AiAgent(
            id=CLIENT_ID,
            agent_name="Portal-US",
            bot_name="Sam Virtual Portal Agent",
            organization_id=PORTAL_AI_AGENTS_ORGANIZATION_ID,
            chat_history_conf=0,
        ),
    ])
    await db_session.commit()

    before = (
        await client.get(
            f"/api/agent/list?organizationId={PORTAL_AI_AGENTS_ORGANIZATION_ID}",
            headers=_auth(admin_token),
        )
    ).json()
    assert before["code"] == 0, before
    va_before = next(item for item in before["data"] if item["id"] == VA_ID)
    assert va_before["agentName"] == "Sam"
    assert va_before["boundClientId"] is None
    assert va_before["boundClientName"] is None

    db_session.add(
        CcVirtualAgentBinding(client_id=CLIENT_ID, virtual_agent_id=VA_ID, created_by=1)
    )
    await db_session.commit()

    after = (
        await client.get(
            f"/api/agent/list?organizationId={PORTAL_AI_AGENTS_ORGANIZATION_ID}",
            headers=_auth(admin_token),
        )
    ).json()
    assert after["code"] == 0, after
    va = next(item for item in after["data"] if item["id"] == VA_ID)
    assert va["boundAsVirtualAgent"] is True
    assert va["agentName"] == "Sam"
    assert va["boundClientId"] == CLIENT_ID
    assert va["boundClientName"] == "Portal-US"
    assert va["boundClientName"] != va["agentName"]
