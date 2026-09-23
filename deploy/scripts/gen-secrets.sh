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
set -euo pipefail
PROJECT="${COMPOSE_PROJECT_NAME:-careconnect}"
VOL="${PROJECT}_cc-secrets"
docker volume create "$VOL" >/dev/null
for name in mariadb-root mariadb-app api-jwt-secret api-internal-token api-client-key mqtt-signature-key; do
  docker run --rm -v "$VOL":/s alpine:3.19 sh -c \
    "if [ -s /s/$name ]; then echo 'keep    $name'; else head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > /s/$name && chmod 600 /s/$name && echo 'created $name'; fi"
done
# Fernet key = urlsafe-base64 of 32 random bytes (not hex like the files above).
docker run --rm -v "$VOL":/s alpine:3.19 sh -c \
  "if [ -s /s/integration-secret-key ]; then echo 'keep    integration-secret-key'; else head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n' > /s/integration-secret-key && chmod 600 /s/integration-secret-key && echo 'created integration-secret-key'; fi"
echo "cc-secrets volume ready: $VOL"
echo "Read a value later with:  docker run --rm -v $VOL:/s:ro alpine cat /s/api-client-key"
