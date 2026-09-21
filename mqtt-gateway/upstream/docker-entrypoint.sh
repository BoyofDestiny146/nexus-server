#!/bin/sh
# docker-entrypoint.sh — careconnect-mqtt-gateway
#
# 1. Resolve MQTT_SIGNATURE_KEY from a secret file (MQTT_SIGNATURE_KEY_FILE)
#    or from the env var directly (MQTT_SIGNATURE_KEY).
# 2. Write config/mqtt.json with the correct production chat_server URL
#    from XIAOZHI_SERVER_WS (default: ws://xiaozhi-server:8000/xiaozhi/v1/).
# 3. Exec the CMD (node app.js).

set -e

# ── Secret: MQTT_SIGNATURE_KEY ────────────────────────────────────────────────
if [ -n "${MQTT_SIGNATURE_KEY_FILE}" ] && [ -f "${MQTT_SIGNATURE_KEY_FILE}" ]; then
    export MQTT_SIGNATURE_KEY="$(cat "${MQTT_SIGNATURE_KEY_FILE}")"
fi

if [ -z "${MQTT_SIGNATURE_KEY}" ]; then
    echo "[entrypoint] WARN: MQTT_SIGNATURE_KEY not set — admin API will refuse to start"
fi

# ── Runtime mqtt.json ─────────────────────────────────────────────────────────
CHAT_SERVER="${XIAOZHI_SERVER_WS:-ws://xiaozhi-server:8000/xiaozhi/v1/}"

cat > /app/config/mqtt.json <<EOF
{
    "production": {
        "chat_servers": [
            "${CHAT_SERVER}"
        ]
    },
    "development": {
        "chat_servers": [
            "${CHAT_SERVER}"
        ],
        "mac_addresss": []
    },
    "debug": false,
    "max_mqtt_payload_size": 8192,
    "mcp_client": {
        "capabilities": {},
        "client_info": {"name": "xiaozhi-mqtt-client", "version": "1.0.0"},
        "max_tools_count": 128
    }
}
EOF

echo "[entrypoint] mqtt.json written (chat_server=${CHAT_SERVER})"

exec "$@"
