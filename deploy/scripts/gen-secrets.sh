#!/usr/bin/env bash
# gen-secrets.sh — create the cc-secrets Docker volume and populate it with
# random secret files. Run ONCE on the deployment host before the first
# `docker compose up`. Re-running does NOT overwrite existing files.
#
# Files written (read by the containers via /run/secrets/<name>):
#   mariadb-root         MariaDB root password
#   mariadb-app          MariaDB app-user password (api + xiaozhi-server)
#   api-jwt-secret       JWT signing secret (careconnect-api)
#   api-internal-token   xiaozhi-server -> api notify token
#   api-client-key       X-API-Key for the client integration API (/api/v1/*)
#   mqtt-signature-key   HMAC key for MQTT credential generation
#   integration-secret-key  Fernet key for per-client Revel (and future) credentials
#
# integration-secret-key uid/gid/mode are cloned from api-client-key (then
# api-internal-token, then api-jwt-secret) so the API's non-root appuser can
# read it after gosu. The volume is mounted :ro; do not create this file as
# alpine root:root 0600. The Fernet key is never printed.
set -euo pipefail
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT="${COMPOSE_PROJECT_NAME:-careconnect}"
VOL="${PROJECT}_cc-secrets"
docker volume create "$VOL" >/dev/null
for name in mariadb-root mariadb-app api-jwt-secret api-internal-token api-client-key mqtt-signature-key; do
  docker run --rm -v "$VOL":/s alpine:3.19 sh -c \
    "if [ -s /s/$name ]; then echo 'keep    $name'; else head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > /s/$name && chmod 600 /s/$name && echo 'created $name'; fi"
done
# Fernet key = urlsafe-base64 of 32 random bytes (not hex like the files above).
if [ ! -f "$SCRIPT_DIR/align_secret_perms.sh" ]; then
  echo "gen-secrets: missing $SCRIPT_DIR/align_secret_perms.sh" >&2
  exit 1
fi
docker run --rm \
  -v "$VOL":/s \
  -v "$SCRIPT_DIR/align_secret_perms.sh":/align_secret_perms.sh:ro \
  alpine:3.19 sh /align_secret_perms.sh
echo "cc-secrets volume ready: $VOL"
echo "Read a value later with:  docker run --rm -v $VOL:/s:ro alpine cat /s/api-client-key"
