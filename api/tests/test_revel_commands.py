"""Revel wake-name, phrase matcher, botName, allowlist command, timeline."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.chat_events import (
    CHAT_TYPE_SYSTEM,
    encode_revel_timeline,
    parse_gcal_timeline,
    parse_revel_timeline,
    persist_revel_timeline,
)
from careconnect_api.integration_crypto import decrypt_secret
from careconnect_api.models import AiAgent, AiAgentChatHistory, ClientIntegration, SysUser
from careconnect_api.envelope import APIException
from careconnect_api.revel_client import (
    AUTH_HEADER,
    RevelMutationDisabled,
    apply_device_tags,
    key_shape,
    list_devices,
    sanitize_revel_api_key,
    _normalize_device,
)
from careconnect_api.revel_config import EXECUTE_ENABLED
from careconnect_api.revel_match import (
    match_enabled_action,
    normalize_phrase,
    strip_wake_prefix,
    utterance_from_asr_text,
)
from careconnect_api.settings import settings
from careconnect_api.triage.runner import _render_dialogue


_SECRET_KEYS = (
    "secret_hash",
    "secret_enc",
    "secretHash",
    "secretEnc",
    "apiKey",
    "api_key",
    "registrationKey",
    "registration_key",
    "registrationKeyEnc",
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


def _blob(payload) -> str:
    return json.dumps(payload)


def _assert_no_secrets(payload) -> None:
    blob = _blob(payload)
    for key in _SECRET_KEYS:
        assert f'"{key}":' not in blob
    assert "revel-live-key" not in blob
    assert "reg-secret" not in blob


async def _onboard(client: AsyncClient, token: str, name: str = "George") -> str:
    resp = await client.post(
        "/api/agent/onboard", json={"name": name}, headers=_auth(token)
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["agentId"]


def _actions(*enabled_phrases: tuple[str, str]):
    """Build the four V1 actions; optional (intent, phrase) pairs are enabled."""
    by = {
        "display_calendar": [],
        "display_photos": [],
        "display_home": [],
        "display_reminders": [],
    }
    enabled = {k: False for k in by}
    for intent, phrase in enabled_phrases:
        by[intent].append(phrase)
        enabled[intent] = True
    return [
        {
            "intent": intent,
            "label": intent,
            "revelTag": "calendar" if intent == "display_calendar" else None,
            "enabled": enabled[intent],
            "phrases": by[intent],
        }
        for intent in by
    ]


# ---------- matcher ----------


@pytest.mark.parametrize(
    "utterance,name,hit,remainder",
    [
        ("Bob show my calendar", "Bob", True, "show my calendar"),
        ("bob show my calendar", "Bob", True, "show my calendar"),
        ("Bob, show my calendar", "Bob", True, "show my calendar"),
        ("BOB: SHOW MY CALENDAR", "Bob", True, "SHOW MY CALENDAR"),
        ("Bob! show my calendar", "Bob", True, "show my calendar"),
        ("Bob", "Bob", True, ""),
        ("Bob!", "Bob", True, ""),
        ("I told Bob to show my calendar", "Bob", False, "I told Bob to show my calendar"),
        ("Can you tell Bob something?", "Bob", False, "Can you tell Bob something?"),
        ("Show my calendar", "Bob", False, "Show my calendar"),
        ("Bobby, show my calendar", "Bob", False, "Bobby, show my calendar"),
        ("show my calendar", "Bob", False, "show my calendar"),
        ("Bob show my calendar", "", False, "Bob show my calendar"),
        ("Bob show my calendar", None, False, "Bob show my calendar"),
    ],
)
def test_strip_wake_prefix(utterance, name, hit, remainder):
    got_hit, got_rest = strip_wake_prefix(utterance, name)
    assert got_hit is hit
    assert got_rest == remainder


def test_utterance_from_asr_json():
    raw = json.dumps({"speaker": "front", "content": "Bob, show my calendar"})
    assert utterance_from_asr_text(raw) == "Bob, show my calendar"


def test_normalize_and_match_phrases():
    actions = [
        {
            "intent": "display_calendar",
            "enabled": True,
            "phrases": ["show my calendar", "display my calendar"],
        },
        {
            "intent": "display_photos",
            "enabled": False,
            "phrases": ["show my photos"],
        },
    ]
    assert match_enabled_action("SHOW MY CALENDAR", actions)["intent"] == "display_calendar"
    assert match_enabled_action("show my calendar!", actions)["intent"] == "display_calendar"
    assert match_enabled_action("  display   my   calendar  ", actions)["intent"] == "display_calendar"
    assert match_enabled_action("show my calendar please", actions) is None
    assert match_enabled_action("show my photos", actions) is None
    assert normalize_phrase("Show my calendar!") == "show my calendar"


def test_apply_device_tags_refuses_mutation():
    assert EXECUTE_ENABLED is False
    with pytest.raises(RevelMutationDisabled):
        apply_device_tags("fake-key", "device-1", ["calendar"])


def test_normalize_device_rest_shape():
    d = _normalize_device(
        {
            "id": "abc",
            "name": "Display1",
            "is_online": True,
            "tags": "home\nphotos",
            "registration_key": "should-not-leak",
        }
    )
    assert d["name"] == "Display1"
    assert d["isOnline"] is True
    assert d["tags"] == ["home", "photos"]
    assert d["registrationKeySet"] is True
    assert "should-not-leak" not in json.dumps(d)
    assert "registration_key" not in d


# ---------- bot name + revel config ----------


@pytest.mark.asyncio
async def test_bot_name_get_patch_round_trip(client: AsyncClient, admin_token: str, db_session: AsyncSession):
    agent_id = await _onboard(client, admin_token)
    got = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    assert got.json()["code"] == 0
    assert got.json()["data"].get("botName") in (None, "")

    patched = await client.patch(
        f"/api/agent/{agent_id}",
        json={"botName": "Bob"},
        headers=_auth(admin_token),
    )
    assert patched.json()["code"] == 0, patched.json()
    assert "botName" in patched.json()["data"]["fields"]

    again = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    assert again.json()["data"]["botName"] == "Bob"
    db_session.expire_all()
    agent = await db_session.get(AiAgent, agent_id)
    assert agent.bot_name == "Bob"

    cleared = await client.patch(
        f"/api/agent/{agent_id}",
        json={"botName": "  "},
        headers=_auth(admin_token),
    )
    assert cleared.json()["code"] == 0
    db_session.expire_all()
    agent = await db_session.get(AiAgent, agent_id)
    assert agent.bot_name is None


@pytest.mark.asyncio
async def test_revel_put_stores_actions_without_echoing_secrets(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    full_key = "revel-live-key-XXXX7F2A"
    saved = await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={
            "apiKey": full_key,
            "apiBaseUrl": "https://api.reveldigital.com",
            "actions": _actions(("display_calendar", "show my calendar")),
        },
        headers=_auth(admin_token),
    )
    body = saved.json()
    assert body["code"] == 0, body
    _assert_no_secrets(body)
    data = body["data"]
    assert data["connected"] is True
    assert data["executeEnabled"] is False
    assert data["voiceRequiresBotName"] is True
    calendar = next(a for a in data["actions"] if a["intent"] == "display_calendar")
    assert "show my calendar" in calendar["phrases"]
    assert calendar["enabled"] is True

    listed = (
        await client.get(
            f"/api/agent/{agent_id}/integrations", headers=_auth(admin_token)
        )
    ).json()
    _assert_no_secrets(listed)
    revel = next(r for r in listed["data"]["list"] if r["provider"] == "revel")
    assert full_key not in (revel.get("maskedKey") or "")
    assert revel["secretHint"] == "7F2A"

    db_session.expire_all()
    row = (
        await db_session.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == "revel",
            )
        )
    ).scalar_one()
    assert decrypt_secret(row.secret_enc) == full_key
    assert "revel-live-key" not in (row.metadata_json or "")


@pytest.mark.asyncio
async def test_revel_registration_key_encrypted_not_returned(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={"apiKey": "revel-live-key-XXXX7F2A"},
        headers=_auth(admin_token),
    )
    saved = await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={"registrationKey": "reg-secret-ABCD"},
        headers=_auth(admin_token),
    )
    body = saved.json()
    assert body["code"] == 0, body
    _assert_no_secrets(body)
    assert body["data"]["registrationKeySet"] is True
    assert body["data"]["registrationKeyHint"] == "ABCD"
    db_session.expire_all()
    row = (
        await db_session.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == "revel",
            )
        )
    ).scalar_one()
    meta = json.loads(row.metadata_json)
    assert meta["registrationKeyEnc"]
    assert "reg-secret" not in meta["registrationKeyEnc"]
    assert decrypt_secret(meta["registrationKeyEnc"]) == "reg-secret-ABCD"


@pytest.mark.asyncio
async def test_revel_discover_is_read_only(client: AsyncClient, admin_token: str, monkeypatch):
    agent_id = await _onboard(client, admin_token)
    await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={"apiKey": "revel-live-key-XXXX7F2A"},
        headers=_auth(admin_token),
    )
    devices = [
        {
            "id": "dev-1",
            "name": "Display1",
            "isOnline": True,
            "tags": ["home", "photos"],
        }
    ]
    mocked = AsyncMock(return_value=devices)
    monkeypatch.setattr("careconnect_api.routers.integrations.list_devices", mocked)
    mutate = AsyncMock(side_effect=AssertionError("must not mutate"))
    monkeypatch.setattr("careconnect_api.revel_client.apply_device_tags", mutate)

    resp = await client.post(
        f"/api/agent/{agent_id}/integrations/revel/discover",
        headers=_auth(admin_token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["ok"] is True
    names = [d["name"] for d in body["data"]["discoveredDevices"]]
    assert names == ["Display1"]
    assert "home" in body["data"]["discoveredTags"]
    _assert_no_secrets(body)
    mocked.assert_awaited()
    mutate.assert_not_called()


# ---------- internal command ----------


@pytest.mark.asyncio
async def test_internal_revel_command_unmatched_does_not_execute(
    client: AsyncClient, admin_token: str, monkeypatch
):
    agent_id = await _onboard(client, admin_token)
    await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={
            "apiKey": "revel-live-key-XXXX7F2A",
            "actions": _actions(("display_calendar", "show my calendar")),
        },
        headers=_auth(admin_token),
    )
    mutate = AsyncMock(side_effect=AssertionError("must not mutate"))
    monkeypatch.setattr("careconnect_api.revel_client.apply_device_tags", mutate)

    resp = await client.post(
        "/api/internal/revel/command",
        json={
            "agentId": agent_id,
            "utterance": "Bob, what's for lunch?",
            "remainder": "what's for lunch?",
        },
        headers={"X-Internal-Token": settings.internal_token},
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["matched"] is False
    assert body["data"]["executed"] is False
    mutate.assert_not_called()


@pytest.mark.asyncio
async def test_internal_revel_command_matched_does_not_mutate(
    client: AsyncClient, admin_token: str, monkeypatch
):
    agent_id = await _onboard(client, admin_token)
    await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={
            "apiKey": "revel-live-key-XXXX7F2A",
            "deviceId": "dev-1",
            "deviceName": "Display1",
            "actions": _actions(("display_calendar", "show my calendar")),
        },
        headers=_auth(admin_token),
    )
    mutate = AsyncMock(side_effect=AssertionError("must not mutate"))
    monkeypatch.setattr("careconnect_api.revel_client.apply_device_tags", mutate)

    resp = await client.post(
        "/api/internal/revel/command",
        json={
            "agentId": agent_id,
            "utterance": "Bob, show my calendar",
            "remainder": "show my calendar",
        },
        headers={"X-Internal-Token": settings.internal_token},
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["matched"] is True
    assert body["data"]["executed"] is False
    assert body["data"]["intent"] == "display_calendar"
    assert body["data"]["reason"] == "execute_not_enabled"
    _assert_no_secrets(body)
    mutate.assert_not_called()


@pytest.mark.asyncio
async def test_internal_revel_command_requires_token(client: AsyncClient, admin_token: str):
    agent_id = await _onboard(client, admin_token)
    resp = await client.post(
        "/api/internal/revel/command",
        json={"agentId": agent_id, "utterance": "x", "remainder": "show my calendar"},
    )
    assert resp.json()["code"] == 401


# ---------- timeline ----------


def test_revel_timeline_encode_parse_and_not_gcal():
    content = encode_revel_timeline(
        requested="Bob, show my calendar",
        intent="display_calendar",
        device_name="Display1",
        result="delivered",
        delivered_at="2026-09-25T00:00:00+00:00",
    )
    assert content.startswith("[[revel]]")
    parsed = parse_revel_timeline(content)
    assert parsed is not None
    assert parsed["intent"] == "display_calendar"
    assert parsed["deviceName"] == "Display1"
    assert parsed["result"] == "delivered"
    assert parsed["requested"] == "Bob, show my calendar"
    assert parse_gcal_timeline(content) is None
    assert "apiKey" not in content
    assert "secret" not in content
    dialogue = _render_dialogue(
        [AiAgentChatHistory(chat_type=3, content=content)]
    )
    assert dialogue.startswith("display_action:")
    assert "calendar_reminder:" not in dialogue


@pytest.mark.asyncio
async def test_persist_revel_timeline_system_event(
    db_session: AsyncSession, client: AsyncClient, admin_token: str
):
    _ = client
    agent_id = await _onboard(client, admin_token)
    payload = await persist_revel_timeline(
        db_session,
        agent_id=agent_id,
        requested="Bob, show my calendar",
        intent="display_calendar",
        device_name="Display1",
        result="failed",
        delivered_at=datetime.now(timezone.utc),
    )
    await db_session.commit()
    assert payload is not None
    assert payload["chatType"] == CHAT_TYPE_SYSTEM
    parsed = parse_revel_timeline(payload["content"])
    assert parsed["result"] == "failed"
    assert parsed["intent"] == "display_calendar"


def test_sanitize_revel_api_key_strips_paste_artifacts():
    raw = '  \ufeff"secret-key-value"  \n'
    assert sanitize_revel_api_key(raw) == "secret-key-value"
    assert sanitize_revel_api_key("Bearer secret-key-value") == "secret-key-value"
    assert sanitize_revel_api_key("X-RevelDigital-ApiKey: secret-key-value") == "secret-key-value"
    assert (
        sanitize_revel_api_key("https://api.reveldigital.com/account?api_key=secret-key-value")
        == "secret-key-value"
    )
    assert key_shape("eyJhbGciOiJIUzI1NiJ9.e30.abc") == "jwt_like"


@pytest.mark.asyncio
async def test_list_devices_401_uses_documented_header_and_safe_error(monkeypatch, caplog):
    captured: dict = {}

    class FakeResp:
        status_code = 401
        text = "Unauthorized"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            return FakeResp()

    monkeypatch.setattr("careconnect_api.revel_client.httpx.AsyncClient", FakeClient)
    secret = "super-secret-key-value-XXXX"
    caplog.set_level("WARNING")
    with pytest.raises(APIException) as exc:
        await list_devices(secret, "https://api.reveldigital.com")
    assert exc.value.code == 401
    assert exc.value.msg.startswith("Revel authentication failed (401)")
    assert captured["url"] == "https://api.reveldigital.com/devices"
    assert captured["headers"][AUTH_HEADER] == secret
    assert "Authorization" not in captured["headers"]
    assert "api_key=" not in captured["url"]
    blob = caplog.text
    assert "header_name=X-RevelDigital-ApiKey" in blob
    assert "base_url=https://api.reveldigital.com" in blob
    assert "key_present=True" in blob
    assert "decrypted=True" in blob
    assert secret not in blob
    assert secret not in (exc.value.msg or "")
    assert secret not in json.dumps(exc.value.data)


@pytest.mark.asyncio
async def test_put_quoted_key_decrypts_trimmed(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    quoted = '"revel-live-key-XXXX7F2A"'
    saved = await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={"apiKey": quoted},
        headers=_auth(admin_token),
    )
    assert saved.json()["code"] == 0, saved.json()
    db_session.expire_all()
    row = (
        await db_session.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == "revel",
            )
        )
    ).scalar_one()
    assert row.secret_enc
    assert decrypt_secret(row.secret_enc) == "revel-live-key-XXXX7F2A"
    assert quoted not in (row.secret_enc or "")


@pytest.mark.asyncio
async def test_discover_401_is_admin_visible(client: AsyncClient, admin_token: str, monkeypatch):
    agent_id = await _onboard(client, admin_token)
    await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={"apiKey": "revel-live-key-XXXX7F2A"},
        headers=_auth(admin_token),
    )

    async def _boom(*_a, **_k):
        raise APIException(401, "Revel authentication failed (401)")

    monkeypatch.setattr("careconnect_api.routers.integrations.list_devices", _boom)
    resp = await client.post(
        f"/api/agent/{agent_id}/integrations/revel/discover",
        headers=_auth(admin_token),
    )
    body = resp.json()
    assert body["code"] == 401
    assert "Revel authentication failed (401)" in body["msg"]
    _assert_no_secrets(body)
