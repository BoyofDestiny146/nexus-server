#!/bin/sh
# careconnect-api entrypoint.
#
# The shared cc-voice volume is created as root:root 755. xiaozhi-server
# overlay runs as root and writes /data/voice_config.json; this API runs as
# appuser. Without world-writable /data, PUT /api/device/{mac}/voice raises
# PermissionError and the dashboard shows "unexpected 500".
set -eu

if [ -d /data ]; then
  chmod 0777 /data 2>/dev/null || true
  if [ -e /data/voice_config.json ]; then
    chmod 0666 /data/voice_config.json 2>/dev/null || true
  fi
fi

if [ "$(id -u)" = "0" ]; then
  exec gosu appuser "$@"
fi
exec "$@"
