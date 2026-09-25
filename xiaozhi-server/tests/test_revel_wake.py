"""Revel command-prefix matching (XiaoZhi copy of the CareConnect matcher)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.revel_wake import next_chat_text, strip_wake_prefix, utterance_from_asr_text


def test_prefix_cases():
    assert strip_wake_prefix("Bob show my calendar", "Bob") == (True, "show my calendar")
    assert strip_wake_prefix("bob show my calendar", "Bob") == (True, "show my calendar")
    assert strip_wake_prefix("Bob, show my calendar", "Bob") == (True, "show my calendar")
    assert strip_wake_prefix("BOB: SHOW MY CALENDAR", "Bob") == (True, "SHOW MY CALENDAR")


def test_prefix_non_matches():
    utt = "I told Bob to show my calendar"
    assert strip_wake_prefix(utt, "Bob") == (False, utt)
    assert strip_wake_prefix("Show my calendar", "Bob")[0] is False
    assert strip_wake_prefix("Bob show my calendar", None)[0] is False
    assert strip_wake_prefix("Bobby, show my calendar", "Bob")[0] is False


def test_empty_remainder_keeps_original_mode():
    hit, remainder = strip_wake_prefix("Bob!", "Bob")
    assert hit is True
    assert remainder == ""
    mode, text = next_chat_text(
        prefix_matched=True,
        remainder="",
        original_text="Bob!",
        command=None,
    )
    assert mode == "original"
    assert text == "Bob!"


def test_unmatched_wake_uses_remainder():
    mode, text = next_chat_text(
        prefix_matched=True,
        remainder="what's for lunch?",
        original_text="Bob, what's for lunch?",
        command={"matched": False},
    )
    assert mode == "remainder"
    assert text == "what's for lunch?"


def test_matched_command_does_not_chat():
    mode, _text = next_chat_text(
        prefix_matched=True,
        remainder="show my calendar",
        original_text="Bob, show my calendar",
        command={"matched": True, "intent": "display_calendar"},
    )
    assert mode == "revel"


def test_no_prefix_keeps_original():
    mode, text = next_chat_text(
        prefix_matched=False,
        remainder="show my calendar",
        original_text="show my calendar",
        command=None,
    )
    assert mode == "original"
    assert text == "show my calendar"


def test_asr_json_content():
    raw = json.dumps({"speaker": "a", "content": "Bob, show my calendar"})
    assert utterance_from_asr_text(raw) == "Bob, show my calendar"
