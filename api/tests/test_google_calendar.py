"""Google Calendar read-only iCal connect, parse, and reminder delivery."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession as AS

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token
from careconnect_api.calendar_poller import poll_google_calendars
from careconnect_api.gcal_ical import (
    CalendarError,
    eligible_for_speech,
    is_due,
    parse_ics,
    spoken_text,
    validate_ical_url,
)
from careconnect_api.integration_crypto import decrypt_secret
from careconnect_api.models import AiAgent, AiDevice, ClientIntegration, SysUser
from careconnect_api.watcher_device import device_id_for_mac


_TZ = ZoneInfo("America/Chicago")
_TOKEN = "private-TESTTOKENXYZ"
_ICAL_URL = (
    "https://calendar.google.com/calendar/ical/"
    f"ada%40example.com/{_TOKEN}/basic.ics"
)
_SPOKEN = (
    "Hi, please take your heat medication.\n"
    "Remember only at 4:00 with food.\n"
    "please tell me when you have taken it.\n"
    "Thank you, and have a pleasant day."
)
_COLON_MAC = "E0:72:A1:DB:36:40"
_OTHER_MAC = "AA:BB:CC:DD:EE:00"


@pytest.fixture(autouse=True)
def _public_google_dns(monkeypatch):
    monkeypatch.setattr(
        "careconnect_api.gcal_ical.resolve_host_ips",
        lambda host: ["142.250.72.110"],
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


async def _onboard(client: AsyncClient, token: str, name: str = "Ada Cal") -> str:
    resp = await client.post(
        "/api/agent/onboard", json={"name": name}, headers=_auth(token)
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["agentId"]


def _vevent_timed(*, uid: str, start: str, desc: str, title: str, rrule: str | None = None) -> str:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTART;TZID=America/Chicago:{start}",
        f"SUMMARY:{title}",
    ]
    if desc:
        folded = desc.replace("\n", "\\n")
        lines.append(f"DESCRIPTION:{folded}")
    if rrule:
        lines.append(f"RRULE:{rrule}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def _ics(*events: str) -> str:
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Nexus//Test//EN\r\n"
        + "\r\n".join(events)
        + "\r\nEND:VCALENDAR\r\n"
    )


def _med_ics(start: str = "20260924T160000", *, rrule: str | None = None) -> str:
    return _ics(
        _vevent_timed(
            uid="med-afternoon@example.com",
            start=start,
            desc=_SPOKEN,
            title="Afternoon Medication Reminder",
            rrule=rrule,
        )
    )


async def _bind_watcher(db: AsyncSession, agent_id: str, mac: str = _COLON_MAC) -> AiDevice:
    dev = AiDevice(
        id=device_id_for_mac(mac),
        mac_address=mac,
        agent_id=agent_id,
        device_type="W1-A",
        firmware_type="xiaozhi",
        board="sensecap_watcher",
    )
    db.add(dev)
    await db.commit()
    await db.refresh(dev)
    return dev


async def _connect_gcal(client: AsyncClient, token: str, agent_id: str, url: str = _ICAL_URL):
    resp = await client.put(
        f"/api/agent/{agent_id}/integrations/google-calendar",
        json={"icalUrl": url},
        headers=_auth(token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


# ---------- parser ----------


def test_simple_vevent_parsed():
    occs = parse_ics(
        _med_ics(),
        now=datetime(2026, 9, 24, 16, 0, tzinfo=_TZ),
    )
    timed = [o for o in occs if o.uid == "med-afternoon@example.com"]
    assert timed
    occ = timed[0]
    assert occ.title == "Afternoon Medication Reminder"
    assert occ.description.startswith("Hi, please take your heat medication.")
    assert occ.all_day is False
    assert occ.start.tzinfo is not None
    assert occ.start.astimezone(_TZ).hour == 16
    assert spoken_text(occ) == _SPOKEN
    assert eligible_for_speech(occ) is True


def test_title_fallback_when_description_empty():
    ics = _ics(
        _vevent_timed(
            uid="title-only@example.com",
            start="20260924T160000",
            desc="",
            title="Afternoon Medication Reminder",
        )
    )
    occs = parse_ics(ics, now=datetime(2026, 9, 24, 16, 0, tzinfo=_TZ))
    occ = next(o for o in occs if o.uid == "title-only@example.com")
    assert occ.description == ""
    assert spoken_text(occ) == "Afternoon Medication Reminder"


def test_recurring_weekday_vevent_expanded():
    ics = _med_ics("20260921T160000", rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR")
    occs = parse_ics(
        ics,
        now=datetime(2026, 9, 21, 12, 0, tzinfo=_TZ),
        window_start=datetime(2026, 9, 21, 0, 0, tzinfo=_TZ),
        window_end=datetime(2026, 9, 26, 0, 0, tzinfo=_TZ),
    )
    starts = [o.start.astimezone(_TZ).date() for o in occs if not o.all_day]
    assert len(starts) == 5
    assert starts[0].isoformat() == "2026-09-21"
    assert starts[-1].isoformat() == "2026-09-25"
    assert occs[0].recurring is True


def test_timezone_aware_event_due_at_chicago_4pm():
    occs = parse_ics(
        _med_ics("20260924T160000"),
        now=datetime(2026, 9, 24, 16, 0, tzinfo=_TZ),
    )
    occ = occs[0]
    now_utc = datetime(2026, 9, 24, 21, 0, tzinfo=ZoneInfo("UTC"))  # 16:00 CDT
    assert is_due(occ, now_utc) is True
    too_late = datetime(2026, 9, 24, 22, 0, tzinfo=ZoneInfo("UTC"))
    assert is_due(occ, too_late) is False


def test_all_day_event_not_eligible_for_speech():
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        "UID:allday@example.com\r\nDTSTART;VALUE=DATE:20260924\r\n"
        "SUMMARY:Family visit\r\nDESCRIPTION:Do not speak this all-day note.\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    occs = parse_ics(ics, now=datetime(2026, 9, 24, 16, 0, tzinfo=_TZ))
    occ = next(o for o in occs if o.uid == "allday@example.com")
    assert occ.all_day is True
    assert occ.public_preview()["allDay"] is True
    assert "T09:00" not in occ.public_preview()["start"]
    assert occ.public_preview()["start"] == "2026-09-24"
    assert eligible_for_speech(occ) is False
    assert is_due(occ, datetime(2026, 9, 24, 16, 0, tzinfo=_TZ)) is False


def test_validate_rejects_http_and_localhost():
    with pytest.raises(Exception):
        validate_ical_url("http://calendar.google.com/calendar/ical/x/private-abc/basic.ics")
    with pytest.raises(Exception):
        validate_ical_url("https://127.0.0.1/calendar/ical/x/private-abc/basic.ics")
    with pytest.raises(Exception):
        validate_ical_url("file:///etc/passwd")


def test_validate_rejects_private_dns(monkeypatch):
    monkeypatch.setattr(
        "careconnect_api.gcal_ical.resolve_host_ips",
        lambda host: ["127.0.0.1"],
    )
    with pytest.raises(Exception):
        validate_ical_url(_ICAL_URL)


# ---------- connect / encrypt / GET ----------


@pytest.mark.asyncio
async def test_connect_stores_encrypted_url_not_plaintext(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, caplog
):
    agent_id = await _onboard(client, admin_token)
    caplog.set_level(logging.INFO)
    data = await _connect_gcal(client, admin_token, agent_id)
    assert data["connected"] is True
    assert data["access"] == "read_only"
    assert data["calendarHost"] == "calendar.google.com"
    assert _ICAL_URL not in _blob(data)
    assert _TOKEN not in _blob(data)
    assert data.get("icalUrl") is None

    listed = await client.get(
        f"/api/agent/{agent_id}/integrations", headers=_auth(admin_token)
    )
    body = listed.json()
    assert body["code"] == 0, body
    assert _ICAL_URL not in _blob(body)
    assert _TOKEN not in _blob(body)
    gcal = next(r for r in body["data"]["list"] if r["provider"] == "google_calendar")
    assert gcal["connected"] is True
    assert gcal.get("icalUrl") is None
    assert gcal.get("url") is None

    db_session.expire_all()
    row = (
        await db_session.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == "google_calendar",
            )
        )
    ).scalar_one()
    assert row.secret_enc
    assert _ICAL_URL not in (row.secret_enc or "")
    assert _TOKEN not in (row.secret_enc or "")
    assert decrypt_secret(row.secret_enc) == _ICAL_URL
    assert _ICAL_URL not in caplog.text
    assert _TOKEN not in caplog.text


@pytest.mark.asyncio
async def test_get_never_returns_ical_url(
    client: AsyncClient, admin_token: str
):
    agent_id = await _onboard(client, admin_token)
    await _connect_gcal(client, admin_token, agent_id)
    listed = await client.get(
        f"/api/agent/{agent_id}/integrations", headers=_auth(admin_token)
    )
    blob = _blob(listed.json())
    assert "calendar.google.com/calendar/ical" not in blob
    assert _TOKEN not in blob


@pytest.mark.asyncio
async def test_disconnect_removes_only_integration(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    await _connect_gcal(client, admin_token, agent_id)
    deleted = await client.delete(
        f"/api/agent/{agent_id}/integrations/google-calendar",
        headers=_auth(admin_token),
    )
    assert deleted.json()["code"] == 0
    db_session.expire_all()
    assert await db_session.get(AiAgent, agent_id) is not None
    n = (
        await db_session.execute(
            select(func.count())
            .select_from(ClientIntegration)
            .where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == "google_calendar",
            )
        )
    ).scalar_one()
    assert int(n or 0) == 0


@pytest.mark.asyncio
async def test_connection_with_mocked_ics(
    client: AsyncClient, admin_token: str, monkeypatch
):
    agent_id = await _onboard(client, admin_token)
    await _connect_gcal(client, admin_token, agent_id)

    async def _fake_fetch(url: str, **_k):
        assert url == _ICAL_URL
        return _med_ics()

    monkeypatch.setattr("careconnect_api.routers.integrations.fetch_ics", _fake_fetch)
    resp = await client.post(
        f"/api/agent/{agent_id}/integrations/google-calendar/test",
        headers=_auth(admin_token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["ok"] is True
    assert data["eventCount"] >= 1
    assert data["nextEvent"]["title"] == "Afternoon Medication Reminder"
    assert _TOKEN not in _blob(body)


# ---------- delivery ----------


class _SpeakCapture:
    def __init__(self, *, spoken: int = 1):
        self.calls: list[dict] = []
        self.spoken = spoken

    async def __call__(self, *, mac, device_id, text):
        self.calls.append({"mac": mac, "deviceId": device_id, "text": text})
        return {"attempted": True, "ok": self.spoken > 0, "spoken": self.spoken}


@pytest.mark.asyncio
async def test_bound_watcher_receives_description_once(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, db_engine
):
    agent_id = await _onboard(client, admin_token)
    await _connect_gcal(client, admin_token, agent_id)
    await _bind_watcher(db_session, agent_id)
    speak = _SpeakCapture()
    now = datetime(2026, 9, 24, 16, 0, tzinfo=_TZ)
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)

    async def fetch(_url: str, **_k):
        return _med_ics()

    summary = await poll_google_calendars(
        session_factory=factory, now=now, fetch_fn=fetch, speak_fn=speak
    )
    assert summary["delivered"] == 1
    assert len(speak.calls) == 1
    assert speak.calls[0]["text"] == _SPOKEN
    assert speak.calls[0]["mac"] == _COLON_MAC
    assert "This is your reminder" not in speak.calls[0]["text"]

    summary2 = await poll_google_calendars(
        session_factory=factory, now=now, fetch_fn=fetch, speak_fn=speak
    )
    assert summary2["delivered"] == 0
    assert len(speak.calls) == 1


@pytest.mark.asyncio
async def test_title_fallback_is_spoken(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, db_engine
):
    agent_id = await _onboard(client, admin_token)
    await _connect_gcal(client, admin_token, agent_id)
    await _bind_watcher(db_session, agent_id)
    speak = _SpeakCapture()
    now = datetime(2026, 9, 24, 16, 0, tzinfo=_TZ)
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)
    ics = _ics(
        _vevent_timed(
            uid="title-only@example.com",
            start="20260924T160000",
            desc="",
            title="Afternoon Medication Reminder",
        )
    )

    async def fetch(_url, **_k):
        return ics

    await poll_google_calendars(
        session_factory=factory, now=now, fetch_fn=fetch, speak_fn=speak
    )
    assert speak.calls[0]["text"] == "Afternoon Medication Reminder"


@pytest.mark.asyncio
async def test_all_day_event_is_not_spoken(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, db_engine
):
    agent_id = await _onboard(client, admin_token)
    await _connect_gcal(client, admin_token, agent_id)
    await _bind_watcher(db_session, agent_id)
    speak = _SpeakCapture()
    now = datetime(2026, 9, 24, 16, 0, tzinfo=_TZ)
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        "UID:allday@example.com\r\nDTSTART;VALUE=DATE:20260924\r\n"
        "SUMMARY:Family visit\r\nDESCRIPTION:Do not speak this.\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )

    async def fetch(_url, **_k):
        return ics

    await poll_google_calendars(
        session_factory=factory, now=now, fetch_fn=fetch, speak_fn=speak
    )
    assert speak.calls == []
    db_session.expire_all()
    row = (
        await db_session.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == "google_calendar",
            )
        )
    ).scalar_one()
    meta = json.loads(row.metadata_json or "{}")
    upcoming = meta.get("upcoming") or []
    assert any(ev.get("allDay") and ev.get("title") == "Family visit" for ev in upcoming)


@pytest.mark.asyncio
async def test_unrelated_client_never_receives_message(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, db_engine
):
    agent_a = await _onboard(client, admin_token, "Ada")
    agent_b = await _onboard(client, admin_token, "Bea")
    url_a = _ICAL_URL
    url_b = (
        "https://calendar.google.com/calendar/ical/"
        "bea%40example.com/private-OTHERTOKEN/basic.ics"
    )
    await _connect_gcal(client, admin_token, agent_a, url_a)
    await _connect_gcal(client, admin_token, agent_b, url_b)
    await _bind_watcher(db_session, agent_a, _COLON_MAC)
    await _bind_watcher(db_session, agent_b, _OTHER_MAC)
    speak = _SpeakCapture()
    now = datetime(2026, 9, 24, 16, 0, tzinfo=_TZ)
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)

    async def fetch(url: str, **_k):
        if "OTHERTOKEN" in url:
            return _ics(
                _vevent_timed(
                    uid="bea-event@example.com",
                    start="20260924T090000",
                    desc="Bea only",
                    title="Bea reminder",
                )
            )
        return _med_ics()

    await poll_google_calendars(
        session_factory=factory, now=now, fetch_fn=fetch, speak_fn=speak
    )
    texts = [c["text"] for c in speak.calls]
    assert _SPOKEN in texts
    assert "Bea only" not in texts
    macs = {c["mac"] for c in speak.calls}
    assert _COLON_MAC in macs
    assert _OTHER_MAC not in macs


@pytest.mark.asyncio
async def test_no_bound_watcher_safe_skip(
    client: AsyncClient, admin_token: str, db_engine, caplog
):
    agent_id = await _onboard(client, admin_token)
    await _connect_gcal(client, admin_token, agent_id)
    speak = _SpeakCapture()
    now = datetime(2026, 9, 24, 16, 0, tzinfo=_TZ)
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)
    caplog.set_level(logging.INFO)

    async def fetch(_url, **_k):
        return _med_ics()

    summary = await poll_google_calendars(
        session_factory=factory, now=now, fetch_fn=fetch, speak_fn=speak
    )
    assert speak.calls == []
    assert summary["errors"] == 0
    assert "no_bound_watcher" in caplog.text
    assert _TOKEN not in caplog.text
    assert _ICAL_URL not in caplog.text


@pytest.mark.asyncio
async def test_watcher_offline_safe_skip_not_fired(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, db_engine
):
    agent_id = await _onboard(client, admin_token)
    await _connect_gcal(client, admin_token, agent_id)
    await _bind_watcher(db_session, agent_id)
    speak = _SpeakCapture(spoken=0)
    now = datetime(2026, 9, 24, 16, 0, tzinfo=_TZ)
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)

    async def fetch(_url, **_k):
        return _med_ics()

    await poll_google_calendars(
        session_factory=factory, now=now, fetch_fn=fetch, speak_fn=speak
    )
    assert len(speak.calls) == 1
    speak.spoken = 1
    await poll_google_calendars(
        session_factory=factory, now=now, fetch_fn=fetch, speak_fn=speak
    )
    assert len(speak.calls) == 2
    assert speak.calls[1]["text"] == _SPOKEN


@pytest.mark.asyncio
async def test_bad_calendar_fetch_does_not_stop_scheduler(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, db_engine, caplog
):
    agent_bad = await _onboard(client, admin_token, "Bad Cal")
    agent_ok = await _onboard(client, admin_token, "Ok Cal")
    await _connect_gcal(client, admin_token, agent_bad, _ICAL_URL)
    other = (
        "https://calendar.google.com/calendar/ical/"
        "ok%40example.com/private-OKTOKEN/basic.ics"
    )
    await _connect_gcal(client, admin_token, agent_ok, other)
    await _bind_watcher(db_session, agent_ok, _OTHER_MAC)
    speak = _SpeakCapture()
    now = datetime(2026, 9, 24, 16, 0, tzinfo=_TZ)
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)
    caplog.set_level(logging.INFO)

    async def fetch(url: str, **_k):
        if "TESTTOKENXYZ" in url:
            raise CalendarError("fetch_failed")
        return _med_ics()

    summary = await poll_google_calendars(
        session_factory=factory, now=now, fetch_fn=fetch, speak_fn=speak
    )
    assert summary["errors"] == 1
    assert summary["delivered"] == 1
    assert speak.calls[0]["text"] == _SPOKEN
    assert _TOKEN not in caplog.text
    assert _ICAL_URL not in caplog.text
    assert "fetch_failed" in caplog.text or summary["errors"] == 1


@pytest.mark.asyncio
async def test_multiple_bound_watchers_all_receive(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, db_engine
):
    agent_id = await _onboard(client, admin_token)
    await _connect_gcal(client, admin_token, agent_id)
    await _bind_watcher(db_session, agent_id, _COLON_MAC)
    await _bind_watcher(db_session, agent_id, _OTHER_MAC)
    speak = _SpeakCapture()
    now = datetime(2026, 9, 24, 16, 0, tzinfo=_TZ)
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)

    async def fetch(_url, **_k):
        return _med_ics()

    await poll_google_calendars(
        session_factory=factory, now=now, fetch_fn=fetch, speak_fn=speak
    )
    macs = {c["mac"] for c in speak.calls}
    assert macs == {_COLON_MAC, _OTHER_MAC}
    assert all(c["text"] == _SPOKEN for c in speak.calls)


@pytest.mark.asyncio
async def test_stale_occurrence_is_not_spoken(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, db_engine
):
    agent_id = await _onboard(client, admin_token)
    await _connect_gcal(client, admin_token, agent_id)
    await _bind_watcher(db_session, agent_id)
    speak = _SpeakCapture()
    now = datetime(2026, 9, 24, 18, 0, tzinfo=_TZ)  # two hours late
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AS)

    async def fetch(_url, **_k):
        return _med_ics("20260924T160000")

    await poll_google_calendars(
        session_factory=factory, now=now, fetch_fn=fetch, speak_fn=speak
    )
    assert speak.calls == []


def test_ical_url_not_in_module_log_format():
    from careconnect_api.gcal_ical import fetch_ics
    from careconnect_api.calendar_poller import poll_one
    import inspect

    for fn in (fetch_ics, poll_one):
        src = inspect.getsource(fn)
        assert "log." in src or fn is fetch_ics
        assert "log.info(url" not in src
        assert "log.warning(url" not in src
        assert "log.error(url" not in src
