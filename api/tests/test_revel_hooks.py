"""Calendar / sensor Revel hooks stay unwired and never write."""
from __future__ import annotations

import inspect

from careconnect_api.calendar_poller import poll_google_calendars, poll_one
from careconnect_api.revel_display import RETURN_HOME, SHOW_HOME, screen_for_intent
from careconnect_api.revel_hooks import (
    TEST_INTENT,
    apply_test_display,
    request_appointment_reminder,
    request_care_alert,
    request_return_home,
    request_sensor_alert,
)


def test_hooks_use_allowlisted_intents():
    assert TEST_INTENT == SHOW_HOME
    assert screen_for_intent(RETURN_HOME) == "home"
    src = inspect.getsource(poll_one)
    assert "request_appointment_reminder" not in src
    assert "apply_display_state" not in src
    src_all = inspect.getsource(poll_google_calendars)
    assert "request_appointment_reminder" not in src_all


def test_hook_functions_exist_for_later_wiring():
    assert request_appointment_reminder.__name__ == "request_appointment_reminder"
    assert request_sensor_alert.__name__ == "request_sensor_alert"
    assert request_care_alert.__name__ == "request_care_alert"
    assert request_return_home.__name__ == "request_return_home"
    assert apply_test_display.__name__ == "apply_test_display"
