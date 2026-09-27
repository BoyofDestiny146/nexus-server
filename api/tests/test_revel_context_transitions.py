"""Historical REVEL CONTEXT topic transitions. Per-session, not current status."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.chat_events import (
    CHAT_TYPE_CAREGIVER,
    CHAT_TYPE_CLIENT,
    CHAT_TYPE_SYSTEM,
    encode_revel_context_timeline,
    is_revel_context_event,
    parse_revel_timeline,
    persist_revel_context_if_changed,
    persist_revel_timeline,
    should_record_revel_context,
)
from careconnect_api.knowledge_retrieval import _record_search_revel_context
from careconnect_api.models import AiAgentChatHistory, SysUser
from careconnect_api.revel_status import latest_revel_event
from careconnect_api.revel_write import revel_execute_enabled, revel_puts_attempted
from careconnect_api.triage.runner import _render_dialogue, compose_assessment_dialogue


QUESTIONS = (
    "What do you know about CareConnect?",
    "Tell me more about CareConnect.",
    "How does the briefs sensor detect humidity?",
    "Does that sensor need wifi?",
    "Back to CareConnect overview.",
)
TAGS = (
    "care_overview",
    "care_overview",
    "bioev_humidity",
    "bioev_humidity",
    "care_overview",
)
AUTO = (True, True, False, False, True)
REPLIES = (
    "CareConnect is the companion overview.",
    "It covers daily care topics.",
    "It measures humidity in the brief.",
    "It posts a silent alert.",
    "Back to the overview.",
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


async def _onboard(client: AsyncClient, token: str, name: str = "Betty") -> str:
    resp = await client.post(
        "/api/agent/onboard", json={"name": name}, headers=_auth(token)
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["agentId"]


def test_should_record_revel_context_dedupes_same_tag():
    assert should_record_revel_context(None, "care_overview") is True
    assert should_record_revel_context("", "care_overview") is True
    assert should_record_revel_context("care_overview", "care_overview") is False
    assert should_record_revel_context("care_overview", "bioev_humidity") is True
    assert should_record_revel_context("bioev_humidity", "bioev_humidity") is False
    assert should_record_revel_context("bioev_humidity", "care_overview") is True
    assert should_record_revel_context("care_overview", "") is False
    assert should_record_revel_context(None, None) is False


def test_encode_revel_context_is_structured_not_dialogue():
    content = encode_revel_context_timeline(
        tag="care_overview",
        auto_trigger=True,
        delivered_at="2026-09-26T15:00:01+00:00",
        session_id="sess-a",
        device_name="Lobby",
    )
    parsed = parse_revel_timeline(content)
    assert parsed is not None
    assert is_revel_context_event(parsed)
    assert parsed["type"] == "REVEL_CONTEXT"
    assert parsed["event_type"] == "revel_context"
    assert parsed["session_id"] == "sess-a"
    assert parsed["tag"] == "care_overview"
    assert parsed["auto_trigger"] is True
    assert parsed["revel_device_name"] == "Lobby"
    assert content.endswith("REVEL CONTEXT")
    assert "graphql" not in content.lower()
    assert "mutation" not in content.lower()


async def _add_chat(
    db: AsyncSession,
    *,
    row_id: int,
    agent_id: str,
    session_id: str,
    chat_type: int,
    content: str,
    when: datetime,
) -> None:
    stamp = when.replace(tzinfo=None) if when.tzinfo else when
    db.add(
        AiAgentChatHistory(
            id=row_id,
            agent_id=agent_id,
            session_id=session_id,
            chat_type=chat_type,
            content=content,
            created_at=stamp,
            updated_at=stamp,
        )
    )
    await db.flush()


async def _session_rows(
    db: AsyncSession, agent_id: str, session_id: str
) -> list[AiAgentChatHistory]:
    return list(
        (
            await db.execute(
                select(AiAgentChatHistory)
                .where(
                    AiAgentChatHistory.agent_id == agent_id,
                    AiAgentChatHistory.session_id == session_id,
                )
                .order_by(AiAgentChatHistory.id.asc())
            )
        ).scalars().all()
    )


def _context_tags(rows: list[AiAgentChatHistory]) -> list[str]:
    tags: list[str] = []
    for row in rows:
        parsed = parse_revel_timeline(row.content)
        if not is_revel_context_event(parsed):
            continue
        tags.append(str((parsed or {}).get("tag") or ""))
    return tags


@pytest.mark.asyncio
async def test_conversation_records_context_only_when_tag_changes(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    session_id = "sess-live"
    start = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)
    next_id = 1
    puts_before = revel_puts_attempted
    assert revel_execute_enabled() is False

    for index, (question, tag, auto, reply) in enumerate(
        zip(QUESTIONS, TAGS, AUTO, REPLIES)
    ):
        when = start + timedelta(minutes=index)
        await _add_chat(
            db_session,
            row_id=next_id,
            agent_id=agent_id,
            session_id=session_id,
            chat_type=CHAT_TYPE_CLIENT,
            content=question,
            when=when,
        )
        next_id += 1
        recorded = await persist_revel_context_if_changed(
            db_session,
            agent_id=agent_id,
            session_id=session_id,
            tag=tag,
            auto_trigger=auto,
            at=when + timedelta(seconds=1),
        )
        if tag != (TAGS[index - 1] if index else None):
            assert recorded is not None
            assert recorded["chatType"] == CHAT_TYPE_SYSTEM
            parsed = parse_revel_timeline(recorded["content"])
            assert parsed is not None
            assert parsed["tag"] == tag
            assert parsed["type"] == "REVEL_CONTEXT"
            assert parsed["session_id"] == session_id
            next_id = max(next_id, int(recorded["id"]) + 1)
        else:
            assert recorded is None
        await _add_chat(
            db_session,
            row_id=next_id,
            agent_id=agent_id,
            session_id=session_id,
            chat_type=CHAT_TYPE_CAREGIVER,
            content=reply,
            when=when + timedelta(seconds=2),
        )
        next_id += 1

    await db_session.commit()
    rows = await _session_rows(db_session, agent_id, session_id)
    assert _context_tags(rows) == [
        "care_overview",
        "bioev_humidity",
        "care_overview",
    ]
    kinds = []
    for row in rows:
        parsed = parse_revel_timeline(row.content)
        if is_revel_context_event(parsed):
            kinds.append("REVEL_CONTEXT")
        elif row.chat_type == CHAT_TYPE_CLIENT:
            kinds.append("CLIENT")
        elif row.chat_type == CHAT_TYPE_CAREGIVER:
            kinds.append("CAREGIVER")
        else:
            kinds.append("OTHER")
    assert kinds == [
        "CLIENT",
        "REVEL_CONTEXT",
        "CAREGIVER",
        "CLIENT",
        "CAREGIVER",
        "CLIENT",
        "REVEL_CONTEXT",
        "CAREGIVER",
        "CLIENT",
        "CAREGIVER",
        "CLIENT",
        "REVEL_CONTEXT",
        "CAREGIVER",
    ]
    for row in rows:
        if is_revel_context_event(parse_revel_timeline(row.content)):
            assert row.chat_type == CHAT_TYPE_SYSTEM

    listed = await client.get(
        f"/api/agent/{agent_id}/chat-history/{session_id}",
        headers=_auth(admin_token),
    )
    body = listed.json()
    assert body["code"] == 0, body
    items = body["data"]
    http_tags = []
    for item in items:
        parsed = parse_revel_timeline(item["content"])
        if is_revel_context_event(parsed):
            http_tags.append(parsed["tag"])
            assert item["chatType"] == CHAT_TYPE_SYSTEM
    assert http_tags == ["care_overview", "bioev_humidity", "care_overview"]
    assert revel_execute_enabled() is False
    assert revel_puts_attempted == puts_before

    dialogue = _render_dialogue(rows)
    assert dialogue.count("REVEL_CONTEXT") == 3
    assert "tag: care_overview" in dialogue
    assert "tag: bioev_humidity" in dialogue
    assert "client: What do you know about CareConnect?" in dialogue
    assert dialogue.index("client: What do you know about CareConnect?") < dialogue.index(
        "tag: care_overview"
    )
    assert "REVEL_DISPLAY" not in dialogue


@pytest.mark.asyncio
async def test_new_session_may_record_its_own_initial_context(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    first = await persist_revel_context_if_changed(
        db_session,
        agent_id=agent_id,
        session_id="sess-a",
        tag="care_overview",
        auto_trigger=True,
    )
    same = await persist_revel_context_if_changed(
        db_session,
        agent_id=agent_id,
        session_id="sess-a",
        tag="care_overview",
        auto_trigger=True,
    )
    other_session = await persist_revel_context_if_changed(
        db_session,
        agent_id=agent_id,
        session_id="sess-b",
        tag="care_overview",
        auto_trigger=True,
    )
    await db_session.commit()
    assert first is not None
    assert same is None
    assert other_session is not None
    a_rows = await _session_rows(db_session, agent_id, "sess-a")
    b_rows = await _session_rows(db_session, agent_id, "sess-b")
    assert _context_tags(a_rows) == ["care_overview"]
    assert _context_tags(b_rows) == ["care_overview"]


@pytest.mark.asyncio
async def test_display_event_stays_separate_from_context(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    await persist_revel_context_if_changed(
        db_session,
        agent_id=agent_id,
        session_id="sess-a",
        tag="care_overview",
        auto_trigger=True,
    )
    display = await persist_revel_timeline(
        db_session,
        agent_id=agent_id,
        requested="show home",
        intent="SHOW_HOME",
        device_name="Lobby",
        result="skipped",
        delivered_at=datetime.now(timezone.utc),
        tag="care_overview",
        reason="revel_write_disabled",
    )
    await db_session.commit()
    assert display is not None
    rows = (
        await db_session.execute(
            select(AiAgentChatHistory)
            .where(AiAgentChatHistory.agent_id == agent_id)
            .order_by(AiAgentChatHistory.id.asc())
        )
    ).scalars().all()
    parsed_rows = [parse_revel_timeline(row.content) for row in rows]
    assert is_revel_context_event(parsed_rows[0])
    assert parsed_rows[1] is not None
    assert not is_revel_context_event(parsed_rows[1])
    assert parsed_rows[1]["event_type"] == "revel_display"
    last = await latest_revel_event(db_session, agent_id)
    assert last is not None
    assert last.get("result") == "skipped"


@pytest.mark.asyncio
async def test_knowledge_search_hook_persists_session_scoped_transitions(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    puts_before = revel_puts_attempted
    payload_overview = {
        "clientId": agent_id,
        "grounded": {
            "context": [
                {"revelTag": "care_overview", "revelAutoTrigger": True},
            ]
        },
        "results": [],
    }
    payload_humidity = {
        "clientId": agent_id,
        "grounded": {
            "context": [
                {"revelTag": "bioev_humidity", "revelAutoTrigger": False},
            ]
        },
        "results": [],
    }
    await _record_search_revel_context(
        db_session, payload_overview, session_id="sess-k"
    )
    await _record_search_revel_context(
        db_session, payload_overview, session_id="sess-k"
    )
    await _record_search_revel_context(
        db_session, payload_humidity, session_id="sess-k"
    )
    await _record_search_revel_context(
        db_session, payload_humidity, session_id="sess-k"
    )
    await _record_search_revel_context(
        db_session, payload_overview, session_id="sess-k"
    )
    rows = await _session_rows(db_session, agent_id, "sess-k")
    assert _context_tags(rows) == [
        "care_overview",
        "bioev_humidity",
        "care_overview",
    ]
    assert revel_execute_enabled() is False
    assert revel_puts_attempted == puts_before


@pytest.mark.asyncio
async def test_assessment_still_supports_revel_context_prepend(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    _ = client, admin_token, db_session
    content = encode_revel_context_timeline(
        tag="bioev_humidity",
        auto_trigger=False,
        delivered_at="2026-09-26T15:02:01+00:00",
        session_id="sess-a",
    )
    topic = compose_assessment_dialogue(
        [
            AiAgentChatHistory(
                chat_type=CHAT_TYPE_CLIENT,
                content="How does the briefs sensor detect humidity?",
            ),
            AiAgentChatHistory(chat_type=CHAT_TYPE_SYSTEM, content=content),
            AiAgentChatHistory(
                chat_type=CHAT_TYPE_CAREGIVER,
                content="It measures humidity in the brief.",
            ),
        ],
        topic_context="REVEL_CONTEXT\ntag: bioev_humidity\nauto_trigger: false",
    )
    assert topic.startswith("REVEL_CONTEXT")
    assert topic.count("REVEL_CONTEXT") >= 2
    assert "REVEL_DISPLAY" not in topic
    assert "How does the briefs sensor detect humidity?" in topic
