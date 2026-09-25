"""Per-Watcher power / sleep settings.

These belong to the physical SenseCAP Watcher (MAC-keyed ``voice_config.json``),
not ``ai_agent``. Volume already lives in that file; sleep settings use the
same store so we do not invent a MariaDB column.

Screen Off keeps Wi-Fi / XiaoZhi WS / audio up (firmware LCD/backlight only).
Deep Sleep is ESP32 deep sleep: Wi-Fi, WS, and listening are gone until a
hardware wake. Deep Sleep + listen-while-screen-off is coerced off — the
ESP32-S3 cannot run WakeNet or keep the socket in deep sleep.
"""
from __future__ import annotations

from typing import Any, Mapping

SLEEP_TIMEOUT_SEC = (0, 30, 60, 120, 300, 600, 1800)
SLEEP_MODES = ("screen_off", "deep_sleep")
POWER_KEYS = ("sleepTimeoutSec", "listenScreenOff", "sleepMode")

DEFAULT_POWER: dict[str, Any] = {
    "sleepTimeoutSec": 300,
    "listenScreenOff": True,
    "sleepMode": "screen_off",
}

# Activity that resets the firmware inactivity timer. Heartbeat / OTA / WS
# pings are intentionally absent.
ACTIVITY_TOUCH = "touch"
ACTIVITY_BUTTON = "button"
ACTIVITY_SPEECH = "speech"
ACTIVITY_TTS = "tts"
ACTIVITY_SOURCES = (ACTIVITY_TOUCH, ACTIVITY_BUTTON, ACTIVITY_SPEECH, ACTIVITY_TTS)
BACKGROUND_SOURCES = ("heartbeat", "ota", "ws_ping", "mqtt_keepalive")


class PowerSettingsError(ValueError):
    """Invalid timeout / mode / listen value from a client."""


def public_power(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    src = raw or {}
    timeout = src.get("sleepTimeoutSec", DEFAULT_POWER["sleepTimeoutSec"])
    listen = src.get("listenScreenOff", DEFAULT_POWER["listenScreenOff"])
    mode = src.get("sleepMode", DEFAULT_POWER["sleepMode"])
    try:
        timeout_i = int(timeout)
    except (TypeError, ValueError):
        timeout_i = int(DEFAULT_POWER["sleepTimeoutSec"])
    listen_b = bool(listen) if not isinstance(listen, str) else listen.strip().lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
    mode_s = str(mode or DEFAULT_POWER["sleepMode"]).strip()
    if mode_s == "deep_sleep":
        listen_b = False
    return {
        "sleepTimeoutSec": timeout_i,
        "listenScreenOff": listen_b,
        "sleepMode": mode_s,
    }


def normalize_power(raw: Mapping[str, Any] | None, *, partial: bool = False) -> dict[str, Any]:
    """Validate and coerce a client payload.

    Unknown timeout/mode values raise ``PowerSettingsError``. Deep Sleep
    forces ``listenScreenOff=False``.
    """
    if not raw:
        if partial:
            return {}
        return dict(DEFAULT_POWER)
    out: dict[str, Any] = {}
    provided = {k for k in POWER_KEYS if k in raw}

    if "sleepTimeoutSec" in provided:
        try:
            timeout = int(raw["sleepTimeoutSec"])
        except (TypeError, ValueError) as exc:
            raise PowerSettingsError("sleepTimeoutSec must be an integer") from exc
        if timeout not in SLEEP_TIMEOUT_SEC:
            raise PowerSettingsError(
                f"sleepTimeoutSec must be one of {list(SLEEP_TIMEOUT_SEC)}"
            )
        out["sleepTimeoutSec"] = timeout
    elif not partial:
        out["sleepTimeoutSec"] = DEFAULT_POWER["sleepTimeoutSec"]

    if "sleepMode" in provided:
        mode = str(raw["sleepMode"] or "").strip()
        if mode not in SLEEP_MODES:
            raise PowerSettingsError(
                f"sleepMode must be one of {list(SLEEP_MODES)}"
            )
        out["sleepMode"] = mode
    elif not partial:
        out["sleepMode"] = DEFAULT_POWER["sleepMode"]

    if "listenScreenOff" in provided:
        val = raw["listenScreenOff"]
        if isinstance(val, bool):
            listen = val
        elif val in (0, 1):
            listen = bool(val)
        elif isinstance(val, str) and val.strip().lower() in ("true", "false", "on", "off", "1", "0"):
            listen = val.strip().lower() in ("true", "on", "1")
        else:
            raise PowerSettingsError("listenScreenOff must be true or false")
        out["listenScreenOff"] = listen
    elif not partial:
        out["listenScreenOff"] = DEFAULT_POWER["listenScreenOff"]

    mode = out.get("sleepMode")
    if mode == "deep_sleep" and out.get("listenScreenOff"):
        out["listenScreenOff"] = False
    return out


def merge_power(existing: Mapping[str, Any] | None, patch: Mapping[str, Any]) -> dict[str, Any]:
    base = public_power(existing) if _entry_has_desired(existing) else dict(DEFAULT_POWER)
    merged = dict(base)
    merged.update(normalize_power(patch, partial=True))
    return normalize_power(merged, partial=False)


def _entry_has_desired(entry: Mapping[str, Any] | None) -> bool:
    if not entry:
        return False
    return any(k in entry for k in POWER_KEYS)


def desired_from_entry(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not _entry_has_desired(entry):
        return None
    try:
        return public_power(entry)
    except Exception:
        return dict(DEFAULT_POWER)


def applied_from_entry(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    applied = entry.get("powerApplied")
    if not isinstance(applied, dict):
        return None
    try:
        return public_power(applied)
    except Exception:
        return None


def apply_state(entry: Mapping[str, Any] | None) -> str:
    """unset | pending_offline | pending_ack | applied"""
    desired = desired_from_entry(entry)
    if desired is None:
        return "unset"
    applied = applied_from_entry(entry)
    if applied == desired:
        return "applied"
    try:
        sent = int((entry or {}).get("powerPushSent") or 0)
    except (TypeError, ValueError):
        sent = 0
    if sent > 0:
        return "pending_ack"
    return "pending_offline"


def keeps_listening(power: Mapping[str, Any] | None) -> bool:
    cfg = public_power(power)
    return cfg["sleepMode"] == "screen_off" and bool(cfg["listenScreenOff"])


def wire_payload(power: Mapping[str, Any]) -> dict[str, Any]:
    cfg = public_power(power)
    return {
        "type": "device_settings",
        "sleepTimeoutSec": cfg["sleepTimeoutSec"],
        "listenScreenOff": cfg["listenScreenOff"],
        "sleepMode": cfg["sleepMode"],
    }


class WatcherPowerController:
    """Firmware inactivity model used by tests (mirrors the C++ controller).

    Background heartbeats must not reset the timer. Real user activity must.
    """

    def __init__(
        self,
        *,
        sleep_timeout_sec: int = 300,
        listen_screen_off: bool = True,
        sleep_mode: str = "screen_off",
        now: float = 0.0,
    ) -> None:
        cfg = normalize_power(
            {
                "sleepTimeoutSec": sleep_timeout_sec,
                "listenScreenOff": listen_screen_off,
                "sleepMode": sleep_mode,
            }
        )
        self.sleep_timeout_sec = int(cfg["sleepTimeoutSec"])
        self.listen_screen_off = bool(cfg["listenScreenOff"])
        self.sleep_mode = str(cfg["sleepMode"])
        self.last_activity = now
        self.screen_on = True
        self.wifi_up = True
        self.ws_up = True
        self.audio_up = True
        self.deep_sleep = False
        self.nvs: dict[str, Any] = {
            "sleepTimeoutSec": self.sleep_timeout_sec,
            "listenScreenOff": self.listen_screen_off,
            "sleepMode": self.sleep_mode,
        }

    def apply_settings(self, payload: Mapping[str, Any], *, persist: bool = True) -> dict[str, Any]:
        cfg = normalize_power(payload, partial=False)
        self.sleep_timeout_sec = int(cfg["sleepTimeoutSec"])
        self.listen_screen_off = bool(cfg["listenScreenOff"])
        self.sleep_mode = str(cfg["sleepMode"])
        if persist:
            self.nvs = dict(cfg)
        if self.deep_sleep:
            return cfg
        if self.sleep_mode == "screen_off" and not self.screen_on:
            self.audio_up = bool(self.listen_screen_off)
            self.wifi_up = True
            self.ws_up = True
        return cfg

    def reboot(self) -> None:
        """Settings survive reboot via NVS; radio is up until the timer fires."""
        cfg = normalize_power(self.nvs)
        self.sleep_timeout_sec = int(cfg["sleepTimeoutSec"])
        self.listen_screen_off = bool(cfg["listenScreenOff"])
        self.sleep_mode = str(cfg["sleepMode"])
        self.screen_on = True
        self.wifi_up = True
        self.ws_up = True
        self.audio_up = True
        self.deep_sleep = False

    def notify(self, source: str, now: float) -> None:
        if source in BACKGROUND_SOURCES:
            return
        if source not in ACTIVITY_SOURCES:
            return
        self.last_activity = now
        if self.deep_sleep:
            return
        if not self.screen_on:
            self.wake_screen()

    def wake_screen(self) -> None:
        if self.deep_sleep:
            return
        self.screen_on = True
        self.wifi_up = True
        self.ws_up = True
        self.audio_up = True

    def tick(self, now: float) -> str | None:
        if self.deep_sleep:
            return "deep_sleep"
        if self.sleep_timeout_sec == 0:
            return None
        if now - self.last_activity < self.sleep_timeout_sec:
            return None
        return self._enter_sleep()

    def _enter_sleep(self) -> str:
        if self.sleep_mode == "deep_sleep":
            self.deep_sleep = True
            self.screen_on = False
            self.wifi_up = False
            self.ws_up = False
            self.audio_up = False
            return "deep_sleep"
        self.screen_on = False
        self.wifi_up = True
        self.ws_up = True
        self.audio_up = bool(self.listen_screen_off)
        return "screen_off"

    def wake_from_button(self, now: float) -> None:
        self.deep_sleep = False
        self.last_activity = now
        self.wake_screen()
