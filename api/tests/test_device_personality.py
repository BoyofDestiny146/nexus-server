"""Per-device Agent Personality override: MariaDB, RBAC, not voice_config.json."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ADMIN, ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import AiDevice, AiMedicalAssessment, SysUser
from careconnect_api.personalities.safety import NEXUS_SAFETY_LAYER
from careconnect_api.personalities.seeds import DEFAULT_PERSONALITY_ID
from careconnect_api.personalities.store import count_personality_usage
from careconnect_api.settings import settings
from careconnect_api.watcher_device import device_id_for_mac

_API_KEY = "test-api-key-abc123"
_MAC_BOB = "E0:72:A1:DB:36:40"
_MAC_BETTY = "E0:72:A1:DB:36:41"
_CARE_ID = "sys_care_empathetic_guardian"
API_DIR = Path(__file__).resolve().parents[1]
MIGRATION = (API_DIR / "migrations" / "029_device_personality.sql").read_text()
VERIFY = (API_DIR / "migrations" / "029_device_personality.verify.sql").read_text()
VOICE_SRC = (API_DIR / "careconnect_api" / "routers" / "voice.py").read_text()
RUNNER_SRC = (API_DIR / "careconnect_api" / "triage" / "runner.py").read_text()
REVEL_SRC = (API_DIR / "careconnect_api" / "revel_write.py").read_text()


@pytest_asyncio.fixture(scope="function")
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    _ = client
    user = SysUser(
        id=1,
        username="admin1",
        password=hash_password("AdminPassword1!"),
        super_admin=ROLE_ROOT,
        status=1,
    )
    db_session.add(user)
    await db_session.commit()
    token, _expire = issue_token(user.id, user.username, ROLE_ROOT)
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _onboard(client: AsyncClient, token: str, name: str) -> str:
    resp = await client.post(
        "/api/agent/onboard", json={"name": name}, headers=_auth(token)
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["agentId"]


async def _register(client: AsyncClient, mac: str) -> None:
    resp = await client.post(
        "/api/v1/watcher/heartbeat",
        json={"mac": mac, "battery": 80, "fw": "1.9.0", "rssi": -40},
        headers={"X-API-Key": _API_KEY},
    )
    assert resp.json()["code"] == 0, resp.json()


async def _attach(client: AsyncClient, token: str, mac: str, agent_id: str) -> None:
    resp = await client.post(
        "/api/device/attach",
        json={"eui": mac, "agentId": agent_id},
        headers=_auth(token),
    )
    assert resp.json()["code"] == 0, resp.json()


def test_migration_029_is_additive_nullable():
    assert "ALTER TABLE ai_device" in MIGRATION
    assert "ADD COLUMN IF NOT EXISTS personality_id VARCHAR(64) NULL" in MIGRATION
    sql_lines = [
        ln for ln in MIGRATION.splitlines() if ln.strip() and not ln.strip().startswith("--")
    ]
    assert any("ADD COLUMN IF NOT EXISTS personality_id" in ln for ln in sql_lines)
    assert all("DROP" not in ln for ln in sql_lines)
    assert all("voice_config" not in ln for ln in sql_lines)
    assert "IF NOT EXISTS" in MIGRATION
    assert "does NOT require dropping" in MIGRATION
    assert "personality_id" in VERIFY
    assert "personality_id" not in VOICE_SRC
    assert "personality_id" not in RUNNER_SRC
    assert "REVEL_EXECUTE" in REVEL_SRC


@pytest.mark.asyncio
async def test_two_devices_same_client_can_differ(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, "Household")
    await _register(client, _MAC_BOB)
    await _register(client, _MAC_BETTY)
    await _attach(client, admin_token, _MAC_BOB, agent_id)
    await _attach(client, admin_token, _MAC_BETTY, agent_id)

    bob = await client.put(
        f"/api/device/{_MAC_BOB}/personality",
        json={"personalityId": DEFAULT_PERSONALITY_ID},
        headers=_auth(admin_token),
    )
    betty = await client.put(
        f"/api/device/{_MAC_BETTY}/personality",
        json={"personalityId": _CARE_ID},
        headers=_auth(admin_token),
    )
    assert bob.json()["code"] == 0, bob.json()
    assert betty.json()["code"] == 0, betty.json()
    assert bob.json()["data"]["personalityId"] == DEFAULT_PERSONALITY_ID
    assert betty.json()["data"]["personalityId"] == _CARE_ID
    assert bob.json()["data"]["source"] == "device"
    assert betty.json()["data"]["clientPersonalityId"] == DEFAULT_PERSONALITY_ID

    inherit = await client.put(
        f"/api/device/{_MAC_BOB}/personality",
        json={"personalityId": None},
        headers=_auth(admin_token),
    )
    assert inherit.json()["code"] == 0
    assert inherit.json()["data"]["personalityId"] is None
    assert inherit.json()["data"]["source"] == "client"

    db_session.expire_all()
    betty_row = await db_session.get(AiDevice, device_id_for_mac(_MAC_BETTY))
    assert betty_row is not None
    assert betty_row.personality_id == _CARE_ID


@pytest.mark.asyncio
async def test_personality_put_does_not_write_voice_config(
    client: AsyncClient, admin_token: str, tmp_path, monkeypatch
):
    path = tmp_path / "voice_config.json"
    path.write_text(json.dumps({"default": {}, "devices": {}}))
    monkeypatch.setattr(settings, "voice_config_path", str(path))
    await _register(client, _MAC_BOB)
    resp = await client.put(
        f"/api/device/{_MAC_BOB}/personality",
        json={"personalityId": _CARE_ID},
        headers=_auth(admin_token),
    )
    assert resp.json()["code"] == 0, resp.json()
    saved = json.loads(path.read_text())
    blob = json.dumps(saved)
    assert "personality" not in blob
    assert _CARE_ID not in blob


@pytest.mark.asyncio
async def test_inactive_and_unknown_personality_rejected(
    client: AsyncClient, admin_token: str
):
    await _register(client, _MAC_BOB)
    missing = await client.put(
        f"/api/device/{_MAC_BOB}/personality",
        json={"personalityId": "cst_does_not_exist"},
        headers=_auth(admin_token),
    )
    assert missing.json()["code"] == 400
    dup = await client.post(
        f"/api/personalities/{DEFAULT_PERSONALITY_ID}/duplicate",
        json={"name": "Soon Off"},
        headers=_auth(admin_token),
    )
    pid = dup.json()["data"]["id"]
    await client.post(f"/api/personalities/{pid}/deactivate", headers=_auth(admin_token))
    inactive = await client.put(
        f"/api/device/{_MAC_BOB}/personality",
        json={"personalityId": pid},
        headers=_auth(admin_token),
    )
    assert inactive.json()["code"] == 400


@pytest.mark.asyncio
async def test_device_assignment_blocks_delete(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    dup = await client.post(
        f"/api/personalities/{DEFAULT_PERSONALITY_ID}/duplicate",
        json={"name": "Device Only"},
        headers=_auth(admin_token),
    )
    pid = dup.json()["data"]["id"]
    await _register(client, _MAC_BOB)
    assigned = await client.put(
        f"/api/device/{_MAC_BOB}/personality",
        json={"personalityId": pid},
        headers=_auth(admin_token),
    )
    assert assigned.json()["code"] == 0, assigned.json()
    used = await count_personality_usage(db_session, pid)
    assert used >= 1
    blocked = await client.delete(f"/api/personalities/{pid}", headers=_auth(admin_token))
    assert blocked.json()["code"] == 409
    assert "devices" in blocked.json()["msg"]


@pytest.mark.asyncio
async def test_session_persona_uses_device_then_client(
    client: AsyncClient, admin_token: str
):
    agent_id = await _onboard(client, admin_token, "Betty Client")
    await _register(client, _MAC_BETTY)
    await _attach(client, admin_token, _MAC_BETTY, agent_id)
    await client.put(
        f"/api/device/{_MAC_BETTY}/personality",
        json={"personalityId": _CARE_ID},
        headers=_auth(admin_token),
    )
    headers = {"X-Internal-Token": settings.internal_token}
    overridden = await client.get(
        f"/api/internal/device/{_MAC_BETTY}/session-persona",
        headers=headers,
    )
    body = overridden.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["personalitySource"] == "device"
    assert data["personalityId"] == _CARE_ID
    assert data["agent_id"] == agent_id
    assert "Agent Personality" in data["system_prompt"]
    assert "Betty" in data["system_prompt"] or "Betty" in (data["agent_name"] or "")
    assert "ENGLISH-ONLY" in data["system_prompt"]

    await client.put(
        f"/api/device/{_MAC_BETTY}/personality",
        json={"personalityId": None},
        headers=_auth(admin_token),
    )
    inherited = await client.get(
        f"/api/internal/device/{_MAC_BETTY}/session-persona",
        headers=headers,
    )
    assert inherited.json()["data"]["personalitySource"] == "client"
    assert inherited.json()["data"]["system_prompt"]


@pytest.mark.asyncio
async def test_device_override_preserves_identity_safety_and_guardrails(
    client: AsyncClient, admin_token: str
):
    created = await client.post(
        "/api/agent/onboard",
        json={
            "name": "Betty Household",
            "condition": "evening medication reminders",
            "escalationPhrases": ["I fell"],
            "topicsToAvoid": ["diagnosis"],
            "botName": "AdaBot",
            "personalityId": DEFAULT_PERSONALITY_ID,
        },
        headers=_auth(admin_token),
    )
    assert created.json()["code"] == 0, created.json()
    agent_id = created.json()["data"]["agentId"]
    detail = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    client_prompt = detail.json()["data"]["systemPrompt"]
    assert client_prompt
    await _register(client, _MAC_BOB)
    await _register(client, _MAC_BETTY)
    await _attach(client, admin_token, _MAC_BOB, agent_id)
    await _attach(client, admin_token, _MAC_BETTY, agent_id)
    await client.put(
        f"/api/device/{_MAC_BOB}/personality",
        json={"personalityId": DEFAULT_PERSONALITY_ID},
        headers=_auth(admin_token),
    )
    await client.put(
        f"/api/device/{_MAC_BETTY}/personality",
        json={"personalityId": _CARE_ID},
        headers=_auth(admin_token),
    )
    headers = {"X-Internal-Token": settings.internal_token}
    bob = (
        await client.get(
            f"/api/internal/device/{_MAC_BOB}/session-persona", headers=headers
        )
    ).json()["data"]
    betty = (
        await client.get(
            f"/api/internal/device/{_MAC_BETTY}/session-persona", headers=headers
        )
    ).json()["data"]

    for data, heading in (
        (bob, "Witty Tech Sidekick"),
        (betty, "Empathetic Guardian"),
    ):
        prompt = data["system_prompt"]
        assert data["agent_id"] == agent_id
        assert data["agent_name"] == "Betty Household"
        assert data["first_name"] == "Betty"
        assert data["bot_name"] == "AdaBot"
        assert data["personalitySource"] == "device"
        assert "ENGLISH-ONLY" in prompt
        assert "cannot be overridden" in prompt
        assert NEXUS_SAFETY_LAYER.strip() in prompt
        assert "AdaBot" in prompt
        assert "{{assistant_name}}" not in prompt
        assert "Betty Household" in prompt
        assert "diagnosis" in prompt
        assert "I fell" in prompt
        assert heading in prompt
        assert "# WHO YOU ARE" not in prompt  # identity block is added by XiaoZhi, not API

    assert "Let's crack the code" in bob["system_prompt"]
    assert "gentle anchor" in betty["system_prompt"]
    assert "Let's crack the code" not in betty["system_prompt"]
    assert "gentle anchor" not in bob["system_prompt"]

    await client.put(
        f"/api/device/{_MAC_BETTY}/personality",
        json={"personalityId": None},
        headers=_auth(admin_token),
    )
    inherited = (
        await client.get(
            f"/api/internal/device/{_MAC_BETTY}/session-persona", headers=headers
        )
    ).json()["data"]
    assert inherited["personalitySource"] == "client"
    assert inherited["system_prompt"] == client_prompt
    assert inherited["bot_name"] == "AdaBot"
    assert "Witty Tech Sidekick" in inherited["system_prompt"]


@pytest.mark.asyncio
async def test_unbound_device_override_composes_without_client(
    client: AsyncClient, admin_token: str
):
    await _register(client, _MAC_BOB)
    resp = await client.put(
        f"/api/device/{_MAC_BOB}/personality",
        json={"personalityId": DEFAULT_PERSONALITY_ID},
        headers=_auth(admin_token),
    )
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["unbound"] is True
    persona = await client.get(
        f"/api/internal/device/{_MAC_BOB}/session-persona",
        headers={"X-Internal-Token": settings.internal_token},
    )
    data = persona.json()["data"]
    assert data["personalitySource"] == "device"
    assert data["agent_id"] is None
    assert data["system_prompt"]
    assert "ENGLISH-ONLY" in data["system_prompt"]


@pytest.mark.asyncio
async def test_personality_requires_auth(client: AsyncClient):
    await _register(client, _MAC_BOB)
    resp = await client.get(f"/api/device/{_MAC_BOB}/personality")
    assert resp.json()["code"] in (401, 403)


@pytest.mark.asyncio
async def test_get_shows_currently_assigned_personality(
    client: AsyncClient, admin_token: str
):
    await _register(client, _MAC_BOB)
    await client.put(
        f"/api/device/{_MAC_BOB}/personality",
        json={"personalityId": _CARE_ID},
        headers=_auth(admin_token),
    )
    shown = await client.get(
        f"/api/device/{_MAC_BOB}/personality",
        headers=_auth(admin_token),
    )
    assert shown.json()["code"] == 0
    assert shown.json()["data"]["personalityId"] == _CARE_ID
    assert shown.json()["data"]["storedPersonalityId"] == _CARE_ID
    assert shown.json()["data"]["source"] == "device"


@pytest.mark.asyncio
async def test_deleted_personality_inherits_client_prompt(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, "Betty Client")
    await _register(client, _MAC_BETTY)
    await _attach(client, admin_token, _MAC_BETTY, agent_id)
    db_session.expire_all()
    row = await db_session.get(AiDevice, device_id_for_mac(_MAC_BETTY))
    assert row is not None
    row.personality_id = "cst_deleted_missing"
    await db_session.commit()

    assignment = await client.get(
        f"/api/device/{_MAC_BETTY}/personality",
        headers=_auth(admin_token),
    )
    body = assignment.json()
    assert body["code"] == 0, body
    assert body["data"]["personalityUnavailable"] is True
    assert body["data"]["personalityId"] is None
    assert body["data"]["source"] == "client"

    persona = await client.get(
        f"/api/internal/device/{_MAC_BETTY}/session-persona",
        headers={"X-Internal-Token": settings.internal_token},
    )
    assert persona.json()["code"] == 0
    assert persona.json()["data"]["personalitySource"] == "client"
    assert persona.json()["data"]["system_prompt"]
    assert persona.json()["data"]["agent_id"] == agent_id


@pytest.mark.asyncio
async def test_missing_device_personality_is_404(
    client: AsyncClient, admin_token: str
):
    resp = await client.get(
        "/api/device/aa:bb:cc:dd:ee:00/personality",
        headers=_auth(admin_token),
    )
    assert resp.json()["code"] == 404


@pytest.mark.asyncio
async def test_session_persona_requires_internal_token(client: AsyncClient):
    await _register(client, _MAC_BOB)
    missing = await client.get(f"/api/internal/device/{_MAC_BOB}/session-persona")
    assert missing.json()["code"] == 401
    wrong = await client.get(
        f"/api/internal/device/{_MAC_BOB}/session-persona",
        headers={"X-Internal-Token": "not-the-token"},
    )
    assert wrong.json()["code"] == 401


@pytest.mark.asyncio
async def test_non_root_cannot_assign_unbound_personality(
    client: AsyncClient, db_session: AsyncSession
):
    user = SysUser(
        id=2,
        username="scoped",
        password=hash_password("ScopedPassword1!"),
        super_admin=ROLE_ADMIN,
        status=1,
    )
    db_session.add(user)
    await db_session.commit()
    token, _expire = issue_token(user.id, user.username, ROLE_ADMIN)
    await _register(client, _MAC_BOB)
    resp = await client.put(
        f"/api/device/{_MAC_BOB}/personality",
        json={"personalityId": DEFAULT_PERSONALITY_ID},
        headers=_auth(token),
    )
    assert resp.json()["code"] == 403


@pytest.mark.asyncio
async def test_assessment_tables_untouched_by_device_personality():
    assert "ai_medical_assessment" not in MIGRATION
    assert "cc_assessment_delivery" not in MIGRATION
    assert AiMedicalAssessment.__tablename__ == "ai_medical_assessment"
