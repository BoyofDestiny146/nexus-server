"""Structured client profile round-trip and Edit Client PATCH."""
from __future__ import annotations

from datetime import datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.client_profile import (
    build_system_prompt,
    dump_profile,
    effective_profile,
    load_profile,
    merge_profile,
    parse_profile_from_prompt,
)
from careconnect_api.models import AiAgent, AiAgentChatHistory, ClientIntegration, SysUser


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="function")
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token

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


def test_generated_prompt_round_trips_structured_fields():
    profile = {
        "dob": "1948-03-12",
        "age": 76,
        "condition": "Recovering from hip replacement",
        "tags": ["fall-risk", "low-sodium"],
        "escalationPhrases": ["I fell", "Chest pain"],
        "topicsToAvoid": ["medical diagnosis"],
        "personaOverride": None,
    }
    prompt = build_system_prompt(name="Jane Smith", profile=profile)
    parsed = parse_profile_from_prompt(prompt)
    assert parsed["condition"] == "Recovering from hip replacement"
    assert parsed["tags"] == ["fall-risk", "low-sodium"]
    assert parsed["escalationPhrases"] == ["I fell", "Chest pain"]
    assert parsed["topicsToAvoid"] == ["medical diagnosis"]
    assert not parsed["personaOverride"]
    assert parsed["dob"] is None
    assert parsed["age"] is None


def test_operator_override_is_not_destroyed_by_parse():
    prompt = build_system_prompt(
        name="George",
        profile={"personaOverride": "Speak slower than usual.\nAvoid jargon."},
    )
    parsed = parse_profile_from_prompt(prompt)
    assert parsed["personaOverride"] == "Speak slower than usual.\nAvoid jargon."


def test_hand_edited_prompt_becomes_persona_override():
    parsed = parse_profile_from_prompt("Be a cheerful companion and never mention hospitals.")
    assert parsed["personaOverride"] == "Be a cheerful companion and never mention hospitals."


def test_merge_preserves_unknown_keys():
    existing = load_profile({"condition": "diabetes", "extraFlag": True})
    merged = merge_profile(existing, {"condition": "diabetes, fall-risk"})
    assert merged["condition"] == "diabetes, fall-risk"
    assert merged["extraFlag"] is True
    assert "extraFlag" in dump_profile(merged)


def test_effective_profile_keeps_hand_edited_prompt_when_json_has_condition():
    stored = dump_profile({"condition": "diabetes", "tags": ["fall-risk"]})
    prompt = "Always greet Cara by name and never discuss finances."
    profile = effective_profile(stored, prompt)
    assert profile["condition"] == "diabetes"
    assert profile["tags"] == ["fall-risk"]
    assert profile["personaOverride"] == prompt


def test_effective_profile_does_not_invent_persona_from_generated_prompt():
    profile = {
        "condition": "diabetes",
        "tags": ["fall-risk"],
        "personaOverride": None,
    }
    prompt = build_system_prompt(name="Cara", profile=profile)
    loaded = effective_profile(dump_profile(profile), prompt)
    assert loaded["condition"] == "diabetes"
    assert not loaded["personaOverride"]


@pytest.mark.asyncio
async def test_onboard_and_patch_profile_round_trip(client: AsyncClient, admin_token: str):
    created = await client.post(
        "/api/agent/onboard",
        json={
            "name": "Ada Lovelace",
            "dob": "1948-03-12",
            "age": 76,
            "condition": "Recovering from hip replacement",
            "tags": ["fall-risk"],
            "escalationPhrases": ["I fell"],
            "topicsToAvoid": ["diagnosis"],
            "personaOverride": "Speak slowly.",
            "botName": "AdaBot",
        },
        headers=_auth(admin_token),
    )
    assert created.json()["code"] == 0, created.json()
    agent_id = created.json()["data"]["agentId"]

    got = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    data = got.json()["data"]
    assert data["agentName"] == "Ada Lovelace"
    assert data["botName"] == "AdaBot"
    assert data["dob"] == "1948-03-12"
    assert data["age"] == 76
    assert data["condition"] == "Recovering from hip replacement"
    assert data["tags"] == ["fall-risk"]
    assert data["escalationPhrases"] == ["I fell"]
    assert data["topicsToAvoid"] == ["diagnosis"]
    assert data["personaOverride"] == "Speak slowly."
    assert "Speak slowly." in (data["systemPrompt"] or "")

    patched = await client.patch(
        f"/api/agent/{agent_id}",
        json={
            "name": "Ada L.",
            "dob": "1948-03-12",
            "age": 77,
            "condition": "Lives alone; evening medication reminders",
            "tags": ["fall-risk", "medication-reminder"],
            "escalationPhrases": ["I fell", "I need help"],
            "topicsToAvoid": ["diagnosis"],
            "personaOverride": "Speak slowly.",
            "botName": "AdaBot",
        },
        headers=_auth(admin_token),
    )
    assert patched.json()["code"] == 0, patched.json()
    assert patched.json()["data"]["agentId"] == agent_id

    again = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    data = again.json()["data"]
    assert data["id"] == agent_id
    assert data["agentName"] == "Ada L."
    assert data["age"] == 77
    assert data["condition"] == "Lives alone; evening medication reminders"
    assert data["tags"] == ["fall-risk", "medication-reminder"]
    assert data["escalationPhrases"] == ["I fell", "I need help"]
    assert data["personaOverride"] == "Speak slowly."
    assert data["botName"] == "AdaBot"


@pytest.mark.asyncio
async def test_patch_profile_does_not_touch_integrations_or_prompt_when_only_bot_name(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    created = await client.post(
        "/api/agent/onboard",
        json={"name": "Bea", "condition": "diabetes"},
        headers=_auth(admin_token),
    )
    agent_id = created.json()["data"]["agentId"]
    await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={"apiKey": "developer-api-key-value"},
        headers=_auth(admin_token),
    )
    before = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    prompt_before = before.json()["data"]["systemPrompt"]

    patched = await client.patch(
        f"/api/agent/{agent_id}",
        json={"botName": "BeaBot"},
        headers=_auth(admin_token),
    )
    assert patched.json()["code"] == 0
    after = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    assert after.json()["data"]["botName"] == "BeaBot"
    assert after.json()["data"]["systemPrompt"] == prompt_before
    assert after.json()["data"]["condition"] == "diabetes"

    integ = await client.get(f"/api/agent/{agent_id}/integrations", headers=_auth(admin_token))
    revel = next(i for i in integ.json()["data"]["list"] if i["provider"] == "revel")
    assert revel["connected"] is True

    row = (
        await db_session.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == "revel",
            )
        )
    ).scalar_one()
    assert row.secret_enc


@pytest.mark.asyncio
async def test_legacy_system_prompt_is_loaded_as_persona_override(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    created = await client.post(
        "/api/agent/onboard",
        json={"name": "Cara"},
        headers=_auth(admin_token),
    )
    agent_id = created.json()["data"]["agentId"]
    agent = (
        await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))
    ).scalar_one()
    agent.profile_json = None
    agent.system_prompt = "Always greet Cara by name and never discuss finances."
    await db_session.commit()

    got = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    assert got.json()["data"]["personaOverride"] == (
        "Always greet Cara by name and never discuss finances."
    )


@pytest.mark.asyncio
async def test_patch_keeps_unknown_profile_keys_chat_and_same_agent(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    created = await client.post(
        "/api/agent/onboard",
        json={"name": "Dana", "condition": "diabetes", "botName": "DanaBot"},
        headers=_auth(admin_token),
    )
    agent_id = created.json()["data"]["agentId"]
    agent = (
        await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))
    ).scalar_one()
    stored = load_profile(agent.profile_json)
    stored["extraFlag"] = True
    agent.profile_json = dump_profile(stored)
    db_session.add(
        AiAgentChatHistory(
            id=101,
            mac_address="AA:BB:CC:DD:EE:FF",
            agent_id=agent_id,
            session_id="sess-edit",
            chat_type=1,
            content="hello from dana",
            created_at=datetime(2026, 9, 22, 12, 0, 0),
            updated_at=datetime(2026, 9, 22, 12, 0, 0),
        )
    )
    await db_session.commit()

    patched = await client.patch(
        f"/api/agent/{agent_id}",
        json={
            "name": "Dana R.",
            "dob": "1940-01-02",
            "age": 86,
            "condition": "Lives alone",
            "tags": ["medication-reminder"],
            "escalationPhrases": ["I need help"],
            "topicsToAvoid": ["finances"],
            "personaOverride": "Speak slowly.",
            "botName": "DanaBot",
        },
        headers=_auth(admin_token),
    )
    assert patched.json()["code"] == 0, patched.json()
    assert patched.json()["data"]["agentId"] == agent_id

    db_session.expire_all()
    agent = (
        await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))
    ).scalar_one()
    assert agent.agent_name == "Dana R."
    assert agent.bot_name == "DanaBot"
    loaded = load_profile(agent.profile_json)
    assert loaded["extraFlag"] is True
    assert loaded["condition"] == "Lives alone"
    assert loaded["personaOverride"] == "Speak slowly."
    chat = (
        await db_session.execute(
            select(AiAgentChatHistory).where(AiAgentChatHistory.agent_id == agent_id)
        )
    ).scalars().all()
    assert len(chat) == 1
    assert chat[0].content == "hello from dana"


@pytest.mark.asyncio
async def test_get_hydrates_old_edit_system_prompt_without_dropping_json_fields(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    created = await client.post(
        "/api/agent/onboard",
        json={"name": "Eli", "condition": "post-op hip", "tags": ["fall-risk"]},
        headers=_auth(admin_token),
    )
    agent_id = created.json()["data"]["agentId"]
    agent = (
        await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))
    ).scalar_one()
    agent.system_prompt = "Be a cheerful companion and never mention hospitals."
    await db_session.commit()

    got = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    data = got.json()["data"]
    assert data["condition"] == "post-op hip"
    assert data["tags"] == ["fall-risk"]
    assert data["personaOverride"] == "Be a cheerful companion and never mention hospitals."
    assert data["id"] == agent_id
