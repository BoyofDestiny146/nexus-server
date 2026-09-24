"""System Status probes: conversational LLM, Watcher counts, E2E criticality."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from careconnect_api.models import AiDevice
from careconnect_api.routers import health as health_mod
from careconnect_api.routers.health import (
    FAIL,
    INFO,
    OK,
    WARN,
    _e2e_and_overall,
    _hop,
    _probe_device,
    _probe_llm,
    _probe_router,
    human_age,
    llm_hop_label,
    model_is_listed,
)
from careconnect_api.watcher_device import device_id_for_mac


class FakeResp:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, routes: dict):
        self.routes = routes

    async def get(self, url, **_k):
        for key, resp in self.routes.items():
            if key.startswith("GET ") and key[4:] in url:
                return resp
        return FakeResp(404)

    async def post(self, url, json=None, timeout=None, **_k):
        for key, resp in self.routes.items():
            if key.startswith("POST ") and key[5:] in url:
                return resp
        return FakeResp(404)


def test_human_age_matches_screenshot_seconds():
    assert human_age(24474) == "6h 47m ago"
    assert human_age(12) == "12s ago"
    assert human_age(90) == "1m ago"
    assert human_age(None) == "never"


def test_model_is_listed_does_not_confuse_qwen_sizes():
    names = ["qwen2.5:7b", "llama3.1:8b-instruct-q4_K_M", "cc-llm:latest"]
    assert model_is_listed(names, "qwen2.5:3b") is False
    assert model_is_listed(["qwen2.5:3b"], "qwen2.5:3b") is True
    assert model_is_listed(["qwen2.5:3b-q4_K_M"], "qwen2.5:3b") is True


def test_llm_label_does_not_call_qwen_ollama():
    assert llm_hop_label("qwen2.5:3b") == "LLM — Qwen 2.5 3B"
    assert "Ollama" not in llm_hop_label("qwen2.5:3b")


def test_e2e_ignores_router_info_and_offline_watcher():
    hops = [
        _hop("device", "Device / Watcher", WARN, "Offline — 0 online / 1 registered", 1),
        _hop("router", "Router / Wi-Fi", INFO, "not measurable", None),
        _hop("mqtt", "MQTT Gateway", OK, "broker :1883 reachable", 1),
        _hop("ws", "XiaoZhi WS Server", OK, "handshake ok", 1),
        _hop("llm", "LLM — Qwen 2.5 3B", OK, "Qwen 2.5 3B reachable via Ollama", 1),
        _hop("tts", "TTS", OK, "healthy", 1),
        _hop("db", "Database (MariaDB)", OK, "SELECT 1 ok", 1),
        _hop("redis", "Redis", OK, "PING ok", 1),
    ]
    e2e, overall = _e2e_and_overall(hops)
    assert e2e["status"] == OK
    assert overall == WARN  # offline Watcher is warn, not a pipeline failure


def test_e2e_fails_only_on_critical_hops():
    hops = [
        _hop("mqtt", "MQTT Gateway", FAIL, "broker down", 1),
        _hop("ota", "OTA Endpoint", FAIL, "internal down", 1),
        _hop("ws", "XiaoZhi WS Server", OK, "ok", 1),
        _hop("llm", "LLM — Qwen 2.5 3B", OK, "ok", 1),
        _hop("tts", "TTS", OK, "ok", 1),
        _hop("db", "Database (MariaDB)", OK, "ok", 1),
        _hop("router", "Router / Wi-Fi", INFO, "info", None),
    ]
    e2e, overall = _e2e_and_overall(hops)
    assert e2e["status"] == OK
    assert "mqtt" not in e2e["detail"]
    assert overall == WARN


def test_e2e_fails_when_llm_fails():
    hops = [
        _hop("ws", "XiaoZhi WS Server", OK, "ok", 1),
        _hop("llm", "LLM — Qwen 2.5 3B", FAIL, "LLM runtime unreachable", 1),
        _hop("tts", "TTS", OK, "ok", 1),
        _hop("db", "Database (MariaDB)", OK, "ok", 1),
    ]
    e2e, _overall = _e2e_and_overall(hops)
    assert e2e["status"] == FAIL
    assert "llm" in e2e["detail"]


@pytest.mark.asyncio
async def test_llm_probe_uses_configured_qwen_not_triage(monkeypatch):
    monkeypatch.setattr(health_mod.settings, "ollama_url", "http://host-gateway:11434")
    monkeypatch.setattr(health_mod.settings, "llm_model", "qwen2.5:3b")
    monkeypatch.setattr(health_mod.settings, "ollama_triage_model", "cc-llm")

    posted: list[dict] = []

    class Client(FakeClient):
        async def post(self, url, json=None, timeout=None, **_k):
            posted.append(json or {})
            return FakeResp(200, {"response": "OK"})

    client = Client(
        {
            "GET /api/version": FakeResp(200, {"version": "0.5.0"}),
            "GET /api/tags": FakeResp(
                200,
                {"models": [{"name": "qwen2.5:3b"}, {"name": "nomic-embed-text"}]},
            ),
        }
    )
    hop = await _probe_llm(client)
    assert hop["status"] == OK
    assert hop["label"] == "LLM — Qwen 2.5 3B"
    assert hop["detail"] == "Qwen 2.5 3B reachable via Ollama"
    assert posted and posted[0]["model"] == "qwen2.5:3b"
    assert posted[0]["stream"] is False
    assert "cc-llm" not in hop["detail"]
    assert "Ollama" in hop["detail"]
    assert hop["label"] != "LLM (Ollama)"


@pytest.mark.asyncio
async def test_llm_missing_model_is_fail(monkeypatch):
    monkeypatch.setattr(health_mod.settings, "ollama_url", "http://host-gateway:11434")
    monkeypatch.setattr(health_mod.settings, "llm_model", "qwen2.5:3b")
    client = FakeClient(
        {
            "GET /api/version": FakeResp(200, {"version": "0.5.0"}),
            "GET /api/tags": FakeResp(200, {"models": [{"name": "cc-llm:latest"}]}),
        }
    )
    hop = await _probe_llm(client)
    assert hop["status"] == FAIL
    assert hop["detail"] == "Ollama reachable; qwen2.5:3b unavailable"


@pytest.mark.asyncio
async def test_llm_unreachable_is_fail(monkeypatch):
    monkeypatch.setattr(health_mod.settings, "ollama_url", "http://host-gateway:11434")
    monkeypatch.setattr(health_mod.settings, "llm_model", "qwen2.5:3b")

    class Boom(FakeClient):
        async def get(self, url, **_k):
            raise OSError("connection refused")

    hop = await _probe_llm(Boom({}))
    assert hop["status"] == FAIL
    assert hop["detail"] == "LLM runtime unreachable"


@pytest.mark.asyncio
async def test_device_counts_watchers_and_humanizes_offline(db_engine, monkeypatch):
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(health_mod, "async_session_factory", factory)
    stale = datetime.utcnow() - timedelta(seconds=24474)
    async with factory() as db:
        db.add(
            AiDevice(
                id=device_id_for_mac("E0:72:FA:41:84"),
                mac_address="E0:72:FA:41:84",
                last_connected_at=stale,
                last_seen=stale,
                rssi=-61,
            )
        )
        db.add(
            AiDevice(
                id=device_id_for_mac("AA:BB:CC:DD:EE:00"),
                mac_address="AA:BB:CC:DD:EE:00",
                last_connected_at=stale,
            )
        )
        await db.commit()

    hop = await _probe_device()
    assert hop["status"] == WARN, hop
    assert "0 online / 2 registered" in hop["detail"]
    assert "6h 47m ago" in hop["detail"]
    assert "24474s" not in hop["detail"]

    wifi = await _probe_router()
    assert wifi["status"] == INFO
    assert "Wi-Fi signal: -61 dBm" in wifi["detail"]
