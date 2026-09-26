#!/usr/bin/env bash
# Read-only Revel binding report. Never enables writes and never issues PUT.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
COMPOSE="${COMPOSE:-$ROOT/deploy/docker-compose.yml}"

TOKEN="$(docker run --rm -v careconnect_cc-secrets:/s:ro alpine cat /s/api-internal-token)"

echo "=== discovered devices (read-only) ==="
docker compose -f "$COMPOSE" exec -T api wget -qO- \
  --header="X-Internal-Token: ${TOKEN}" \
  http://127.0.0.1:8080/api/internal/revel/devices
echo
echo "=== data tables (read-only) ==="
docker compose -f "$COMPOSE" exec -T api wget -qO- \
  --header="X-Internal-Token: ${TOKEN}" \
  http://127.0.0.1:8080/api/internal/revel/datatables
echo
echo "=== control table binding (REVEL_CONTROL_TABLE_ID, read-only) ==="
docker compose -f "$COMPOSE" exec -T api wget -qO- \
  --header="X-Internal-Token: ${TOKEN}" \
  http://127.0.0.1:8080/api/internal/revel/control
echo
echo "This script does not PUT, bind a player, or set REVEL_EXECUTE_ENABLED."
