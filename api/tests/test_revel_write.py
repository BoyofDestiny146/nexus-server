"""Phase 2C Revel write path: column binding, row targeting, REST PUT, default off."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.envelope import APIException
from careconnect_api.models import SysUser
from careconnect_api.revel_client import AUTH_HEADER, RevelMutationDisabled
from careconnect_api.revel_signage import RevelSignageSettings
from careconnect_api.revel_write import (
    REQUIRED_FIELDS,
    bind_column_keys,
    build_update_payload,
    revel_execute_enabled,
    revel_puts_attempted,
    select_control_row,
    update_data_table_row,
    write_plan,
)
from careconnect_api.settings import settings


_SECRET = "revel-live-key-XXXX7F2A"
_BANNED = ("apiKey", "api_key", "secret", "X-RevelDigital-ApiKey", "X-Internal-Token", _SECRET)

COLUMNS = [
    {"key": "device_key", "name": "Device Key", "type": "TEXT"},
    {"key": "screen", "name": "Screen", "type": "TEXT"},
    {"key": "title", "name": "Title", "type": "TEXT"},
    {"key": "message", "name": "Message", "type": "TEXT"},
    {"key": "image_url", "name": "Image", "type": "URL"},
    {"key": "priority", "name": "Priority", "type": "NUMBER"},
    {"key": "expires_at", "name": "Expires", "type": "DATE"},
    {"key": "updated_at", "name": "Updated", "type": "DATE"},
]


@pytest.fixture(autouse=True)
def _reset_puts():
    import careconnect_api.revel_write as rw

    rw.revel_puts_attempted = 0
    yield
    rw.revel_puts_attempted = 0


@pytest_asyncio.fixture
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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _internal() -> dict[str, str]:
    return {"X-Internal-Token": settings.internal_token}


def _state(**over) -> dict:
    base = {
        "deviceKey": "betty-room-101",
        "revelDeviceId": "immutable-revel-id",
        "intent": "SHOW_APPOINTMENT_REMINDER",
        "screen": "appointment",
        "title": "Upcoming Appointment",
        "message": "Your appointment begins at 2:30 PM.",
        "imageUrl": "https://cdn.example.com/appt.png",
        "priority": 50,
        "expiresAt": "2026-09-26T20:30:00Z",
        "createdAt": "2026-09-26T18:00:00Z",
        "source": "calendar",
    }
    base.update(over)
    return base


def test_revel_execute_enabled_defaults_false():
    assert revel_execute_enabled() is False
    assert RevelSignageSettings().revel_execute_enabled is False


def test_bind_uses_live_keys_only():
    bound = bind_column_keys(COLUMNS)
    assert bound["missingRequired"] == []
    assert bound["mapping"]["device_key"] == "device_key"
    assert bound["mapping"]["screen"] == "screen"
    weird = bind_column_keys(
        [
            {"key": "deviceKey", "type": "TEXT"},
            {"key": "Screen", "type": "TEXT"},
            {"key": "Title", "type": "TEXT"},
            {"key": "Message", "type": "TEXT"},
        ]
    )
    assert weird["mapping"]["device_key"] == "deviceKey"
    assert weird["mapping"]["screen"] == "Screen"
    missing = bind_column_keys([{"key": "foo", "type": "TEXT"}])
    assert set(missing["missingRequired"]) == set(REQUIRED_FIELDS)


def test_bind_ignores_display_labels():
    labeled = bind_column_keys(
        [
            {"key": "col_a", "name": "Device Key", "type": "TEXT"},
            {"key": "col_b", "name": "Screen", "type": "TEXT"},
            {"key": "col_c", "name": "Title", "type": "TEXT"},
            {"key": "col_d", "name": "Message", "type": "TEXT"},
        ]
    )
    assert set(labeled["missingRequired"]) == set(REQUIRED_FIELDS)
    assert labeled["mapping"] == {}


def test_payload_allowlist_and_no_passthrough():
    bound = bind_column_keys(COLUMNS)
    payload = build_update_payload(
        display_state=_state(intent="SHOW_HOME", revelDeviceId="should-not-appear"),
        binding=bound,
        written_at="2026-09-26T18:00:00Z",
    )
    assert set(payload.keys()) == {"data"}
    data = payload["data"]
    assert data["device_key"] == "betty-room-101"
    assert data["screen"] == "appointment"
    assert data["title"] == "Upcoming Appointment"
    assert data["message"] == "Your appointment begins at 2:30 PM."
    assert data["image_url"] == "https://cdn.example.com/appt.png"
    assert data["priority"] == 50
    assert data["expires_at"] == "2026-09-26T20:30:00Z"
    assert data["updated_at"] == "2026-09-26T18:00:00Z"
    assert "revelDeviceId" not in data
    assert "intent" not in data
    assert "source" not in data
    assert "command" not in data
    assert "apiKey" not in data
    media = bind_column_keys(
        [
            {"key": "device_key", "type": "TEXT"},
            {"key": "screen", "type": "TEXT"},
            {"key": "title", "type": "TEXT"},
            {"key": "message", "type": "TEXT"},
            {"key": "image_url", "type": "MEDIA"},
        ]
    )
    media_payload = build_update_payload(display_state=_state(), binding=media)
    assert "image_url" not in media_payload["data"]
    plan = write_plan(table_id="tbl-control", row_id="row-betty", payload=payload)
    assert plan["method"] == "PUT"
    assert plan["path"] == "/datatables/tbl-control/rows/row-betty"


def test_zero_and_duplicate_control_rows():
    none = select_control_row(
        [{"id": "r1", "data": {"device_key": "other"}}],
        device_key="betty-room-101",
        device_key_column="device_key",
    )
    assert none["reason"] == "control_row_not_found"
    dup = select_control_row(
        [
            {"id": "r1", "data": {"device_key": "betty-room-101"}},
            {"id": "r2", "data": {"device_key": "betty-room-101"}},
        ],
        device_key="betty-room-101",
        device_key_column="device_key",
    )
    assert dup["reason"] == "ambiguous_control_row"
    one = select_control_row(
        [{"id": "row-betty", "data": {"device_key": "betty-room-101"}}],
        device_key="betty-room-101",
        device_key_column="device_key",
    )
    assert one["ok"] is True
    assert one["row"]["id"] == "row-betty"


@pytest.mark.asyncio
async def test_update_refuses_when_execute_disabled(monkeypatch):
    monkeypatch.setattr("careconnect_api.revel_write.revel_execute_enabled", lambda: False)
    with pytest.raises(RevelMutationDisabled):
        await update_data_table_row(
            table_id="tbl-control",
            row_id="row-betty",
            payload={"data": {"screen": "home"}},
        )
    import careconnect_api.revel_write as rw

    assert rw.revel_puts_attempted == 0


class _PutClient:
    def __init__(self, capture, resp=None, error=None):
        self.capture = capture
        self.resp = resp
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def put(self, url, headers=None, json=None):
        self.capture.append({"url": url, "headers": dict(headers or {}), "json": json})
        if self.error:
            raise self.error
        return self.resp

    async def post(self, *args, **kwargs):
        raise AssertionError("write must not POST GraphQL")

    async def get(self, *args, **kwargs):
        raise AssertionError("write must not GET")


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or "json"

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.mark.asyncio
async def test_successful_put_is_sent_once(monkeypatch):
    monkeypatch.setattr("careconnect_api.revel_write.revel_execute_enabled", lambda: True)
    monkeypatch.setattr("careconnect_api.revel_write.load_revel_api_key", lambda: "k" * 32)
    capture = []

    def factory(*args, **kwargs):
        return _PutClient(
            capture, _Resp(payload={"id": "row-betty", "updatedAt": "2026-09-26T00:00:00Z"})
        )

    monkeypatch.setattr("careconnect_api.revel_write.httpx.AsyncClient", factory)
    payload = {"data": {"device_key": "betty-room-101", "screen": "appointment"}}
    out = await update_data_table_row(
        table_id="tbl-control", row_id="row-betty", payload=payload
    )
    assert out["id"] == "row-betty"
    assert len(capture) == 1
    call = capture[0]
    assert call["url"].endswith("/datatables/tbl-control/rows/row-betty")
    assert call["json"] == payload
    assert AUTH_HEADER in call["headers"]
    assert "sendDeviceCommand" not in call["url"]
    assert "refresh" not in call["url"]
    import careconnect_api.revel_write as rw

    assert rw.revel_puts_attempted == 1
    blob = json.dumps(call["json"])
    for token in _BANNED:
        assert token not in blob


@pytest.mark.asyncio
async def test_timeout_and_non_2xx_and_malformed(monkeypatch):
    monkeypatch.setattr("careconnect_api.revel_write.revel_execute_enabled", lambda: True)
    monkeypatch.setattr("careconnect_api.revel_write.load_revel_api_key", lambda: "k" * 32)

    def timeout_factory(*a, **k):
        return _PutClient([], error=httpx.TimeoutException("timed out"))

    monkeypatch.setattr("careconnect_api.revel_write.httpx.AsyncClient", timeout_factory)
    with pytest.raises(APIException) as exc:
        await update_data_table_row(
            table_id="tbl-control",
            row_id="row-betty",
            payload={"data": {"screen": "home"}},
        )
    assert exc.value.data["reason"] == "timeout"

    def http_factory(*a, **k):
        return _PutClient([], _Resp(status_code=500, payload={"error": "nope"}, text="fail"))

    monkeypatch.setattr("careconnect_api.revel_write.httpx.AsyncClient", http_factory)
    with pytest.raises(APIException) as exc:
        await update_data_table_row(
            table_id="tbl-control",
            row_id="row-betty",
            payload={"data": {"screen": "home"}},
        )
    assert exc.value.data["reason"] == "http_error"

    def bad_factory(*a, **k):
        return _PutClient([], _Resp(status_code=200, payload=None, text="nope"))

    monkeypatch.setattr("careconnect_api.revel_write.httpx.AsyncClient", bad_factory)
    with pytest.raises(APIException) as exc:
        await update_data_table_row(
            table_id="tbl-control",
            row_id="row-betty",
            payload={"data": {"screen": "home"}},
        )
    assert exc.value.data["reason"] == "malformed"


@pytest.mark.asyncio
async def test_http_row_outcomes(
    client: AsyncClient, admin_token: str, monkeypatch
):
    agent_id = (
        await client.post(
            "/api/agent/onboard", json={"name": "Betty"}, headers=_auth(admin_token)
        )
    ).json()["data"]["agentId"]
    await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={
            "apiKey": _SECRET,
            "deviceId": "immutable-revel-id",
            "deviceName": "Betty Room 101",
        },
        headers=_auth(admin_token),
    )
    monkeypatch.setattr(
        "careconnect_api.revel_display.configured_control_table_id",
        lambda: "tbl-control",
    )
    monkeypatch.setattr("careconnect_api.revel_display.revel_execute_enabled", lambda: False)

    class _ExplodingPut:
        def __init__(self, *args, **kwargs):
            raise AssertionError("disabled writes must not PUT")

        async def __aenter__(self):
            raise AssertionError("disabled writes must not PUT")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("careconnect_api.revel_write.httpx.AsyncClient", _ExplodingPut)

    async def none_rows():
        return {
            "table": {"id": "tbl-control", "name": "Control", "columns": COLUMNS},
            "rows": [],
        }

    monkeypatch.setattr("careconnect_api.revel_display.fetch_control_table", none_rows)
    missing = await client.post(
        "/api/internal/revel/display",
        json={"agentId": agent_id, "intent": "SHOW_HOME"},
        headers=_internal(),
    )
    assert missing.json()["data"]["reason"] == "control_row_not_found"
    assert missing.json()["data"]["result"] == "failed"

    async def dup_rows():
        return {
            "table": {"id": "tbl-control", "name": "Control", "columns": COLUMNS},
            "rows": [
                {"id": "r1", "data": {"device_key": "betty-room-101"}},
                {"id": "r2", "data": {"device_key": "betty-room-101"}},
            ],
        }

    monkeypatch.setattr("careconnect_api.revel_display.fetch_control_table", dup_rows)
    dup = await client.post(
        "/api/internal/revel/display",
        json={"agentId": agent_id, "intent": "SHOW_HOME"},
        headers=_internal(),
    )
    assert dup.json()["data"]["reason"] == "ambiguous_control_row"

    async def no_cols():
        return {
            "table": {"id": "tbl-control", "name": "Control", "columns": [{"key": "x"}]},
            "rows": [],
        }

    monkeypatch.setattr("careconnect_api.revel_display.fetch_control_table", no_cols)
    cols = await client.post(
        "/api/internal/revel/display",
        json={"agentId": agent_id, "intent": "SHOW_HOME"},
        headers=_internal(),
    )
    assert cols.json()["data"]["reason"] == "missing_control_columns"
    _assert = json.dumps(cols.json())
    for token in _BANNED:
        assert token not in _assert


@pytest.mark.asyncio
async def test_execute_true_http_sent_once(
    client: AsyncClient, admin_token: str, monkeypatch
):
    agent_id = (
        await client.post(
            "/api/agent/onboard", json={"name": "Betty"}, headers=_auth(admin_token)
        )
    ).json()["data"]["agentId"]
    await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={
            "apiKey": _SECRET,
            "deviceId": "immutable-revel-id",
            "deviceName": "Betty Room 101",
        },
        headers=_auth(admin_token),
    )
    monkeypatch.setattr(
        "careconnect_api.revel_display.configured_control_table_id",
        lambda: "tbl-control",
    )
    monkeypatch.setattr("careconnect_api.revel_display.revel_execute_enabled", lambda: True)
    monkeypatch.setattr("careconnect_api.revel_write.revel_execute_enabled", lambda: True)
    monkeypatch.setattr("careconnect_api.revel_write.load_revel_api_key", lambda: "k" * 32)

    async def one_row():
        return {
            "table": {"id": "tbl-control", "name": "Control", "columns": COLUMNS},
            "rows": [{"id": "row-betty", "data": {"device_key": "betty-room-101"}}],
        }

    monkeypatch.setattr("careconnect_api.revel_display.fetch_control_table", one_row)
    capture = []

    def factory(*a, **k):
        return _PutClient(
            capture, _Resp(payload={"id": "row-betty", "updatedAt": "2026-09-26T00:00:00Z"})
        )

    monkeypatch.setattr("careconnect_api.revel_write.httpx.AsyncClient", factory)
    resp = await client.post(
        "/api/internal/revel/display",
        json={
            "agentId": agent_id,
            "intent": "SHOW_SENSOR_ALERT",
            "title": "Sensor Alert",
            "message": "Humidity is high.",
            "source": "sensor",
        },
        headers=_internal(),
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["result"] == "sent"
    assert body["data"]["executed"] is True
    assert len(capture) == 1
    assert capture[0]["json"]["data"]["screen"] == "sensor_alert"
    assert "refresh" not in capture[0]["url"]
    status = await client.get(
        f"/api/agent/{agent_id}/revel/status", headers=_auth(admin_token)
    )
    assert status.json()["data"]["lastEvent"]["result"] == "sent"
    blob = json.dumps(body)
    for token in _BANNED:
        assert token not in blob
    assert "sendDeviceCommand" not in capture[0]["url"]
    assert "graphql" not in capture[0]["url"]


async def _mapped_agent(client: AsyncClient, admin_token: str) -> str:
    agent_id = (
        await client.post(
            "/api/agent/onboard", json={"name": "Betty"}, headers=_auth(admin_token)
        )
    ).json()["data"]["agentId"]
    await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={
            "apiKey": _SECRET,
            "deviceId": "immutable-revel-id",
            "deviceName": "Betty Room 101",
        },
        headers=_auth(admin_token),
    )
    return agent_id


def _one_row_fetch():
    async def one_row():
        return {
            "table": {"id": "tbl-control", "name": "Control", "columns": COLUMNS},
            "rows": [{"id": "row-betty", "data": {"device_key": "betty-room-101"}}],
        }

    return one_row


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "factory,reason",
    [
        (
            lambda capture: _PutClient(capture, error=httpx.TimeoutException("timed out")),
            "revel_unavailable",
        ),
        (
            lambda capture: _PutClient(
                capture, _Resp(status_code=500, payload={"error": "nope"}, text="fail")
            ),
            "revel_write_failed",
        ),
        (
            lambda capture: _PutClient(capture, _Resp(status_code=200, payload=None, text="nope")),
            "revel_write_failed",
        ),
        (
            lambda capture: _PutClient(capture, _Resp(status_code=200, payload={"sortOrder": 1})),
            "revel_write_failed",
        ),
    ],
)
async def test_execute_true_http_failures_are_failed(
    client: AsyncClient, admin_token: str, monkeypatch, factory, reason
):
    agent_id = await _mapped_agent(client, admin_token)
    monkeypatch.setattr(
        "careconnect_api.revel_display.configured_control_table_id",
        lambda: "tbl-control",
    )
    monkeypatch.setattr("careconnect_api.revel_display.revel_execute_enabled", lambda: True)
    monkeypatch.setattr("careconnect_api.revel_write.revel_execute_enabled", lambda: True)
    monkeypatch.setattr("careconnect_api.revel_write.load_revel_api_key", lambda: "k" * 32)
    monkeypatch.setattr("careconnect_api.revel_display.fetch_control_table", _one_row_fetch())
    capture = []
    monkeypatch.setattr(
        "careconnect_api.revel_write.httpx.AsyncClient",
        lambda *a, **k: factory(capture),
    )
    resp = await client.post(
        "/api/internal/revel/display",
        json={"agentId": agent_id, "intent": "SHOW_HOME"},
        headers=_internal(),
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["result"] == "failed"
    assert body["data"]["reason"] == reason
    assert body["data"]["executed"] is False
    assert len(capture) == 1
    import careconnect_api.revel_write as rw

    assert rw.revel_puts_attempted == 1
    status = await client.get(
        f"/api/agent/{agent_id}/revel/status", headers=_auth(admin_token)
    )
    event = status.json()["data"]["lastEvent"]
    assert event["result"] == "failed"
    assert event["reason"] == reason
    blob = json.dumps(body) + json.dumps(status.json())
    for token in _BANNED:
        assert token not in blob


@pytest.mark.asyncio
async def test_control_inspect_is_read_only(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(
        "careconnect_api.revel_write.configured_control_table_id",
        lambda: "tbl-control",
    )

    async def fake_fetch():
        return {
            "table": {
                "id": "tbl-control",
                "name": "Nexus Control",
                "rowCount": 1,
                "columns": COLUMNS,
            },
            "rows": [{"id": "row-betty", "data": {"device_key": "betty-room-101"}}],
        }

    monkeypatch.setattr("careconnect_api.revel_write.fetch_control_table", fake_fetch)
    monkeypatch.setattr("careconnect_api.revel_write.revel_execute_enabled", lambda: False)
    denied = await client.get("/api/internal/revel/control")
    assert denied.json()["code"] == 401
    listed = await client.get("/api/internal/revel/control", headers=_internal())
    body = listed.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["ok"] is True
    assert data["executeEnabled"] is False
    assert data["table"]["id"] == "tbl-control"
    assert data["table"]["name"] == "Nexus Control"
    assert data["binding"]["mapping"]["device_key"] == "device_key"
    assert data["rows"][0]["id"] == "row-betty"
    _blob = json.dumps(body)
    for token in _BANNED:
        assert token not in _blob


def test_result_for_attempt_write_reasons():
    from careconnect_api.revel_status import result_for_attempt

    assert result_for_attempt(executed=True, reason=None) == "sent"
    assert result_for_attempt(executed=False, reason="revel_write_disabled") == "skipped"
    for why in (
        "unmapped_player",
        "control_row_not_found",
        "ambiguous_control_row",
        "missing_control_columns",
        "control_table_not_configured",
        "revel_not_configured",
        "revel_auth_failed",
        "revel_unavailable",
        "revel_write_failed",
    ):
        assert result_for_attempt(executed=False, reason=why) == "failed"


@pytest.mark.asyncio
async def test_read_failures_are_controlled(client: AsyncClient, admin_token: str, monkeypatch):
    agent_id = await _mapped_agent(client, admin_token)
    monkeypatch.setattr(
        "careconnect_api.revel_display.configured_control_table_id",
        lambda: "tbl-control",
    )
    monkeypatch.setattr("careconnect_api.revel_display.revel_execute_enabled", lambda: False)

    class _ExplodingPut:
        def __init__(self, *args, **kwargs):
            raise AssertionError("disabled writes must not PUT")

        async def __aenter__(self):
            raise AssertionError("disabled writes must not PUT")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("careconnect_api.revel_write.httpx.AsyncClient", _ExplodingPut)

    async def auth_fail():
        raise APIException(401, "Revel authentication failed", data={"reason": "auth"})

    monkeypatch.setattr("careconnect_api.revel_display.fetch_control_table", auth_fail)
    auth = await client.post(
        "/api/internal/revel/display",
        json={"agentId": agent_id, "intent": "SHOW_HOME"},
        headers=_internal(),
    )
    assert auth.json()["data"]["reason"] == "revel_auth_failed"
    assert auth.json()["data"]["result"] == "failed"
    assert auth.json()["data"]["write"] is None or "data" not in (auth.json()["data"].get("write") or {})

    async def missing_key():
        raise APIException(503, "Revel API key is not configured", data={"ok": False})

    monkeypatch.setattr("careconnect_api.revel_display.fetch_control_table", missing_key)
    missing = await client.post(
        "/api/internal/revel/display",
        json={"agentId": agent_id, "intent": "SHOW_HOME"},
        headers=_internal(),
    )
    assert missing.json()["data"]["reason"] == "revel_not_configured"

    async def down():
        raise APIException(502, "Could not reach Revel", data={"reason": "transport"})

    monkeypatch.setattr("careconnect_api.revel_display.fetch_control_table", down)
    gone = await client.post(
        "/api/internal/revel/display",
        json={"agentId": agent_id, "intent": "SHOW_HOME"},
        headers=_internal(),
    )
    assert gone.json()["data"]["reason"] == "revel_unavailable"
    blob = json.dumps(auth.json()) + json.dumps(missing.json()) + json.dumps(gone.json())
    for token in _BANNED:
        assert token not in blob


@pytest.mark.asyncio
async def test_control_inspect_auth_failure_has_no_invented_ids(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(
        "careconnect_api.revel_write.configured_control_table_id",
        lambda: "tbl-control",
    )

    async def auth_fail():
        raise APIException(401, "Revel authentication failed", data={"reason": "auth"})

    monkeypatch.setattr("careconnect_api.revel_write.fetch_control_table", auth_fail)
    listed = await client.get("/api/internal/revel/control", headers=_internal())
    body = listed.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["ok"] is False
    assert data["reason"] == "revel_auth_failed"
    assert data["table"] is None
    assert data["rows"] == []
    assert data.get("binding") is None
    _blob = json.dumps(body)
    for token in _BANNED:
        assert token not in _blob
