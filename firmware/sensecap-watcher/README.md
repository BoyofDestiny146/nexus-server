# CareConnect SenseCAP Watcher power / sleep

This tree is **not** the full XiaoZhi ESP32 firmware. The Watcher firmware
lives in the separate `xiaozhi-esp32` SenseCAP Watcher board package. These
files are the CareConnect power-management module to drop into that tree.

Current stock firmware (what Orin Watchers run today):

- After a conversation the device returns to idle and listens for the
  WakeNet wake word (`Jarvis`) while the LCD stays on.
- XiaoZhi-server may close the WebSocket after `close_connection_no_voice_time`
  (300s in CareConnect). That is a **server** idle close, not ESP32 sleep.
- There is **no** CareConnect-controlled inactivity timer, Screen Off, or
  Deep Sleep setting. MCP `set_volume` / `set_brightness` (if advertised)
  cannot implement an inactivity timer or deep sleep.
- OTA JSON from this server only returns websocket/mqtt/firmware URL — no
  sleep fields.

## Two states (this module)

### Screen Off (`sleepMode=screen_off`)

LCD/backlight off only (`CcBoardSetLcdOn(false)`).

`listenScreenOff=true` (default): Wi-Fi stays up, XiaoZhi WebSocket stays
connected, I2S/WakeNet stay available, incoming Nexus TTS/reminders still
play, touch/button/speech/TTS wake the screen.

`listenScreenOff=false`: screen off and audio **capture** paused. The
session is **not** torn down.

### Deep Sleep (`sleepMode=deep_sleep`)

`esp_deep_sleep_start()`. Wi-Fi down, WebSocket down, audio/listening
unavailable, Nexus sees the Watcher offline. Incoming reminders wait until
the device reconnects (existing `spoken=0` path).

Deep Sleep **forces** `listenScreenOff=false`. WakeNet cannot run in ESP32
deep sleep.

## Wake sources implemented

| Source | Screen Off | Deep Sleep |
|---|---|---|
| User/power button (RTC GPIO, default `GPIO_NUM_0`) | yes (activity) | **yes** (`ext0`) |
| LCD touch (CST816 I2C) | yes (activity) | **no** — not an RTC wake pin |
| Speech / accepted audio | yes | **no** |
| Incoming Nexus TTS | yes (wakes LCD) | **no** — device is offline |
| Timer auto-wake | not enabled | **not enabled** (would cycle the radio) |
| Microphone wake word | yes, if listening kept | **no** |
| CareConnect HTTP heartbeat / OTA / WS ping | ignored | ignored |

Confirm `CC_POWER_WAKE_GPIO` against the SenseCAP Watcher schematic before
flash. Only RTC-capable GPIOs work with `esp_sleep_enable_ext0_wakeup`.

## NVS

Namespace `cc_power`: `timeout` (i32 seconds), `listen` (u8), `mode` (u8).
Survives reboot. CareConnect remains source of truth: on the next WebSocket
hello, Nexus pushes desired settings and firmware re-saves NVS.

## Protocol (existing XiaoZhi WS text JSON)

Nexus → Watcher:

```json
{"type":"device_settings","sleepTimeoutSec":300,"listenScreenOff":true,"sleepMode":"screen_off"}
```

Watcher → Nexus ack:

```json
{"type":"device_settings","status":"applied","sleepTimeoutSec":300,"listenScreenOff":true,"sleepMode":"screen_off"}
```

Timeouts: `0, 30, 60, 120, 300, 600, 1800`. `0` = Never.

Unknown `type` values are already ignored by stock firmware, so shipping
the server first is safe: CareConnect shows **Pending** until this module
acks.

## Integration (xiaozhi-esp32)

1. Copy `power_settings.h` / `power_settings.cc` into
   `main/boards/sensecap-watcher/`.
2. Add the `.cc` to that component `CMakeLists.txt`.
3. Implement board hooks in `sensecap_watcher.cc`:

   ```cpp
   extern "C" void CcBoardSetLcdOn(bool on) {
       GetDisplay()->SetPowerSaveMode(!on);  // or SetBrightness(on ? 100 : 0)
   }
   extern "C" void CcBoardSetAudioCaptureOn(bool on) {
       // enable/disable I2S mic / audio processor only. Do not close Wi-Fi.
   }
   ```

4. `Application::Start`: `CcPowerStart()`.
5. `Application::OnIncomingJson`: if `CcPowerHandleIncomingJson(json, &ack)`
   then send `ack` on the websocket.
6. Activity:
   - LVGL / CST816 touch → `CcPowerNotify(CcActivity::kTouch)`
   - Boot/power button → `CcPowerNotify(CcActivity::kButton)`
   - Listen start / VAD speech / wake detect → `CcPowerNotify(CcActivity::kSpeech)`
   - TTS playback start → `CcPowerNotify(CcActivity::kTts)`
   - Do **not** notify on OTA poll, websocket ping, or CareConnect heartbeat.
7. After `esp_deep_sleep_start` reboot: `CcPowerWakeFromDeepSleep()` then
   reconnect Wi-Fi / XiaoZhi as today.

## Flash / OTA

Required for Applied state. Build the SenseCAP Watcher xiaozhi-esp32 image
with this module, then flash or publish via the existing OTA URL. Do not
flash from this repo automatically — there is no Watcher firmware build
here.
