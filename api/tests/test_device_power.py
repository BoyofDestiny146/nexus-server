"""Power / sleep validation and firmware inactivity model."""
from __future__ import annotations

import pytest

from careconnect_api.device_power import (
    ACTIVITY_BUTTON,
    ACTIVITY_SPEECH,
    ACTIVITY_TOUCH,
    ACTIVITY_TTS,
    PowerSettingsError,
    WatcherPowerController,
    apply_state,
    keeps_listening,
    merge_power,
    normalize_power,
    public_power,
    wire_payload,
)


def test_valid_timeouts_accepted():
    for sec in (0, 30, 60, 120, 300, 600, 1800):
        cfg = normalize_power({"sleepTimeoutSec": sec, "listenScreenOff": True, "sleepMode": "screen_off"})
        assert cfg["sleepTimeoutSec"] == sec


def test_invalid_timeout_rejected():
    with pytest.raises(PowerSettingsError):
        normalize_power({"sleepTimeoutSec": 45, "listenScreenOff": True, "sleepMode": "screen_off"})


def test_screen_off_listen_on():
    cfg = normalize_power(
        {"sleepTimeoutSec": 300, "listenScreenOff": True, "sleepMode": "screen_off"}
    )
    assert cfg["listenScreenOff"] is True
    assert keeps_listening(cfg) is True
    payload = wire_payload(cfg)
    assert payload["type"] == "device_settings"
    assert payload["sleepMode"] == "screen_off"


def test_screen_off_listen_off():
    cfg = normalize_power(
        {"sleepTimeoutSec": 60, "listenScreenOff": False, "sleepMode": "screen_off"}
    )
    assert cfg["listenScreenOff"] is False
    assert keeps_listening(cfg) is False


def test_deep_sleep_forces_listen_off():
    cfg = normalize_power(
        {"sleepTimeoutSec": 300, "listenScreenOff": True, "sleepMode": "deep_sleep"}
    )
    assert cfg["sleepMode"] == "deep_sleep"
    assert cfg["listenScreenOff"] is False
    assert keeps_listening(cfg) is False


def test_apply_state_not_applied_just_because_saved():
    entry = {
        "sleepTimeoutSec": 300,
        "listenScreenOff": True,
        "sleepMode": "screen_off",
        "powerPushSent": 0,
    }
    assert apply_state(entry) == "pending_offline"
    entry["powerPushSent"] = 1
    assert apply_state(entry) == "pending_ack"
    entry["powerApplied"] = public_power(entry)
    assert apply_state(entry) == "applied"


def test_merge_does_not_drop_unknown_keys_via_public_power():
    merged = merge_power(
        {"sleepTimeoutSec": 300, "listenScreenOff": True, "sleepMode": "screen_off"},
        {"sleepTimeoutSec": 600},
    )
    assert merged["sleepTimeoutSec"] == 600
    assert merged["listenScreenOff"] is True


def test_inactivity_resets_from_real_activity_not_heartbeat():
    ctl = WatcherPowerController(sleep_timeout_sec=30, now=0)
    ctl.notify("heartbeat", now=10)
    assert ctl.tick(30) == "screen_off"
    ctl2 = WatcherPowerController(sleep_timeout_sec=30, now=0)
    ctl2.notify(ACTIVITY_TOUCH, now=20)
    ctl2.notify(ACTIVITY_BUTTON, now=25)
    ctl2.notify(ACTIVITY_SPEECH, now=28)
    ctl2.notify(ACTIVITY_TTS, now=29)
    assert ctl2.tick(50) is None
    assert ctl2.tick(59) == "screen_off"


def test_heartbeat_does_not_keep_screen_awake_forever():
    ctl = WatcherPowerController(sleep_timeout_sec=30, listen_screen_off=True, now=0)
    for t in range(0, 120, 5):
        ctl.notify("heartbeat", now=t)
        ctl.notify("ota", now=t)
        ctl.notify("ws_ping", now=t)
    assert ctl.tick(30) == "screen_off"
    assert ctl.wifi_up is True
    assert ctl.ws_up is True
    assert ctl.audio_up is True
    assert ctl.screen_on is False


def test_deep_sleep_disconnects_radio():
    ctl = WatcherPowerController(
        sleep_timeout_sec=30, listen_screen_off=True, sleep_mode="deep_sleep", now=0
    )
    assert ctl.listen_screen_off is False
    assert ctl.tick(30) == "deep_sleep"
    assert ctl.wifi_up is False
    assert ctl.ws_up is False
    assert ctl.audio_up is False


def test_settings_survive_reboot():
    ctl = WatcherPowerController(sleep_timeout_sec=600, listen_screen_off=False, now=0)
    ctl.apply_settings(
        {"sleepTimeoutSec": 120, "listenScreenOff": True, "sleepMode": "screen_off"}
    )
    ctl.reboot()
    assert ctl.nvs["sleepTimeoutSec"] == 120
    assert ctl.listen_screen_off is True
    assert ctl.sleep_timeout_sec == 120
    assert ctl.screen_on is True


def test_never_does_not_auto_sleep():
    ctl = WatcherPowerController(sleep_timeout_sec=0, now=0)
    assert ctl.tick(10_000) is None
    assert ctl.screen_on is True
