"""Phase 2 Knowledge sources: persist files, metadata, keyword test search."""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import KnowledgeSource, KnowledgeTopic, SysUser
from careconnect_api.settings import settings


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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


@pytest.fixture
def source_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dest = tmp_path / "knowledge-sources"
    dest.mkdir()
    monkeypatch.setattr(settings, "knowledge_source_dir", str(dest))
    return dest


async def _create_text_source(
    client: AsyncClient, token: str, kb_id: int, **kwargs
) -> dict:
    data = {
        "name": kwargs.get("name", "Bio-EV Product Overview"),
        "sourceType": kwargs.get("sourceType", "text"),
        "description": kwargs.get("description", "Sales one-pager"),
        "enabled": kwargs.get("enabled", "true"),
        "bodyText": kwargs.get(
            "bodyText",
            "The Bio-EV adult brief sensor measures humidity and posts a silent alert.",
        ),
    }
    if "topicId" in kwargs and kwargs["topicId"] is not None:
        data["topicId"] = str(kwargs["topicId"])
    res = await client.post(
        f"/api/knowledge-base/{kb_id}/sources",
        data=data,
        headers=_auth(token),
    )
    assert res.json()["code"] == 0, res.json()
    return res.json()["data"]


@pytest.mark.asyncio
async def test_manual_text_source_persists_on_disk(
    client: AsyncClient, admin_token: str, source_dir: Path
):
    kb = await _create_kb(client, admin_token)
    created = await _create_text_source(client, admin_token, kb["id"])
    assert created["name"] == "Bio-EV Product Overview"
    assert created["sourceType"] == "text"
    assert created["status"] == "uploaded"
    assert created["enabled"] is True
    assert created["hasFile"] is True
    assert created["fileSize"] > 0

    stored = list(source_dir.rglob("source.txt"))
    assert len(stored) == 1
    assert "adult brief sensor" in stored[0].read_text()

    listed = await client.get(
        f"/api/knowledge-base/{kb['id']}/sources", headers=_auth(admin_token)
    )
    assert listed.json()["data"]["total"] == 1
    detail = await client.get(
        f"/api/knowledge-base/{kb['id']}", headers=_auth(admin_token)
    )
    assert detail.json()["data"]["sourceCount"] == 1
    assert detail.json()["data"]["clientCount"] == 0
    assert detail.json()["data"]["assignedClients"] == []


@pytest.mark.asyncio
async def test_file_source_upload_and_download(
    client: AsyncClient, admin_token: str, source_dir: Path
):
    kb = await _create_kb(client, admin_token)
    payload = b"%PDF-1.4 demo product sheet"
    res = await client.post(
        f"/api/knowledge-base/{kb['id']}/sources",
        data={
            "name": "Adult Brief Sensor Manual",
            "sourceType": "pdf",
            "description": "Sensor manual",
        },
        files={"file": ("brief-sensor.pdf", payload, "application/pdf")},
        headers=_auth(admin_token),
    )
    assert res.json()["code"] == 0, res.json()
    source = res.json()["data"]
    assert source["sourceType"] == "pdf"
    assert source["originalFilename"] == "brief-sensor.pdf"
    assert source["fileSize"] == len(payload)

    downloaded = await client.get(
        f"/api/knowledge-source/{source['id']}/file",
        headers=_auth(admin_token),
    )
    assert downloaded.status_code == 200
    assert downloaded.content == payload
    assert "application/pdf" in downloaded.headers.get("content-type", "")

    anon = await client.get(f"/api/knowledge-source/{source['id']}/file")
    assert anon.json()["code"] == 401


@pytest.mark.asyncio
async def test_replace_enable_disable_and_delete_does_not_remove_topic(
    client: AsyncClient, admin_token: str, source_dir: Path, db_session: AsyncSession
):
    kb = await _create_kb(client, admin_token)
    topic_res = await client.post(
        f"/api/knowledge-base/{kb['id']}/topics",
        json={
            "topicKey": "humidity_monitoring",
            "title": "Humidity Monitoring",
            "revelTag": "bioev_humidity",
        },
        headers=_auth(admin_token),
    )
    topic_id = topic_res.json()["data"]["id"]
    created = await _create_text_source(
        client, admin_token, kb["id"], topicId=topic_id
    )
    assert created["topicId"] == topic_id
    assert created["revelTag"] == "bioev_humidity"

    off = await client.put(
        f"/api/knowledge-source/{created['id']}",
        json={"enabled": False},
        headers=_auth(admin_token),
    )
    assert off.json()["data"]["enabled"] is False

    replaced = await client.post(
        f"/api/knowledge-source/{created['id']}/replace",
        data={"bodyText": "Updated humidity walkthrough for the Bio-EV booth."},
        headers=_auth(admin_token),
    )
    assert replaced.json()["code"] == 0, replaced.json()
    assert "Updated humidity" in (
        list(source_dir.rglob("source.txt"))[0].read_text()
    )

    deleted = await client.delete(
        f"/api/knowledge-source/{created['id']}", headers=_auth(admin_token)
    )
    assert deleted.json()["data"]["deleted"] is True
    db_session.expire_all()
    topics = (
        await db_session.execute(
            select(KnowledgeTopic).where(KnowledgeTopic.id == topic_id)
        )
    ).scalar_one_or_none()
    assert topics is not None
    assert topics.title == "Humidity Monitoring"
    leftover = (
        await db_session.execute(select(KnowledgeSource))
    ).scalars().all()
    assert leftover == []


@pytest.mark.asyncio
async def test_reprocess_and_test_search_are_not_fake_rag(
    client: AsyncClient, admin_token: str, source_dir: Path
):
    kb = await _create_kb(client, admin_token)
    topic_res = await client.post(
        f"/api/knowledge-base/{kb['id']}/topics",
        json={
            "topicKey": "adult_brief_sensor",
            "title": "Adult Brief Sensor",
            "revelTag": "bioev_brief_demo",
        },
        headers=_auth(admin_token),
    )
    topic_id = topic_res.json()["data"]["id"]
    source = await _create_text_source(client, admin_token, kb["id"], topicId=topic_id)

    reprocess = await client.post(
        f"/api/knowledge-source/{source['id']}/reprocess",
        headers=_auth(admin_token),
    )
    body = reprocess.json()["data"]
    assert body["reprocess"] is False
    assert body["retrievalConfigured"] is False
    assert body["status"] == "uploaded"

    empty = await client.get(
        f"/api/knowledge-base/{kb['id']}/test-search",
        headers=_auth(admin_token),
    )
    assert empty.json()["data"]["retrievalConfigured"] is False
    assert empty.json()["data"]["mode"] == "keyword"
    assert empty.json()["data"]["list"] == []

    hit = await client.get(
        f"/api/knowledge-base/{kb['id']}/test-search",
        params={"q": "How does the Bio-EV adult brief sensor work?"},
        headers=_auth(admin_token),
    )
    data = hit.json()["data"]
    assert data["retrievalConfigured"] is False
    assert data["mode"] == "keyword"
    assert data["total"] == 1
    assert data["list"][0]["source"] == "Bio-EV Product Overview"
    assert data["list"][0]["topic"] == "Adult Brief Sensor"
    assert data["list"][0]["revelTag"] == "bioev_brief_demo"
    assert "sensor" in (data["list"][0]["matchedText"] or "").lower()

    miss = await client.get(
        f"/api/knowledge-base/{kb['id']}/test-search",
        params={"q": "quantum teleportation schedule"},
        headers=_auth(admin_token),
    )
    miss_data = miss.json()["data"]
    assert miss_data["total"] == 0
    assert "Retrieval service not configured" in miss_data["message"]


@pytest.mark.asyncio
async def test_list_includes_source_and_client_counts(
    client: AsyncClient, admin_token: str, source_dir: Path
):
    kb = await _create_kb(client, admin_token)
    await _create_text_source(client, admin_token, kb["id"])
    agent_id = await _onboard(client, admin_token, "B Dalton")
    await client.put(
        f"/api/agent/{agent_id}/knowledge-bases",
        json={"assignments": [{"knowledgeBaseId": kb["id"], "enabled": True}]},
        headers=_auth(admin_token),
    )
    listed = await client.get("/api/knowledge-base", headers=_auth(admin_token))
    row = listed.json()["data"]["list"][0]
    assert row["sourceCount"] == 1
    assert row["clientCount"] == 1
    assert row["topicCount"] == 0

    detail = await client.get(
        f"/api/knowledge-base/{kb['id']}", headers=_auth(admin_token)
    )
    clients = detail.json()["data"]["assignedClients"]
    assert [c["agentName"] for c in clients] == ["B Dalton"]


@pytest.mark.asyncio
async def test_source_rejects_unknown_type_and_missing_file(
    client: AsyncClient, admin_token: str, source_dir: Path
):
    kb = await _create_kb(client, admin_token)
    bad_type = await client.post(
        f"/api/knowledge-base/{kb['id']}/sources",
        data={"name": "Nope", "sourceType": "exe", "bodyText": "x"},
        headers=_auth(admin_token),
    )
    assert bad_type.json()["code"] == 400

    missing_file = await client.post(
        f"/api/knowledge-base/{kb['id']}/sources",
        data={"name": "Manual PDF", "sourceType": "pdf"},
        headers=_auth(admin_token),
    )
    assert missing_file.json()["code"] == 400
    assert "file is required" in missing_file.json()["msg"]
