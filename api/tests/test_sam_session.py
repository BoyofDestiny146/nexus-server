"""Sam session persistence, feedback, and review isolation."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.bridgingcare_inbound_auth import require_bridgingcare_inbound
from careconnect_api.envelope import APIException
from careconnect_api.main import app
from careconnect_api.models import (
    AiAgent,
    AiAgentChatHistory,
    AiDevice,
    CcBridgingcareCompanionMap,
    CcSamSession,
    CcSamSessionTurn,
    CcVirtualAgentBinding,
    CcVirtualAgentMessage,
)
from careconnect_api.sam_session import (
    feedback_summary,
    record_completed_turn,
    record_feedback,
    record_follow_up_response,
    require_owned_session,
    review_sam_sessions,
    sam_session_retention_cutoff,
    session_claim_from_body,
)
from careconnect_api.settings import settings
import careconnect_api.routers.partner_bridgingcare as partner

SAM_ID = "1c46373751d84d29a1aab382fd65501f"
BILL_ID = "billbob000000000000000000000001"
API_DIR = Path(__file__).resolve().parents[1]


def _agent(agent_id: str, name: str) -> AiAgent:
    return AiAgent(id=agent_id, agent_name=name, bot_name=name, chat_history_conf=0)


def _library(subject: str = "auth0|master-a", session_id: str | None = None):
    return session_claim_from_body({
        "nexusClientId": BILL_ID,
        "session": {
            "ownerSubject": subject,
            "contextKind": "library",
            "accountId": "account-1",
            "registeredDeviceId": "band-1",
            **({"sessionId": session_id} if session_id else {}),
        },
    })


def _device(subject: str = "auth0|master-a", session_id: str | None = None, account: str = "account-1"):
    return session_claim_from_body({
        "clientId": BILL_ID,
        "session": {
            "ownerSubject": subject,
            "contextKind": "account_device",
            "accountId": account,
            "registeredDeviceId": "band-1",
            **({"sessionId": session_id} if session_id else {}),
        },
    })


@pytest.mark.asyncio
async def test_server_session_persists_only_completed_turns(db_session: AsyncSession, monkeypatch):
    db_session.add_all([
        _agent(SAM_ID, "Portal-US"),
        _agent(BILL_ID, "Bill Bob"),
        AiDevice(id="watcher-1", mac_address="80:45:6B:4D:5D:74", agent_id=BILL_ID, alias="Betty"),
    ])
    await db_session.commit()
    claim = _library()
    assert claim is not None
    assert claim.account_id is None
    assert claim.registered_device_id is None
    session_id = (await record_completed_turn(
        db_session, claim, SAM_ID, "What is the library?", "A completed answer.", "One follow-up",
    )).session_id
    assert session_id != BILL_ID
    assert len(session_id) == 32
    row = await db_session.get(CcSamSession, session_id)
    assert row is not None
    assert row.virtual_agent_id == SAM_ID
    assert row.owner_subject == "auth0|master-a"
    assert row.context_kind == "library"
    assert row.nexus_client_id is None
    assert row.answer_count == 1
    assert row.helpful is None
    turn = (await db_session.execute(select(CcSamSessionTurn))).scalars().one()
    assert turn.question == "What is the library?"
    assert "reading" not in turn.__dict__

    with pytest.raises(APIException) as denied:
        await require_owned_session(db_session, _library("auth0|other", session_id), SAM_ID)
    assert denied.value.code == 403
    with pytest.raises(APIException) as crossed:
        (await record_completed_turn(
            db_session, _device("auth0|master-a", session_id), SAM_ID, "Again?", "No.", None,
        )).session_id
    assert crossed.value.code == 403
    db_session.expire_all()
    assert (await db_session.get(CcSamSession, session_id)).answer_count == 1

    device_claim = _device()
    device_id = (await record_completed_turn(db_session, device_claim, SAM_ID, "Device?", "Answer.", None)).session_id
    device_row = await db_session.get(CcSamSession, device_id)
    assert device_row.context_kind == "account_device"
    assert device_row.account_id == "account-1"
    assert device_row.registered_device_id == "band-1"
    assert device_row.nexus_client_id is None
    assert (await db_session.execute(select(func.count()).select_from(CcVirtualAgentMessage))).scalar_one() == 0
    watcher = await db_session.get(AiDevice, "watcher-1")
    assert watcher.agent_id == BILL_ID
    monkeypatch.setattr(settings, "sam_client_id", SAM_ID)


@pytest.mark.asyncio
async def test_feedback_updates_and_summary_waits_for_ten_votes(db_session: AsyncSession):
    db_session.add(_agent(SAM_ID, "Portal-US"))
    await db_session.commit()
    ids = []
    for index in range(10):
        ids.append((await record_completed_turn(
            db_session, _library(f"auth0|user-{index}"), SAM_ID, f"Question {index}", "Answer.", None,
        )).session_id)
    for index, session_id in enumerate(ids[:9]):
        await record_feedback(db_session, _library(f"auth0|user-{index}", session_id), index % 2 == 0)
    early = await review_sam_sessions(db_session, SAM_ID)
    assert early["summary"]["votes"] == 9
    assert early["summary"]["percent"] is None
    await record_feedback(db_session, _library("auth0|user-9", ids[9]), True)
    await record_feedback(db_session, _library("auth0|user-9", ids[9]), False)
    final = await review_sam_sessions(db_session, SAM_ID)
    assert final["summary"]["votes"] == 10
    assert final["summary"] == feedback_summary(5, 5)
    assert final["sessions"][0]["helpful"] is False
    assert len(final["sessions"][0]["turns"]) == 1
    library = next(item for item in final["sessions"] if item["sessionId"] == ids[0])
    assert library["accountId"] is None
    assert library["nexusClientId"] is None
    other = await review_sam_sessions(db_session, BILL_ID)
    assert other["sessions"] == []
    assert sam_session_retention_cutoff.__doc__
    sql = (API_DIR / "migrations" / "032_sam_conversation_session.sql").read_text()
    assert "INTERVAL 30 DAY" in sql
    assert "CREATE EVENT" not in sql
    assert "cc_virtual_agent_message" in sql


@pytest.mark.asyncio
async def test_route_rejects_reuse_and_a_failed_generation_writes_nothing(
    client: AsyncClient, db_session: AsyncSession, monkeypatch,
):
    db_session.add(_agent(SAM_ID, "Portal-US"))
    await db_session.commit()
    monkeypatch.setattr(settings, "sam_client_id", SAM_ID)

    async def answer(body, **_kwargs):
        if body.get("question") == "fail":
            raise APIException(503, "Sam is unavailable")
        return {
            "agentId": "bridgingcare-sam",
            "model": "qwen2.5:3b",
            "message": "This is a completed Sam answer for the stored session.",
            "followUpQuestion": "Has a clinician already approved a limit?",
            "documents": [],
        }

    monkeypatch.setattr(partner, "handle_sam_conversation", answer)
    denied = (await client.post("/api/v1/integrations/bridgingcare/sam/feedback", json={"helpful": True})).json()
    assert denied["code"] == 401
    app.dependency_overrides[require_bridgingcare_inbound] = lambda: "inbound"

    created = (await client.post("/api/v1/integrations/bridgingcare/sam/conversation", json={
        "question": "What is the Master Alarm Library?",
        "language": "en",
        "nexusClientId": BILL_ID,
        "session": {"ownerSubject": "auth0|master-a", "contextKind": "library"},
    })).json()
    assert created["code"] == 0, created
    session_id = created["data"]["sessionId"]
    turn_id = created["data"]["turnId"]
    assert session_id != BILL_ID
    assert isinstance(turn_id, int) and turn_id >= 1

    reused = (await client.post("/api/v1/integrations/bridgingcare/sam/conversation", json={
        "question": "Another question",
        "language": "en",
        "session": {"sessionId": session_id, "ownerSubject": "auth0|other", "contextKind": "library"},
    })).json()
    assert reused["code"] == 403

    failed = (await client.post("/api/v1/integrations/bridgingcare/sam/conversation", json={
        "question": "fail",
        "language": "en",
        "session": {"ownerSubject": "auth0|master-a", "contextKind": "library"},
    })).json()
    assert failed["code"] == 503
    db_session.expire_all()
    assert (await db_session.execute(select(func.count()).select_from(CcSamSessionTurn))).scalar_one() == 1

    voted = (await client.post("/api/v1/integrations/bridgingcare/sam/feedback", json={
        "helpful": True,
        "nexusClientId": BILL_ID,
        "session": {"sessionId": session_id, "ownerSubject": "auth0|master-a", "contextKind": "library"},
    })).json()
    assert voted["code"] == 0, voted
    db_session.expire_all()
    stored = await db_session.get(CcSamSession, session_id)
    assert stored.helpful == 1
    assert stored.nexus_client_id is None
    assert (await db_session.execute(select(func.count()).select_from(CcVirtualAgentMessage))).scalar_one() == 0

    answered = (await client.post("/api/v1/integrations/bridgingcare/sam/follow-up-response", json={
        "response": False,
        "turnId": turn_id,
        "session": {"sessionId": session_id, "ownerSubject": "auth0|master-a", "contextKind": "library"},
    })).json()
    assert answered["code"] == 0, answered
    db_session.expire_all()
    turn_row = await db_session.get(CcSamSessionTurn, turn_id)
    assert turn_row is not None
    assert turn_row.follow_up_response == 0
    stored_after = await db_session.get(CcSamSession, session_id)
    assert stored_after.helpful == 1
    review = await review_sam_sessions(db_session, SAM_ID, view="agent")
    assert review["sessions"][0]["turns"][0]["followUpResponse"] is False
    assert review["sessions"][0]["turns"][0]["id"] == turn_id
    assert review["sessions"][0]["helpful"] is True


@pytest.mark.asyncio
async def test_follow_up_response_requires_follow_up_and_stays_off_helpfulness(
    db_session: AsyncSession,
):
    db_session.add(_agent(SAM_ID, "Portal-US"))
    await db_session.commit()
    claim = _library()
    with_follow = await record_completed_turn(
        db_session, claim, SAM_ID, "Q1", "A1", "Has a clinician already approved a limit?",
    )
    without_follow = await record_completed_turn(
        db_session,
        _library("auth0|master-a", with_follow.session_id),
        SAM_ID,
        "Q2",
        "A2",
        None,
    )
    await record_follow_up_response(db_session, _library("auth0|master-a", with_follow.session_id), with_follow.turn_id, True)
    db_session.expire_all()
    yes_row = await db_session.get(CcSamSessionTurn, with_follow.turn_id)
    assert yes_row.follow_up_response == 1
    session = await db_session.get(CcSamSession, with_follow.session_id)
    assert session.helpful is None
    with pytest.raises(APIException) as missing_follow:
        await record_follow_up_response(
            db_session, _library("auth0|master-a", with_follow.session_id), without_follow.turn_id, False,
        )
    assert missing_follow.value.code == 400
    await record_follow_up_response(db_session, _library("auth0|master-a", with_follow.session_id), with_follow.turn_id, False)
    db_session.expire_all()
    assert (await db_session.get(CcSamSessionTurn, with_follow.turn_id)).follow_up_response == 0
    review = await review_sam_sessions(db_session, SAM_ID, view="agent")
    turns = review["sessions"][0]["turns"]
    assert turns[0]["followUpResponse"] is False
    assert turns[1]["followUpResponse"] is None


OTHER_ID = "otherclient00000000000000000001"
CLIENT_A = "a" * 32
CLIENT_B = "b" * 32
ACCOUNT_A = "11111111-1111-4111-8111-111111111111"
ACCOUNT_B = "22222222-2222-4222-8222-222222222222"
BC_CLIENT_A = "33333333-3333-4333-8333-333333333333"
BC_CLIENT_B = "44444444-4444-4444-8444-444444444444"


def _verified_map(
    agent_id: str, name: str, account_id: str, companion_id: str, bridgingcare_client_id: str,
) -> CcBridgingcareCompanionMap:
    now = datetime.now()
    return CcBridgingcareCompanionMap(
        agent_id=agent_id,
        companion_id=companion_id,
        account_id=account_id,
        client_id=bridgingcare_client_id,
        expected_agent_name=name,
        verified_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_review_keeps_legacy_sessions_and_does_not_share_them(db_session: AsyncSession):
    db_session.add_all([
        _agent(SAM_ID, "Portal-US"),
        _agent(BILL_ID, "Bill Bob"),
        _agent(OTHER_ID, "Another Client"),
        AiDevice(id="watcher-1", mac_address="80:45:6B:4D:5D:74", agent_id=BILL_ID, alias="Betty"),
    ])
    await db_session.commit()
    legacy = (await record_completed_turn(
        db_session, _library(), SAM_ID, "Legacy question", "Legacy answer.", None,
    )).session_id
    owned = CcSamSession(
        session_id="billowned000000000000000000001",
        virtual_agent_id=SAM_ID,
        owner_subject="auth0|bill",
        context_kind="account_device",
        account_id=ACCOUNT_A,
        registered_device_id="band-1",
        nexus_client_id=BILL_ID,
        answer_count=1,
        created_at=datetime(2026, 11, 3, 12, 0, 0),
    )
    other = CcSamSession(
        session_id="otherowned00000000000000000001",
        virtual_agent_id=SAM_ID,
        owner_subject="auth0|other",
        context_kind="account_device",
        account_id=ACCOUNT_B,
        registered_device_id="band-2",
        nexus_client_id=OTHER_ID,
        answer_count=1,
        helpful=1,
        created_at=datetime(2026, 11, 3, 12, 1, 0),
    )
    db_session.add_all([
        owned,
        other,
        CcSamSessionTurn(
            session_id=owned.session_id, question="Bill question", answer="Bill answer.", created_at=owned.created_at,
        ),
    ])
    await db_session.commit()

    portal = await review_sam_sessions(db_session, SAM_ID)
    bill = await review_sam_sessions(db_session, BILL_ID)
    another = await review_sam_sessions(db_session, OTHER_ID)
    assert [item["sessionId"] for item in portal["sessions"]] == [legacy]
    assert portal["summary"]["helpful"] == 0
    assert [item["sessionId"] for item in bill["sessions"]] == [owned.session_id]
    assert bill["sessions"][0]["turns"][0]["question"] == "Bill question"
    assert [item["sessionId"] for item in another["sessions"]] == [other.session_id]
    assert another["summary"]["helpful"] == 1
    assert legacy not in [item["sessionId"] for item in bill["sessions"]]
    assert owned.session_id not in [item["sessionId"] for item in another["sessions"]]
    assert (await db_session.execute(select(func.count()).select_from(AiAgentChatHistory))).scalar_one() == 0
    watcher = await db_session.get(AiDevice, "watcher-1")
    assert watcher is not None
    assert watcher.agent_id == BILL_ID


@pytest.mark.asyncio
async def test_client_is_recorded_only_when_the_account_matches_one_binding(db_session: AsyncSession):
    db_session.add_all([
        _agent(SAM_ID, "Portal-US"),
        _agent(CLIENT_A, "Client A"),
        _agent(CLIENT_B, "Client B"),
        _verified_map(CLIENT_A, "Client A", ACCOUNT_A, "Nx-ClientA", BC_CLIENT_A),
        CcVirtualAgentBinding(client_id=CLIENT_A, virtual_agent_id=SAM_ID, created_by=1),
    ])
    await db_session.commit()

    verified = (await record_completed_turn(
        db_session, _device(account=ACCOUNT_A), SAM_ID, "Owned?", "Yes.", None,
    )).session_id
    row = await db_session.get(CcSamSession, verified)
    assert row is not None
    assert row.virtual_agent_id == SAM_ID
    assert row.nexus_client_id == CLIENT_A

    unbound = (await record_completed_turn(
        db_session, _device(account=ACCOUNT_B), SAM_ID, "Other account?", "No client.", None,
    )).session_id
    assert (await db_session.get(CcSamSession, unbound)).nexus_client_id is None

    library = (await record_completed_turn(
        db_session, _library(), SAM_ID, "Library?", "No client.", None,
    )).session_id
    assert (await db_session.get(CcSamSession, library)).nexus_client_id is None

    db_session.add(CcVirtualAgentBinding(client_id=CLIENT_B, virtual_agent_id=SAM_ID, created_by=1))
    await db_session.commit()
    ambiguous = (await record_completed_turn(
        db_session, _device(account=ACCOUNT_A), SAM_ID, "Two bindings?", "No client.", None,
    )).session_id
    assert (await db_session.get(CcSamSession, ambiguous)).nexus_client_id is None
    assert (await db_session.execute(select(func.count()).select_from(CcSamSessionTurn))).scalar_one() == 4


@pytest.mark.asyncio
async def test_a_binding_without_the_verified_account_does_not_assign_a_client(db_session: AsyncSession):
    db_session.add_all([
        _agent(SAM_ID, "Portal-US"),
        _agent(CLIENT_A, "Client A"),
        _agent(CLIENT_B, "Client B"),
        _verified_map(CLIENT_B, "Client B", ACCOUNT_B, "Nx-ClientB", BC_CLIENT_B),
        CcVirtualAgentBinding(client_id=CLIENT_A, virtual_agent_id=SAM_ID, created_by=1),
    ])
    await db_session.commit()
    mismatched = (await record_completed_turn(
        db_session, _device(account=ACCOUNT_B), SAM_ID, "Wrong client?", "Unassigned.", None,
    )).session_id
    stored = await db_session.get(CcSamSession, mismatched)
    assert stored is not None
    assert stored.virtual_agent_id == SAM_ID
    assert stored.nexus_client_id is None
    visible = await review_sam_sessions(db_session, CLIENT_A)
    assert visible["sessions"] == []
    other_client = await review_sam_sessions(db_session, CLIENT_B)
    assert other_client["sessions"] == []
    agent_review = await review_sam_sessions(db_session, SAM_ID)
    assert [item["sessionId"] for item in agent_review["sessions"]] == [mismatched]


@pytest.mark.asyncio
async def test_agent_view_shows_authorized_client_sessions_only(db_session: AsyncSession):
    db_session.add_all([
        _agent(SAM_ID, "Portal-US"),
        _agent(BILL_ID, "Bill Bob"),
        _agent(OTHER_ID, "Another Client"),
        AiDevice(id="watcher-1", mac_address="80:45:6B:4D:5D:74", agent_id=BILL_ID, alias="Betty"),
    ])
    await db_session.commit()
    legacy_ids = []
    for index in range(4):
        legacy_ids.append((await record_completed_turn(
            db_session, _library(f"auth0|legacy-{index}"), SAM_ID, f"Legacy {index}", "Stored answer.", None,
        )).session_id)
    bill = CcSamSession(
        session_id="clientaview0000000000000000001",
        virtual_agent_id=SAM_ID,
        owner_subject="auth0|client-a",
        context_kind="account_device",
        account_id=ACCOUNT_A,
        registered_device_id="band-a",
        nexus_client_id=BILL_ID,
        answer_count=1,
        helpful=1,
        created_at=datetime(2026, 11, 3, 13, 0, 0),
    )
    other = CcSamSession(
        session_id="clientbview0000000000000000001",
        virtual_agent_id=SAM_ID,
        owner_subject="auth0|client-b",
        context_kind="account_device",
        account_id=ACCOUNT_B,
        registered_device_id="band-b",
        nexus_client_id=OTHER_ID,
        answer_count=1,
        helpful=0,
        created_at=datetime(2026, 11, 3, 13, 1, 0),
    )
    db_session.add_all([bill, other])
    await db_session.commit()

    portal = await review_sam_sessions(db_session, SAM_ID)
    bill_only = await review_sam_sessions(db_session, BILL_ID)
    other_only = await review_sam_sessions(db_session, OTHER_ID)
    assert {item["sessionId"] for item in portal["sessions"]} == set(legacy_ids)
    assert len(portal["sessions"]) == 4
    assert [item["sessionId"] for item in bill_only["sessions"]] == [bill.session_id]
    assert [item["sessionId"] for item in other_only["sessions"]] == [other.session_id]

    scoped = await review_sam_sessions(
        db_session, SAM_ID, view="agent", allowed_client_ids=[BILL_ID],
    )
    assert {item["sessionId"] for item in scoped["sessions"]} == set(legacy_ids) | {bill.session_id}
    assert scoped["summary"]["helpful"] == 1
    assert scoped["summary"]["notHelpful"] == 0

    root = await review_sam_sessions(db_session, SAM_ID, view="agent", allowed_client_ids=None)
    assert {item["sessionId"] for item in root["sessions"]} == set(legacy_ids) | {bill.session_id, other.session_id}
    assert root["summary"]["notHelpful"] == 1
    assert (await db_session.execute(select(func.count()).select_from(AiAgentChatHistory))).scalar_one() == 0
    watcher = await db_session.get(AiDevice, "watcher-1")
    assert watcher is not None
    assert watcher.agent_id == BILL_ID


@pytest.mark.asyncio
async def test_historical_session_shape_survives_a_reversible_reassignment(db_session: AsyncSession):
    portal = "care-client-000000000000000001"
    sam = "virtual-agent-0000000000000001"
    session_id = "hist0000000000000000000000000001"
    created = datetime(2026, 11, 2, 15, 0, 0)
    feedback_at = datetime(2026, 11, 2, 15, 5, 0)
    db_session.add_all([
        _agent(portal, "Care Client"),
        _agent(sam, "Virtual Agent"),
        CcSamSession(
            session_id=session_id,
            virtual_agent_id=portal,
            owner_subject="auth0|owner",
            context_kind="account_device",
            account_id="account-1",
            registered_device_id="device-1",
            nexus_client_id=None,
            answer_count=2,
            helpful=1,
            feedback_at=feedback_at,
            created_at=created,
        ),
        CcSamSessionTurn(
            session_id=session_id,
            created_at=created,
            question="How do I configure an alarm?",
            answer="An authorized administrator opens Manage Alarm.",
            follow_up="Which device?",
        ),
    ])
    await db_session.commit()
    claim = session_claim_from_body({
        "session": {
            "ownerSubject": "auth0|owner",
            "contextKind": "account_device",
            "accountId": "account-1",
            "registeredDeviceId": "device-1",
            "sessionId": session_id,
        },
    })
    assert claim is not None
    await require_owned_session(db_session, claim, portal)
    with pytest.raises(APIException) as denied:
        await require_owned_session(db_session, claim, sam)
    assert denied.value.code == 403

    before = await review_sam_sessions(db_session, portal, view="client")
    assert before["summary"]["helpful"] == 1
    assert before["sessions"][0]["sessionId"] == session_id
    assert before["sessions"][0]["nexusClientId"] is None
    assert before["sessions"][0]["turns"][0]["question"] == "How do I configure an alarm?"
    assert (await review_sam_sessions(db_session, sam, view="agent"))["sessions"] == []

    row = await db_session.get(CcSamSession, session_id)
    assert row is not None
    row.virtual_agent_id = sam
    row.nexus_client_id = portal
    await db_session.commit()

    moved = await db_session.get(CcSamSession, session_id)
    assert moved is not None
    assert moved.answer_count == 2
    assert moved.helpful == 1
    assert moved.feedback_at == feedback_at
    assert moved.created_at == created
    client_view = await review_sam_sessions(db_session, portal, view="client")
    assert client_view["sessions"][0]["sessionId"] == session_id
    assert client_view["sessions"][0]["turns"][0]["answer"] == "An authorized administrator opens Manage Alarm."
    assert (await review_sam_sessions(db_session, sam, view="agent"))["sessions"][0]["sessionId"] == session_id
    hidden = await review_sam_sessions(db_session, sam, view="agent", allowed_client_ids=["other-client"])
    assert hidden["sessions"] == []
    await require_owned_session(db_session, claim, sam)

    moved.virtual_agent_id = portal
    moved.nexus_client_id = None
    await db_session.commit()
    restored = await db_session.get(CcSamSession, session_id)
    assert restored is not None
    assert restored.virtual_agent_id == portal
    assert restored.nexus_client_id is None
    assert restored.answer_count == 2
    assert (await review_sam_sessions(db_session, portal, view="client"))["sessions"][0]["nexusClientId"] is None
    assert (await review_sam_sessions(db_session, sam, view="agent"))["sessions"] == []


def test_generation_module_and_phase_2a_reader_stay_separate():
    source = (API_DIR / "careconnect_api" / "sam_conversation.py").read_text()
    route = (API_DIR / "careconnect_api" / "routers" / "partner_bridgingcare.py").read_text()
    reader = (API_DIR / "careconnect_api" / "routers" / "virtual_agent_binding.py").read_text()
    migration = (API_DIR / "migrations" / "033_sam_follow_up_response.sql").read_text()
    assert "sam_session" not in source
    assert "AiAgentChatHistory" not in source
    assert "You are Sam, the read-only BridgingCare assistant." in source
    assert "cc_virtual_agent_message" not in route
    assert "/sam/follow-up-response" in route
    assert "record_follow_up_response" in route
    assert "follow_up_response" in migration
    assert "helpful" not in migration.split("ADD COLUMN", 1)[1]
    assert "CcVirtualAgentMessage" in reader
    assert "review_sam_sessions" in reader
