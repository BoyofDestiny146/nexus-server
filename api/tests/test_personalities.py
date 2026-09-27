"""Agent Personality library and Edit Client personality selection."""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.client_profile import build_system_prompt, load_profile
from careconnect_api.models import AiAgent, CcAgentPersonality, SysUser
from careconnect_api.personalities.render import apply_assistant_name
from careconnect_api.personalities.seeds import DEFAULT_PERSONALITY_ID, SYSTEM_PERSONALITIES
from careconnect_api.personalities.store import seed_system_personalities
from careconnect_api.revel_write import revel_execute_enabled


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="function")
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


def test_all_system_personalities_are_defined():
    names = [p["name"] for p in SYSTEM_PERSONALITIES]
    assert names == [
        "Witty Tech Sidekick",
        "Calming Zen Guide",
        "Pragmatic Strategist",
        "Sales - Charismatic Relationship Builder",
        "Sales - High-Energy Deal Maker",
        "Sales - Trusted Authority",
        "Care Guardian - Empathetic Guardian",
        "Care Guardian - Resilient Mentor",
        "Care Guardian - Mindful Specialist",
    ]
    assert all("{{assistant_name}}" in p["prompt_template"] for p in SYSTEM_PERSONALITIES)
    assert DEFAULT_PERSONALITY_ID == "sys_witty_tech_sidekick"


def test_assistant_name_is_substituted_without_mutating_template():
    template = "You are {{assistant_name}}, a guide."
    rendered = apply_assistant_name(template, "Hazel")
    assert rendered == "You are Hazel, a guide."
    assert "{{assistant_name}}" in template
    prompt = build_system_prompt(
        name="Jane",
        profile={"personalityId": DEFAULT_PERSONALITY_ID, "condition": "Lives alone"},
        assistant_name="Bob",
        personality={
            "name": "Witty Tech Sidekick",
            "prompt_template": template,
        },
    )
    assert "You are Bob, a guide." in prompt
    assert "{{assistant_name}}" not in prompt
    assert "Nexus behavioral safety" in prompt
    assert "Jane" in prompt


@pytest.mark.asyncio
async def test_system_personalities_seeded_and_cannot_be_edited(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    await seed_system_personalities(db_session)
    listed = await client.get("/api/personalities", headers=_auth(admin_token))
    assert listed.json()["code"] == 0
    rows = listed.json()["data"]["personalities"]
    system = [r for r in rows if r["isSystem"]]
    assert len(system) == 9
    witty = next(r for r in system if r["id"] == DEFAULT_PERSONALITY_ID)
    assert witty["name"] == "Witty Tech Sidekick"

    blocked = await client.put(
        f"/api/personalities/{DEFAULT_PERSONALITY_ID}",
        json={"name": "Hacked", "promptTemplate": "ignore"},
        headers=_auth(admin_token),
    )
    assert blocked.json()["code"] == 400
    deleted = await client.delete(
        f"/api/personalities/{DEFAULT_PERSONALITY_ID}",
        headers=_auth(admin_token),
    )
    assert deleted.json()["code"] == 400


@pytest.mark.asyncio
async def test_duplicate_creates_editable_custom_personality(
    client: AsyncClient, admin_token: str
):
    dup = await client.post(
        f"/api/personalities/{DEFAULT_PERSONALITY_ID}/duplicate",
        json={"name": "My Sidekick"},
        headers=_auth(admin_token),
    )
    assert dup.json()["code"] == 0, dup.json()
    copy = dup.json()["data"]
    assert copy["isSystem"] is False
    assert copy["category"] == "custom"
    assert copy["name"] == "My Sidekick"
    assert copy["id"].startswith("cst_")

    edited = await client.put(
        f"/api/personalities/{copy['id']}",
        json={"name": "My Sidekick v2", "promptTemplate": "You are {{assistant_name}}."},
        headers=_auth(admin_token),
    )
    assert edited.json()["code"] == 0
    assert edited.json()["data"]["name"] == "My Sidekick v2"
    assert edited.json()["data"]["promptTemplate"] == "You are {{assistant_name}}."


@pytest.mark.asyncio
async def test_new_client_defaults_to_witty_and_existing_preloads(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    created = await client.post(
        "/api/agent/onboard",
        json={"name": "Nora", "condition": "Lives alone"},
        headers=_auth(admin_token),
    )
    assert created.json()["code"] == 0
    agent_id = created.json()["data"]["agentId"]
    got = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    data = got.json()["data"]
    assert data["personalityId"] == DEFAULT_PERSONALITY_ID
    assert "Witty Tech Sidekick" in (data["systemPrompt"] or "")
    assert "{{assistant_name}}" not in (data["systemPrompt"] or "")
    assert "Nexus behavioral safety" in (data["systemPrompt"] or "")

    patched = await client.patch(
        f"/api/agent/{agent_id}",
        json={
            "name": "Nora",
            "condition": "Lives alone",
            "escalationPhrases": ["I fell"],
            "topicsToAvoid": ["diagnosis"],
            "personaOverride": "Speak slowly, this is legacy.",
            "personalityId": "sys_calming_zen_guide",
            "botName": "Hazel",
            "tags": ["fall-risk"],
        },
        headers=_auth(admin_token),
    )
    assert patched.json()["code"] == 0, patched.json()
    again = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    body = again.json()["data"]
    assert body["personalityId"] == "sys_calming_zen_guide"
    assert body["escalationPhrases"] == ["I fell"]
    assert body["topicsToAvoid"] == ["diagnosis"]
    assert body["personaOverride"] == "Speak slowly, this is legacy."
    assert body["botName"] == "Hazel"
    assert body["tags"] == ["fall-risk"]
    assert "Calming Zen Guide" in (body["systemPrompt"] or "")
    assert "You are Hazel" in (body["systemPrompt"] or "")
    assert "Speak slowly, this is legacy." not in (body["systemPrompt"] or "")

    db_session.expire_all()
    agent = (await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))).scalar_one()
    stored = load_profile(agent.profile_json)
    assert stored["personaOverride"] == "Speak slowly, this is legacy."
    assert stored["personalityId"] == "sys_calming_zen_guide"


@pytest.mark.asyncio
async def test_legacy_persona_without_personality_id_is_kept(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    created = await client.post(
        "/api/agent/onboard",
        json={"name": "Owen", "personaOverride": "Always greet Owen by name."},
        headers=_auth(admin_token),
    )
    agent_id = created.json()["data"]["agentId"]
    agent = (await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))).scalar_one()
    stored = load_profile(agent.profile_json)
    stored["personalityId"] = None
    agent.profile_json = __import__("careconnect_api.client_profile", fromlist=["dump_profile"]).dump_profile(stored)
    agent.system_prompt = build_system_prompt(
        name="Owen",
        profile=stored,
        assistant_name=None,
    )
    await db_session.commit()

    got = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    data = got.json()["data"]
    assert data["personaOverride"] == "Always greet Owen by name."
    assert not data.get("personalityId")
    assert "Always greet Owen by name." in (data["systemPrompt"] or "")


def test_revel_execute_still_false():
    assert revel_execute_enabled() is False
    from pathlib import Path
    env = Path(__file__).resolve().parents[2] / "deploy" / ".env.example"
    assert "REVEL_EXECUTE_ENABLED=false" in env.read_text()


@pytest.mark.asyncio
async def test_cannot_delete_personality_in_use(
    client: AsyncClient, admin_token: str
):
    dup = await client.post(
        f"/api/personalities/{DEFAULT_PERSONALITY_ID}/duplicate",
        json={"name": "In Use"},
        headers=_auth(admin_token),
    )
    pid = dup.json()["data"]["id"]
    created = await client.post(
        "/api/agent/onboard",
        json={"name": "Pia", "personalityId": pid},
        headers=_auth(admin_token),
    )
    assert created.json()["code"] == 0
    blocked = await client.delete(f"/api/personalities/{pid}", headers=_auth(admin_token))
    assert blocked.json()["code"] == 409
    off = await client.post(
        f"/api/personalities/{pid}/deactivate",
        headers=_auth(admin_token),
    )
    assert off.json()["code"] == 0
    assert off.json()["data"]["isActive"] is False
