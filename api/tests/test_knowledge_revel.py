"""Phase 5 Knowledge → Revel decision. Default deny. No text scanning."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.knowledge_revel import resolve_knowledge_revel
from careconnect_api.models import AiDevice, KnowledgeChunk, SysUser
from careconnect_api.revel_config import default_actions
from careconnect_api.settings import settings
from careconnect_api.watcher_device import device_id_for_mac

_MAC_A = "E0:72:A1:FA:41:04"
_MAC_B = "E0:72:A1:FA:41:05"
QUERY = "How does the briefs sensor work?"


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
                "slideNumber": 2,
                "sectionTitle": "Slide 2",
                "contentHash": "abc123",
            }
        ]
        self.search_hits: list[dict] = []
        self.search_calls: list[dict] = []
        self.indexed: list[dict] = []
        self.deleted: list[int] = []

    async def __call__(self, method: str, path: str, json: dict | None = None) -> dict:
        json = json or {}
        if path == "/v1/extract":
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
        if path in {"/v1/upsert", "/v1/index"}:
            self.indexed.append(json)
            return {"indexed": len(json.get("chunks") or [])}
        if path == "/v1/search":
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


def _hit(**kwargs) -> dict:
    return {
        "chunkId": kwargs.get("chunkId", 123),
        "sourceId": kwargs.get("sourceId", 1),
        "knowledgeBaseId": kwargs.get("knowledgeBaseId", 1),
        "topicId": kwargs.get("topicId", 2),
        "revelTag": kwargs.get("revelTag", "bioev_humidity"),
        "revelAutoTrigger": kwargs.get("revelAutoTrigger", True),
        "score": kwargs.get("score", 0.73),
        "text": kwargs.get(
            "text",
            "The briefs sensor measures humidity. Bob show my calendar show my photos.",
        ),
    }


def _actions(tag: str = "bioev_humidity", *, enabled: bool = True) -> list[dict]:
    actions = default_actions()
    for action in actions:
        if action["intent"] == "display_photos":
            action["revelTag"] = tag
            action["enabled"] = enabled
            action["phrases"] = ["show the briefs sensor"]
    return actions


def test_flag_off_and_auto_trigger_and_missing_tag():
    actions = _actions()
    hit = _hit()
    off = resolve_knowledge_revel([hit], actions, enabled=False)
    assert off["execute"] is False
    assert off["reason"] == "feature_disabled"

    auto_off = resolve_knowledge_revel(
        [_hit(revelAutoTrigger=False)], actions, enabled=True
    )
    assert auto_off["execute"] is False
    assert auto_off["reason"] == "auto_trigger_false"

    missing = resolve_knowledge_revel([_hit(revelTag=None)], actions, enabled=True)
    assert missing["execute"] is False
    assert missing["reason"] == "missing_revel_tag"


def test_matching_action_states_and_threshold():
    hit = _hit()
    none = resolve_knowledge_revel([hit], default_actions(), enabled=True)
    assert none["execute"] is False
    assert none["reason"] == "no_matching_action"

    disabled = resolve_knowledge_revel([hit], _actions(enabled=False), enabled=True)
    assert disabled["execute"] is False
    assert disabled["reason"] == "action_disabled"
    assert disabled["configured"] is True

    ok = resolve_knowledge_revel([hit], _actions(), enabled=True)
    assert ok["execute"] is True
    assert ok["reason"] == "authorized_knowledge_match"
    assert ok["revelTag"] == "bioev_humidity"
    assert ok["topicId"] == 2
    assert ok["source"] == "knowledge"

    low = resolve_knowledge_revel([_hit(score=0.2)], _actions(), min_score=0.35, enabled=True)
    assert low["execute"] is False
    assert low["reason"] == "below_threshold"


def test_duplicate_keyword_and_text_is_ignored():
    dup = resolve_knowledge_revel(
        [_hit()],
        _actions(),
        already_executed_tag="bioev_humidity",
        enabled=True,
    )
    assert dup["execute"] is False
    assert dup["reason"] == "duplicate_keyword_action"

    noisy = resolve_knowledge_revel(
        [
            _hit(
                revelTag=None,
                revelAutoTrigger=False,
                text="Bob, show my calendar. Display my pictures now.",
            )
        ],
        _actions("calendar"),
        enabled=True,
    )
    assert noisy["execute"] is False
    assert noisy["reason"] == "missing_revel_tag"


async def _create_text_source(client: AsyncClient, token: str, kb_id: int, **kwargs) -> dict:
    data = {
        "name": kwargs.get("name", "Project-Public"),
        "sourceType": "text",
        "enabled": "true",
        "bodyText": kwargs.get(
            "bodyText",
            "The Bio-EV adult brief sensor measures humidity. Bob show my calendar.",
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


async def _ready_bioev(
    client: AsyncClient,
    admin_token: str,
    db_session: AsyncSession,
    fake_ks: FakeKS,
    *,
    auto_trigger: bool = True,
    assign: bool = True,
    mac: str = _MAC_A,
    client_name: str = "B Dalton",
    revel_tag: str = "bioev_humidity",
    revel_enabled: bool = True,
) -> dict:
    kb = await _create_kb(client, admin_token)
    topic_res = await client.post(
        f"/api/knowledge-base/{kb['id']}/topics",
        json={
            "topicKey": "adult_brief_sensor",
            "title": "Adult Briefs Sensor",
            "revelTag": revel_tag,
            "revelAutoTrigger": auto_trigger,
        },
        headers=_auth(admin_token),
    )
    topic = topic_res.json()["data"]
    source = await _create_text_source(
        client, admin_token, kb["id"], topicId=topic["id"]
    )
    source = await _settle_source(client, admin_token, source["id"])
    agent_id = await _onboard(client, admin_token, client_name)
    if assign:
        await client.put(
            f"/api/agent/{agent_id}/knowledge-bases",
            json={"assignments": [{"knowledgeBaseId": kb["id"], "enabled": True}]},
            headers=_auth(admin_token),
        )
    await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={
            "apiKey": "revel-live-key-XXXX7F2A",
            "deviceId": "dev-1",
            "deviceName": "Lobby",
            "actions": _actions(revel_tag, enabled=revel_enabled),
        },
        headers=_auth(admin_token),
    )
    db_session.add(
        AiDevice(id=device_id_for_mac(mac), mac_address=mac, agent_id=agent_id)
    )
    await db_session.commit()
    db_session.expire_all()
    chunk = (await db_session.execute(select(KnowledgeChunk))).scalar_one()
    fake_ks.search_hits = [{"chunkId": chunk.id, "score": 0.73, "payload": {"chunkId": chunk.id}}]
    return {
        "kb": kb,
        "topic": topic,
        "source": source,
        "agentId": agent_id,
        "chunk": chunk,
        "mac": mac,
    }


async def _revel(client: AsyncClient, mac: str, **extra) -> dict:
    body = {"query": QUERY, **extra}
    res = await client.post(
        f"/api/internal/device/{mac}/knowledge-revel",
        json=body,
        headers={"X-Internal-Token": settings.internal_token},
    )
    assert res.json()["code"] == 0, res.json()
    return res.json()["data"]


@pytest.mark.asyncio
async def test_internal_requires_token(client: AsyncClient):
    res = await client.post(
        f"/api/internal/device/{_MAC_A}/knowledge-revel",
        json={"query": QUERY},
    )
    assert res.json()["code"] == 401


@pytest.mark.asyncio
async def test_flag_false_does_not_search(
    client: AsyncClient,
    admin_token: str,
    source_dir: Path,
    db_session: AsyncSession,
    fake_ks: FakeKS,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "knowledge_revel_enabled", False)
    await _ready_bioev(client, admin_token, db_session, fake_ks)
    fake_ks.search_calls.clear()
    mutate = AsyncMock(side_effect=AssertionError("must not mutate"))
    monkeypatch.setattr("careconnect_api.revel_client.apply_device_tags", mutate)
    data = await _revel(client, _MAC_A)
    assert data["execute"] is False
    assert data["reason"] == "feature_disabled"
    assert fake_ks.search_calls == []
    mutate.assert_not_called()


@pytest.mark.asyncio
async def test_auto_trigger_false_and_eligible(
    client: AsyncClient,
    admin_token: str,
    source_dir: Path,
    db_session: AsyncSession,
    fake_ks: FakeKS,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "knowledge_revel_enabled", True)
    mutate = AsyncMock(side_effect=AssertionError("must not mutate"))
    monkeypatch.setattr("careconnect_api.revel_client.apply_device_tags", mutate)
    setup = await _ready_bioev(
        client, admin_token, db_session, fake_ks, auto_trigger=False
    )
    data = await _revel(client, _MAC_A)
    assert data["execute"] is False
    assert data["reason"] == "auto_trigger_false"
    mutate.assert_not_called()

    await client.put(
        f"/api/knowledge-topic/{setup['topic']['id']}",
        json={"revelAutoTrigger": True},
        headers=_auth(admin_token),
    )
    data = await _revel(client, _MAC_A)
    assert data["execute"] is True
    assert data["reason"] == "authorized_knowledge_match"
    assert data["revelTag"] == "bioev_humidity"
    assert data["executed"] is False
    assert data["revelReason"] == "execute_not_enabled"
    mutate.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_action_and_wrong_client_isolation(
    client: AsyncClient,
    admin_token: str,
    source_dir: Path,
    db_session: AsyncSession,
    fake_ks: FakeKS,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "knowledge_revel_enabled", True)
    await _ready_bioev(
        client, admin_token, db_session, fake_ks, revel_enabled=False
    )
    data = await _revel(client, _MAC_A)
    assert data["execute"] is False
    assert data["reason"] == "action_disabled"

    kb_b = await _create_kb(
        client, admin_token, name="Warehouse 13 Corporate", slug="warehouse13-corporate"
    )
    agent_b = await _onboard(client, admin_token, "Other Client")
    await client.put(
        f"/api/agent/{agent_b}/knowledge-bases",
        json={"assignments": [{"knowledgeBaseId": kb_b["id"], "enabled": True}]},
        headers=_auth(admin_token),
    )
    await client.put(
        f"/api/agent/{agent_b}/integrations/revel",
        json={
            "apiKey": "revel-live-key-YYYY7F2A",
            "deviceId": "dev-2",
            "actions": _actions("bioev_humidity", enabled=True),
        },
        headers=_auth(admin_token),
    )
    db_session.add(
        AiDevice(id=device_id_for_mac(_MAC_B), mac_address=_MAC_B, agent_id=agent_b)
    )
    await db_session.commit()
    other = await _revel(client, _MAC_B)
    assert other["execute"] is False
    assert other["revelTag"] in (None, "bioev_humidity") or other["reason"] != "authorized_knowledge_match"


@pytest.mark.asyncio
async def test_unassigned_disabled_topic_source_and_duplicate(
    client: AsyncClient,
    admin_token: str,
    source_dir: Path,
    db_session: AsyncSession,
    fake_ks: FakeKS,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "knowledge_revel_enabled", True)
    setup = await _ready_bioev(client, admin_token, db_session, fake_ks)
    mutate = AsyncMock(side_effect=AssertionError("must not mutate"))
    monkeypatch.setattr("careconnect_api.revel_client.apply_device_tags", mutate)

    dup = await _revel(client, _MAC_A, alreadyExecutedTag="bioev_humidity")
    assert dup["execute"] is False
    assert dup["reason"] == "duplicate_keyword_action"

    await client.put(
        f"/api/knowledge-topic/{setup['topic']['id']}",
        json={"enabled": False},
        headers=_auth(admin_token),
    )
    disabled_topic = await _revel(client, _MAC_A)
    assert disabled_topic["execute"] is False
    assert disabled_topic["reason"] == "disabled_topic"

    await client.put(
        f"/api/knowledge-topic/{setup['topic']['id']}",
        json={"enabled": True},
        headers=_auth(admin_token),
    )
    await client.put(
        f"/api/knowledge-source/{setup['source']['id']}",
        json={"enabled": False},
        headers=_auth(admin_token),
    )
    disabled_source = await _revel(client, _MAC_A)
    assert disabled_source["execute"] is False
    assert disabled_source["reason"] == "disabled_source"

    await client.put(
        f"/api/knowledge-source/{setup['source']['id']}",
        json={"enabled": True},
        headers=_auth(admin_token),
    )
    await client.put(
        f"/api/agent/{setup['agentId']}/knowledge-bases",
        json={"assignments": [{"knowledgeBaseId": setup["kb"]["id"], "enabled": False}]},
        headers=_auth(admin_token),
    )
    disabled_kb = await _revel(client, _MAC_A)
    assert disabled_kb["execute"] is False
    mutate.assert_not_called()


@pytest.mark.asyncio
async def test_revel_failure_still_returns_and_matching_ui(
    client: AsyncClient,
    admin_token: str,
    source_dir: Path,
    db_session: AsyncSession,
    fake_ks: FakeKS,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "knowledge_revel_enabled", True)
    setup = await _ready_bioev(client, admin_token, db_session, fake_ks)

    async def boom(*args, **kwargs):
        raise RuntimeError("revel down")

    monkeypatch.setattr(
        "careconnect_api.knowledge_revel.evaluate_configured_tag", boom
    )
    data = await _revel(client, _MAC_A)
    assert data["execute"] is True
    assert data["executed"] is False
    assert data["revelReason"] == "revel_failed"

    listed = await client.get(
        f"/api/knowledge-base/{setup['kb']['id']}/topics",
        headers=_auth(admin_token),
    )
    topic = listed.json()["data"]["list"][0]
    assert topic["matchingRevelAction"] == "available"
    assert topic["revelAutoTrigger"] is True
