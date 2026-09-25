#include "power_settings.h"

#include <cstring>
#include <cstdio>
#include <string>

#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "cJSON.h"

static const char* TAG = "cc_power";
static const char* kNvsNs = "cc_power";

static CcPowerSettings s_settings;
static int64_t s_last_activity_us = 0;
static esp_timer_handle_t s_timer = nullptr;
static bool s_screen_off = false;
static bool s_in_deep_sleep = false;

// Board hooks — implemented by sensecap_watcher.cc (display backlight / codec).
extern "C" void CcBoardSetLcdOn(bool on);
extern "C" void CcBoardSetAudioCaptureOn(bool on);

static const int kAllowedTimeouts[] = {0, 30, 60, 120, 300, 600, 1800};

static bool TimeoutAllowed(int sec) {
    for (int t : kAllowedTimeouts) {
        if (t == sec) return true;
    }
    return false;
}

bool CcPowerNormalize(int timeout_sec, bool listen, const char* mode,
                      CcPowerSettings* out) {
    if (!out) return false;
    if (!TimeoutAllowed(timeout_sec)) return false;
    CcSleepMode sm = CcSleepMode::kScreenOff;
    if (mode && strcmp(mode, "deep_sleep") == 0) {
        sm = CcSleepMode::kDeepSleep;
    } else if (mode && strcmp(mode, "screen_off") == 0) {
        sm = CcSleepMode::kScreenOff;
    } else if (mode && mode[0]) {
        return false;
    }
    if (sm == CcSleepMode::kDeepSleep) {
        listen = false;
    }
    out->sleep_timeout_sec = timeout_sec;
    out->listen_screen_off = listen;
    out->sleep_mode = sm;
    return true;
}

bool CcPowerLoad(CcPowerSettings* out) {
    nvs_handle_t h;
    if (nvs_open(kNvsNs, NVS_READONLY, &h) != ESP_OK) {
        if (out) *out = CcPowerSettings{};
        return false;
    }
    CcPowerSettings s;
    uint8_t mode = 0;
    uint8_t listen = 1;
    int32_t timeout = 300;
    nvs_get_i32(h, "timeout", &timeout);
    nvs_get_u8(h, "listen", &listen);
    nvs_get_u8(h, "mode", &mode);
    nvs_close(h);
    if (!CcPowerNormalize(static_cast<int>(timeout), listen != 0,
                          mode == 1 ? "deep_sleep" : "screen_off", &s)) {
        s = CcPowerSettings{};
    }
    if (out) *out = s;
    s_settings = s;
    return true;
}

bool CcPowerSave(const CcPowerSettings& settings) {
    nvs_handle_t h;
    if (nvs_open(kNvsNs, NVS_READWRITE, &h) != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open failed");
        return false;
    }
    nvs_set_i32(h, "timeout", settings.sleep_timeout_sec);
    nvs_set_u8(h, "listen", settings.listen_screen_off ? 1 : 0);
    nvs_set_u8(h, "mode", settings.sleep_mode == CcSleepMode::kDeepSleep ? 1 : 0);
    nvs_commit(h);
    nvs_close(h);
    s_settings = settings;
    return true;
}

static void EnterScreenOff() {
    s_screen_off = true;
    CcBoardSetLcdOn(false);
    if (s_settings.listen_screen_off) {
        // Wi-Fi, WS, WakeNet, I2S stay up. Incoming Nexus TTS still plays.
        CcBoardSetAudioCaptureOn(true);
        ESP_LOGI(TAG, "screen off; listening remains available");
    } else {
        CcBoardSetAudioCaptureOn(false);
        ESP_LOGI(TAG, "screen off; audio capture paused (session kept)");
    }
}

static void EnterDeepSleep() {
    s_in_deep_sleep = true;
    CcBoardSetLcdOn(false);
    CcBoardSetAudioCaptureOn(false);
    ESP_LOGW(TAG, "deep sleep: wifi/ws/audio down until button wake");
    esp_sleep_enable_ext0_wakeup(CC_POWER_WAKE_GPIO, 0);
    esp_deep_sleep_start();
}

static void TimerCb(void* /*arg*/) {
    if (s_settings.sleep_timeout_sec <= 0) return;
    int64_t now = esp_timer_get_time();
    int64_t idle_us = now - s_last_activity_us;
    if (idle_us < static_cast<int64_t>(s_settings.sleep_timeout_sec) * 1000000LL) {
        return;
    }
    if (s_settings.sleep_mode == CcSleepMode::kDeepSleep) {
        EnterDeepSleep();
    } else if (!s_screen_off) {
        EnterScreenOff();
    }
}

void CcPowerStart() {
    CcPowerLoad(&s_settings);
    s_last_activity_us = esp_timer_get_time();
    s_screen_off = false;
    s_in_deep_sleep = false;
    if (s_timer) return;
    esp_timer_create_args_t args = {
        .callback = &TimerCb,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "cc_power",
        .skip_unhandled_events = true,
    };
    if (esp_timer_create(&args, &s_timer) == ESP_OK) {
        esp_timer_start_periodic(s_timer, 1000000);  // 1s
    }
}

void CcPowerStop() {
    if (s_timer) {
        esp_timer_stop(s_timer);
        esp_timer_delete(s_timer);
        s_timer = nullptr;
    }
}

void CcPowerApply(const CcPowerSettings& settings) {
    s_settings = settings;
    CcPowerSave(settings);
    s_last_activity_us = esp_timer_get_time();
    if (s_screen_off && settings.sleep_mode == CcSleepMode::kScreenOff) {
        CcBoardSetAudioCaptureOn(settings.listen_screen_off);
    }
}

void CcPowerNotify(CcActivity source) {
    switch (source) {
        case CcActivity::kHeartbeat:
        case CcActivity::kOta:
        case CcActivity::kWsPing:
            return;  // must not keep the screen awake
        default:
            break;
    }
    s_last_activity_us = esp_timer_get_time();
    if (s_screen_off && !s_in_deep_sleep) {
        s_screen_off = false;
        CcBoardSetLcdOn(true);
        CcBoardSetAudioCaptureOn(true);
    }
}

void CcPowerWakeFromDeepSleep() {
    s_in_deep_sleep = false;
    s_screen_off = false;
    s_last_activity_us = esp_timer_get_time();
    CcBoardSetLcdOn(true);
    CcBoardSetAudioCaptureOn(true);
}

bool CcPowerHandleIncomingJson(const char* json, std::string* ack_json) {
    if (!json) return false;
    cJSON* root = cJSON_Parse(json);
    if (!root) return false;
    cJSON* type = cJSON_GetObjectItem(root, "type");
    if (!cJSON_IsString(type) || strcmp(type->valuestring, "device_settings") != 0) {
        cJSON_Delete(root);
        return false;
    }
    cJSON* timeout = cJSON_GetObjectItem(root, "sleepTimeoutSec");
    cJSON* listen = cJSON_GetObjectItem(root, "listenScreenOff");
    cJSON* mode = cJSON_GetObjectItem(root, "sleepMode");
    int t = cJSON_IsNumber(timeout) ? timeout->valueint : s_settings.sleep_timeout_sec;
    bool l = cJSON_IsBool(listen) ? cJSON_IsTrue(listen) : s_settings.listen_screen_off;
    const char* m = cJSON_IsString(mode) ? mode->valuestring : (
        s_settings.sleep_mode == CcSleepMode::kDeepSleep ? "deep_sleep" : "screen_off");
    CcPowerSettings next;
    bool ok = CcPowerNormalize(t, l, m, &next);
    cJSON_Delete(root);
    if (!ok) {
        ESP_LOGW(TAG, "rejected device_settings");
        return true;
    }
    CcPowerApply(next);
    if (ack_json) {
        char buf[192];
        snprintf(buf, sizeof(buf),
                 "{\"type\":\"device_settings\",\"status\":\"applied\","
                 "\"sleepTimeoutSec\":%d,\"listenScreenOff\":%s,"
                 "\"sleepMode\":\"%s\"}",
                 next.sleep_timeout_sec,
                 next.listen_screen_off ? "true" : "false",
                 next.sleep_mode == CcSleepMode::kDeepSleep ? "deep_sleep" : "screen_off");
        *ack_json = buf;
    }
    return true;
}
