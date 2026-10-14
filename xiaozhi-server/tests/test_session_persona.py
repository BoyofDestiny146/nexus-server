"""Device personality session lookup: API compose first, SQL inherit fallback."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import careconnect_db as db  # noqa: E402

MAC = "E0:72:A1:DB:36:41"


def test_fetch_session_persona_uses_device_prompt(monkeypatch):
    monkeypatch.setattr(db, "_token", lambda: "secret")
    monkeypatch.setenv(
        "CC_KNOWLEDGE_SEARCH_URL",
        "http://api.test/api/internal/device/{mac}/knowledge-search",
    )
    captured = {}

    class Ok:
        status_code = 200

        def json(self):
            return {
                "code": 0,
                "msg": "ok",
                "data": {
                    "agent_id": "agt_betty",
                    "agent_name": "Betty",
                    "system_prompt": "# ENGLISH-ONLY\nEmpathetic Guardian",
                    "first_name": "Betty",
                    "bot_name": "Nexus",
                    "personalityId": "sys_care_empathetic_guardian",
                    "personalitySource": "device",
                },
            }

    def get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return Ok()

    monkeypatch.setattr(httpx, "get", get)
    persona = db.lookup_agent_persona(MAC)
    assert persona is not None
    assert persona["personalitySource"] == "device"
    assert "Empathetic Guardian" in persona["system_prompt"]
    assert captured["url"].endswith(f"/session-persona")
    assert "/knowledge-search" not in captured["url"]
    assert captured["headers"]["X-Internal-Token"] == "secret"


def test_fetch_session_persona_fail_open_uses_sql(monkeypatch):
    monkeypatch.setattr(db, "_token", lambda: "secret")

    def timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "get", timeout)

    called = {"sql": False}

    def fake_sql(mac_address: str):
        called["sql"] = True
        return {
            "agent_id": "agt_bob",
            "agent_name": "Bob",
            "system_prompt": "client snapshot",
            "first_name": "Bob",
            "bot_name": "Nexus",
        }

    # lookup_agent_persona falls through to SQL after fetch returns None.
    monkeypatch.setattr(db, "_mac_lookup_candidates", lambda mac: [mac])

    class Conn:
        def cursor(self):
            return self

        def execute(self, sql, params=None):
            called["sql"] = True
            called["query"] = sql
            return self

        def fetchone(self):
            return ("agt_bob", "Bob", "client snapshot", "Nexus")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def close(self):
            return None

    monkeypatch.setattr(db, "_connect", lambda: Conn())
    persona = db.lookup_agent_persona(MAC)
    assert persona["system_prompt"] == "client snapshot"
    assert persona["agent_id"] == "agt_bob"
    assert called["sql"] is True


def test_explicit_session_persona_url_wins(monkeypatch):
    monkeypatch.setenv(
        "CC_SESSION_PERSONA_URL",
        "http://api:8080/api/internal/device/{mac}/session-persona",
    )
    monkeypatch.setenv("CC_KNOWLEDGE_SEARCH_URL", "http://knowledge:8090/custom-search")
    monkeypatch.setenv("CC_NOTIFY_URL", "http://ignored:9/api/internal/notify/chat-turn")
    url = db._session_persona_url("aa:bb:cc:dd:ee:ff")
    assert url == "http://api:8080/api/internal/device/aa:bb:cc:dd:ee:ff/session-persona"


def test_session_persona_url_uses_notify_origin_when_knowledge_path_unexpected(monkeypatch):
    monkeypatch.delenv("CC_SESSION_PERSONA_URL", raising=False)
    monkeypatch.setenv("CC_KNOWLEDGE_SEARCH_URL", "http://knowledge:8090/custom-search")
    monkeypatch.setenv("CC_NOTIFY_URL", "http://api:8080/api/internal/notify/chat-turn")
    url = db._session_persona_url("aa:bb:cc:dd:ee:ff")
    assert url == "http://api:8080/api/internal/device/aa:bb:cc:dd:ee:ff/session-persona"
    assert "knowledge" not in url
    assert "8090" not in url
    assert "custom-search" not in url


def test_fetch_session_persona_skips_http_without_token(monkeypatch):
    monkeypatch.setattr(db, "_token", lambda: None)
    called = {"http": False}

    def boom(*_a, **_k):
        called["http"] = True
        raise AssertionError("must not call API without a token")

    monkeypatch.setattr(httpx, "get", boom)
    assert db.fetch_session_persona(MAC) is None
    assert called["http"] is False


def test_fetch_timeout_and_header_do_not_log_token(monkeypatch, caplog):
    monkeypatch.setattr(db, "_token", lambda: "super-secret-internal-token")
    monkeypatch.setenv(
        "CC_SESSION_PERSONA_URL",
        "http://api:8080/api/internal/device/{mac}/session-persona",
    )
    captured = {}

    def timeout(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "get", timeout)
    caplog.set_level("DEBUG")
    assert db.fetch_session_persona(MAC) is None
    assert captured["headers"]["X-Internal-Token"] == "super-secret-internal-token"
    assert captured["timeout"] == 2.5
    assert captured["url"].endswith("/session-persona")
    assert "super-secret-internal-token" not in caplog.text


def test_connection_reuses_lookup_and_keeps_identity_block():
    src = (ROOT / "core" / "connection.py").read_text()
    init = src.split("def _initialize_components")[1].split("def _initialize_asr")[0]
    assert "lookup_agent_persona" in init
    assert "WHO YOU ARE — HARD RULE" in init
    assert "persona.get(\"agent_id\") or \"unbound\"" in init
    assert "voice_config" in src
    assert "forcibly" not in init.lower()
    assert "disconnect" not in init.lower() or "never block startup" in init
