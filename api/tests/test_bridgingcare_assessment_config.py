"""Read-only Bridging Care assessment-config GET (inbound M2M)."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.assessment_engine.schedule import (
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULED,
    apply_assessment_schedule,
)
from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.bridgingcare_delivery import bridgingcare_execute_enabled
from careconnect_api.bridgingcare_inbound_auth import require_bridgingcare_inbound
from careconnect_api.bridgingcare_mapping import (
    BETTY_ACCOUNT_ID,
    BETTY_CLIENT_ID,
    BETTY_COMPANION_DISPLAY_NAME,
    BETTY_COMPANION_ID,
    BILL_BOB_AGENT_ID,
    BILL_BOB_DISPLAY_NAME,
    _freeze_verified_mapping,
    bind_verified_mapping,
    resolve_verified_mapping_by_companion,
)
from careconnect_api.chat_events import CHAT_TYPE_CLIENT
from careconnect_api.client_profile import dump_profile, load_profile, merge_profile
from careconnect_api.envelope import APIException
from careconnect_api.models import (
    AiAgent,
    AiAgentChatHistory,
    AiMedicalAssessment,
    CcBridgingcareCompanionMap,
    SysUser,
)
from careconnect_api.partner_auth import AUTH_FAIL_MSG
from careconnect_api.routers.partner_bridgingcare import NOT_FOUND_MSG
from careconnect_api.settings import settings


API_DIR = Path(__file__).resolve().parents[1]
REPO = API_DIR.parent
ROUTER_SRC = (
    API_DIR / "careconnect_api" / "routers" / "partner_bridgingcare.py"
).read_text()
INBOUND_AUTH_SRC = (
    API_DIR / "careconnect_api" / "bridgingcare_inbound_auth.py"
).read_text()
COMPOSE = (REPO / "deploy" / "docker-compose.yml").read_text()
GEN_SECRETS = (REPO / "deploy" / "scripts" / "gen-secrets.sh").read_text()
R60C_SCHEMA = (
    REPO / "docs" / "contracts" / "bridgingcare-ai-companion-assessment-1.0.schema.json"
).read_text()

_CONFIG_PREFIX = "/api/v1/integrations/bridgingcare/companions"
_WATCHER_KEY = "test-api-key-abc123"
_ASSESSMENT_PK = 94000
_CHAT_PK = 95000
_INBOUND_ID = "bc-nexus-inbound-test"
_INBOUND_SECRET = "test-bridgingcare-inbound-secret"
_UTC_Z = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


def _config_path(companion_id: str) -> str:
    return f"{_CONFIG_PREFIX}/{companion_id}/assessment-config"


def _blob(payload) -> str:
    return json.dumps(payload)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _inbound(client_id: str, secret: str) -> dict[str, str]:
    return {
        "X-Client-Id": client_id,
        "X-Client-Secret": secret,
        "Accept": "application/json",
    }


@pytest.fixture
def inbound_creds(tmp_path, monkeypatch) -> tuple[str, str]:
    hash_file = tmp_path / "bridgingcare-inbound-secret-hash"
    hash_file.write_text(hash_password(_INBOUND_SECRET))
    hash_file.chmod(0o600)
    monkeypatch.setattr(settings, "bridgingcare_inbound_client_id", _INBOUND_ID)
    monkeypatch.setattr(settings, "bridgingcare_inbound_secret_hash_file", hash_file)
    monkeypatch.setattr(settings, "tz", "America/Chicago")
    return _INBOUND_ID, _INBOUND_SECRET


@pytest_asyncio.fixture
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


async def _onboard(client: AsyncClient, token: str, name: str) -> str:
    resp = await client.post(
        "/api/agent/onboard", json={"name": name}, headers=_auth(token)
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["agentId"]


async def _connect_cc(client: AsyncClient, token: str, agent_id: str) -> tuple[str, str]:
    resp = await client.post(
        f"/api/agent/{agent_id}/integrations/careconnect",
        headers=_auth(token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["publicId"], body["data"]["secret"]


async def _bind_betty(db: AsyncSession, agent_id: str) -> CcBridgingcareCompanionMap:
    row = await bind_verified_mapping(
        db,
        agent_id=agent_id,
        companion_id=BETTY_COMPANION_ID,
        account_id=BETTY_ACCOUNT_ID,
        client_id=BETTY_CLIENT_ID,
        expected_agent_name=BILL_BOB_DISPLAY_NAME,
    )
    await db.commit()
    await db.refresh(row)
    return row


async def _seed_assessment(
    db: AsyncSession,
    agent_id: str,
    *,
    trigger_type: str = TRIGGER_SCHEDULED,
    generated_at: datetime,
) -> AiMedicalAssessment:
    global _ASSESSMENT_PK
    _ASSESSMENT_PK += 1
    row = AiMedicalAssessment(
        id=_ASSESSMENT_PK,
        agent_id=agent_id,
        for_date=generated_at.date() if generated_at else date(2026, 10, 19),
        risk_level="low",
        confidence=Decimal("0.600"),
        concerns_json='["fatigue"]',
        recommendations_json='["Phone check-in"]',
        source_msg_count=3,
        llm_model="qwen2.5:3b",
        generated_at=generated_at,
        trigger_type=trigger_type,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _store_interval_60(db: AsyncSession, agent_id: str) -> None:
    agent = await db.get(AiAgent, agent_id)
    assert agent is not None
    apply_assessment_schedule(
        agent,
        {
            "enabled": True,
            "mode": "interval",
            "intervalMinutes": 60,
            "onlyIfNewData": True,
            "assessOnEscalationPhrases": True,
        },
    )
    await db.commit()


def test_router_is_get_only_and_does_not_call_evaluate_due():
    assert "evaluate_due" not in ROUTER_SRC
    assert "has_meaningful_new_data" not in ROUTER_SRC
    assert "profile_payload" not in ROUTER_SRC
    assert "delivery_view" not in ROUTER_SRC
    assert "@router.put" not in ROUTER_SRC
    assert "@router.post" not in ROUTER_SRC
    assert "@router.patch" not in ROUTER_SRC
    assert "@router.delete" not in ROUTER_SRC
    assert "_read_or_create_secret" not in INBOUND_AUTH_SRC
    assert "ClientIntegration" not in INBOUND_AUTH_SRC
    assert "from .models import" not in INBOUND_AUTH_SRC
    assert "select(" not in INBOUND_AUTH_SRC
    assert "X-Client-Secret" in INBOUND_AUTH_SRC
    assert "never log" in INBOUND_AUTH_SRC.lower() or "Never logs" in INBOUND_AUTH_SRC


def test_compose_declares_inbound_env_on_api_only_and_execute_stays_false():
    marker = "  api:\n    image:"
    assert marker in COMPOSE
    api = COMPOSE.split(marker, 1)[1].split("  web:\n", 1)[0]
    xz_marker = "  xiaozhi-server:\n    image:"
    xz = COMPOSE.split(xz_marker, 1)[1].split("    volumes:", 1)[0]
    assert "CC_BRIDGINGCARE_INBOUND_CLIENT_ID" in api
    assert "CC_BRIDGINGCARE_INBOUND_SECRET_HASH_FILE" in api
    assert "/run/secrets/bridgingcare-inbound-secret-hash" in api
    assert 'CC_BRIDGINGCARE_EXECUTE_ENABLED: "${CC_BRIDGINGCARE_EXECUTE_ENABLED:-false}"' in api
    assert 'BRIDGINGCARE_EXECUTE_ENABLED: "${BRIDGINGCARE_EXECUTE_ENABLED:-false}"' in api
    assert "EXECUTE_ENABLED:-true" not in COMPOSE
    assert "CC_BRIDGINGCARE_INBOUND_CLIENT_ID" not in xz
    assert "CC_BRIDGINGCARE_INBOUND_SECRET_HASH_FILE" not in xz
    loop = GEN_SECRETS.split("for name in", 1)[1].split("do", 1)[0]
    assert "bridgingcare-inbound-secret-hash" not in loop
    assert "bridgingcare-api-key" not in loop
    assert "Never auto-generate" in GEN_SECRETS or "never auto-generate" in GEN_SECRETS
    assert "additionalProperties" in R60C_SCHEMA
    assert "assessment-config" not in R60C_SCHEMA


def test_orm_verified_at_matches_migration_030_not_null():
    col = CcBridgingcareCompanionMap.__table__.c.verified_at
    assert col.nullable is False
    migration = (
        API_DIR / "migrations" / "030_bridgingcare_assessment_delivery.sql"
    ).read_text()
    assert "verified_at          DATETIME     NOT NULL" in migration


def test_settings_default_to_absent_hash_and_empty_inbound_id():
    assert (settings.bridgingcare_inbound_client_id or "") == ""
    assert "bridgingcare-inbound-secret-hash-absent" in str(
        settings.bridgingcare_inbound_secret_hash_file
    )
    assert bridgingcare_execute_enabled() is False


@pytest.mark.asyncio
async def test_missing_headers_and_query_secret_are_auth_failure(
    client: AsyncClient, inbound_creds, caplog
):
    client_id, secret = inbound_creds
    caplog.set_level(logging.INFO)
    only_id = await client.get(
        _config_path(BETTY_COMPANION_ID), headers={"X-Client-Id": client_id}
    )
    only_secret = await client.get(
        _config_path(BETTY_COMPANION_ID), headers={"X-Client-Secret": secret}
    )
    missing = await client.get(_config_path(BETTY_COMPANION_ID))
    query = await client.get(
        f"{_config_path(BETTY_COMPANION_ID)}?clientId={client_id}&secret={secret}"
    )
    for resp in (only_id, only_secret, missing, query):
        body = resp.json()
        assert resp.status_code == 200
        assert body["code"] == 401
        assert body["msg"] == AUTH_FAIL_MSG
        assert body["data"] is None
        assert secret not in _blob(body)
        app_logs = "\n".join(
            rec.getMessage() for rec in caplog.records if not rec.name.startswith("httpx")
        )
        assert secret not in app_logs


@pytest.mark.asyncio
async def test_wrong_secret_and_unknown_inbound_id_match_missing_envelope(
    client: AsyncClient, inbound_creds
):
    client_id, secret = inbound_creds
    missing = await client.get(_config_path(BETTY_COMPANION_ID))
    wrong = await client.get(
        _config_path(BETTY_COMPANION_ID),
        headers=_inbound(client_id, "wrong-secret"),
    )
    unknown = await client.get(
        _config_path(BETTY_COMPANION_ID),
        headers=_inbound("unknown-inbound-id", secret),
    )
    for resp in (missing, wrong, unknown):
        body = resp.json()
        assert body["code"] == 401
        assert body["msg"] == AUTH_FAIL_MSG
        assert body["data"] is None
        assert "not found" not in body["msg"].lower()
        assert secret not in _blob(body)


@pytest.mark.asyncio
async def test_dashboard_jwt_watcher_key_and_outbound_ingest_key_are_auth_failure(
    client: AsyncClient, admin_token: str, inbound_creds
):
    _ = inbound_creds
    jwt_only = await client.get(
        _config_path(BETTY_COMPANION_ID), headers=_auth(admin_token)
    )
    watcher = await client.get(
        _config_path(BETTY_COMPANION_ID), headers={"X-API-Key": _WATCHER_KEY}
    )
    outbound = await client.get(
        _config_path(BETTY_COMPANION_ID),
        headers={"X-Api-Key": "outbound-ingest-key-must-not-work"},
    )
    for resp in (jwt_only, watcher, outbound):
        body = resp.json()
        assert body["code"] == 401
        assert body["msg"] == AUTH_FAIL_MSG
        assert body["data"] is None


@pytest.mark.asyncio
async def test_careconnect_partner_headers_are_auth_failure(
    client: AsyncClient, admin_token: str, inbound_creds
):
    _ = inbound_creds
    agent_id = await _onboard(client, admin_token, "Cara")
    public_id, secret = await _connect_cc(client, admin_token, agent_id)
    resp = await client.get(
        _config_path(BETTY_COMPANION_ID), headers=_inbound(public_id, secret)
    )
    body = resp.json()
    assert body["code"] == 401
    assert body["msg"] == AUTH_FAIL_MSG
    assert body["data"] is None
    assert secret not in _blob(body)


@pytest.mark.asyncio
async def test_missing_inbound_config_fails_closed(client: AsyncClient, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "bridgingcare_inbound_client_id", _INBOUND_ID)
    monkeypatch.setattr(
        settings,
        "bridgingcare_inbound_secret_hash_file",
        tmp_path / "bridgingcare-inbound-secret-hash-absent",
    )
    resp = await client.get(
        _config_path(BETTY_COMPANION_ID), headers=_inbound(_INBOUND_ID, _INBOUND_SECRET)
    )
    body = resp.json()
    assert body["code"] == 401
    assert body["msg"] == AUTH_FAIL_MSG
    assert body["data"] is None
    assert not (tmp_path / "bridgingcare-inbound-secret-hash-absent").exists()


@pytest.mark.asyncio
async def test_unknown_companion_is_404_without_agent_id(
    client: AsyncClient, inbound_creds, admin_token: str, db_session: AsyncSession
):
    client_id, secret = inbound_creds
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    await _bind_betty(db_session, agent_id)
    resp = await client.get(
        _config_path("Nx-NOPE00000"), headers=_inbound(client_id, secret)
    )
    body = resp.json()
    assert body["code"] == 404
    assert body["msg"] == NOT_FOUND_MSG
    assert body["data"] is None
    assert agent_id not in _blob(body)
    assert BILL_BOB_AGENT_ID not in _blob(body)
    assert "795e977e" not in _blob(body)


@pytest.mark.asyncio
async def test_malformed_companion_id_is_404(
    client: AsyncClient, inbound_creds, admin_token: str, db_session: AsyncSession
):
    client_id, secret = inbound_creds
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    await _bind_betty(db_session, agent_id)
    for bad in (agent_id, BILL_BOB_AGENT_ID, "not-an-nx-id", "Betty"):
        resp = await client.get(_config_path(bad), headers=_inbound(client_id, secret))
        body = resp.json()
        assert body["code"] == 404, body
        assert body["msg"] == NOT_FOUND_MSG
        assert body["data"] is None
        assert agent_id not in _blob(body)


@pytest.mark.asyncio
async def test_unverified_mapping_is_404(
    client: AsyncClient, inbound_creds, admin_token: str, db_session: AsyncSession
):
    """A stored row that fails freeze rules is treated as unverified.

    Production ``verified_at`` is NOT NULL (migration 030). Tests must not
    persist NULL. Companion name as ``expected_agent_name`` is refused by
    ``assert_expected_name_is_client`` — same 404 as an unverified map.
    """
    client_id, secret = inbound_creds
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    now = datetime.now()
    db_session.add(
        CcBridgingcareCompanionMap(
            agent_id=agent_id,
            companion_id=BETTY_COMPANION_ID,
            account_id=BETTY_ACCOUNT_ID,
            client_id=BETTY_CLIENT_ID,
            expected_agent_name=BETTY_COMPANION_DISPLAY_NAME,
            verified_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.commit()
    assert await resolve_verified_mapping_by_companion(db_session, BETTY_COMPANION_ID) is None
    resp = await client.get(
        _config_path(BETTY_COMPANION_ID), headers=_inbound(client_id, secret)
    )
    body = resp.json()
    assert body["code"] == 404
    assert body["msg"] == NOT_FOUND_MSG
    assert body["data"] is None
    assert agent_id not in _blob(body)


@pytest.mark.asyncio
async def test_freeze_helper_rejects_missing_row_without_null_verified_at(
    db_session: AsyncSession,
):
    assert await _freeze_verified_mapping(db_session, None) is None
    in_memory = CcBridgingcareCompanionMap(
        agent_id="a" * 32,
        companion_id=BETTY_COMPANION_ID,
        account_id=BETTY_ACCOUNT_ID,
        client_id=BETTY_CLIENT_ID,
        expected_agent_name=BILL_BOB_DISPLAY_NAME,
        verified_at=None,
    )
    assert await _freeze_verified_mapping(db_session, in_memory) is None


@pytest.mark.asyncio
async def test_identity_mismatch_is_404(
    client: AsyncClient, inbound_creds, admin_token: str, db_session: AsyncSession
):
    client_id, secret = inbound_creds
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    await _bind_betty(db_session, agent_id)
    agent = await db_session.get(AiAgent, agent_id)
    assert agent is not None
    agent.agent_name = "Someone Else"
    await db_session.commit()
    assert await resolve_verified_mapping_by_companion(db_session, BETTY_COMPANION_ID) is None
    resp = await client.get(
        _config_path(BETTY_COMPANION_ID), headers=_inbound(client_id, secret)
    )
    body = resp.json()
    assert body["code"] == 404
    assert body["msg"] == NOT_FOUND_MSG
    assert body["data"] is None
    assert agent_id not in _blob(body)


@pytest.mark.asyncio
async def test_betty_bill_bob_returns_stored_schedule_default_profile_and_utc_times(
    client: AsyncClient,
    inbound_creds,
    admin_token: str,
    db_session: AsyncSession,
    monkeypatch,
    caplog,
):
    client_id, secret = inbound_creds
    caplog.set_level(logging.INFO)

    async def _boom(*_args, **_kwargs):
        raise AssertionError("evaluate_due / chat inspect must not run")

    monkeypatch.setattr(
        "careconnect_api.assessment_engine.schedule.evaluate_due", _boom
    )
    monkeypatch.setattr(
        "careconnect_api.assessment_engine.schedule.has_meaningful_new_data",
        _boom,
    )

    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    await _bind_betty(db_session, agent_id)
    await _store_interval_60(db_session, agent_id)

    agent = await db_session.get(AiAgent, agent_id)
    stored = load_profile(agent.profile_json)
    assert "assessmentProfile" not in stored
    stored = merge_profile(
        stored,
        {
            "dob": "1940-01-01",
            "age": 86,
            "condition": "Lives alone",
            "tags": ["fall-risk"],
            "escalationPhrases": ["help me"],
            "topicsToAvoid": ["finances"],
        },
    )
    agent.profile_json = dump_profile(stored)
    await db_session.commit()

    global _CHAT_PK
    _CHAT_PK += 1
    db_session.add(
        AiAgentChatHistory(
            id=_CHAT_PK,
            agent_id=agent_id,
            chat_type=CHAT_TYPE_CLIENT,
            content="secret conversation about meds",
        )
    )
    await db_session.commit()

    await _seed_assessment(
        db_session,
        agent_id,
        trigger_type=TRIGGER_SCHEDULED,
        generated_at=datetime(2026, 10, 19, 5, 59, 26),
    )
    await _seed_assessment(
        db_session,
        agent_id,
        trigger_type=TRIGGER_MANUAL,
        generated_at=datetime(2026, 10, 19, 6, 7, 31),
    )
    await _seed_assessment(
        db_session,
        agent_id,
        trigger_type=TRIGGER_SCHEDULED,
        generated_at=datetime(2026, 10, 19, 7, 37, 46),
    )

    resp = await client.get(
        _config_path(BETTY_COMPANION_ID), headers=_inbound(client_id, secret)
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["code"] == 0, body
    assert body["msg"] == "success"
    data = body["data"]
    assert data["companionId"] == BETTY_COMPANION_ID
    assert data["source"] == "nexus"
    assert data["assessmentProfile"] == {
        "id": "care_wellness",
        "displayName": "Care & Wellness",
    }
    assert data["assessmentSchedule"] == {
        "enabled": True,
        "mode": "interval",
        "intervalMinutes": 60,
        "onlyIfNewData": True,
        "assessOnEscalationPhrases": True,
    }
    assert data["lastAssessmentAt"] == "2026-10-19T12:37:46Z"
    assert data["nextAssessmentAt"] == "2026-10-19T13:37:46Z"
    assert data["bridgingCare"] == {"mapped": True, "executeEnabled": False}
    assert bridgingcare_execute_enabled() is False
    assert set(data) == {
        "companionId",
        "source",
        "assessmentProfile",
        "assessmentSchedule",
        "lastAssessmentAt",
        "nextAssessmentAt",
        "bridgingCare",
    }
    blob = _blob(body)
    assert agent_id not in blob
    assert BILL_BOB_AGENT_ID not in blob
    assert BETTY_ACCOUNT_ID not in blob
    assert BETTY_CLIENT_ID not in blob
    assert secret not in blob
    assert client_id not in blob
    assert secret not in caplog.text
    assert "assessmentDelivery" not in blob
    assert "intervalChoices" not in blob
    assert "deliveryChoices" not in blob
    assert "scheduleSupported" not in blob
    assert '"profiles"' not in blob
    assert "fatigue" not in blob
    assert "Phone check-in" not in blob
    assert "secret conversation about meds" not in blob
    assert "1940-01-01" not in blob
    assert "fall-risk" not in blob
    assert "help me" not in blob
    assert "finances" not in blob
    assert "secret_hash" not in blob
    assert "bridgingcare-inbound-secret-hash" not in blob
    assert re.fullmatch(_UTC_Z, data["lastAssessmentAt"])
    assert re.fullmatch(_UTC_Z, data["nextAssessmentAt"])


@pytest.mark.asyncio
async def test_last_assessment_includes_later_manual(
    client: AsyncClient, inbound_creds, admin_token: str, db_session: AsyncSession
):
    client_id, secret = inbound_creds
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    await _bind_betty(db_session, agent_id)
    await _store_interval_60(db_session, agent_id)
    await _seed_assessment(
        db_session,
        agent_id,
        trigger_type=TRIGGER_SCHEDULED,
        generated_at=datetime(2026, 10, 19, 7, 37, 46),
    )
    await _seed_assessment(
        db_session,
        agent_id,
        trigger_type=TRIGGER_MANUAL,
        generated_at=datetime(2026, 10, 19, 8, 10, 0),
    )
    resp = await client.get(
        _config_path(BETTY_COMPANION_ID), headers=_inbound(client_id, secret)
    )
    data = resp.json()["data"]
    assert data["lastAssessmentAt"] == "2026-10-19T13:10:00Z"
    assert data["nextAssessmentAt"] == "2026-10-19T14:10:00Z"


@pytest.mark.asyncio
async def test_put_post_patch_delete_do_not_mutate_profile(
    client: AsyncClient, inbound_creds, admin_token: str, db_session: AsyncSession
):
    client_id, secret = inbound_creds
    agent_id = await _onboard(client, admin_token, BILL_BOB_DISPLAY_NAME)
    await _bind_betty(db_session, agent_id)
    await _store_interval_60(db_session, agent_id)
    db_session.expire_all()
    before = load_profile((await db_session.get(AiAgent, agent_id)).profile_json)
    path = _config_path(BETTY_COMPANION_ID)
    payload = {
        "assessmentSchedule": {
            "enabled": True,
            "mode": "interval",
            "intervalMinutes": 1440,
        }
    }
    for method in ("PUT", "POST", "PATCH", "DELETE"):
        resp = await client.request(
            method, path, headers=_inbound(client_id, secret), json=payload
        )
        body = resp.json()
        assert body.get("code") != 0 or (
            not isinstance(body.get("data"), dict)
            or body["data"].get("assessmentSchedule", {}).get("intervalMinutes") != 1440
        )
        assert body.get("data") != payload
    db_session.expire_all()
    after = load_profile((await db_session.get(AiAgent, agent_id)).profile_json)
    assert after == before
    assert after["assessmentSchedule"]["intervalMinutes"] == 60


@pytest.mark.asyncio
async def test_require_bridgingcare_inbound_never_returns_on_blank(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "bridgingcare_inbound_client_id", "")
    monkeypatch.setattr(
        settings,
        "bridgingcare_inbound_secret_hash_file",
        tmp_path / "missing-hash",
    )
    with pytest.raises(APIException) as exc:
        require_bridgingcare_inbound(
            x_client_id=_INBOUND_ID, x_client_secret=_INBOUND_SECRET
        )
    assert exc.value.code == 401
    assert exc.value.msg == AUTH_FAIL_MSG
