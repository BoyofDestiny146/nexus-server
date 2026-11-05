"""Per-agent Ask Sam model storage. The conversation route is not switched."""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ADMIN, ROLE_ROOT, hash_password, issue_token
from careconnect_api.models import AdminClientAccess, AiAgent, CcVirtualAgentBinding, SysUser
from careconnect_api.routers import agent as agent_router
from careconnect_api.routers import ollama_models as catalog_router
from careconnect_api.routers.health import probe_selected_model
from careconnect_api.routers.virtual_agent_binding import PORTAL_AI_AGENTS_ORGANIZATION_ID
from careconnect_api.settings import settings

CLIENT_ID = "care-client-000000000000000001"
AGENT_ID = "virtual-agent-0000000000000001"
OTHER_ID = "other-agent-000000000000000001"
OTHER_ORG = "other-org-00000000000000000001"


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    _ = client
    db_session.add(SysUser(id=1, username="admin1", password=hash_password("unused"), super_admin=ROLE_ROOT, status=1))
    await db_session.commit()
    token, _expire = issue_token(1, "admin1", ROLE_ROOT)
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _agent(agent_id: str, name: str, org: str, prompt: str = "KEEP-PROMPT") -> AiAgent:
    return AiAgent(
        id=agent_id,
        agent_name=name,
        bot_name=name,
        organization_id=org,
        chat_history_conf=0,
        llm_model_id="LLM_OllamaLLM",
        tts_model_id="TTS_CustomTTS",
        tts_voice_id=None,
        system_prompt=prompt,
        profile_json='{"condition":"steady","personalityId":"sys_witty_tech_sidekick"}',
    )


async def _tags(_client):
    return True, ["server-model", "triage-model", "embed-model", "other-model"]


@pytest.mark.asyncio
async def test_model_discovery_identifies_server_triage_and_embedding(
    client: AsyncClient, admin_token: str, monkeypatch
):
    monkeypatch.setattr(catalog_router, "read_ollama_tags", _tags)
    monkeypatch.setattr(settings, "llm_model", "server-model")
    monkeypatch.setattr(settings, "ollama_triage_model", "triage-model")
    monkeypatch.setattr(settings, "ollama_embed_model", "embed-model")
    denied = await client.get("/api/models/ollama")
    assert denied.json()["code"] == 401
    body = (await client.get("/api/models/ollama", headers=_auth(admin_token))).json()
    assert body["code"] == 0, body
    roles = {item["name"]: item["roles"] for item in body["data"]["models"]}
    assert body["data"]["serverModel"] == "server-model"
    assert roles["server-model"] == ["server-default"]
    assert roles["triage-model"] == ["triage"]
    assert roles["embed-model"] == ["embedding"]
    assert roles["other-model"] == ["available"]


@pytest.mark.asyncio
async def test_save_clear_and_reject_without_touching_voice_or_routing(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(agent_router, "read_ollama_tags", _tags)
    monkeypatch.setattr(settings, "llm_model", "server-model")
    monkeypatch.setattr(settings, "sam_client_id", CLIENT_ID)
    db_session.add_all([
        _agent(CLIENT_ID, "Care Client", PORTAL_AI_AGENTS_ORGANIZATION_ID),
        _agent(AGENT_ID, "Virtual Agent", PORTAL_AI_AGENTS_ORGANIZATION_ID),
        _agent(OTHER_ID, "Bill", OTHER_ORG),
    ])
    db_session.add(CcVirtualAgentBinding(client_id=CLIENT_ID, virtual_agent_id=AGENT_ID, created_by=1))
    await db_session.commit()

    saved = (
        await client.put(
            f"/api/agent/{AGENT_ID}/ask-sam-model",
            json={"askSamModel": "other-model"},
            headers=_auth(admin_token),
        )
    ).json()
    assert saved["code"] == 0, saved
    assert saved["data"]["storedModel"] == "other-model"
    assert saved["data"]["effectiveModel"] is None
    assert saved["data"]["isActiveIntegrationAgent"] is False

    db_session.expire_all()
    row = await db_session.get(AiAgent, AGENT_ID)
    assert row is not None
    assert row.llm_model_id == "LLM_OllamaLLM"
    assert row.tts_model_id == "TTS_CustomTTS"
    assert row.system_prompt == "KEEP-PROMPT"
    assert "other-model" in (row.profile_json or "")
    assert "steady" in (row.profile_json or "")
    assert "sys_witty_tech_sidekick" in (row.profile_json or "")

    detail = (await client.get(f"/api/agent/{AGENT_ID}", headers=_auth(admin_token))).json()["data"]
    assert detail["storedModel"] == "other-model"
    assert detail["serverModel"] == "server-model"
    assert detail["virtualAgent"] is True
    client_detail = (await client.get(f"/api/agent/{CLIENT_ID}", headers=_auth(admin_token))).json()["data"]
    assert client_detail["virtualAgent"] is False
    assert client_detail["isActiveIntegrationAgent"] is True
    assert client_detail["effectiveModel"] == "server-model"
    assert client_detail["storedModel"] is None

    listed = (await client.get("/api/agent/virtual", headers=_auth(admin_token))).json()
    assert [item["id"] for item in listed["data"]] == [AGENT_ID]

    rejected = (
        await client.put(
            f"/api/agent/{AGENT_ID}/ask-sam-model",
            json={"askSamModel": "missing-model"},
            headers=_auth(admin_token),
        )
    ).json()
    assert rejected["code"] == 400

    cleared = (
        await client.put(
            f"/api/agent/{AGENT_ID}/ask-sam-model",
            json={"askSamModel": None},
            headers=_auth(admin_token),
        )
    ).json()
    assert cleared["code"] == 0, cleared
    assert cleared["data"]["storedModel"] is None
    db_session.expire_all()
    cleared_row = await db_session.get(AiAgent, AGENT_ID)
    assert cleared_row is not None
    assert "other-model" not in (cleared_row.profile_json or "")
    assert cleared_row.system_prompt == "KEEP-PROMPT"
    other = await db_session.get(AiAgent, OTHER_ID)
    assert other is not None
    assert "other-model" not in (other.profile_json or "")
    assert other.llm_model_id == "LLM_OllamaLLM"
    assert other.system_prompt == "KEEP-PROMPT"


@pytest.mark.asyncio
async def test_scoped_admin_cannot_edit_an_unauthorized_agent(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(agent_router, "read_ollama_tags", _tags)
    db_session.add_all([
        _agent(CLIENT_ID, "Care Client", PORTAL_AI_AGENTS_ORGANIZATION_ID),
        _agent(AGENT_ID, "Virtual Agent", PORTAL_AI_AGENTS_ORGANIZATION_ID),
        SysUser(id=2, username="scoped", password=hash_password("unused"), super_admin=ROLE_ADMIN, status=1),
        AdminClientAccess(admin_user_id=2, agent_id=CLIENT_ID, granted_by=1),
    ])
    await db_session.commit()
    token, _expire = issue_token(2, "scoped", ROLE_ADMIN)
    denied = (
        await client.put(
            f"/api/agent/{AGENT_ID}/ask-sam-model",
            json={"askSamModel": "other-model"},
            headers=_auth(token),
        )
    ).json()
    assert denied["code"] == 403
    db_session.expire_all()
    row = await db_session.get(AiAgent, AGENT_ID)
    assert row is not None
    assert "other-model" not in (row.profile_json or "")


@pytest.mark.asyncio
async def test_model_probe_does_not_claim_the_integration_is_connected(monkeypatch):
    class Client:
        async def get(self, url, **_k):
            class Resp:
                status_code = 200

                def json(self):
                    if url.endswith("/api/tags"):
                        return {"models": [{"name": "other-model"}]}
                    return {"version": "0"}

            return Resp()

        async def post(self, url, json=None, timeout=None, **_k):
            class Resp:
                status_code = 200

                def json(self):
                    return {"response": "OK"}

            return Resp()

    monkeypatch.setattr(settings, "ollama_url", "http://ollama.example")
    result = await probe_selected_model(Client(), "other-model")
    assert result["ollamaReachable"] is True
    assert result["listed"] is True
    assert result["generationSucceeded"] is True


def test_ask_sam_route_uses_the_configured_agent_model():
    """Class A / image-exact: Sam agent id comes from settings.sam_client_id."""
    partner = Path(__file__).resolve().parents[1] / "careconnect_api" / "routers" / "partner_bridgingcare.py"
    conversation = Path(__file__).resolve().parents[1] / "careconnect_api" / "sam_conversation.py"
    partner_text = partner.read_text()
    conversation_text = conversation.read_text()
    assert "settings.sam_client_id" in partner_text
    assert "virtual_agent_id = settings.sam_client_id.strip()" in partner_text
    assert "configured_integration_agent_id()" not in partner_text
    assert "conversation_model_for_profile(" not in partner_text
    assert "model=model or None" not in partner_text
    assert "chosen_model = model or settings.llm_model" in conversation_text
    assert 'payload.get("model")' not in partner_text
    assert "1c46373751d84d29a1aab382fd65501f" not in partner_text
    assert "62d1a3a6575f4bb8a0f3814f3e29a1ea" not in partner_text
    assert "qwen2.5:3b" not in partner_text
