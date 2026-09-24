"""Read-only Google Calendar iCal fetch + occurrence expansion.

Nexus never creates, edits, moves, or deletes Google Calendar events.
The private iCal URL is a secret credential: never log it, never return it.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from dateutil.rrule import rrulestr
from icalendar import Calendar

from .envelope import APIException
from .settings import settings


log = logging.getLogger("gcal")

PROVIDER = "google_calendar"
ALLOWED_HOSTS = frozenset({"calendar.google.com", "www.google.com"})
_MAX_REDIRECTS = 3
ResolveFn = Callable[[str], list[str]]


class CalendarError(Exception):
    """Safe error: ``str()`` never includes the iCal URL."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True)
class CalendarOccurrence:
    provider: str
    uid: str
    title: str
    description: str
    location: str
    start: datetime
    end: datetime | None
    all_day: bool
    recurring: bool

    def public_preview(self) -> dict[str, Any]:
        start_out: str
        if self.all_day:
            start_out = self.start.date().isoformat()
        else:
            start_out = self.start.isoformat()
        end_out: str | None
        if self.end is None:
            end_out = None
        elif self.all_day:
            end_out = self.end.date().isoformat()
        else:
            end_out = self.end.isoformat()
        return {
            "title": self.title,
            "start": start_out,
            "end": end_out,
            "allDay": self.all_day,
            "location": self.location,
        }


def fallback_tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.tz or "America/Chicago")
    except ZoneInfoNotFoundError:
        return ZoneInfo("America/Chicago")


def spoken_text(occ: CalendarOccurrence) -> str:
    """Primary: description. Fallback: title. No wrapper, no client name."""
    desc = (occ.description or "").strip()
    if desc:
        return desc
    return (occ.title or "").strip()


def eligible_for_speech(occ: CalendarOccurrence) -> bool:
    """Timed events only. All-day DATE events never generate Watcher TTS."""
    if occ.all_day:
        return False
    return bool(spoken_text(occ))


def dedup_key(agent_id: str, occ: CalendarOccurrence) -> str:
    start = occ.start.isoformat()
    return f"{occ.uid}|{start}|{agent_id}"


def is_due(
    occ: CalendarOccurrence,
    now: datetime,
    *,
    lookback_s: int | None = None,
    lookahead_s: int | None = None,
) -> bool:
    if occ.all_day:
        return False
    lookback = lookback_s if lookback_s is not None else settings.gcal_due_lookback_seconds
    lookahead = lookahead_s if lookahead_s is not None else settings.gcal_due_lookahead_seconds
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    start = occ.start.astimezone(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    return (start - timedelta(seconds=lookahead)) <= now_utc <= (start + timedelta(seconds=lookback))


def calendar_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def resolve_host_ips(host: str) -> list[str]:
    """DNS lookup used by SSRF checks. Tests monkeypatch this."""
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            addr = info[4][0]
            if addr and addr not in ips:
                ips.append(addr)
    except OSError as exc:
        raise CalendarError("dns_failed") from exc
    return ips


def _is_blocked_ip(raw: str) -> bool:
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return True
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return True
    if addr.is_multicast or addr.is_reserved or addr.is_unspecified:
        return True
    if str(addr) in {"169.254.169.254", "::ffff:169.254.169.254"}:
        return True
    return False


def _require_google_https_url(url: str) -> Any:
    raw = (url or "").strip()
    if not raw:
        raise APIException(400, "Private Google Calendar iCal URL is required")
    if any(ch.isspace() for ch in raw):
        raise APIException(400, "iCal URL is not a Google Calendar HTTPS address")
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https":
        raise APIException(400, "iCal URL must be HTTPS")
    if parsed.username or parsed.password:
        raise APIException(400, "iCal URL is not a Google Calendar HTTPS address")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise APIException(400, "iCal URL is not a Google Calendar HTTPS address")
    if parsed.port not in (None, 443):
        raise APIException(400, "iCal URL is not a Google Calendar HTTPS address")
    path = parsed.path or ""
    if "/calendar/" not in path.lower():
        raise APIException(400, "iCal URL is not a Google Calendar HTTPS address")
    if not path.lower().endswith(".ics"):
        raise APIException(400, "iCal URL is not a Google Calendar HTTPS address")
    if host == "www.google.com" and not path.lower().startswith("/calendar/"):
        raise APIException(400, "iCal URL is not a Google Calendar HTTPS address")
    return parsed


def validate_ical_url(url: str, *, resolver: ResolveFn | None = None) -> str:
    """HTTPS Google Calendar iCal URL, public DNS only. Raises APIException."""
    parsed = _require_google_https_url(url)
    host = (parsed.hostname or "").lower()
    resolve = resolver or resolve_host_ips
    try:
        ips = resolve(host)
    except CalendarError as exc:
        raise APIException(400, "Could not resolve the calendar host") from exc
    if not ips:
        raise APIException(400, "Could not resolve the calendar host")
    for ip in ips:
        if _is_blocked_ip(ip):
            raise APIException(400, "iCal URL must not point at a private network")
    # Drop fragment; keep query if Google ever uses one.
    cleaned = parsed._replace(fragment="").geturl()
    return cleaned


def _ical_dt(value: Any) -> date | datetime | None:
    if value is None:
        return None
    dt = getattr(value, "dt", value)
    if isinstance(dt, datetime) or (isinstance(dt, date) and not isinstance(dt, datetime)):
        return dt
    return None


def _tzid_of(prop: Any) -> str | None:
    try:
        params = getattr(prop, "params", None)
        if params is None:
            return None
        raw = params.get("TZID")
        if raw is None:
            return None
        text = str(raw).strip()
        return text or None
    except Exception:
        return None


def _as_aware(dt: datetime, tzid: str | None) -> datetime:
    if dt.tzinfo is not None:
        key = getattr(dt.tzinfo, "key", None) or getattr(dt.tzinfo, "zone", None)
        if key:
            try:
                return dt.astimezone(ZoneInfo(str(key)))
            except Exception:
                pass
        return dt
    if tzid:
        try:
            return dt.replace(tzinfo=ZoneInfo(tzid))
        except Exception:
            log.info("calendar timezone missing; using %s", settings.tz)
    else:
        log.info("calendar timezone missing; using %s", settings.tz)
    return dt.replace(tzinfo=fallback_tz())


def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", "replace")
    else:
        text = str(value)
    return (
        text.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .strip()
    )


def _uid_of(event: Any) -> str:
    return _plain_text(event.get("uid")) or ""


def _status_cancelled(event: Any) -> bool:
    return _plain_text(event.get("status")).upper() == "CANCELLED"


def _duration_delta(event: Any) -> timedelta | None:
    dur = event.get("duration")
    if dur is None:
        return None
    dt = getattr(dur, "dt", dur)
    if isinstance(dt, timedelta):
        return dt
    return None


def _event_end(event: Any, start: datetime, *, all_day: bool) -> datetime | None:
    end_prop = event.get("dtend")
    end_raw = _ical_dt(end_prop) if end_prop is not None else None
    if isinstance(end_raw, datetime):
        return _as_aware(end_raw, _tzid_of(end_prop))
    if isinstance(end_raw, date) and not isinstance(end_raw, datetime):
        tz = start.tzinfo or fallback_tz()
        return datetime.combine(end_raw, time.min, tzinfo=tz)
    delta = _duration_delta(event)
    if delta is not None:
        return start + delta
    if all_day:
        return start + timedelta(days=1)
    return None


def _exdates(event: Any) -> set[datetime]:
    out: set[datetime] = set()
    raw = event.get("exdate")
    if raw is None:
        return out
    items: list[Any]
    if isinstance(raw, list):
        items = raw
    else:
        items = [raw]
    for item in items:
        dts = getattr(item, "dts", None)
        tzid = _tzid_of(item)
        if dts:
            for sub in dts:
                dt = _ical_dt(sub)
                if isinstance(dt, datetime):
                    out.add(_as_aware(dt, tzid or _tzid_of(sub)))
                elif isinstance(dt, date):
                    out.add(datetime.combine(dt, time.min, tzinfo=fallback_tz()))
        else:
            dt = _ical_dt(item)
            if isinstance(dt, datetime):
                out.add(_as_aware(dt, tzid))
    return out


def _rrule_occurrences(
    event: Any,
    dtstart: datetime,
    window_start: datetime,
    window_end: datetime,
) -> list[datetime]:
    rrule = event.get("rrule")
    if rrule is None:
        return []
    try:
        body = rrule.to_ical()
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        rule = rrulestr(body, dtstart=dtstart)
        found = rule.between(window_start, window_end, inc=True)
        return [dt if dt.tzinfo else _as_aware(dt, None) for dt in found]
    except Exception:
        log.warning("calendar rrule expand failed uid=%s", _uid_of(event)[:80])
        return []


def _is_all_day(dtstart_raw: date | datetime) -> bool:
    return isinstance(dtstart_raw, date) and not isinstance(dtstart_raw, datetime)


def _occurrence_from_event(
    event: Any,
    start: datetime,
    *,
    all_day: bool,
    recurring: bool,
) -> CalendarOccurrence:
    uid = _uid_of(event)
    title = _plain_text(event.get("summary"))
    description = _plain_text(event.get("description"))
    location = _plain_text(event.get("location"))
    end = _event_end(event, start, all_day=all_day)
    return CalendarOccurrence(
        provider=PROVIDER,
        uid=uid,
        title=title,
        description=description,
        location=location,
        start=start,
        end=end,
        all_day=all_day,
        recurring=recurring,
    )


def parse_ics(
    text: str,
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    now: datetime | None = None,
) -> list[CalendarOccurrence]:
    """Parse VEVENT entries and expand RRULE inside the window."""
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if window_start is None:
        window_start = now - timedelta(hours=2)
    if window_end is None:
        window_end = now + timedelta(days=settings.gcal_upcoming_days)
    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=timezone.utc)
    if window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=timezone.utc)

    try:
        calendar = Calendar.from_ical(text.encode("utf-8") if isinstance(text, str) else text)
    except Exception as exc:
        raise CalendarError("ics_parse_failed") from exc

    masters: dict[str, Any] = {}
    overrides: dict[tuple[str, str], Any] = {}
    cancelled_starts: set[tuple[str, str]] = set()
    cancelled_uids: set[str] = set()

    for event in calendar.walk("VEVENT"):
        uid = _uid_of(event)
        if not uid:
            continue
        rec_id = event.get("recurrence-id")
        start_prop = event.get("dtstart")
        start_raw = _ical_dt(start_prop)
        if start_raw is None:
            continue
        if _status_cancelled(event):
            if rec_id is not None:
                rec_raw = _ical_dt(rec_id)
                if rec_raw is not None:
                    cancelled_starts.add((uid, str(rec_raw)))
            else:
                cancelled_uids.add(uid)
            continue
        if rec_id is not None:
            rec_raw = _ical_dt(rec_id) or start_raw
            overrides[(uid, str(rec_raw))] = event
            continue
        masters[uid] = event

    out: list[CalendarOccurrence] = []
    for uid, event in masters.items():
        if uid in cancelled_uids:
            continue
        start_prop = event.get("dtstart")
        start_raw = _ical_dt(start_prop)
        if start_raw is None:
            continue
        all_day = _is_all_day(start_raw)
        has_rrule = event.get("rrule") is not None
        excluded = _exdates(event)

        if all_day:
            assert isinstance(start_raw, date)
            tz = fallback_tz()
            if has_rrule:
                dummy_start = datetime.combine(start_raw, time.min, tzinfo=tz)
                starts = _rrule_occurrences(event, dummy_start, window_start, window_end)
            else:
                starts = [datetime.combine(start_raw, time.min, tzinfo=tz)]
            for start in starts:
                if start.tzinfo is None:
                    start = start.replace(tzinfo=tz)
                key = (uid, str(start.date()))
                if any(ex.date() == start.date() for ex in excluded):
                    continue
                if (uid, str(start.date())) in cancelled_starts or (
                    uid,
                    str(start),
                ) in cancelled_starts:
                    continue
                override = overrides.get(key) or overrides.get((uid, str(start)))
                src = override or event
                if start < window_start - timedelta(days=1) or start > window_end:
                    continue
                out.append(
                    _occurrence_from_event(src, start, all_day=True, recurring=has_rrule)
                )
            continue

        assert isinstance(start_raw, datetime)
        dtstart = _as_aware(start_raw, _tzid_of(start_prop))
        if has_rrule:
            starts = _rrule_occurrences(event, dtstart, window_start, window_end)
        else:
            starts = [dtstart]

        duration = None
        end0 = _event_end(event, dtstart, all_day=False)
        if end0 is not None:
            duration = end0 - dtstart

        for start in starts:
            if start.tzinfo is None:
                start = _as_aware(start, _tzid_of(start_prop))
            if any(abs((ex - start).total_seconds()) < 1 for ex in excluded):
                continue
            if (uid, str(start)) in cancelled_starts:
                continue
            if start < window_start or start > window_end:
                continue
            override = overrides.get((uid, str(start)))
            if override is not None:
                o_start_prop = override.get("dtstart")
                o_raw = _ical_dt(o_start_prop)
                if isinstance(o_raw, datetime):
                    start = _as_aware(o_raw, _tzid_of(o_start_prop))
                out.append(
                    _occurrence_from_event(
                        override, start, all_day=False, recurring=True
                    )
                )
                continue
            occ = _occurrence_from_event(
                event, start, all_day=False, recurring=has_rrule
            )
            if duration is not None and occ.end is None:
                occ = CalendarOccurrence(
                    provider=occ.provider,
                    uid=occ.uid,
                    title=occ.title,
                    description=occ.description,
                    location=occ.location,
                    start=occ.start,
                    end=start + duration,
                    all_day=False,
                    recurring=has_rrule,
                )
            out.append(occ)

    out.sort(key=lambda o: o.start)
    return out


def _validate_redirect(location: str, current: str) -> str:
    joined = urljoin(current, location)
    return validate_ical_url(joined)


async def fetch_ics(url: str, *, client: httpx.AsyncClient | None = None) -> str:
    """HTTPS GET of a validated Google Calendar iCal URL. Never logs the URL."""
    current = validate_ical_url(url)
    timeout = httpx.Timeout(settings.gcal_fetch_timeout_s)
    own_client = client is None
    http = client or httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            try:
                resp = await http.get(current)
            except CalendarError:
                raise
            except httpx.HTTPError as exc:
                raise CalendarError("fetch_failed") from exc
            if resp.status_code in {301, 302, 303, 307, 308}:
                location = resp.headers.get("Location") or ""
                if not location:
                    raise CalendarError("redirect_invalid")
                try:
                    current = _validate_redirect(location, current)
                except APIException as exc:
                    raise CalendarError("redirect_blocked") from exc
                continue
            if resp.status_code != 200:
                raise CalendarError("fetch_http_error")
            data = resp.content or b""
            if len(data) > settings.gcal_max_ics_bytes:
                raise CalendarError("ics_too_large")
            text = data.decode("utf-8", "replace")
            if "BEGIN:VCALENDAR" not in text.upper():
                raise CalendarError("not_ics")
            return text
        raise CalendarError("too_many_redirects")
    finally:
        if own_client:
            await http.aclose()


def next_and_upcoming(
    occurrences: Iterable[CalendarOccurrence],
    now: datetime,
    *,
    limit: int = 3,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    future: list[CalendarOccurrence] = []
    for occ in occurrences:
        start = occ.start
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if occ.all_day:
            local = start.astimezone(start.tzinfo or fallback_tz())
            day_end = datetime.combine(local.date(), time(23, 59, 59), tzinfo=local.tzinfo)
            if now <= day_end:
                future.append(occ)
            continue
        if start >= now - timedelta(minutes=1):
            future.append(occ)
    future.sort(key=lambda o: o.start)
    preview = [o.public_preview() for o in future[:limit]]
    nxt = preview[0] if preview else None
    return nxt, preview
