"""Phase 4: XiaoZhi authorized knowledge grounding. Fail-open. No Revel execute."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.knowledge_grounding import (  # noqa: E402
    GROUNDING_PREAMBLE,
    apply_grounding,
    build_knowledge_section,
    clip_chars,
    expand_query,
    format_grounding_section,
    ground_turn_messages,
    knowledge_enabled,
    should_retrieve,
)

MAC_A = "E0:72:A1:FA:41:04"
MAC_B = "AA:BB:CC:DD:EE:FF"
PERSONA = "You are B Dalton, a warm companion for Bio-EV sales conversations."
QUERY = "How does the briefs sensor work?"


class FakeLog:
    def __init__(self):
        self.records: list[tuple[str, str]] = []

    def bind(self, **kwargs):
        return self

    def debug(self, msg, *args, **kwargs):
        self.records.append(("debug", str(msg)))

    def info(self, msg, *args, **kwargs):
        self.records.append(("info", str(msg)))

    def warning(self, msg, *args, **kwargs):
        self.records.append(("warning", str(msg)))


def _hit(**kwargs):
    return {
        "chunkId": kwargs.get("chunkId", 123),
        "sourceId": kwargs.get("sourceId", 1),
        "sourceName": kwargs.get("sourceName", "Project-Public"),
        "originalFilename": kwargs.get("originalFilename", "Project-Public.pptx"),
        "citation": kwargs.get("citation", "Project-Public — Slide 2"),
        "topic": kwargs.get("topic", "Adult Briefs Sensor"),
        "text": kwargs.get(
            "text",
            "The briefs sensor measures humidity in the brief and posts a silent alert.",
        ),
        "score": kwargs.get("score", 0.7357),
        "revelTag": kwargs.get("revelTag", "bioev_humidity"),
        "revelAutoTrigger": kwargs.get("revelAutoTrigger", False),
        "knowledgeBaseId": kwargs.get("knowledgeBaseId", 1),
        "slideNumber": kwargs.get("slideNumber", 2),
        "pageNumber": kwargs.get("pageNumber"),
    }


def _payload(context=None, *, client_id="agent-a", bases=None):
    items = list(context if context is not None else [_hit()])
    bases = bases if bases is not None else [{"id": 1, "name": "Bio-EV Sales"}]
    return {
        "query": QUERY,
        "clientId": client_id,
        "knowledgeBaseIds": [row["id"] for row in bases],
        "knowledgeBases": bases,
        "results": items,
        "grounded": {"query": QUERY, "knowledgeBases": bases, "context": items},
    }


def test_knowledge_disabled_by_default():
    assert knowledge_enabled(environ={}) is False
    assert knowledge_enabled(environ={"CC_XIAOZHI_KNOWLEDGE_ENABLED": "false"}) is False
    assert knowledge_enabled(environ={"CC_XIAOZHI_KNOWLEDGE_ENABLED": "0"}) is False
    assert knowledge_enabled(environ={"CC_XIAOZHI_KNOWLEDGE_ENABLED": "true"}) is True


def test_disabled_skips_retrieval_call():
    calls = []

    def search(mac, query, limit=3):
        calls.append((mac, query, limit))
        raise AssertionError("retrieval must not run when disabled")

    section, meta = build_knowledge_section(
        mac=MAC_A, query=QUERY, search=search, enabled=False
    )
    assert section == ""
    assert meta["grounding_applied"] is False
    assert meta["skipped"] == "disabled"
    assert meta["revel_execute"] is False
    assert calls == []


def test_trivial_utterances_skip_retrieval():
    calls = []

    def search(mac, query, limit=3):
        calls.append(query)
        return _payload()

    for phrase in ("hello", "thank you", "yes", "no", "goodbye", "ok"):
        section, meta = build_knowledge_section(
            mac=MAC_A, query=phrase, search=search, enabled=True
        )
        assert section == ""
        assert meta["skipped"] == "trivial"
        assert meta["grounding_applied"] is False
    assert calls == []
    assert should_retrieve(QUERY, enabled=True) is True


def test_no_client_or_kb_keeps_normal_conversation():
    empty = {
        "query": QUERY,
        "clientId": None,
        "knowledgeBaseIds": [],
        "knowledgeBases": [],
        "results": [],
        "grounded": {"query": QUERY, "knowledgeBases": [], "context": []},
    }
    section, meta = build_knowledge_section(
        mac=MAC_A, query=QUERY, search=lambda *a, **k: empty, enabled=True
    )
    assert section == ""
    assert "AUTHORIZED NEXUS KNOWLEDGE" not in section
    assert meta["grounding_applied"] is False
    assert meta["bases"] == 0
    messages = [{"role": "system", "content": PERSONA}, {"role": "user", "content": QUERY}]
    assert apply_grounding(messages, section) is messages


def test_relevant_kb_adds_grounded_context_and_preserves_persona():
    messages = [
        {"role": "system", "content": PERSONA},
        {"role": "user", "content": QUERY},
    ]
    section, meta = build_knowledge_section(
        mac=MAC_A, query=QUERY, search=lambda *a, **k: _payload(), enabled=True
    )
    out = apply_grounding(messages, section)
    assert messages[0]["content"] == PERSONA
    assert PERSONA in out[0]["content"]
    assert GROUNDING_PREAMBLE.split("\n", 1)[0] in out[0]["content"]
    assert "Project-Public — Slide 2" in out[0]["content"]
    assert "briefs sensor" in out[0]["content"]
    assert "Qdrant" not in out[0]["content"].split("AUTHORIZED NEXUS KNOWLEDGE")[-1]
    assert meta["grounding_applied"] is True
    assert meta["revel_execute"] is False
    assert meta["context"][0]["revelTag"] == "bioev_humidity"
    assert meta["context"][0]["revelAutoTrigger"] is False


def test_retrieval_timeout_and_500_fail_open():
    messages = [{"role": "system", "content": PERSONA}, {"role": "user", "content": QUERY}]

    def boom(mac, query, limit=3):
        raise httpx.TimeoutException("timed out")

    section, meta = build_knowledge_section(
        mac=MAC_A, query=QUERY, search=boom, enabled=True, logger=FakeLog()
    )
    assert section == ""
    assert meta["skipped"] == "search_error"
    assert apply_grounding(messages, section)[0]["content"] == PERSONA

    section_none, meta_none = build_knowledge_section(
        mac=MAC_A, query=QUERY, search=lambda *a, **k: None, enabled=True
    )
    assert section_none == ""
    assert meta_none["grounding_applied"] is False


def test_results_below_threshold_no_prompt_garbage():
    payload = _payload(context=[])
    payload["clientId"] = "agent-a"
    payload["knowledgeBases"] = [{"id": 1, "name": "Bio-EV Sales"}]
    section, meta = build_knowledge_section(
        mac=MAC_A, query=QUERY, search=lambda *a, **k: payload, enabled=True
    )
    assert section == ""
    assert "AUTHORIZED NEXUS KNOWLEDGE" not in section
    assert meta["grounding_applied"] is False
    assert meta["skipped"] == "no_results"


def test_multi_client_isolation():
    seen = []

    def search(mac, query, limit=3):
        seen.append(mac)
        if mac == MAC_A:
            return _payload()
        return {
            "clientId": "agent-b",
            "knowledgeBaseIds": [9],
            "knowledgeBases": [{"id": 9, "name": "Warehouse 13 Corporate"}],
            "results": [],
            "grounded": {
                "query": query,
                "knowledgeBases": [{"id": 9, "name": "Warehouse 13 Corporate"}],
                "context": [],
            },
        }

    a_section, a_meta = build_knowledge_section(
        mac=MAC_A, query=QUERY, search=search, enabled=True
    )
    b_section, b_meta = build_knowledge_section(
        mac=MAC_B, query=QUERY, search=search, enabled=True
    )
    assert "briefs sensor" in a_section
    assert a_meta["client"] == "agent-a"
    assert b_section == ""
    assert b_meta["grounding_applied"] is False
    assert b_meta["client"] == "agent-b"
    assert "Bio-EV" not in b_section
    assert seen == [MAC_A, MAC_B]


def test_context_result_limit():
    items = [
        _hit(chunkId=i, citation=f"Project-Public — Slide {i}", text=f"Fact {i} about the briefs sensor.")
        for i in range(1, 6)
    ]
    section = format_grounding_section(_payload(items), max_results=3, max_chars=6000)
    assert "[1] Project-Public — Slide 1" in section
    assert "[3] Project-Public — Slide 3" in section
    assert "[4]" not in section
    assert "[5]" not in section


def test_context_character_limit_does_not_cut_mid_word():
    long_text = "The briefs sensor " + ("humidity alert details " * 400)
    section = format_grounding_section(
        _payload([_hit(text=long_text)]),
        max_results=3,
        max_chars=1200,
    )
    assert len(section) <= 1200
    assert section[-1].isalnum() or section[-1] in ".!?"
    assert "briefs sensor" in section
    assert clip_chars("one two three four", 10) == "one two"


def test_revel_metadata_carried_not_executed():
    payload = _payload([_hit(revelTag="bioev_humidity", revelAutoTrigger=True)])
    section, meta = build_knowledge_section(
        mac=MAC_A, query=QUERY, search=lambda *a, **k: payload, enabled=True
    )
    assert meta["revel_execute"] is False
    assert meta["context"][0]["revelTag"] == "bioev_humidity"
    assert meta["context"][0]["revelAutoTrigger"] is True
    assert "execute" not in section.lower()
    assert "revel" not in section.lower()


def test_expand_query_only_when_referential():
    assert expand_query(QUERY, "Earlier topic") == QUERY
    expanded = expand_query("How long does it last?", "Tell me about the briefs sensor")
    assert "briefs sensor" in expanded
    assert "How long does it last?" in expanded


def test_search_device_knowledge_fail_open(monkeypatch):
    from config import careconnect_db as db

    monkeypatch.setattr(db, "_token", lambda: "secret")
    monkeypatch.setenv(
        "CC_KNOWLEDGE_SEARCH_URL",
        "http://api.test/api/internal/device/{mac}/knowledge-search",
    )

    def timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", timeout)
    assert db.search_device_knowledge(MAC_A, QUERY) is None

    class Boom:
        status_code = 500

        def json(self):
            return {"code": 500, "msg": "fail", "data": None}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: Boom())
    assert db.search_device_knowledge(MAC_A, QUERY) is None

    captured = {}

    class Ok:
        status_code = 200

        def json(self):
            return {"code": 0, "msg": "ok", "data": _payload()}

    def post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return Ok()

    monkeypatch.setattr(httpx, "post", post)
    data = db.search_device_knowledge(MAC_A, QUERY, limit=3)
    assert data["grounded"]["context"][0]["chunkId"] == 123
    assert MAC_A in captured["url"]
    assert captured["headers"]["X-Internal-Token"] == "secret"
    assert captured["json"]["query"] == QUERY
    assert captured["timeout"] == pytest.approx(2.0)


def test_chat_path_preserves_persona_and_existing_llm_flow(monkeypatch):
    conn_src = (ROOT / "core" / "connection.py").read_text()
    chat_fn = conn_src.split("def chat(self, query, tool_call=False, depth=0):", 1)[1]
    chat_fn = chat_fn.split("\n    def ", 1)[0]
    assert "SentenceType.FIRST" in chat_fn
    assert "_cc_ground_llm_messages" in chat_fn
    assert "ground_turn_messages" in conn_src
    assert chat_fn.index("SentenceType.FIRST") < chat_fn.index("_cc_ground_llm_messages")
    assert "get_llm_dialogue_with_memory" in chat_fn
    assert "change_system_prompt" not in chat_fn
    assert "llm.response" in chat_fn

    revel_src = (ROOT / "core" / "handle" / "receiveAudioHandle.py").read_text()
    start = revel_src.split("async def startToChat(conn, text):", 1)[1]
    start = start.split("\nasync def ", 1)[0]
    assert "post_revel_command" in start
    assert start.index("post_revel_command") < start.index("conn.chat")

    logger = FakeLog()
    messages = [
        {"role": "system", "content": PERSONA},
        {"role": "user", "content": QUERY},
    ]
    monkeypatch.setenv("CC_XIAOZHI_KNOWLEDGE_ENABLED", "true")
    out, meta = ground_turn_messages(
        mac=MAC_A,
        query=QUERY,
        messages=messages,
        search=lambda mac, query, limit=3: _payload(),
        enabled=True,
        logger=logger,
    )
    assert messages[0]["content"] == PERSONA
    assert PERSONA in out[0]["content"]
    assert meta["grounding_applied"] is True
    assert meta["revel_execute"] is False
    assert any("grounding_applied=True" in rec[1] for rec in logger.records)
    assert any("revel_execute=false" in rec[1] for rec in logger.records)

    calls = []

    def no_call(*args, **kwargs):
        calls.append(args)
        raise AssertionError("disabled path must not retrieve")

    disabled, disabled_meta = ground_turn_messages(
        mac=MAC_A,
        query=QUERY,
        messages=messages,
        search=no_call,
        enabled=False,
    )
    assert disabled[0]["content"] == PERSONA
    assert disabled_meta["grounding_applied"] is False
    assert calls == []


def test_compose_declares_knowledge_flag_default_false():
    compose = Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml"
    text = compose.read_text()
    marker = "  xiaozhi-server:\n    image:"
    assert marker in text
    xz = text.split(marker, 1)[1]
    env = xz.split("    volumes:", 1)[0]
    assert 'CC_XIAOZHI_KNOWLEDGE_ENABLED: "${CC_XIAOZHI_KNOWLEDGE_ENABLED:-false}"' in env
    assert "CC_KNOWLEDGE_SEARCH_URL:" in env
    assert "CC_KNOWLEDGE_CONTEXT_MAX_RESULTS:" in env
    assert "CC_KNOWLEDGE_CONTEXT_MAX_CHARS:" in env
    assert "CC_KNOWLEDGE_SEARCH_TIMEOUT:" in env
