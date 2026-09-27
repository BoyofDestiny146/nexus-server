"""Nexus Assessment Engine Phase 1: registry, persistence, wrapper, cron."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.assessment_engine.engine import assess_agent
from careconnect_api.assessment_engine.profiles import (
    CARE_WELLNESS_ID,
    list_assessment_profiles,
    parse_selectable_profile_id,
    resolve_assessment_profile_id,
)
from careconnect_api.assessment_engine.storage import (
    apply_assessment_profile,
    resolved_assessment_profile_dict,
)
from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.client_profile import dump_profile, load_profile, merge_profile
from careconnect_api.envelope import APIException
from careconnect_api.models import AiAgent, AiMedicalAssessment, SysUser
from careconnect_api.revel_write import revel_execute_enabled

API_DIR = Path(__file__).resolve().parents[1]
ROOT = API_DIR.parent
RUNNER_SRC = (API_DIR / "careconnect_api" / "triage" / "runner.py").read_text()
ENGINE_SRC = (API_DIR / "careconnect_api" / "assessment_engine" / "engine.py").read_text()
SCHEDULER_SRC = (API_DIR / "careconnect_api" / "scheduler.py").read_text()
PROMPT_SRC = (API_DIR / "careconnect_api" / "triage" / "prompt.md").read_text()
ENV_EXAMPLE = (ROOT / "deploy" / ".env.example").read_text()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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


async def _onboard(client: AsyncClient, token: str, name: str = "Betty") -> str:
    resp = await client.post(
        "/api/agent/onboard",
        json={"name": name, "condition": "Lives alone"},
        headers=_auth(token),
    )
    assert resp.json()["code"] == 0, resp.json()
    return resp.json()["data"]["agentId"]


def test_registry_exposes_all_four_profiles_only_care_wellness_implemented():
    profiles = list_assessment_profiles()
    assert [p.id for p in profiles] == [
        "care_wellness",
        "sales_product",
        "information_kiosk",
        "operations_staff",
    ]
    assert [p.displayName for p in profiles] == [
        "Care & Wellness",
        "Sales & Product Guide",
        "Information Kiosk",
        "Operations & Staff Assistant",
    ]
    assert [p.implemented for p in profiles] == [True, True, False, False]


def test_missing_empty_unknown_resolve_to_care_wellness():
    assert resolve_assessment_profile_id(None) == CARE_WELLNESS_ID
    assert resolve_assessment_profile_id("") == CARE_WELLNESS_ID
    assert resolve_assessment_profile_id("   ") == CARE_WELLNESS_ID
    assert resolve_assessment_profile_id("not_a_profile") == CARE_WELLNESS_ID
    assert resolve_assessment_profile_id({"id": "sales_product"}) == CARE_WELLNESS_ID
    assert resolve_assessment_profile_id("sales_product") == "sales_product"
    assert resolve_assessment_profile_id("information_kiosk") == CARE_WELLNESS_ID
    assert resolve_assessment_profile_id("operations_staff") == CARE_WELLNESS_ID
    assert resolve_assessment_profile_id("care_wellness") == CARE_WELLNESS_ID


def test_unimplemented_profiles_cannot_be_persisted():
    with pytest.raises(APIException) as unknown:
        parse_selectable_profile_id("hacked_engine")
    assert unknown.value.code == 400
    for pid in ("information_kiosk", "operations_staff"):
        with pytest.raises(APIException) as pending:
            parse_selectable_profile_id(pid)
        assert pending.value.code == 400
        assert "not available" in pending.value.msg
    assert parse_selectable_profile_id("care_wellness") == CARE_WELLNESS_ID
    assert parse_selectable_profile_id("sales_product") == "sales_product"


def test_unrelated_profile_json_fields_are_preserved_on_merge():
    existing = load_profile(
        {
            "condition": "diabetes",
            "extraFlag": True,
            "partnerSite": "north-wing",
            "assessmentProfile": "care_wellness",
        }
    )
    merged = merge_profile(existing, {"condition": "diabetes, fall-risk"})
    assert merged["extraFlag"] is True
    assert merged["partnerSite"] == "north-wing"
    assert merged["assessmentProfile"] == "care_wellness"
    assert merged["condition"] == "diabetes, fall-risk"
    dumped = dump_profile(merged)
    assert "extraFlag" in dumped
    assert "partnerSite" in dumped


def test_apply_assessment_profile_does_not_drop_unrelated_keys():
    agent = AiAgent(id="agt_test", agent_name="Test")
    agent.profile_json = dump_profile(
        {"condition": "COPD", "extraFlag": 7, "tags": ["oxygen"]}
    )
    apply_assessment_profile(agent, CARE_WELLNESS_ID)
    loaded = load_profile(agent.profile_json)
    assert loaded["condition"] == "COPD"
    assert loaded["extraFlag"] == 7
    assert loaded["tags"] == ["oxygen"]
    assert loaded["assessmentProfile"] == CARE_WELLNESS_ID


def test_missing_assessment_profile_does_not_rewrite_profile_json():
    agent = AiAgent(id="agt_empty", agent_name="Empty")
    agent.profile_json = dump_profile({"condition": "arthritis"})
    resolved = resolved_assessment_profile_dict(agent.profile_json)
    assert resolved["id"] == CARE_WELLNESS_ID
    loaded = load_profile(agent.profile_json)
    assert "assessmentProfile" not in loaded or loaded.get("assessmentProfile") is None
    assert loaded["condition"] == "arthritis"


@pytest.mark.asyncio
async def test_get_resolves_missing_and_unknown_without_migration(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, "Ada")
    got = await client.get(
        f"/api/agent/{agent_id}/assessment/profile",
        headers=_auth(admin_token),
    )
    body = got.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["assessmentProfile"]["id"] == "care_wellness"
    assert data["assessmentProfile"]["displayName"] == "Care & Wellness"
    assert data["assessmentProfile"]["implemented"] is True
    assert [p["id"] for p in data["profiles"]] == [
        "care_wellness",
        "sales_product",
        "information_kiosk",
        "operations_staff",
    ]
    assert [p["implemented"] for p in data["profiles"]] == [True, True, False, False]

    agent = (await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))).scalar_one()
    stored = load_profile(agent.profile_json)
    stored["extraFlag"] = True
    stored["assessmentProfile"] = "totally_unknown"
    agent.profile_json = dump_profile(stored)
    await db_session.commit()

    unknown = await client.get(
        f"/api/agent/{agent_id}/assessment/profile",
        headers=_auth(admin_token),
    )
    assert unknown.json()["data"]["assessmentProfile"]["id"] == "care_wellness"

    db_session.expire_all()
    agent = (await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))).scalar_one()
    loaded = load_profile(agent.profile_json)
    assert loaded["extraFlag"] is True
    assert loaded["assessmentProfile"] == "totally_unknown"
    assert loaded["condition"] == "Lives alone"


@pytest.mark.asyncio
async def test_put_rejects_arbitrary_and_unimplemented_ids(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, "Cara")
    for bad in ("hacked_profile", "information_kiosk", "operations_staff"):
        resp = await client.put(
            f"/api/agent/{agent_id}/assessment/profile",
            json={"assessmentProfile": bad},
            headers=_auth(admin_token),
        )
        assert resp.json()["code"] == 400, resp.json()

    db_session.expire_all()
    agent = (await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))).scalar_one()
    loaded = load_profile(agent.profile_json)
    assert loaded.get("assessmentProfile") not in {
        "hacked_profile",
        "information_kiosk",
        "operations_staff",
    }
    assert loaded["condition"] == "Lives alone"

    ok = await client.put(
        f"/api/agent/{agent_id}/assessment/profile",
        json={"assessmentProfile": "care_wellness"},
        headers=_auth(admin_token),
    )
    assert ok.json()["code"] == 0
    assert ok.json()["data"]["assessmentProfile"]["id"] == "care_wellness"
    db_session.expire_all()
    agent = (await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))).scalar_one()
    loaded = load_profile(agent.profile_json)
    assert loaded["assessmentProfile"] == "care_wellness"
    assert loaded["condition"] == "Lives alone"


@pytest.mark.asyncio
async def test_put_preserves_unrelated_profile_json_keys(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, "Dana")
    agent = (await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))).scalar_one()
    stored = load_profile(agent.profile_json)
    stored["extraFlag"] = True
    stored["partnerSite"] = "east"
    agent.profile_json = dump_profile(stored)
    await db_session.commit()

    ok = await client.put(
        f"/api/agent/{agent_id}/assessment/profile",
        json={"assessmentProfile": "care_wellness"},
        headers=_auth(admin_token),
    )
    assert ok.json()["code"] == 0
    db_session.expire_all()
    agent = (await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))).scalar_one()
    loaded = load_profile(agent.profile_json)
    assert loaded["extraFlag"] is True
    assert loaded["partnerSite"] == "east"
    assert loaded["assessmentProfile"] == "care_wellness"
    assert loaded["condition"] == "Lives alone"


@pytest.mark.asyncio
async def test_agent_details_include_resolved_assessment_profile(
    client: AsyncClient, admin_token: str
):
    agent_id = await _onboard(client, admin_token, "Eli")
    got = await client.get(f"/api/agent/{agent_id}", headers=_auth(admin_token))
    data = got.json()["data"]
    assert data["assessmentProfile"]["id"] == "care_wellness"
    assert data["assessmentProfile"]["displayName"] == "Care & Wellness"
    assert data["condition"] == "Lives alone"


@pytest.mark.asyncio
async def test_edit_client_patch_preserves_assessment_profile(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token, "Hana")
    put = await client.put(
        f"/api/agent/{agent_id}/assessment/profile",
        json={"assessmentProfile": "care_wellness"},
        headers=_auth(admin_token),
    )
    assert put.json()["code"] == 0
    patched = await client.patch(
        f"/api/agent/{agent_id}",
        json={"name": "Hana K.", "condition": "COPD"},
        headers=_auth(admin_token),
    )
    assert patched.json()["code"] == 0, patched.json()
    db_session.expire_all()
    agent = (await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))).scalar_one()
    loaded = load_profile(agent.profile_json)
    assert loaded["assessmentProfile"] == "care_wellness"
    assert loaded["condition"] == "COPD"
    assert agent.agent_name == "Hana K."


@pytest.mark.asyncio
async def test_catalog_endpoint_lists_four_profiles(client: AsyncClient, admin_token: str):
    got = await client.get("/api/assessment/profiles", headers=_auth(admin_token))
    assert got.json()["code"] == 0
    profiles = got.json()["data"]["profiles"]
    assert len(profiles) == 4
    assert profiles[0]["id"] == "care_wellness"
    assert profiles[0]["implemented"] is True
    assert profiles[1]["id"] == "sales_product"
    assert profiles[1]["implemented"] is True
    assert all(not p["implemented"] for p in profiles[2:])


@pytest.mark.asyncio
async def test_unimplemented_stored_profile_still_runs_care_wellness(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "Faye")
    agent = (await db_session.execute(select(AiAgent).where(AiAgent.id == agent_id))).scalar_one()
    stored = load_profile(agent.profile_json)
    stored["assessmentProfile"] = "information_kiosk"
    agent.profile_json = dump_profile(stored)
    await db_session.commit()

    called: dict[str, object] = {}

    async def fake_run(db, aid, for_date=None):
        called["agent_id"] = aid
        called["for_date"] = for_date
        row = AiMedicalAssessment(
            id=1,
            agent_id=aid,
            for_date=None,
            risk_level="low",
            confidence=0,
            concerns_json="[]",
            recommendations_json="[]",
            source_msg_count=0,
            llm_model="test",
        )
        return row

    monkeypatch.setattr(
        "careconnect_api.assessment_engine.engine.run_for_agent",
        fake_run,
    )
    row = await assess_agent(db_session, agent_id)
    assert called["agent_id"] == agent_id
    assert row is not None
    assert row.profile_id == CARE_WELLNESS_ID
    assert row.medical is not None
    assert row.medical.risk_level == "low"
    assert row.generic is None


@pytest.mark.asyncio
async def test_regenerate_uses_engine_then_existing_runner(
    client: AsyncClient, admin_token: str, monkeypatch
):
    agent_id = await _onboard(client, admin_token, "George")
    called: list[str] = []

    async def fake_run(db, aid, for_date=None):
        called.append(aid)
        return AiMedicalAssessment(
            id=501,
            agent_id=aid,
            for_date=date.today(),
            risk_level="low",
            confidence=Decimal("0.000"),
            concerns_json="[]",
            recommendations_json="[]",
            source_msg_count=0,
            llm_model="cc-llm",
            generated_at=datetime.now(),
        )

    monkeypatch.setattr(
        "careconnect_api.assessment_engine.engine.run_for_agent",
        fake_run,
    )
    resp = await client.post(
        f"/api/agent/{agent_id}/assessment/regenerate",
        headers=_auth(admin_token),
    )
    assert resp.json()["code"] == 0, resp.json()
    assert called == [agent_id]
    data = resp.json()["data"]
    assert data["riskLevel"] == "low"
    assert data["confidence"] == 0.0
    assert data["concerns"] == []
    assert data["recommendations"] == []
    assert data["sourceMsgCount"] == 0
    assert data["agentId"] == agent_id


def test_care_wellness_runner_source_is_unchanged():
    assert 'from ..triage.runner import run_for_agent' in ENGINE_SRC
    assert "await run_for_agent(db, agent_id, for_date)" in ENGINE_SRC
    assert "run_sales_for_agent" in ENGINE_SRC
    assert '"num_predict": 300' in RUNNER_SRC
    assert '"temperature": 0.2' in RUNNER_SRC
    assert ".order_by(AiAgentChatHistory.id.asc())" in RUNNER_SRC
    assert ".limit(settings.triage_max_messages)" in RUNNER_SRC
    assert 'TriageResult("low", 0.0, [], [])' in RUNNER_SRC
    assert "ALLOWED_LEVELS = {\"low\", \"moderate\", \"elevated\", \"urgent\"}" in RUNNER_SRC
    assert "revel_status_for_agent" in RUNNER_SRC
    assert "compose_assessment_dialogue" in RUNNER_SRC
    assert "push_assessment_best_effort" in RUNNER_SRC
    assert "publish_assessment_updated" not in RUNNER_SRC
    assert "risk_level" in PROMPT_SRC


def test_nightly_cron_still_calls_run_for_all_directly():
    assert "from .triage.runner import run_for_all" in SCHEDULER_SRC
    assert 'id="daily_triage"' in SCHEDULER_SRC
    assert "assess_agent" not in SCHEDULER_SRC
    assert "assessment_engine" not in SCHEDULER_SRC
    assert "CronTrigger(hour=settings.triage_cron_hour, minute=settings.triage_cron_minute)" in SCHEDULER_SRC


def test_no_revel_execute_from_engine_or_runner():
    assert "revel_write" not in ENGINE_SRC
    assert "update_data_table_row" not in ENGINE_SRC
    assert "sendDeviceCommand" not in ENGINE_SRC
    assert "update_data_table_row" not in RUNNER_SRC
    assert "sendDeviceCommand" not in RUNNER_SRC
    assert revel_execute_enabled() is False
    assert "REVEL_EXECUTE_ENABLED=false" in ENV_EXAMPLE


def test_settings_and_env_keep_revel_execute_off():
    assert revel_execute_enabled() is False
    assert ENV_EXAMPLE.splitlines().count("REVEL_EXECUTE_ENABLED=false") >= 1
    from careconnect_api.revel_signage import load_revel_signage_settings

    assert load_revel_signage_settings().revel_execute_enabled is False
