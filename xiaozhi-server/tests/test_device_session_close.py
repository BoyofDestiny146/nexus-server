"""Live Watcher session close + per-device Chroma forget.

Proves the internal XiaoZhi path:
  * matches active_connections by colon / stripped / canonical device id
  * closes the WebSocket only (no send)
  * never emits factory / Wi-Fi / OTA / NVS / MCP commands
  * chroma collections keyed to that MAC are deleted
  * a failing close is logged and does not raise
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.device_session import (  # noqa: E402
    close_live_sessions,
    handler_matches,
    identity_keys,
)
from core.device_session import (  # noqa: E402
    chroma_collection_names,
    forget_device_collections,
)

COLON = "E0:72:A1:DB:36:40"
STRIPPED = "E072A1DB3640"
CANONICAL = "watcher-e072a1db3640"

FORBIDDEN_SOURCE_TOKENS = (
    "call_mcp_tool",
    "factory_reset",
    "factory-reset",
    "wifi_reset",
    "wifi reset",
    "nvs_erase",
    "erase_nvs",
    "reboot",
)


class FakeWS:
    def __init__(self):
        self.closed = False
        self.sent: list = []
        self.close_should_fail = False

    async def close(self):
        if self.close_should_fail:
            raise RuntimeError("socket close failed")
        self.closed = True

    async def send(self, msg):
        self.sent.append(msg)


class FakeHandler:
    def __init__(self, device_id: str, *, fail_close: bool = False):
        self.device_id = device_id
        self.websocket = FakeWS()
        self.websocket.close_should_fail = fail_close
        self.mcp_calls: list = []

    async def call_mcp_tool(self, *a, **k):
        self.mcp_calls.append((a, k))


class FakeServer:
    def __init__(self, handlers):
        self.active_connections = set(handlers)


def test_identity_keys_match_colon_stripped_and_canonical():
    a = identity_keys(COLON)
    b = identity_keys(STRIPPED)
    c = identity_keys(CANONICAL)
    assert a & b
    assert a & c
    assert b & c


def test_handler_matches_normalized_mac_and_device_id():
    h = FakeHandler("e0:72:a1:db:36:40")
    assert handler_matches(h, COLON)
    assert handler_matches(h, STRIPPED)
    assert handler_matches(h, CANONICAL)
    assert not handler_matches(h, "AA:BB:CC:DD:EE:FF")
    assert not handler_matches(FakeHandler(None), COLON)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_close_live_sessions_closes_matching_socket_only():
    keep = FakeHandler("aa:bb:cc:dd:ee:00")
    target = FakeHandler("e0:72:a1:db:36:40")
    server = FakeServer([keep, target])

    n = await close_live_sessions(server, mac=STRIPPED, device_id=CANONICAL)
    assert n == 1
    assert target.websocket.closed is True
    assert keep.websocket.closed is False
    assert target.websocket.sent == []
    assert keep.websocket.sent == []
    assert target.mcp_calls == []


@pytest.mark.asyncio
async def test_close_live_sessions_offline_server_is_zero():
    assert await close_live_sessions(None, mac=COLON) == 0
    assert await close_live_sessions(FakeServer([]), mac=COLON) == 0


@pytest.mark.asyncio
async def test_close_failure_is_best_effort():
    bad = FakeHandler("e0:72:a1:db:36:40", fail_close=True)
    good = FakeHandler("e0:72:a1:db:36:40")
    # two connections same MAC — first fails, second still closes
    n = await close_live_sessions(FakeServer([bad, good]), mac=COLON)
    assert n == 1
    assert good.websocket.closed is True
    assert bad.websocket.closed is False
    assert good.websocket.sent == []
    assert bad.mcp_calls == []
    assert good.mcp_calls == []


def test_chroma_collection_names_cover_mac_forms():
    names = chroma_collection_names(COLON, CANONICAL)
    assert "agent_e0_72_a1_db_36_40" in names
    assert "agent_E0_72_A1_DB_36_40" in names or "agent_e0_72_a1_db_36_40" in names
    assert "agent_e072a1db3640" in names
    assert "agent_watcher-e072a1db3640" in names


def test_forget_device_collections_deletes_mac_keyed_chroma():
    class Coll:
        def __init__(self, name):
            self.name = name

    class FakeChroma:
        def __init__(self):
            self.names = [
                "agent_e0_72_a1_db_36_40",
                "agent_e072a1db3640",
                "agent_watcher-e072a1db3640",
                "agent_other_device",
            ]
            self.deleted: list[str] = []

        def list_collections(self):
            return [Coll(n) for n in self.names]

        def delete_collection(self, name):
            self.deleted.append(name)
            self.names.remove(name)

    chroma = FakeChroma()
    removed = forget_device_collections(
        mac=COLON, device_id=CANONICAL, chroma_client=chroma
    )
    assert "agent_e0_72_a1_db_36_40" in removed
    assert "agent_e072a1db3640" in removed
    assert "agent_watcher-e072a1db3640" in removed
    assert "agent_other_device" not in removed
    assert "agent_other_device" in chroma.names


def test_session_modules_never_emit_reset_or_mcp():
    files = [
        ROOT / "core/device_session.py",
        ROOT / "core/api/session_close_handler.py",
    ]
    for path in files:
        src = path.read_text().lower()
        for token in FORBIDDEN_SOURCE_TOKENS:
            assert token not in src, f"{token!r} found in {path.name}"
        tree = ast.parse(path.read_text())
        calls = [
            n.func.attr.lower()
            if isinstance(n.func, ast.Attribute)
            else (n.func.id.lower() if isinstance(n.func, ast.Name) else "")
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
        ]
        assert "send" not in calls
        assert "call_mcp_tool" not in calls


def test_close_handler_websocket_source_only_closes():
    from core import device_session as ds

    src = inspect.getsource(ds.close_handler_websocket)
    assert "ws.close" in src or "websocket.close" in src.lower() or "await ws.close()" in src
    assert "send(" not in src
    assert "mcp" not in src.lower()
