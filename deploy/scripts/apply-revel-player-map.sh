#!/usr/bin/env bash
# Apply 020 (create cc_revel_player_map) then 021 (nullable control row cache)
# to the existing Nexus MariaDB. Additive only — does not drop or recreate
# tables, and does not enable REVEL_EXECUTE_ENABLED.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
COMPOSE="${COMPOSE:-$ROOT/deploy/docker-compose.yml}"
DB_NAME="${CC_DB_NAME:-xiaozhi_esp32_server}"

if [[ ! -f "$COMPOSE" ]]; then
  echo "apply-revel-player-map: compose file not found: $COMPOSE" >&2
  exit 1
fi
if [[ ! -f "$ROOT/api/migrations/020_revel_player_map.sql" ]]; then
  echo "apply-revel-player-map: missing 020_revel_player_map.sql" >&2
  exit 1
fi
if [[ ! -f "$ROOT/api/migrations/021_revel_player_control_row.sql" ]]; then
  echo "apply-revel-player-map: missing 021_revel_player_control_row.sql" >&2
  exit 1
fi

run_sql() {
  local file="$1"
  docker compose -f "$COMPOSE" exec -T mariadb sh -c \
    'mariadb -u root -p"$(cat /run/secrets/mariadb-root)" "$MARIADB_DATABASE"' \
    < "$file"
}

echo "Applying 020_revel_player_map.sql to $DB_NAME (CREATE TABLE IF NOT EXISTS)..."
run_sql "$ROOT/api/migrations/020_revel_player_map.sql"
echo "Applying 021_revel_player_control_row.sql (ADD COLUMN IF missing)..."
run_sql "$ROOT/api/migrations/021_revel_player_control_row.sql"
echo "Verification:"
run_sql "$ROOT/api/migrations/020_021_revel_player_map.verify.sql"
echo "Done. REVEL_EXECUTE_ENABLED is not changed by this script."
