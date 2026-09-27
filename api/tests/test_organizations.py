"""Organization grouping and client assignment."""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import AiAgent, SysUser
from careconnect_api.revel_write import revel_execute_enabled


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="function")
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    _ = client
    user = SysUser(
        id=1,
        username="admin1",
        password=hash_password("unused"),
        super_admin=ROLE_ROOT,
        status=1,
    )
    db_session.add(user)
    await db_session.commit()
    token, _ = issue_token(user.id, user.username, ROLE_ROOT)
    return token


async def _create_org(client: AsyncClient, token: str, name: str = "North Clinic") -> dict:
    resp = await client.post(
        "/api/organizations",
        json={
            "name": name,
            "mainContactName": "Pat Lee",
            "mainContactEmail": "pat@example.com",
            "mainContactPhone": "555-0100",
            "addressLine1": "1 Care Way",
            "city": "Austin",
            "state": "TX",
            "postalCode": "78701",
            "country": "US",
            "notes": "Primary facility",
        },
        headers=_auth(token),
    )
    assert resp.json()["code"] == 0, resp.json()
    return resp.json()["data"]


@pytest.mark.asyncio
async def test_create_and_edit_organization_preloads(client: AsyncClient, admin_token: str):
    created = await _create_org(client, admin_token)
    oid = created["id"]
    assert created["name"] == "North Clinic"
    assert created["status"] == "active"
    assert created["mainContactName"] == "Pat Lee"

    got = await client.get(f"/api/organizations/{oid}", headers=_auth(admin_token))
    data = got.json()["data"]
    assert data["mainContactEmail"] == "pat@example.com"
    assert data["city"] == "Austin"
    assert data["notes"] == "Primary facility"
    assert data["clientCount"] == 0
    assert data["clients"] == []

    updated = await client.put(
        f"/api/organizations/{oid}",
        json={"name": "North Clinic West", "city": "Round Rock"},
        headers=_auth(admin_token),
    )
    assert updated.json()["code"] == 0
    again = await client.get(f"/api/organizations/{oid}", headers=_auth(admin_token))
    body = again.json()["data"]
    assert body["name"] == "North Clinic West"
    assert body["city"] == "Round Rock"
    assert body["mainContactName"] == "Pat Lee"


@pytest.mark.asyncio
async def test_existing_clients_are_unassigned_until_linked(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    created = await client.post(
        "/api/agent/onboard",
        json={"name": "Una"},
        headers=_auth(admin_token),
    )
    agent_id = created.json()["data"]["agentId"]
    got = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    assert got.json()["data"]["organizationId"] in (None, "")

    listed = await client.get("/api/agent/list?organizationId=unassigned", headers=_auth(admin_token))
    ids = [a["id"] for a in listed.json()["data"]]
    assert agent_id in ids

    org = await _create_org(client, admin_token, "West Wing")
    patched = await client.patch(
        f"/api/agent/{agent_id}",
        json={"organizationId": org["id"]},
        headers=_auth(admin_token),
    )
    assert patched.json()["code"] == 0, patched.json()
    again = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    assert again.json()["data"]["organizationId"] == org["id"]
    assert again.json()["data"]["agentName"] == "Una"

    grouped = await client.get(
        f"/api/agent/list?organizationId={org['id']}",
        headers=_auth(admin_token),
    )
    assert [a["id"] for a in grouped.json()["data"]] == [agent_id]

    leftover = await client.get("/api/agent/list?organizationId=unassigned", headers=_auth(admin_token))
    assert agent_id not in [a["id"] for a in leftover.json()["data"]]

    db_session.expire_all()
    row = (await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))).scalar_one()
    assert row.organization_id == org["id"]


@pytest.mark.asyncio
async def test_onboard_can_assign_organization_and_edit_preloads(
    client: AsyncClient, admin_token: str
):
    org = await _create_org(client, admin_token, "Sales Floor")
    created = await client.post(
        "/api/agent/onboard",
        json={"name": "Riley", "organizationId": org["id"], "condition": "Demo walk-in"},
        headers=_auth(admin_token),
    )
    agent_id = created.json()["data"]["agentId"]
    got = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    data = got.json()["data"]
    assert data["organizationId"] == org["id"]
    assert data["condition"] == "Demo walk-in"
    assert data["personalityId"] == "sys_witty_tech_sidekick"


@pytest.mark.asyncio
async def test_cannot_delete_organization_with_clients(
    client: AsyncClient, admin_token: str
):
    org = await _create_org(client, admin_token, "Occupied")
    await client.post(
        "/api/agent/onboard",
        json={"name": "Sam", "organizationId": org["id"]},
        headers=_auth(admin_token),
    )
    blocked = await client.delete(f"/api/organizations/{org['id']}", headers=_auth(admin_token))
    assert blocked.json()["code"] == 409

    off = await client.post(
        f"/api/organizations/{org['id']}/deactivate",
        headers=_auth(admin_token),
    )
    assert off.json()["code"] == 0
    assert off.json()["data"]["status"] == "inactive"
    # Deactivate does not orphan: the client stays linked.
    listed = await client.get(
        f"/api/agent/list?organizationId={org['id']}",
        headers=_auth(admin_token),
    )
    assert len(listed.json()["data"]) == 1


@pytest.mark.asyncio
async def test_empty_org_can_be_deleted(client: AsyncClient, admin_token: str):
    org = await _create_org(client, admin_token, "Empty")
    deleted = await client.delete(f"/api/organizations/{org['id']}", headers=_auth(admin_token))
    assert deleted.json()["code"] == 0
    missing = await client.get(f"/api/organizations/{org['id']}", headers=_auth(admin_token))
    assert missing.json()["code"] == 404


def test_revel_execute_still_false_with_organizations():
    assert revel_execute_enabled() is False
