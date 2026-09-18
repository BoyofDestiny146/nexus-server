"""careconnect per-patient reminders / medication scheduler.

A tiny JSON-backed store (same pattern as ``config/voice_config.py``) keyed by
``agent_id``. The patient (or a caregiver) can say things like:

    "remind me to take my medicine at 3 pm"
    "set a reminder to drink water in 20 minutes"
    "remind me to call my daughter every day at 9 am"

``parse_reminder`` turns that into ``{"text", "due_at", "recurring"}``; the
connection layer stores it, confirms verbally, and a per-connection loop speaks
the reminder when it comes due (and replays any missed ones on the next
connect — the Watcher can't be woken from standby, so overdue reminders are
delivered the next time the device checks in).

Storage shape (``/data/reminders.json`` on the shared cc-voice volume):

    {
      "<agent_id>": [
        {"id": "ab12cd", "text": "take your medicine", "due_at": 1717880400,
         "created_at": 1717800000, "recurring": "daily", "fired": false}
      ]
    }
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

_PATH = Path(os.environ.get(
    "CC_REMINDERS_PATH",
    str(Path(os.environ.get("CC_VOICE_CONFIG",
            str(Path(__file__).resolve().parent.parent / "data" / "voice_config.json"))
            ).resolve().parent / "reminders.json"),
))
_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #
def _load() -> dict:
    try:
        d = json.loads(_PATH.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2))
    tmp.replace(_PATH)


def add_reminder(agent_id: str, text: str, due_at: int,
                 recurring: str | None = None) -> dict:
    rec = {
        "id": secrets.token_hex(3),
        "text": text.strip(),
        "due_at": int(due_at),
        "created_at": int(time.time()),
        "recurring": recurring,
        "fired": False,
    }
    with _lock:
        d = _load()
        d.setdefault(agent_id, []).append(rec)
        _save(d)
    return rec


def due_reminders(agent_id: str, now: int | None = None) -> list[dict]:
    """Un-fired reminders whose due time has arrived (<= now)."""
    now = int(now if now is not None else time.time())
    return [r for r in _load().get(agent_id, [])
            if not r.get("fired") and int(r.get("due_at", 0)) <= now]


def mark_fired(agent_id: str, reminder_id: str) -> None:
    """Mark a reminder fired. Daily/recurring ones roll to the next day instead."""
    with _lock:
        d = _load()
        changed = False
        for r in d.get(agent_id, []):
            if r.get("id") != reminder_id:
                continue
            if r.get("recurring") == "daily":
                r["due_at"] = int(r.get("due_at", time.time())) + 86400
                r["fired"] = False
            else:
                r["fired"] = True
            changed = True
        # prune one-shot reminders that fired more than a day ago
        for aid in list(d.keys()):
            d[aid] = [r for r in d[aid]
                      if not (r.get("fired") and int(r.get("due_at", 0)) < time.time() - 86400)]
            if not d[aid]:
                d.pop(aid)
        if changed:
            _save(d)


def list_reminders(agent_id: str) -> list[dict]:
    return [r for r in _load().get(agent_id, []) if not r.get("fired")]


# --------------------------------------------------------------------------- #
# natural-language parsing
# --------------------------------------------------------------------------- #
_TRIGGER = re.compile(
    r"\b(remind me|set (?:a |an )?reminder|reminder to|remind us|"
    r"can you remind|don'?t let me forget|set (?:a |an )?(?:timer|alarm|stopwatch))\b",
    re.IGNORECASE,
)
# strip the leading trigger + connective so the remaining text is the task
_LEAD = re.compile(
    r"^.*?\b(?:remind me|reminder|remind us|timer|alarm|stopwatch)\b\s*(?:to|that|about|for)?\s*",
    re.IGNORECASE,
)
_REL = re.compile(
    r"\b(?:in|for|after)\s+(\d+|a|an|one|two|three|four|five|ten|fifteen|twenty|thirty|half an)\s*"
    r"(second|seconds|sec|minute|minutes|min|hour|hours|hr)\b",
    re.IGNORECASE,
)
_CLOCK = re.compile(
    r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|o'?clock|oclock)?\b",
    re.IGNORECASE,
)
_WORDNUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "half an": 30}


def is_reminder_request(text: str) -> bool:
    try:
        return bool(text and _TRIGGER.search(text))
    except Exception:
        return False


def _clean_task(text: str) -> str:
    """Pull out the task phrase: drop the trigger lead-in and the time clause."""
    t = _LEAD.sub("", text).strip()
    t = _REL.sub("", t)
    t = _CLOCK.sub("", t)
    t = re.sub(r"\b(every\s*day|everyday|daily|each day|tomorrow|today)\b", "", t, flags=re.IGNORECASE)
    # drop any leftover bare duration (e.g. a stray "30 seconds" from a timer)
    t = re.sub(r"\b\d+\s*(?:second|seconds|sec|minute|minutes|min|hour|hours|hr)\b", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s{2,}", " ", t).strip(" .,!?")
    return t or "your reminder"


def parse_reminder(text: str, now: int | None = None):
    """Return ``{"text","due_at","recurring"}`` or ``None`` if no time is found.

    ``now`` is epoch seconds (local-tz clock is read from the process tz, which
    the container sets to America/Chicago).
    """
    if not text:
        return None
    now = int(now if now is not None else time.time())
    base = datetime.fromtimestamp(now)
    recurring = "daily" if re.search(r"\b(every\s*day|everyday|daily|each day)\b", text, re.I) else None
    due = None

    m = _REL.search(text)
    if m:
        qty_raw, unit = m.group(1).lower(), m.group(2).lower()
        qty = int(qty_raw) if qty_raw.isdigit() else _WORDNUM.get(qty_raw, 1)
        if unit.startswith("sec"):
            due = base + timedelta(seconds=qty)
        elif unit.startswith("min") or unit == "min":
            due = base + timedelta(minutes=qty)
        else:
            due = base + timedelta(hours=qty)
        recurring = None  # relative timers are one-shot
    else:
        m = _CLOCK.search(text)
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2) or 0)
            ampm = (m.group(3) or "").lower().replace(".", "")
            if ampm == "pm" and hh < 12:
                hh += 12
            elif ampm == "am" and hh == 12:
                hh = 0
            if hh > 23 or mm > 59:
                return None
            due = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if re.search(r"\btomorrow\b", text, re.I):
                due += timedelta(days=1)
            elif due <= base:  # time already passed today -> next day
                due += timedelta(days=1)

    if due is None:
        return None
    return {"text": _clean_task(text), "due_at": int(due.timestamp()), "recurring": recurring}


def humanize(due_at: int) -> str:
    """A short spoken time, e.g. '3:00 PM' or 'in 20 minutes'."""
    now = time.time()
    delta = int(due_at) - now
    if 0 < delta <= 5400:
        mins = max(1, round(delta / 60))
        return f"in {mins} minute{'s' if mins != 1 else ''}"
    dt = datetime.fromtimestamp(int(due_at))
    return dt.strftime("%-I:%M %p").lstrip("0")
