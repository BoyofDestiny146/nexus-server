#!/usr/bin/env bash
# preprovision-watcher.sh — reusable XiaoZhi-Watcher provisioning station.
#
# One command to (re)configure any SenseCAP/XiaoZhi-edition Watcher for the
# careconnect deployment. Self-bootstraps its own toolchain (esptool +
# Espressif NVS generator) into a venv — no conda, no global installs.
#
# What it writes: the device's `nvs` partition (offset 0x3b000, size 0xd2000),
# wifi namespace keys: ssid, password, ota_url. Firmware/voice partitions are
# untouched (NVS-only). phy.cal_data is dropped (firmware re-calibrates on first
# boot — a one-time slow boot, no functional loss).
#
# The OTA URL is the ONE constant for every watcher in the fleet; WiFi is per
# site. For remote-over-internet deployment the default OTA URL is the public
# tunnel host so the device is location-independent.
#
# COMMANDS
#   backup                      read the current nvs partition to a timestamped .bin
#   provision                   generate NVS (ota_url [+ wifi]) and flash it
#   restore <file.bin>          flash a saved nvs backup (rollback)
#   info                        chip id / flash id (sanity that esptool can talk)
#
# COMMON FLAGS
#   --port <dev>                serial port (default: /dev/ttyACM1)
#   --ota-url <url>             default: https://ota.haizel.online/xiaozhi/ota/
#   --ssid <ssid>               WiFi SSID (omit to leave WiFi unset -> device
#                               boots into SoftAP for on-site WiFi setup)
#   --password <pw>             WiFi password (discouraged; prefer --password-file)
#   --password-file <path>      file containing the WiFi password
#   --backup-dir <dir>          where backups go (default: snapshots/watcher-nvs/)
#   --no-flash                  generate the NVS image but DON'T write it (review)
#   --golden <dir>              restore the full golden flash from a snapshot dir
#
# EXAMPLES
#   # Office: bake the fleet OTA URL only; installer sets WiFi on-site via app
#   ./preprovision-watcher.sh provision
#   # Kit with known WiFi
#   ./preprovision-watcher.sh provision --ssid careconnect --password-file ~/wifi.txt
#   # Always back up first
#   ./preprovision-watcher.sh backup && ./preprovision-watcher.sh provision --ssid ...
set -euo pipefail

NVS_OFFSET=0x3b000
NVS_SIZE=0xd2000
CHIP=esp32s3
BAUD=460800

PORT=/dev/ttyACM1
OTA_URL="https://ota.haizel.online/xiaozhi/ota/"
SSID=""
PASSWORD=""
PASSWORD_FILE=""
BACKUP_DIR="$(cd "$(dirname "$0")/.." && pwd)/snapshots/watcher-nvs"
FLASH=1
VENV="${CARECONNECT_FLASHER_VENV:-$HOME/.cache/careconnect/flasher-venv}"

# esptool needs serial access; the user may not be in `dialout`.
SUDO=""
if [ ! -r "$PORT" ] 2>/dev/null && [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

log() { printf '\033[1;36m[flash]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[flash] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

bootstrap_venv() {
    if [ -x "$VENV/bin/esptool.py" ] || [ -x "$VENV/bin/esptool" ]; then return; fi
    log "bootstrapping flasher toolchain into $VENV (one-time)…"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" -q install --upgrade pip >/dev/null
    "$VENV/bin/pip" -q install esptool esp-idf-nvs-partition-gen >/dev/null
    log "toolchain ready."
}

esptool_bin() {
    if [ -x "$VENV/bin/esptool.py" ]; then echo "$VENV/bin/esptool.py"; else echo "$VENV/bin/esptool"; fi
}

cmd_info() {
    bootstrap_venv
    $SUDO "$(esptool_bin)" --port "$PORT" --chip "$CHIP" --before default-reset flash-id
}

cmd_backup() {
    bootstrap_venv
    mkdir -p "$BACKUP_DIR"
    local out="$BACKUP_DIR/nvs-$(date +%Y%m%d-%H%M%S).bin"
    log "reading nvs partition ($NVS_OFFSET +$NVS_SIZE) from $PORT -> $out"
    $SUDO "$(esptool_bin)" --port "$PORT" --baud "$BAUD" --chip "$CHIP" \
        --before default-reset --after hard-reset \
        read-flash "$NVS_OFFSET" "$NVS_SIZE" "$out"
    log "backup saved: $out ($(stat -c%s "$out" 2>/dev/null || echo '?') bytes)"
    echo "$out"
}

cmd_restore() {
    local img="${1:-}"; [ -f "$img" ] || die "restore needs an existing .bin (got '$img')"
    bootstrap_venv
    log "restoring $img to nvs @ $NVS_OFFSET on $PORT"
    $SUDO "$(esptool_bin)" --port "$PORT" --baud "$BAUD" --chip "$CHIP" \
        --before default-reset --after hard-reset \
        write-flash "$NVS_OFFSET" "$img"
    log "restore complete."
}

cmd_provision() {
    bootstrap_venv
    [ -n "$PASSWORD_FILE" ] && PASSWORD="$(tr -d '\r\n' < "$PASSWORD_FILE")"
    local csv img
    csv="$(mktemp --suffix=.csv)"
    img="$(mktemp --suffix=.bin)"
    {
        echo "key,type,encoding,value"
        echo "wifi,namespace,,"
        if [ -n "$SSID" ]; then
            echo "ssid,data,string,$SSID"
            echo "password,data,string,$PASSWORD"
        fi
        echo "ota_url,data,string,$OTA_URL"
    } > "$csv"
    log "NVS keys:  ota_url=$OTA_URL  ssid=${SSID:-<unset: SoftAP setup on-site>}  password=${PASSWORD:+***}"
    "$VENV/bin/python" -m esp_idf_nvs_partition_gen.nvs_partition_gen generate "$csv" "$img" "$NVS_SIZE"
    rm -f "$csv"
    log "generated NVS image: $img ($(stat -c%s "$img") bytes)"
    if [ "$FLASH" -eq 1 ]; then
        log "flashing to $PORT @ $NVS_OFFSET …"
        $SUDO "$(esptool_bin)" --port "$PORT" --baud "$BAUD" --chip "$CHIP" \
            --before default-reset --after hard-reset \
            write-flash "$NVS_OFFSET" "$img"
        log "flash complete. Device will reboot and POST to $OTA_URL"
    else
        log "--no-flash: image left at $img (review, then flash with: $(esptool_bin) --port $PORT --chip $CHIP write-flash $NVS_OFFSET $img)"
    fi
}

cmd_golden() {
    local dir="${1:-}"; [ -d "$dir" ] || die "golden restore needs a snapshot dir"
    die "golden full-flash restore is intentionally manual — see $dir/README-RESTORE.md (full-chip write, higher risk)."
}

CMD="${1:-}"; shift || true
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; shift 2;;
        --ota-url) OTA_URL="$2"; shift 2;;
        --ssid) SSID="$2"; shift 2;;
        --password) PASSWORD="$2"; shift 2;;
        --password-file) PASSWORD_FILE="$2"; shift 2;;
        --backup-dir) BACKUP_DIR="$2"; shift 2;;
        --no-flash) FLASH=0; shift;;
        --golden) cmd_golden "$2"; exit 0;;
        *) RESTORE_ARG="$1"; shift;;
    esac
done

case "$CMD" in
    info)      cmd_info;;
    backup)    cmd_backup;;
    provision) cmd_provision;;
    restore)   cmd_restore "${RESTORE_ARG:-}";;
    *) die "usage: $0 {backup|provision|restore <file>|info} [flags] (see header)";;
esac
