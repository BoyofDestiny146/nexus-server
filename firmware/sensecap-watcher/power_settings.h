#pragma once

#include <cstdint>
#include <string>

#include "driver/gpio.h"

// CareConnect Watcher power / sleep. Drop into xiaozhi-esp32 as
// main/boards/sensecap-watcher/power_settings.h (or main/power_settings.h)
// and call from Application + the SenseCAP Watcher board.

enum class CcSleepMode : uint8_t {
    kScreenOff = 0,
    kDeepSleep = 1,
};

enum class CcActivity : uint8_t {
    kTouch = 0,
    kButton = 1,
    kSpeech = 2,
    kTts = 3,
    kHeartbeat = 4,  // ignored — must not reset the inactivity timer
    kOta = 5,        // ignored
    kWsPing = 6,     // ignored
};

struct CcPowerSettings {
    int sleep_timeout_sec = 300;  // 0 = Never
    bool listen_screen_off = true;
    CcSleepMode sleep_mode = CcSleepMode::kScreenOff;
};

// NVS namespace "cc_power". Survives reboot. Not a credential.
bool CcPowerLoad(CcPowerSettings* out);
bool CcPowerSave(const CcPowerSettings& settings);

// Validate + coerce. Deep sleep forces listen_screen_off=false.
bool CcPowerNormalize(int timeout_sec, bool listen, const char* mode,
                      CcPowerSettings* out);

void CcPowerStart();
void CcPowerStop();
void CcPowerApply(const CcPowerSettings& settings);
void CcPowerNotify(CcActivity source);
void CcPowerWakeFromDeepSleep();

// JSON on the existing XiaoZhi websocket (type=device_settings).
// Incoming from Nexus: sleepTimeoutSec, listenScreenOff, sleepMode.
// Ack: {"type":"device_settings","status":"applied",...}
bool CcPowerHandleIncomingJson(const char* json, std::string* ack_json);

// Deep-sleep wake: RTC GPIO of the user/power button only.
// SenseCAP Watcher knob/LCD-touch (CST816, I2C) is not an RTC wake source.
// WakeNet / microphone cannot run in ESP32 deep sleep.
#ifndef CC_POWER_WAKE_GPIO
#define CC_POWER_WAKE_GPIO GPIO_NUM_0
#endif
