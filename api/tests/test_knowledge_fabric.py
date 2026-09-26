"""Phase 1 NEXUS Knowledge Fabric: catalog, assignments, device inheritance."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import AiDevice, SysUser
from careconnect_api.revel_config import EXECUTE_ENABLED
from careconnect_api.watcher_device import device_id_for_mac

_COLON_MAC = "E0:72:A1:DB:36:40"
_UNBOUND_MAC = "AA:BB:CC:DD:EE:FF"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="function")
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    _ = client
    user = SysUser(
        id=1,
        username="admin1",
        password=hash_password("unused-in-these-tests"),
        super_admin=ROLE_ROOT,
        status=1,
    )
    db_session.add(user)
    await db_session.commit()
    token, _expire = issue_token(user.id, user.username, ROLE_ROOT)
    return token


async def _onboard(client: AsyncClient, token: str, name: str) -> str:
    created = await client.post(
        "/api/agent/onboard",
        json={"name": name, "condition": "demo"},
        headers=_auth(token),
    )
    assert created.json()["code"] == 0, created.json()
    return created.json()["data"]["agentId"]


async def _create_kb(client: AsyncClient, token: str, **kwargs) -> dict:
    body = {"name": "Bio-EV Sales", "slug": "bioev-sales", **kwargs}
    res = await client.post("/api/knowledge-base", json=body, headers=_auth(token))
    assert res.json()["code"] == 0, res.json()
    return res.json()["data"]


@pytest.mark.asyncio
async def test_create_and_update_knowledge_base(client: AsyncClient, admin_token: str):
    created = await _create_kb(
        client, admin_token, description="Sales floor", knowledgeType="sales"
    )
    assert created["name"] == "Bio-EV Sales"
    assert created["slug"] == "bioev-sales"
    assert created["enabled"] is True
    assert created["knowledgeType"] == "sales"
    assert created["topicCount"] == 0

    listed = await client.get("/api/knowledge-base", headers=_auth(admin_token))
    assert listed.json()["code"] == 0
    assert listed.json()["data"]["total"] == 1
    assert listed.json()["data"]["list"][0]["id"] == created["id"]

    updated = await client.put(
        f"/api/knowledge-base/{created['id']}",
        json={"description": "Updated sales kit", "enabled": False},
        headers=_auth(admin_token),
    )
    assert updated.json()["code"] == 0
    data = updated.json()["data"]
    assert data["description"] == "Updated sales kit"
    assert data["enabled"] is False
    assert data["name"] == "Bio-EV Sales"


@pytest.mark.asyncio
async def test_enable_disable_knowledge_base(client: AsyncClient, admin_token: str):
    kb = await _create_kb(client, admin_token)
    off = await client.put(
        f"/api/knowledge-base/{kb['id']}",
        json={"enabled": False},
        headers=_auth(admin_token),
    )
    assert off.json()["data"]["enabled"] is False
    on = await client.put(
        f"/api/knowledge-base/{kb['id']}",
        json={"enabled": True},
        headers=_auth(admin_token),
    )
    assert on.json()["data"]["enabled"] is True


@pytest.mark.asyncio
async def test_topic_crud_and_revel_metadata(client: AsyncClient, admin_token: str):
    kb = await _create_kb(client, admin_token)
    created = await client.post(
        f"/api/knowledge-base/{kb['id']}/topics",
        json={
            "topicKey": "humidity_monitoring",
            "title": "Humidity Monitoring",
            "description": "Sensor walkthrough",
            "revelTag": "bioev_humidity_demo",
            "revelAutoTrigger": True,
        },
        headers=_auth(admin_token),
    )
    assert created.json()["code"] == 0, created.json()
    topic = created.json()["data"]
    assert topic["topicKey"] == "humidity_monitoring"
    assert topic["revelTag"] == "bioev_humidity_demo"
    assert topic["revelAutoTrigger"] is True
    assert topic["enabled"] is True

    listed = await client.get(
        f"/api/knowledge-base/{kb['id']}/topics", headers=_auth(admin_token)
    )
    assert listed.json()["data"]["total"] == 1

    patched = await client.put(
        f"/api/knowledge-topic/{topic['id']}",
        json={"title": "Humidity Sensor", "revelAutoTrigger": False},
        headers=_auth(admin_token),
    )
    assert patched.json()["data"]["title"] == "Humidity Sensor"
    assert patched.json()["data"]["revelAutoTrigger"] is False
    assert patched.json()["data"]["revelTag"] == "bioev_humidity_demo"

    detail = await client.get(
        f"/api/knowledge-base/{kb['id']}", headers=_auth(admin_token)
    )
    assert detail.json()["data"]["topicCount"] == 1
    assert detail.json()["data"]["topics"][0]["topicKey"] == "humidity_monitoring"

    deleted = await client.delete(
        f"/api/knowledge-topic/{topic['id']}", headers=_auth(admin_token)
    )
    assert deleted.json()["data"]["deleted"] is True
    empty = await client.get(
        f"/api/knowledge-base/{kb['id']}/topics", headers=_auth(admin_token)
    )
    assert empty.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_assign_multiple_bases_and_share_across_clients(
    client: AsyncClient, admin_token: str
):
    bio = await _create_kb(client, admin_token)
    corp = await _create_kb(
        client, admin_token, name="Warehouse 13 Corporate", slug="warehouse13-corporate"
    )
    care = await _create_kb(
        client, admin_token, name="Care Assistant", slug="care-assistant"
    )
    a = await _onboard(client, admin_token, "Fargo Bio-EV Demo")
    b = await _onboard(client, admin_token, "Other Site")

    put_a = await client.put(
        f"/api/agent/{a}/knowledge-bases",
        json={
            "assignments": [
                {"knowledgeBaseId": bio["id"], "enabled": True},
                {"knowledgeBaseId": corp["id"], "enabled": True},
            ]
        },
        headers=_auth(admin_token),
    )
    assert put_a.json()["code"] == 0, put_a.json()
    assert put_a.json()["data"]["total"] == 2
    slugs = {row["slug"] for row in put_a.json()["data"]["list"]}
    assert slugs == {"bioev-sales", "warehouse13-corporate"}
    assert care["id"] not in {row["id"] for row in put_a.json()["data"]["list"]}

    put_b = await client.put(
        f"/api/agent/{b}/knowledge-bases",
        json={"assignments": [{"knowledgeBaseId": bio["id"], "enabled": True}]},
        headers=_auth(admin_token),
    )
    assert put_b.json()["data"]["total"] == 1
    assert put_b.json()["data"]["list"][0]["id"] == bio["id"]

    got_a = await client.get(f"/api/agent/{a}/knowledge-bases", headers=_auth(admin_token))
    assert got_a.json()["data"]["total"] == 2

    removed = await client.put(
        f"/api/agent/{a}/knowledge-bases",
        json={"assignments": [{"knowledgeBaseId": bio["id"], "enabled": True}]},
        headers=_auth(admin_token),
    )
    assert removed.json()["data"]["total"] == 1
    assert removed.json()["data"]["list"][0]["slug"] == "bioev-sales"

    empty = await client.put(
        f"/api/agent/{a}/knowledge-bases",
        json={"assignments": []},
        headers=_auth(admin_token),
    )
    assert empty.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_client_with_no_assignments_returns_empty_list(
    client: AsyncClient, admin_token: str
):
    agent_id = await _onboard(client, admin_token, "No Knowledge")
    got = await client.get(
        f"/api/agent/{agent_id}/knowledge-bases", headers=_auth(admin_token)
    )
    assert got.json()["code"] == 0
    assert got.json()["data"]["list"] == []
    assert got.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_device_inherits_client_knowledge_and_unbound_is_empty(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    kb = await _create_kb(client, admin_token)
    await client.post(
        f"/api/knowledge-base/{kb['id']}/topics",
        json={
            "topicKey": "humidity_monitoring",
            "title": "Humidity Monitoring",
            "revelTag": "bioev_humidity_demo",
            "revelAutoTrigger": True,
        },
        headers=_auth(admin_token),
    )
    await client.post(
        f"/api/knowledge-base/{kb['id']}/topics",
        json={
            "topicKey": "disabled_topic",
            "title": "Hidden",
            "enabled": False,
            "revelTag": "should_not_appear",
        },
        headers=_auth(admin_token),
    )
    agent_id = await _onboard(client, admin_token, "Fargo Bio-EV Demo")
    await client.put(
        f"/api/agent/{agent_id}/knowledge-bases",
        json={"assignments": [{"knowledgeBaseId": kb["id"], "enabled": True}]},
        headers=_auth(admin_token),
    )

    bound = AiDevice(
        id=device_id_for_mac(_COLON_MAC),
        mac_address=_COLON_MAC,
        agent_id=agent_id,
        alias="demo-watcher",
    )
    unbound = AiDevice(
        id=device_id_for_mac(_UNBOUND_MAC),
        mac_address=_UNBOUND_MAC,
        agent_id=None,
    )
    db_session.add_all([bound, unbound])
    await db_session.commit()

    inherited = await client.get(
        f"/api/device/{_COLON_MAC}/knowledge-context",
        headers=_auth(admin_token),
    )
    assert inherited.json()["code"] == 0, inherited.json()
    data = inherited.json()["data"]
    assert data["deviceMac"] == "e0:72:a1:db:36:40"
    assert data["clientId"] == agent_id
    assert len(data["knowledgeBases"]) == 1
    assert data["knowledgeBases"][0]["slug"] == "bioev-sales"
    topics = data["knowledgeBases"][0]["topics"]
    assert [t["topicKey"] for t in topics] == ["humidity_monitoring"]
    assert topics[0]["revelTag"] == "bioev_humidity_demo"
    assert topics[0]["revelAutoTrigger"] is True

    none = await client.get(
        f"/api/device/{_UNBOUND_MAC}/knowledge-context",
        headers=_auth(admin_token),
    )
    assert none.json()["data"]["clientId"] is None
    assert none.json()["data"]["knowledgeBases"] == []


@pytest.mark.asyncio
async def test_resolver_does_not_execute_revel(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    assert EXECUTE_ENABLED is False
    kb = await _create_kb(client, admin_token)
    await client.post(
        f"/api/knowledge-base/{kb['id']}/topics",
        json={
            "topicKey": "humidity_monitoring",
            "title": "Humidity Monitoring",
            "revelTag": "bioev_humidity_demo",
            "revelAutoTrigger": True,
        },
        headers=_auth(admin_token),
    )
    agent_id = await _onboard(client, admin_token, "Demo")
    await client.put(
        f"/api/agent/{agent_id}/knowledge-bases",
        json={"assignments": [{"knowledgeBaseId": kb["id"], "enabled": True}]},
        headers=_auth(admin_token),
    )
    db_session.add(
        AiDevice(
            id=device_id_for_mac(_COLON_MAC),
            mac_address=_COLON_MAC,
            agent_id=agent_id,
        )
    )
    await db_session.commit()

    with (
        patch("careconnect_api.revel_client.apply_device_tags", new_callable=AsyncMock) as apply_tags,
        patch("careconnect_api.revel_command.evaluate_voice_command", new_callable=AsyncMock) as evaluate,
    ):
        res = await client.get(
            f"/api/device/{_COLON_MAC}/knowledge-context",
            headers=_auth(admin_token),
        )
        assert res.json()["code"] == 0
        assert res.json()["data"]["knowledgeBases"][0]["topics"][0]["revelTag"] == "bioev_humidity_demo"
        apply_tags.assert_not_called()
        evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_existing_client_and_patch_unchanged_with_empty_knowledge(
    client: AsyncClient, admin_token: str
):
    agent_id = await _onboard(client, admin_token, "Ada Lovelace")
    patched = await client.patch(
        f"/api/agent/{agent_id}",
        json={"name": "Ada L.", "botName": "AdaBot"},
        headers=_auth(admin_token),
    )
    assert patched.json()["code"] == 0, patched.json()
    got = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    assert got.json()["data"]["agentName"] == "Ada L."
    assert got.json()["data"]["botName"] == "AdaBot"
    assignments = await client.get(
        f"/api/agent/{agent_id}/knowledge-bases", headers=_auth(admin_token)
    )
    assert assignments.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_disabled_assignment_or_base_is_not_inherited(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    kb = await _create_kb(client, admin_token)
    await client.post(
        f"/api/knowledge-base/{kb['id']}/topics",
        json={"topicKey": "humidity_monitoring", "title": "Humidity Monitoring"},
        headers=_auth(admin_token),
    )
    agent_id = await _onboard(client, admin_token, "Demo")
    await client.put(
        f"/api/agent/{agent_id}/knowledge-bases",
        json={"assignments": [{"knowledgeBaseId": kb["id"], "enabled": False}]},
        headers=_auth(admin_token),
    )
    db_session.add(
        AiDevice(
            id=device_id_for_mac(_COLON_MAC),
            mac_address=_COLON_MAC,
            agent_id=agent_id,
        )
    )
    await db_session.commit()
    res = await client.get(
        f"/api/device/{_COLON_MAC}/knowledge-context", headers=_auth(admin_token)
    )
    assert res.json()["data"]["clientId"] == agent_id
    assert res.json()["data"]["knowledgeBases"] == []
