"""Phase 1 Nexus-system Revel discovery: allowlisted GraphQL, no writes."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from httpx import AsyncClient

from careconnect_api.envelope import APIException
from careconnect_api.revel_client import AUTH_HEADER
from careconnect_api.revel_signage import (
    DEFAULT_API_KEY_FILE,
    DEFAULT_GRAPHQL_URL,
    DEVICES_QUERY,
    RevelSignageSettings,
    list_signage_devices,
    load_revel_api_key,
    normalize_signage_device,
    resolve_graphql_url,
)
from careconnect_api.settings import settings

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.yml"
GEN_SECRETS = ROOT / "deploy" / "scripts" / "gen-secrets.sh"
ALIGN = ROOT / "deploy" / "scripts" / "align_secret_perms.sh"
LIVE_SKIP = "REVEL_API_KEY_FILE missing or empty; skip live Revel read"

_SECRET = "super-secret-revel-key-value-XXXX"


def _has_live_key() -> bool:
    env_path = Path(
        os.environ.get("REVEL_API_KEY_FILE")
        or os.environ.get("CC_REVEL_API_KEY_FILE")
        or str(DEFAULT_API_KEY_FILE)
    )
    try:
        return env_path.is_file() and bool(load_revel_api_key(env_path))
    except OSError:
        return False


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else ("" if payload is None else "json")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeClient:
    def __init__(self, capture, resp, *args, **kwargs):
        self._capture = capture
        self._resp = resp
        capture["timeout"] = kwargs.get("timeout")
        capture["follow_redirects"] = kwargs.get("follow_redirects")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        self._capture["method"] = "POST"
        self._capture["url"] = url
        self._capture["headers"] = dict(headers or {})
        self._capture["json"] = json
        return self._resp

    async def get(self, url, headers=None):
        raise AssertionError(f"Phase 1 must not GET {url}")


def _patch_httpx(monkeypatch, capture, resp):
    def _factory(*args, **kwargs):
        return FakeClient(capture, resp, *args, **kwargs)

    monkeypatch.setattr("careconnect_api.revel_signage.httpx.AsyncClient", _factory)


def test_devices_query_is_allowlisted_device_read():
    assert "mutation" not in DEVICES_QUERY.casefold()
    assert "sendDeviceCommand" not in DEVICES_QUERY
    assert "dataTable" not in DEVICES_QUERY.casefold()
    assert "device(limit: 100)" in DEVICES_QUERY
    assert "isOnline" in DEVICES_QUERY
    assert DEVICES_QUERY.strip() == DEVICES_QUERY


def test_normalize_signage_device_shape_and_rejects_bad_ids():
    raw = {
        "id": "player-101",
        "name": "Betty Room 101",
        "isOnline": True,
        "tags": ["lobby"],
        "apiKey": "must-not-leak",
        "deviceType": {"name": "BrightSign", "manufacturer": "BrightSign"},
        "pingData": {"playerVersion": "1.2.3", "ipAddress": "10.0.0.9"},
        "location": {"city": "Austin", "state": "TX"},
    }
    norm = normalize_signage_device(raw)
    assert norm == {
        "id": "player-101",
        "name": "Betty Room 101",
        "status": "online",
        "metadata": {
            "tags": ["lobby"],
            "deviceType": {"name": "BrightSign", "manufacturer": "BrightSign"},
            "pingData": {"playerVersion": "1.2.3", "ipAddress": "10.0.0.9"},
            "location": {"city": "Austin", "state": "TX"},
        },
    }
    assert "apiKey" not in norm["metadata"]
    assert normalize_signage_device({"id": "", "name": "x"}) is None
    assert normalize_signage_device({"id": "has space", "name": "x"}) is None
    assert normalize_signage_device({"name": "no-id"}) is None
    assert normalize_signage_device({"id": "d1", "isOnline": False})["status"] == "offline"


def test_revel_env_aliases(monkeypatch, tmp_path):
    key_file = tmp_path / "revel-api-key"
    key_file.write_text("unused-for-this-test")
    monkeypatch.setenv("REVEL_API_BASE", "https://api.reveldigital.com")
    monkeypatch.setenv("REVEL_GRAPHQL_URL", "https://api.reveldigital.com/graphql")
    monkeypatch.setenv("REVEL_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("REVEL_CONTROL_TABLE_ID", "table-1")
    monkeypatch.setenv("REVEL_DEFAULT_DEVICE_ID", "player-101")
    cfg = RevelSignageSettings()
    assert cfg.api_base == "https://api.reveldigital.com"
    assert cfg.graphql_url == DEFAULT_GRAPHQL_URL
    assert cfg.api_key_file == key_file
    assert cfg.control_table_id == "table-1"
    assert cfg.default_device_id == "player-101"
    assert resolve_graphql_url(cfg) == DEFAULT_GRAPHQL_URL


def test_execute_enabled_only_from_revel_env(monkeypatch):
    monkeypatch.delenv("REVEL_EXECUTE_ENABLED", raising=False)
    monkeypatch.delenv("CC_REVEL_EXECUTE_ENABLED", raising=False)
    monkeypatch.setenv("EXECUTE_ENABLED", "true")
    assert RevelSignageSettings().execute_enabled is False
    monkeypatch.setenv("REVEL_EXECUTE_ENABLED", "false")
    assert RevelSignageSettings().execute_enabled is False
    monkeypatch.setenv("REVEL_EXECUTE_ENABLED", "true")
    assert RevelSignageSettings().execute_enabled is True
    monkeypatch.delenv("REVEL_EXECUTE_ENABLED", raising=False)
    monkeypatch.setenv("CC_REVEL_EXECUTE_ENABLED", "true")
    assert RevelSignageSettings().execute_enabled is True


def test_cc_prefixed_aliases(monkeypatch, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("x")
    monkeypatch.delenv("REVEL_API_KEY_FILE", raising=False)
    monkeypatch.setenv("CC_REVEL_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("CC_REVEL_GRAPHQL_URL", "https://api.reveldigital.com/graphql")
    cfg = RevelSignageSettings()
    assert cfg.api_key_file == key_file


def test_load_revel_api_key_does_not_create_file(tmp_path):
    missing = tmp_path / "revel-api-key"
    assert not missing.exists()
    assert load_revel_api_key(missing) == ""
    assert not missing.exists()


def test_load_revel_api_key_strips_and_never_returns_path_as_key(tmp_path):
    path = tmp_path / "revel-api-key"
    path.write_text(f"  {_SECRET} \n")
    assert load_revel_api_key(path) == _SECRET


@pytest.mark.asyncio
async def test_list_signage_devices_posts_allowlisted_graphql(monkeypatch, tmp_path, caplog):
    key_file = tmp_path / "revel-api-key"
    key_file.write_text(_SECRET)
    capture: dict = {}
    payload = {
        "data": {
            "device": [
                {
                    "id": "dev-1",
                    "name": "Lobby",
                    "isOnline": False,
                    "tags": ["home"],
                    "deviceType": {"name": "Android", "manufacturer": "Revel"},
                    "pingData": {"timestamp": "2026-09-26T20:00:00Z"},
                    "location": {"city": "Austin"},
                }
            ]
        }
    }
    _patch_httpx(monkeypatch, capture, FakeResp(200, payload))
    caplog.set_level(logging.INFO)
    devices = await list_signage_devices(key_file=key_file)
    assert capture["method"] == "POST"
    assert capture["url"] == DEFAULT_GRAPHQL_URL
    assert capture["follow_redirects"] is False
    assert capture["headers"][AUTH_HEADER] == _SECRET
    assert "Authorization" not in capture["headers"]
    assert "api_key=" not in capture["url"]
    assert capture["json"] == {"query": DEVICES_QUERY}
    assert "mutation" not in capture["json"]["query"].casefold()
    assert devices == [
        {
            "id": "dev-1",
            "name": "Lobby",
            "status": "offline",
            "metadata": {
                "tags": ["home"],
                "deviceType": {"name": "Android", "manufacturer": "Revel"},
                "pingData": {"timestamp": "2026-09-26T20:00:00Z"},
                "location": {"city": "Austin"},
            },
        }
    ]
    blob = caplog.text
    assert _SECRET not in blob
    assert "query=device_list" in blob
    assert "devices=1" in blob


@pytest.mark.asyncio
async def test_list_signage_devices_missing_key_is_clear_error(tmp_path):
    missing = tmp_path / "no-such-key"
    with pytest.raises(APIException) as exc:
        await list_signage_devices(key_file=missing)
    assert exc.value.code == 503
    assert "not configured" in exc.value.msg
    assert exc.value.data["ok"] is False
    assert exc.value.data["devices"] == []
    assert _SECRET not in exc.value.msg


@pytest.mark.asyncio
async def test_list_signage_devices_401_does_not_log_key(monkeypatch, tmp_path, caplog):
    key_file = tmp_path / "revel-api-key"
    key_file.write_text(_SECRET)
    capture: dict = {}
    _patch_httpx(monkeypatch, capture, FakeResp(401, None, text="Unauthorized api_key=leak"))
    caplog.set_level(logging.WARNING)
    with pytest.raises(APIException) as exc:
        await list_signage_devices(key_file=key_file)
    assert exc.value.code == 401
    assert capture["headers"][AUTH_HEADER] == _SECRET
    blob = caplog.text
    assert _SECRET not in blob
    assert "api_key=leak" not in blob
    assert "header_name=X-RevelDigital-ApiKey" in blob
    assert "key_present=True" in blob


@pytest.mark.asyncio
async def test_graphql_errors_are_safe(monkeypatch, tmp_path, caplog):
    key_file = tmp_path / "revel-api-key"
    key_file.write_text(_SECRET)
    capture: dict = {}
    _patch_httpx(
        monkeypatch,
        capture,
        FakeResp(200, {"errors": [{"message": f"bad key {_SECRET}"}]}),
    )
    caplog.set_level(logging.WARNING)
    with pytest.raises(APIException) as exc:
        await list_signage_devices(key_file=key_file)
    assert exc.value.code == 502
    assert _SECRET not in (exc.value.msg or "")
    assert _SECRET not in caplog.text


@pytest.mark.asyncio
async def test_internal_revel_devices_requires_token(client: AsyncClient):
    resp = await client.get("/api/internal/revel/devices")
    assert resp.json()["code"] == 401
    assert resp.json()["msg"]


@pytest.mark.asyncio
async def test_internal_revel_devices_ok(client: AsyncClient, monkeypatch, tmp_path):
    key_file = tmp_path / "revel-api-key"
    key_file.write_text(_SECRET)
    capture: dict = {}
    payload = {
        "data": {
            "device": [
                {"id": "abc", "name": "Room 101", "isOnline": True, "tags": []},
            ]
        }
    }
    _patch_httpx(monkeypatch, capture, FakeResp(200, payload))
    monkeypatch.setenv("REVEL_API_KEY_FILE", str(key_file))
    resp = await client.get(
        "/api/internal/revel/devices",
        headers={"X-Internal-Token": settings.internal_token},
        params={"query": "mutation { sendDeviceCommand(deviceId: \"x\") { success } }"},
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["ok"] is True
    assert body["data"]["devices"][0]["id"] == "abc"
    assert body["data"]["devices"][0]["status"] == "online"
    assert "metadata" in body["data"]["devices"][0]
    assert _SECRET not in str(body)
    assert capture["json"]["query"] == DEVICES_QUERY
    assert "mutation" not in capture["json"]["query"].casefold()


@pytest.mark.asyncio
async def test_internal_revel_devices_missing_key(client: AsyncClient, monkeypatch, tmp_path):
    monkeypatch.setenv("REVEL_API_KEY_FILE", str(tmp_path / "absent-revel-api-key"))
    resp = await client.get(
        "/api/internal/revel/devices",
        headers={"X-Internal-Token": settings.internal_token},
    )
    body = resp.json()
    assert body["code"] == 503
    assert body["data"]["ok"] is False
    assert body["data"]["devices"] == []
    assert "not configured" in body["msg"]
    assert _SECRET not in str(body)


@pytest.mark.asyncio
async def test_internal_revel_devices_does_not_accept_post_graphql(client: AsyncClient):
    resp = await client.post(
        "/api/internal/revel/devices",
        headers={"X-Internal-Token": settings.internal_token},
        json={"query": "mutation { sendDeviceCommand(deviceId: \"x\") { success } }"},
    )
    # Method not allowed — callers cannot POST arbitrary GraphQL to this path.
    if "application/json" in (resp.headers.get("content-type") or ""):
        code = resp.json().get("code", resp.status_code)
        assert code in (404, 405, 400)
    else:
        assert resp.status_code in (404, 405)


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_live_key(), reason=LIVE_SKIP)
async def test_live_readonly_list_signage_devices():
    devices = await list_signage_devices()
    assert isinstance(devices, list)
    for row in devices:
        assert row["id"]
        assert "name" in row
        assert row["status"] in {"online", "offline", "unknown"}
        assert isinstance(row.get("metadata"), dict)
        blob = str(row).casefold()
        assert "apikey" not in blob
        assert "api_key" not in blob


def test_compose_declares_nexus_revel_env_on_api_only():
    text = COMPOSE.read_text()
    marker = "  api:\n    image:"
    assert marker in text
    api = text.split(marker, 1)[1].split("  web:\n", 1)[0]
    xz_marker = "  xiaozhi-server:\n    image:"
    xz = text.split(xz_marker, 1)[1].split("    volumes:", 1)[0]
    assert 'REVEL_API_BASE: "${REVEL_API_BASE:-https://api.reveldigital.com}"' in api
    assert (
        'REVEL_GRAPHQL_URL: "${REVEL_GRAPHQL_URL:-https://api.reveldigital.com/graphql}"'
        in api
    )
    assert 'REVEL_API_KEY_FILE: "${REVEL_API_KEY_FILE:-/run/secrets/revel-api-key}"' in api
    assert 'REVEL_CONTROL_TABLE_ID: "${REVEL_CONTROL_TABLE_ID:-}"' in api
    assert 'REVEL_DEFAULT_DEVICE_ID: "${REVEL_DEFAULT_DEVICE_ID:-}"' in api
    assert 'REVEL_EXECUTE_ENABLED: "${REVEL_EXECUTE_ENABLED:-false}"' in api
    assert "REVEL_EXECUTE_ENABLED" not in xz
    assert "REVEL_API_KEY_FILE" not in xz
    assert "cc-secrets:/run/secrets:ro" in api


def test_gen_secrets_does_not_invent_revel_developer_key():
    text = GEN_SECRETS.read_text()
    loop = text.split("for name in", 1)[1].split("do", 1)[0]
    assert "revel-api-key" not in loop
    assert "urandom" in text
    align = ALIGN.read_text()
    assert "revel-api-key" in align
    assert "Never auto-create" in align or "never auto-create" in align.casefold() or "not generated" in align
