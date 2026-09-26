"""Phase 2B Revel display state: mapping, validation, skipped writes, discovery."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.chat_events import parse_revel_timeline
from careconnect_api.envelope import APIException
from careconnect_api.models import AiAgentChatHistory, RevelPlayerMap, SysUser
from careconnect_api.revel_config import EXECUTE_ENABLED
from careconnect_api.revel_datatables import (
    ALLOWED_QUERIES,
    DATATABLE_QUERY,
    DATATABLE_ROWS_QUERY,
    DATATABLES_QUERY,
    DOCUMENTED_WRITE_OPERATIONS,
    graphql_read,
    list_data_tables,
)
from careconnect_api.revel_display import (
    DISPLAY_INTENTS,
    INTENT_TO_SCREEN,
    REASON_EXECUTE_DISABLED,
    TITLE_MAX,
    apply_display_state,
    build_display_state,
    screen_for_intent,
    validate_expiration,
    validate_image_url,
)
from careconnect_api.revel_player_map import (
    allocate_device_key,
    upsert_selected_player,
)
from careconnect_api.revel_write import revel_execute_enabled, revel_puts_attempted
from careconnect_api.settings import settings


_SECRET = "revel-live-key-XXXX7F2A"
_BANNED = (
    "apiKey",
    "api_key",
    "secret",
    "registrationKey",
    "X-RevelDigital-ApiKey",
    "X-Internal-Token",
    _SECRET,
)


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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _internal() -> dict[str, str]:
    return {"X-Internal-Token": settings.internal_token}


def _assert_clean(payload) -> None:
    blob = json.dumps(payload)
    for token in _BANNED:
        assert token not in blob


async def _onboard(client: AsyncClient, token: str, name: str = "Betty") -> str:
    resp = await client.post("/api/agent/onboard", json={"name": name}, headers=_auth(token))
    assert resp.json()["code"] == 0, resp.json()
    return resp.json()["data"]["agentId"]


async def _connect_and_map(
    client: AsyncClient,
    token: str,
    agent_id: str,
    *,
    device_id: str = "immutable-revel-id",
    device_name: str = "Betty Room 101",
) -> None:
    res = await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={
            "apiKey": _SECRET,
            "deviceId": device_id,
            "deviceName": device_name,
        },
        headers=_auth(token),
    )
    assert res.json()["code"] == 0, res.json()


def _future_iso(hours: int = 2) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class _ExplodingClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("Phase 2C must not open an HTTP client for display writes")

    async def __aenter__(self):
        raise AssertionError("Phase 2C must not open an HTTP client for display writes")

    async def __aexit__(self, *args):
        return False


CONTROL_COLUMNS = [
    {"id": "c1", "name": "Device Key", "key": "device_key", "type": "TEXT"},
    {"id": "c2", "name": "Screen", "key": "screen", "type": "TEXT"},
    {"id": "c3", "name": "Title", "key": "title", "type": "TEXT"},
    {"id": "c4", "name": "Message", "key": "message", "type": "TEXT"},
    {"id": "c5", "name": "Image URL", "key": "image_url", "type": "URL"},
    {"id": "c6", "name": "Priority", "key": "priority", "type": "NUMBER"},
    {"id": "c7", "name": "Expires", "key": "expires_at", "type": "DATE"},
    {"id": "c8", "name": "Updated", "key": "updated_at", "type": "DATE"},
]


def _sample_control(*, device_key="betty-room-101", rows=None):
    if rows is None:
        rows = [
            {
                "id": "row-betty",
                "sortOrder": 0,
                "data": {"device_key": device_key, "screen": "home"},
                "updatedAt": None,
            }
        ]
    return {
        "ok": True,
        "table": {
            "id": "tbl-control",
            "name": "Nexus Control",
            "rowCount": len(rows),
            "columns": CONTROL_COLUMNS,
            "isControlTable": True,
        },
        "rows": rows,
        "writesEnabled": False,
    }


@pytest.fixture
def stub_control(monkeypatch):
    async def fake_fetch():
        return _sample_control()

    monkeypatch.setattr("careconnect_api.revel_display.fetch_control_table", fake_fetch)
    monkeypatch.setattr(
        "careconnect_api.revel_display.configured_control_table_id",
        lambda: "tbl-control",
    )
    monkeypatch.setattr("careconnect_api.revel_write.revel_execute_enabled", lambda: False)
    monkeypatch.setattr("careconnect_api.revel_display.revel_execute_enabled", lambda: False)

    class _ExplodingPut:
        def __init__(self, *args, **kwargs):
            raise AssertionError("REVEL_EXECUTE_ENABLED=false must not open a write HTTP client")

        async def __aenter__(self):
            raise AssertionError("REVEL_EXECUTE_ENABLED=false must not open a write HTTP client")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("careconnect_api.revel_write.httpx.AsyncClient", _ExplodingPut)
    import careconnect_api.revel_write as rw

    rw.revel_puts_attempted = 0
    yield
    rw.revel_puts_attempted = 0


# ---------------------------------------------------------------------------
# Intent mapper + validation (no HTTP)
# ---------------------------------------------------------------------------


def test_execute_enabled_stays_false():
    assert EXECUTE_ENABLED is False
    assert revel_execute_enabled() is False
    import inspect
    import careconnect_api.revel_display as mod

    assert "httpx" not in inspect.getsource(mod)


def test_every_supported_intent_maps_to_allowed_screen():
    expected = {
        "SHOW_HOME": "home",
        "RETURN_HOME": "home",
        "SHOW_APPOINTMENT_REMINDER": "appointment",
        "SHOW_MEDICATION_REMINDER": "medication",
        "SHOW_CARE_ALERT": "care_alert",
        "SHOW_SENSOR_ALERT": "sensor_alert",
    }
    assert set(DISPLAY_INTENTS) == set(expected)
    assert INTENT_TO_SCREEN == expected
    for intent, screen in expected.items():
        assert screen_for_intent(intent) == screen


def test_unsupported_intent_rejected():
    with pytest.raises(APIException) as exc:
        screen_for_intent("SHOW_PHOTOS")
    assert exc.value.code == 400
    with pytest.raises(APIException):
        screen_for_intent("display_calendar")
    with pytest.raises(APIException):
        build_display_state(
            device_key="betty-room-101",
            revel_device_id="immutable-revel-id",
            intent="sendDeviceCommand",
        )


def test_screen_cannot_be_overridden_by_caller():
    with pytest.raises(APIException) as exc:
        build_display_state(
            device_key="betty-room-101",
            revel_device_id="immutable-revel-id",
            intent="SHOW_HOME",
            caller_screen="medication",
        )
    assert exc.value.code == 400
    state = build_display_state(
        device_key="betty-room-101",
        revel_device_id="immutable-revel-id",
        intent="SHOW_APPOINTMENT_REMINDER",
        caller_screen="appointment",
    )
    assert state["screen"] == "appointment"


def test_title_and_message_length_validation():
    with pytest.raises(APIException) as exc:
        build_display_state(
            device_key="k",
            revel_device_id="d1",
            intent="SHOW_HOME",
            title="T" * (TITLE_MAX + 1),
        )
    assert "title" in exc.value.msg
    with pytest.raises(APIException) as exc:
        build_display_state(
            device_key="k",
            revel_device_id="d1",
            intent="SHOW_HOME",
            message="M" * 501,
        )
    assert "message" in exc.value.msg


def test_image_url_validation():
    assert validate_image_url(None) is None
    assert validate_image_url("https://cdn.example.com/appt.png")
    with pytest.raises(APIException):
        validate_image_url("http://cdn.example.com/appt.png")
    with pytest.raises(APIException):
        validate_image_url("https://user:pass@cdn.example.com/x.png")
    with pytest.raises(APIException):
        validate_image_url("javascript:alert(1)")
    with pytest.raises(APIException):
        validate_image_url("https://localhost/secret.png")


def test_expiration_validation():
    future = _future_iso()
    assert validate_expiration(future)
    with pytest.raises(APIException):
        validate_expiration("not-a-date")
    with pytest.raises(APIException):
        validate_expiration("2020-01-01T00:00:00Z")
    too_far = (datetime.now(timezone.utc) + timedelta(days=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with pytest.raises(APIException):
        validate_expiration(too_far)


def test_duplicate_device_name_does_not_reuse_key():
    taken = set()
    first = allocate_device_key("Betty Room 101", "id-aaa", taken)
    taken.add(first)
    second = allocate_device_key("Betty Room 101", "id-bbb", taken)
    assert first == "betty-room-101"
    assert second != first
    assert second.startswith("betty-room-101-")
    assert "idbbb" in second or second.endswith("id-bbb".replace("-", "")[-8:])


# ---------------------------------------------------------------------------
# HTTP display path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_display_skipped_when_execute_disabled(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, stub_control
):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(client, admin_token, agent_id)
    before = revel_puts_attempted
    import careconnect_api.revel_write as rw

    rw.revel_puts_attempted = 0

    resp = await client.post(
        "/api/internal/revel/display",
        json={
            "agentId": agent_id,
            "deviceKey": "betty-room-101",
            "intent": "SHOW_APPOINTMENT_REMINDER",
            "title": "Upcoming Appointment",
            "message": "Your appointment begins at 2:30 PM.",
            "imageUrl": "https://cdn.example.com/appt.png",
            "expiresAt": _future_iso(),
            "source": "calendar",
        },
        headers=_internal(),
    )
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]
    _assert_clean(body)
    assert data["result"] == "skipped"
    assert data["executed"] is False
    assert data["reason"] == REASON_EXECUTE_DISABLED
    assert data["displayState"]["screen"] == "appointment"
    assert data["displayState"]["intent"] == "SHOW_APPOINTMENT_REMINDER"
    assert data["displayState"]["revelDeviceId"] == "immutable-revel-id"
    assert data["write"]["method"] == "PUT"
    assert data["write"]["path"] == "/datatables/tbl-control/rows/row-betty"
    assert set(data["write"]["body"].keys()) == {"data"}
    assert "revelDeviceId" not in data["write"]["body"]["data"]
    assert data["write"]["body"]["data"]["device_key"] == "betty-room-101"
    assert data["write"]["body"]["data"]["screen"] == "appointment"
    assert rw.revel_puts_attempted == before == 0

    status = await client.get(
        f"/api/agent/{agent_id}/revel/status",
        headers=_auth(admin_token),
    )
    event = status.json()["data"]["lastEvent"]
    assert event["result"] == "skipped"
    assert event["intent"] == "SHOW_APPOINTMENT_REMINDER"
    assert event["screen"] == "appointment"
    assert event["reason"] == REASON_EXECUTE_DISABLED
    _assert_clean(status.json())

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(AiAgentChatHistory).where(AiAgentChatHistory.agent_id == agent_id)
        )
    ).scalars().all()
    revel_rows = [r for r in rows if r.chat_type == 3]
    assert revel_rows
    parsed = parse_revel_timeline(revel_rows[-1].content)
    assert parsed is not None
    assert parsed["result"] == "skipped"
    assert parsed["screen"] == "appointment"
    assert parsed["intent"] == "SHOW_APPOINTMENT_REMINDER"
    assert parsed["reason"] == REASON_EXECUTE_DISABLED
    blob = json.dumps(parsed)
    for token in _BANNED:
        assert token not in blob
        assert token not in (revel_rows[-1].content or "")


def careconnect_writes_unchanged(before: int) -> bool:
    import careconnect_api.revel_write as rw

    return rw.revel_puts_attempted == before


@pytest.mark.asyncio
async def test_client_cannot_supply_revel_device_id(client: AsyncClient, admin_token: str):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(client, admin_token, agent_id)
    resp = await client.post(
        "/api/internal/revel/display",
        json={
            "agentId": agent_id,
            "intent": "SHOW_HOME",
            "revelDeviceId": "attacker-chosen-id",
        },
        headers=_internal(),
    )
    assert resp.json()["code"] == 400


@pytest.mark.asyncio
async def test_screen_field_rejected_on_http(client: AsyncClient, admin_token: str):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(client, admin_token, agent_id)
    resp = await client.post(
        "/api/internal/revel/display",
        json={
            "agentId": agent_id,
            "intent": "SHOW_HOME",
            "screen": "medication",
        },
        headers=_internal(),
    )
    assert resp.json()["code"] == 400


@pytest.mark.asyncio
async def test_graphql_and_command_fields_rejected(client: AsyncClient, admin_token: str):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(client, admin_token, agent_id)
    for extra in (
        {"query": "{ device { id } }"},
        {"mutation": "createDataTableRow"},
        {"command": "restart"},
        {"graphql": "nope"},
    ):
        body = {"agentId": agent_id, "intent": "SHOW_HOME", **extra}
        resp = await client.post(
            "/api/internal/revel/display", json=body, headers=_internal()
        )
        assert resp.json()["code"] == 400, extra


@pytest.mark.asyncio
async def test_caller_cannot_select_row_or_table(client: AsyncClient, admin_token: str):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(client, admin_token, agent_id)
    for extra in (
        {"rowId": "row-attacker"},
        {"tableId": "tbl-attacker"},
        {"controlTableId": "tbl-attacker"},
        {"data": {"screen": "hacked"}},
        {"executeEnabled": True},
    ):
        body = {"agentId": agent_id, "intent": "SHOW_HOME", **extra}
        resp = await client.post(
            "/api/internal/revel/display", json=body, headers=_internal()
        )
        assert resp.json()["code"] == 400, extra


@pytest.mark.asyncio
async def test_unsupported_intent_http_rejected(client: AsyncClient, admin_token: str):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(client, admin_token, agent_id)
    resp = await client.post(
        "/api/internal/revel/display",
        json={"agentId": agent_id, "intent": "SHOW_PHOTOS"},
        headers=_internal(),
    )
    assert resp.json()["code"] == 400


@pytest.mark.asyncio
async def test_missing_player_mapping_fails(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, caplog
):
    agent_id = await _onboard(client, admin_token)
    await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={"apiKey": _SECRET},
        headers=_auth(admin_token),
    )
    caplog.set_level(logging.INFO)
    resp = await client.post(
        "/api/internal/revel/display",
        json={"agentId": agent_id, "intent": "SHOW_HOME"},
        headers=_internal(),
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["result"] == "failed"
    assert body["data"]["reason"] == "unmapped_player"
    assert body["data"]["executed"] is False
    _assert_clean(body)
    assert _SECRET not in caplog.text

    status = await client.get(
        f"/api/agent/{agent_id}/revel/status",
        headers=_auth(admin_token),
    )
    event = status.json()["data"]["lastEvent"]
    assert event["result"] == "failed"
    assert event["intent"] == "SHOW_HOME"


@pytest.mark.asyncio
async def test_duplicate_names_keep_immutable_ids(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(
        client, admin_token, agent_id, device_id="id-alpha", device_name="Lobby"
    )
    db_session.expire_all()
    second = await upsert_selected_player(
        db_session,
        agent_id=agent_id,
        revel_device_id="id-beta",
        revel_device_name="Lobby",
    )
    await db_session.commit()
    assert second is not None
    assert second.device_key != "lobby"

    async def fake_fetch():
        return _sample_control(
            rows=[
                {"id": "row-a", "data": {"device_key": "lobby", "screen": "home"}},
                {
                    "id": "row-b",
                    "data": {"device_key": second.device_key, "screen": "home"},
                },
            ]
        )

    monkeypatch.setattr("careconnect_api.revel_display.fetch_control_table", fake_fetch)
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

    first = await client.post(
        "/api/internal/revel/display",
        json={"agentId": agent_id, "deviceKey": "lobby", "intent": "SHOW_HOME"},
        headers=_internal(),
    )
    assert first.json()["data"]["displayState"]["revelDeviceId"] == "id-alpha"
    assert first.json()["data"]["controlRowId"] == "row-a"
    other = await client.post(
        "/api/internal/revel/display",
        json={
            "agentId": agent_id,
            "deviceKey": second.device_key,
            "intent": "SHOW_CARE_ALERT",
        },
        headers=_internal(),
    )
    assert other.json()["data"]["displayState"]["revelDeviceId"] == "id-beta"
    assert other.json()["data"]["displayState"]["screen"] == "care_alert"
    assert other.json()["data"]["controlRowId"] == "row-b"
    maps = (
        await db_session.execute(
            select(RevelPlayerMap).where(RevelPlayerMap.agent_id == agent_id)
        )
    ).scalars().all()
    ids = {row.revel_device_id for row in maps}
    assert ids == {"id-alpha", "id-beta"}


@pytest.mark.asyncio
async def test_disconnect_revel_deletes_player_map(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(client, admin_token, agent_id)
    gone = await client.delete(
        f"/api/agent/{agent_id}/integrations/revel",
        headers=_auth(admin_token),
    )
    assert gone.json()["code"] == 0
    db_session.expire_all()
    maps = (
        await db_session.execute(
            select(RevelPlayerMap).where(RevelPlayerMap.agent_id == agent_id)
        )
    ).scalars().all()
    assert maps == []


@pytest.mark.asyncio
async def test_display_requires_internal_token(client: AsyncClient, admin_token: str):
    agent_id = await _onboard(client, admin_token)
    denied = await client.post(
        "/api/internal/revel/display",
        json={"agentId": agent_id, "intent": "SHOW_HOME"},
    )
    assert denied.json()["code"] == 401


@pytest.mark.asyncio
async def test_apply_display_state_never_writes_when_disabled(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, stub_control
):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(client, admin_token, agent_id)
    import careconnect_api.revel_write as rw

    rw.revel_puts_attempted = 0
    db_session.expire_all()
    result = await apply_display_state(
        db_session,
        agent_id=agent_id,
        intent="SHOW_MEDICATION_REMINDER",
        message="Take the evening dose.",
        source="internal",
    )
    assert result["result"] == "skipped"
    assert result["reason"] == REASON_EXECUTE_DISABLED
    assert result["displayState"]["screen"] == "medication"
    assert result["write"]["method"] == "PUT"
    assert rw.revel_puts_attempted == 0
    assert revel_execute_enabled() is False
    assert EXECUTE_ENABLED is False


@pytest.mark.asyncio
async def test_device_key_frozen_when_name_changes(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(
        client, admin_token, agent_id, device_id="dev-1", device_name="Betty Room 101"
    )
    renamed = await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={"deviceId": "dev-1", "deviceName": "Betty Suite"},
        headers=_auth(admin_token),
    )
    assert renamed.json()["code"] == 0
    db_session.expire_all()
    row = (
        await db_session.execute(
            select(RevelPlayerMap).where(RevelPlayerMap.agent_id == agent_id)
        )
    ).scalar_one()
    assert row.device_key == "betty-room-101"
    assert row.revel_device_id == "dev-1"
    assert row.revel_device_name == "Betty Suite"
    status = await client.get(
        f"/api/agent/{agent_id}/revel/status",
        headers=_auth(admin_token),
    )
    assert status.json()["data"]["deviceKey"] == "betty-room-101"


@pytest.mark.asyncio
async def test_forbidden_extra_device_id_on_apply(
    db_session: AsyncSession, client: AsyncClient, admin_token: str
):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(client, admin_token, agent_id)
    with pytest.raises(APIException) as exc:
        await apply_display_state(
            db_session,
            agent_id=agent_id,
            intent="SHOW_HOME",
            extra={"revelDeviceId": "attacker-chosen-id"},
        )
    assert exc.value.code == 400


@pytest.mark.asyncio
async def test_all_intents_skip_via_http(
    client: AsyncClient, admin_token: str, stub_control
):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(client, admin_token, agent_id)
    for intent, screen in INTENT_TO_SCREEN.items():
        resp = await client.post(
            "/api/internal/revel/display",
            json={"agentId": agent_id, "intent": intent, "source": "internal"},
            headers=_internal(),
        )
        data = resp.json()["data"]
        assert data["result"] == "skipped", intent
        assert data["displayState"]["screen"] == screen
        assert data["displayState"]["intent"] == intent


@pytest.mark.asyncio
async def test_oversized_title_http(client: AsyncClient, admin_token: str):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(client, admin_token, agent_id)
    resp = await client.post(
        "/api/internal/revel/display",
        json={
            "agentId": agent_id,
            "intent": "SHOW_HOME",
            "title": "T" * 121,
        },
        headers=_internal(),
    )
    assert resp.json()["code"] == 400


@pytest.mark.asyncio
async def test_invalid_image_and_expiration_http(client: AsyncClient, admin_token: str):
    agent_id = await _onboard(client, admin_token)
    await _connect_and_map(client, admin_token, agent_id)
    bad_img = await client.post(
        "/api/internal/revel/display",
        json={
            "agentId": agent_id,
            "intent": "SHOW_HOME",
            "imageUrl": "http://insecure.example/x.png",
        },
        headers=_internal(),
    )
    assert bad_img.json()["code"] == 400
    bad_exp = await client.post(
        "/api/internal/revel/display",
        json={
            "agentId": agent_id,
            "intent": "SHOW_HOME",
            "expiresAt": "yesterday",
        },
        headers=_internal(),
    )
    assert bad_exp.json()["code"] == 400


# ---------------------------------------------------------------------------
# Data Table discovery — read-only
# ---------------------------------------------------------------------------


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

    async def put(self, *args, **kwargs):
        raise AssertionError("Data Table discovery must not PUT")

    async def delete(self, *args, **kwargs):
        raise AssertionError("Data Table discovery must not DELETE")


def test_datatable_queries_are_reads_only():
    for query in ALLOWED_QUERIES:
        assert "mutation" not in query.casefold()
        for op in DOCUMENTED_WRITE_OPERATIONS:
            assert op not in query
    assert "dataTables" in DATATABLES_QUERY
    assert "dataTable(tableId" in DATATABLE_QUERY
    assert "columns" in DATATABLE_QUERY
    assert "dataTableRows" in DATATABLE_ROWS_QUERY


@pytest.mark.asyncio
async def test_datatables_list_read_only(monkeypatch):
    capture: dict = {}
    payload = {
        "data": {
            "dataTables": {
                "data": [
                    {
                        "id": "tbl-1",
                        "name": "Control",
                        "description": "screens",
                        "columnCount": 4,
                        "rowCount": 2,
                        "updatedAt": "2026-09-26T00:00:00Z",
                    }
                ],
                "continuationToken": None,
            }
        }
    }

    def _factory(*args, **kwargs):
        return FakeClient(capture, FakeResp(payload=payload), *args, **kwargs)

    monkeypatch.setattr("careconnect_api.revel_datatables.httpx.AsyncClient", _factory)
    monkeypatch.setattr(
        "careconnect_api.revel_datatables.load_revel_api_key", lambda path=None: "k" * 32
    )
    tables = await list_data_tables()
    assert tables["ok"] is True
    assert tables["tables"][0]["id"] == "tbl-1"
    assert capture["method"] == "POST"
    assert capture["json"]["query"] == DATATABLES_QUERY
    assert "mutation" not in capture["json"]["query"].casefold()
    assert "X-RevelDigital-ApiKey" in capture["headers"]
    assert capture["headers"]["X-RevelDigital-ApiKey"] == "k" * 32


@pytest.mark.asyncio
async def test_graphql_read_rejects_unknown_and_mutations():
    with pytest.raises(APIException):
        await graphql_read("mutation { createDataTableRow(input: {}) { success } }")
    with pytest.raises(APIException):
        await graphql_read("{ device { id } }")


@pytest.mark.asyncio
async def test_datatables_endpoints_require_token_and_stay_clean(
    client: AsyncClient, admin_token: str, monkeypatch, caplog
):
    _ = admin_token
    denied = await client.get("/api/internal/revel/datatables")
    assert denied.json()["code"] == 401
    capture: dict = {}
    payload = {"data": {"dataTables": {"data": [], "continuationToken": None}}}

    def _factory(*args, **kwargs):
        return FakeClient(capture, FakeResp(payload=payload), *args, **kwargs)

    monkeypatch.setattr("careconnect_api.revel_datatables.httpx.AsyncClient", _factory)
    monkeypatch.setattr(
        "careconnect_api.revel_datatables.load_revel_api_key", lambda path=None: "k" * 32
    )
    caplog.set_level(logging.INFO)
    listed = await client.get("/api/internal/revel/datatables", headers=_internal())
    assert listed.json()["code"] == 0
    _assert_clean(listed.json())
    assert _SECRET not in caplog.text
    assert "k" * 32 not in caplog.text

    table_payload = {
        "data": {
            "dataTable": {
                "id": "tbl-1",
                "name": "Control",
                "rowCount": 1,
                "cacheTtlSeconds": 30,
                "columns": [{"id": "c1", "name": "Screen", "key": "screen", "type": "TEXT"}],
            }
        }
    }
    rows_payload = {
        "data": {
            "dataTableRows": {
                "data": [{"id": "row-1", "sortOrder": 0, "data": {"screen": "home"}, "updatedAt": None}],
                "totalCount": 1,
                "continuationToken": None,
            }
        }
    }
    queue = [FakeResp(payload=table_payload), FakeResp(payload=rows_payload)]
    capture2: dict = {}

    class QueueClient(FakeClient):
        async def post(self, url, headers=None, json=None):
            self._capture.setdefault("queries", []).append((json or {}).get("query"))
            self._capture["json"] = json
            return queue.pop(0)

    def _factory2(*args, **kwargs):
        return QueueClient(capture2, FakeResp(payload={}), *args, **kwargs)

    monkeypatch.setattr("careconnect_api.revel_datatables.httpx.AsyncClient", _factory2)
    detail = await client.get("/api/internal/revel/datatables/tbl-1", headers=_internal())
    assert detail.json()["code"] == 0, detail.json()
    data = detail.json()["data"]
    assert data["table"]["id"] == "tbl-1"
    assert data["rows"][0]["id"] == "row-1"
    assert data["writesEnabled"] is False
    _assert_clean(detail.json())
    assert all("mutation" not in (q or "").casefold() for q in capture2.get("queries", []))

    bad = await client.get(
        "/api/internal/revel/datatables/%7BcreateDataTableRow%7D",
        headers=_internal(),
    )
    assert bad.json()["code"] == 400
