"""Phase 3 Knowledge retrieval: scoped search, processing, device resolver."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import AiDevice, KnowledgeChunk, KnowledgeSource, SysUser
from careconnect_api.settings import settings
from careconnect_api.watcher_device import device_id_for_mac

_COLON_MAC = "E0:72:A1:DB:36:40"
_UNBOUND_MAC = "AA:BB:CC:DD:EE:FF"


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


class FakeKS:
    def __init__(self) -> None:
        self.extract_chunks: list[dict] = [
            {
                "chunkIndex": 0,
                "text": "The Bio-EV adult brief sensor measures humidity and posts a silent alert.",
                "pageNumber": None,
                "slideNumber": 4,
                "sectionTitle": "Slide 4",
                "contentHash": "abc123",
            }
        ]
        self.search_hits: list[dict] = []
        self.indexed: list[dict] = []
        self.deleted: list[int] = []
        self.search_calls: list[dict] = []
        self.calls: list[str] = []
        self.fail_extract = False

    async def __call__(self, method: str, path: str, json: dict | None = None):
        json = json or {}
        self.calls.append(path)
        if path == "/v1/extract":
            if self.fail_extract:
                from careconnect_api.envelope import APIException

                raise APIException(502, "extractor exploded")
            units = [
                {
                    "text": row["text"],
                    "pageNumber": row.get("pageNumber"),
                    "slideNumber": row.get("slideNumber"),
                    "sectionTitle": row.get("sectionTitle"),
                }
                for row in self.extract_chunks
            ]
            text = " ".join(u["text"] for u in units)
            return {
                "units": units,
                "characterCount": len(text),
                "unitCount": len(units),
                "metadata": {"characterCount": len(text)},
            }
        if path == "/v1/chunk":
            return {"chunks": self.extract_chunks, "chunkCount": len(self.extract_chunks)}
        if path == "/v1/embed":
            texts = list(json.get("texts") or [])
            return {"embeddings": [[0.01] * 8 for _ in texts], "count": len(texts)}
        if path == "/v1/upsert":
            self.indexed.append(json)
            return {"indexed": len(json.get("chunks") or [])}
        if path == "/v1/index":
            self.indexed.append(json)
            return {"indexed": len(json.get("chunks") or [])}
        if path == "/v1/search":
            assert json.get("knowledgeBaseIds"), json
            self.search_calls.append(json)
            return {"query": json.get("query"), "results": self.search_hits}
        if path == "/v1/delete-source":
            self.deleted.append(int(json["sourceId"]))
            return {"deleted": True, "sourceId": json["sourceId"]}
        if path == "/v1/set-enabled":
            return json
        raise AssertionError(f"unexpected knowledge-service call {method} {path}")


@pytest.fixture
def fake_ks(monkeypatch: pytest.MonkeyPatch) -> FakeKS:
    ks = FakeKS()
    monkeypatch.setattr(settings, "knowledge_service_url", "http://knowledge-service:8090")
    monkeypatch.setattr("careconnect_api.knowledge_retrieval.ks_request", ks)
    return ks


async def _create_text_source(client: AsyncClient, token: str, kb_id: int, **kwargs) -> dict:
    data = {
        "name": kwargs.get("name", "Bio-EV Product Overview"),
        "sourceType": kwargs.get("sourceType", "text"),
        "description": kwargs.get("description", "Sales one-pager"),
        "enabled": "true",
        "bodyText": kwargs.get(
            "bodyText",
            "The Bio-EV adult brief sensor measures humidity and posts a silent alert.",
        ),
    }
    if kwargs.get("topicId") is not None:
        data["topicId"] = str(kwargs["topicId"])
    res = await client.post(
        f"/api/knowledge-base/{kb_id}/sources",
        data=data,
        headers=_auth(token),
    )
    assert res.json()["code"] == 0, res.json()
    return res.json()["data"]


async def _latest_source(client: AsyncClient, token: str, source_id: int) -> dict:
    res = await client.get(f"/api/knowledge-source/{source_id}", headers=_auth(token))
    assert res.json()["code"] == 0, res.json()
    return res.json()["data"]


async def _settle_source(client: AsyncClient, token: str, source_id: int) -> dict:
    last: dict | None = None
    for _ in range(40):
        last = await _latest_source(client, token, source_id)
        if last["status"] != "processing":
            return last
        await asyncio.sleep(0.05)
    assert last is not None
    return last


@pytest.mark.asyncio
async def test_search_requires_knowledge_base_ids(
    client: AsyncClient, admin_token: str, fake_ks: FakeKS
):
    missing = await client.post(
        "/api/knowledge/search",
        json={"query": "How does the adult brief sensor work?", "limit": 5},
        headers=_auth(admin_token),
    )
    assert missing.json()["code"] == 400
    assert "never global" in missing.json()["msg"]
    empty = await client.post(
        "/api/knowledge/search",
        json={"knowledgeBaseIds": [], "query": "sensor", "limit": 5},
        headers=_auth(admin_token),
    )
    assert empty.json()["code"] == 400
    assert fake_ks.search_calls == []


@pytest.mark.asyncio
async def test_search_unauthenticated(client: AsyncClient):
    res = await client.post(
        "/api/knowledge/search",
        json={"knowledgeBaseIds": [1], "query": "sensor"},
    )
    assert res.json()["code"] == 401


@pytest.mark.asyncio
async def test_reprocess_indexes_chunks_and_scoped_search(
    client: AsyncClient,
    admin_token: str,
    source_dir: Path,
    db_session: AsyncSession,
    fake_ks: FakeKS,
):
    kb = await _create_kb(client, admin_token)
    other = await _create_kb(
        client, admin_token, name="Warehouse 13 Corporate", slug="warehouse13-corporate"
    )
    topic_res = await client.post(
        f"/api/knowledge-base/{kb['id']}/topics",
        json={
            "topicKey": "adult_brief_sensor",
            "title": "Adult Brief Sensor",
            "revelTag": "care_overview",
            "revelAutoTrigger": False,
        },
        headers=_auth(admin_token),
    )
    topic_id = topic_res.json()["data"]["id"]
    source = await _create_text_source(client, admin_token, kb["id"], topicId=topic_id)
    assert source["status"] == "processing"
    assert source["processingStage"] == "extracting"
    assert source["errorMessage"] is None
    source = await _settle_source(client, admin_token, source["id"])
    assert source["status"] == "ready"
    assert source["processingStage"] == "ready"
    assert source["processingProgress"] == 100
    assert source["chunkCount"] == 1
    assert source["indexedChunkCount"] == 1
    assert source["extractedCharCount"] and source["extractedCharCount"] > 0
    assert source["indexedAt"] is not None
    assert source["processedAt"] is not None
    assert fake_ks.calls.count("/v1/extract") >= 1
    assert fake_ks.calls.count("/v1/chunk") >= 1
    assert fake_ks.calls.count("/v1/embed") >= 1
    assert fake_ks.calls.count("/v1/upsert") >= 1

    db_session.expire_all()
    chunks = list((await db_session.execute(select(KnowledgeChunk))).scalars().all())
    assert len(chunks) == 1
    assert chunks[0].slide_number == 4
    assert fake_ks.indexed and fake_ks.indexed[0]["chunks"][0]["chunkId"] == chunks[0].id
    assert fake_ks.indexed[0]["chunks"][0]["revelTag"] == "care_overview"

    fake_ks.search_hits = [
        {
            "chunkId": chunks[0].id,
            "score": 0.83,
            "payload": {
                "knowledgeBaseId": kb["id"],
                "sourceId": source["id"],
                "chunkId": chunks[0].id,
            },
        }
    ]
    searched = await client.post(
        "/api/knowledge/search",
        json={
            "knowledgeBaseIds": [kb["id"]],
            "query": "How does the adult brief sensor work?",
            "limit": 5,
        },
        headers=_auth(admin_token),
    )
    body = searched.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["query"] == "How does the adult brief sensor work?"
    assert len(data["results"]) == 1
    hit = data["results"][0]
    assert hit["knowledgeBaseId"] == kb["id"]
    assert hit["sourceId"] == source["id"]
    assert hit["sourceName"] == "Bio-EV Product Overview"
    assert hit["topicId"] == topic_id
    assert hit["slideNumber"] == 4
    assert hit["score"] == 0.83
    assert hit["revelTag"] == "care_overview"
    assert hit["revelAutoTrigger"] is False
    assert "adult brief sensor" in hit["text"].lower()
    assert fake_ks.search_calls[-1]["knowledgeBaseIds"] == [kb["id"]]
    assert other["id"] not in fake_ks.search_calls[-1]["knowledgeBaseIds"]


@pytest.mark.asyncio
async def test_image_source_ready_with_zero_chunks(
    client: AsyncClient, admin_token: str, source_dir: Path, fake_ks: FakeKS
):
    fake_ks.extract_chunks = []
    kb = await _create_kb(client, admin_token)
    res = await client.post(
        f"/api/knowledge-base/{kb['id']}/sources",
        data={"name": "J-Style Image", "sourceType": "image"},
        files={"file": ("J-Stlye Image.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        headers=_auth(admin_token),
    )
    created = res.json()["data"]
    assert created["status"] in {"processing", "ready"}
    source = await _settle_source(client, admin_token, created["id"])
    assert source["status"] == "ready"
    assert source["processingStage"] == "ready"
    assert source["processingProgress"] == 100
    assert source["chunkCount"] == 0
    assert source["indexedChunkCount"] == 0
    assert fake_ks.indexed == []


@pytest.mark.asyncio
async def test_device_search_is_scoped_and_requires_internal_token(
    client: AsyncClient,
    admin_token: str,
    source_dir: Path,
    db_session: AsyncSession,
    fake_ks: FakeKS,
):
    kb = await _create_kb(client, admin_token)
    other = await _create_kb(
        client, admin_token, name="Other Client Kit", slug="other-kit"
    )
    topic_res = await client.post(
        f"/api/knowledge-base/{kb['id']}/topics",
        json={"topicKey": "care_overview", "title": "Care Overview", "revelTag": "care_overview"},
        headers=_auth(admin_token),
    )
    source = await _create_text_source(
        client, admin_token, kb["id"], topicId=topic_res.json()["data"]["id"]
    )
    source = await _settle_source(client, admin_token, source["id"])
    assert source["status"] == "ready"
    agent_id = await _onboard(client, admin_token, "Fargo Bio-EV Demo")
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
    db_session.add(
        AiDevice(
            id=device_id_for_mac(_UNBOUND_MAC),
            mac_address=_UNBOUND_MAC,
            agent_id=None,
        )
    )
    await db_session.commit()

    db_session.expire_all()
    chunk = (await db_session.execute(select(KnowledgeChunk))).scalar_one()
    fake_ks.search_hits = [{"chunkId": chunk.id, "score": 0.71, "payload": {"chunkId": chunk.id}}]

    denied = await client.post(
        f"/api/internal/device/{_COLON_MAC}/knowledge-search",
        json={"query": "How does the adult brief sensor work?", "limit": 5},
    )
    assert denied.json()["code"] == 401

    allowed = await client.post(
        f"/api/internal/device/{_COLON_MAC}/knowledge-search",
        json={"query": "How does the adult brief sensor work?", "limit": 5},
        headers={"X-Internal-Token": settings.internal_token},
    )
    body = allowed.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["clientId"] == agent_id
    assert data["knowledgeBaseIds"] == [kb["id"]]
    assert other["id"] not in data["knowledgeBaseIds"]
    assert fake_ks.search_calls[-1]["knowledgeBaseIds"] == [kb["id"]]
    assert data["results"][0]["sourceId"] == source["id"]
    assert data["results"][0]["revelTag"] == "care_overview"

    unbound = await client.post(
        f"/api/internal/device/{_UNBOUND_MAC}/knowledge-search",
        json={"query": "How does the adult brief sensor work?"},
        headers={"X-Internal-Token": settings.internal_token},
    )
    empty = unbound.json()["data"]
    assert empty["results"] == []
    assert empty["knowledgeBaseIds"] == []
    assert empty["clientId"] is None
    assert len(fake_ks.search_calls) == 1


@pytest.mark.asyncio
async def test_delete_source_removes_chunks_and_vectors(
    client: AsyncClient,
    admin_token: str,
    source_dir: Path,
    db_session: AsyncSession,
    fake_ks: FakeKS,
):
    kb = await _create_kb(client, admin_token)
    source = await _create_text_source(client, admin_token, kb["id"])
    source = await _settle_source(client, admin_token, source["id"])
    source_id = source["id"]
    deleted = await client.delete(
        f"/api/knowledge-source/{source_id}", headers=_auth(admin_token)
    )
    assert deleted.json()["data"]["deleted"] is True
    db_session.expire_all()
    leftover = (await db_session.execute(select(KnowledgeChunk))).scalars().all()
    assert leftover == []
    assert source_id in fake_ks.deleted
    sources = (await db_session.execute(select(KnowledgeSource))).scalars().all()
    assert sources == []


@pytest.mark.asyncio
async def test_processing_commits_real_stage_progress(
    client: AsyncClient,
    admin_token: str,
    source_dir: Path,
    fake_ks: FakeKS,
    monkeypatch: pytest.MonkeyPatch,
):
    from careconnect_api import knowledge_retrieval as kr

    snapshots: list[dict] = []
    original = kr._save_progress
    fake_ks.extract_chunks = [
        {
            "chunkIndex": i,
            "text": f"Chunk {i} of the Bio-EV adult brief sensor humidity alert.",
            "pageNumber": i + 1,
            "slideNumber": None,
            "sectionTitle": f"Page {i + 1}",
            "contentHash": f"hash{i}",
        }
        for i in range(3)
    ]
    monkeypatch.setattr(settings, "knowledge_index_batch_size", 1)

    async def spy(db, source, **fields):
        row = await original(db, source, **fields)
        snapshots.append(
            {
                "status": row.status,
                "stage": row.processing_stage,
                "progress": row.processing_progress,
                "chunks": row.chunk_count,
                "indexed": row.indexed_chunk_count,
            }
        )
        return row

    monkeypatch.setattr(kr, "_save_progress", spy)
    kb = await _create_kb(client, admin_token)
    created = await _create_text_source(client, admin_token, kb["id"])
    assert created["status"] == "processing"
    source = await _settle_source(client, admin_token, created["id"])
    assert source["status"] == "ready"
    assert source["processingStage"] == "ready"
    assert source["processingProgress"] == 100
    assert source["chunkCount"] == 3
    assert source["indexedChunkCount"] == 3

    stages = [row["stage"] for row in snapshots]
    assert "extracting" in stages or created["processingStage"] == "extracting"
    assert "chunking" in stages
    assert "generating_embeddings" in stages
    assert "indexing" in stages
    assert stages[-1] == "ready"
    assert snapshots[-1]["status"] == "ready"
    embed_snaps = [row for row in snapshots if row["stage"] == "generating_embeddings"]
    assert embed_snaps[0]["indexed"] == 0
    assert embed_snaps[0]["progress"] == 0
    assert embed_snaps[-1]["indexed"] == 2
    assert embed_snaps[-1]["progress"] == 67
    assert any(row["indexed"] == 3 and row["progress"] == 100 for row in snapshots)


@pytest.mark.asyncio
async def test_reprocess_clears_error_and_rebuilds(
    client: AsyncClient,
    admin_token: str,
    source_dir: Path,
    fake_ks: FakeKS,
):
    fake_ks.fail_extract = True
    kb = await _create_kb(client, admin_token)
    created = await _create_text_source(client, admin_token, kb["id"])
    failed = await _settle_source(client, admin_token, created["id"])
    assert failed["status"] == "failed"
    assert "exploded" in (failed["errorMessage"] or "").lower()

    fake_ks.fail_extract = False
    listed = await client.get(
        f"/api/knowledge-base/{kb['id']}/sources", headers=_auth(admin_token)
    )
    listed_row = listed.json()["data"]["list"][0]
    assert listed_row["status"] == "failed"
    assert listed_row["errorMessage"]

    reprocess = await client.post(
        f"/api/knowledge-source/{created['id']}/reprocess",
        headers=_auth(admin_token),
    )
    body = reprocess.json()["data"]
    assert body["reprocess"] is True
    assert body["status"] in {"processing", "ready"}
    if body["status"] == "processing":
        assert body["errorMessage"] is None
        assert body["processingStage"] == "extracting"
    ready = await _settle_source(client, admin_token, created["id"])
    assert ready["status"] == "ready"
    assert ready["errorMessage"] is None
    assert ready["processingStage"] == "ready"
    assert ready["processingProgress"] == 100
    assert ready["chunkCount"] == 1
    assert ready["indexedChunkCount"] == 1
    assert ready["processedAt"] is not None
