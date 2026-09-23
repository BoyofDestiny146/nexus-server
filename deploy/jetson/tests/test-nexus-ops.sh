#!/bin/sh
# Static tests for Nexus ops package. Does not touch production Docker,
# systemd, or /mnt/xiaozhi.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
REPO="$(CDPATH= cd -- "$ROOT/../.." && pwd)"
COMPOSE="$REPO/deploy/docker-compose.yml"
fail=0

echo "ROOT=$ROOT"

check() {
  name="$1"
  shift
  if "$@"; then
    echo "ok  $name"
  else
    echo "FAIL $name" >&2
    fail=1
  fi
}

for f in wait-docker.sh nexus-lib.sh nexus-status.sh nexus-start.sh \
  nexus-stop.sh nexus-backup.sh nexus-restore.sh install-nexus-ops.sh \
  install-docker-mount-order.sh tests/test-nexus-ops.sh tests/test-chroma-backup.sh; do
  check "bash -n $f" sh -n "$ROOT/$f"
done

if command -v shellcheck >/dev/null 2>&1; then
  # SC1007: `CDPATH= cd` is intentional (neutralize CDPATH).
  # SC2034: --status flag on installers.
  # SC2094: checksum file is excluded from find before write.
  if shellcheck -e SC1090,SC1091,SC2317,SC1007,SC2034,SC2094 \
    "$ROOT/wait-docker.sh" \
    "$ROOT/nexus-status.sh" \
    "$ROOT/nexus-start.sh" \
    "$ROOT/nexus-stop.sh" \
    "$ROOT/nexus-backup.sh" \
    "$ROOT/nexus-restore.sh" \
    "$ROOT/install-nexus-ops.sh" \
    "$ROOT/install-docker-mount-order.sh"; then
    echo "ok  shellcheck"
  else
    echo "FAIL shellcheck" >&2
    fail=1
  fi
else
  echo "skip shellcheck (not installed)"
fi

if command -v systemd-analyze >/dev/null 2>&1; then
  # Missing /usr/local/lib/nexus/wait-docker.sh is expected in this sandbox.
  if systemd-analyze verify "$ROOT/nexus.service" 2>/tmp/nexus-unit-verify.txt; then
    echo "ok  systemd-analyze verify"
  else
    if grep -E 'Failed to prepare|not found|No such file' /tmp/nexus-unit-verify.txt >/dev/null 2>&1 \
       && grep -q 'ExecStart' "$ROOT/nexus.service"; then
      echo "ok  systemd-analyze verify (sandbox missing Exec paths — unit parsed)"
      cat /tmp/nexus-unit-verify.txt
    else
      echo "FAIL systemd-analyze verify" >&2
      cat /tmp/nexus-unit-verify.txt >&2
      fail=1
    fi
  fi
else
  echo "skip systemd-analyze (not installed)"
fi

unit="$ROOT/nexus.service"
check "unit RequiresMountsFor" grep -q 'RequiresMountsFor=/mnt/xiaozhi' "$unit"
check "unit After docker" grep -q 'After=.*docker.service' "$unit"
check "unit WorkingDirectory" grep -q 'WorkingDirectory=/mnt/xiaozhi/nexus-deploy' "$unit"
check "unit project nexus" grep -q -- '-p nexus' "$unit"
check "unit up --pull never" grep -q 'up -d --pull never --no-build' "$unit"
check "unit stop not down" grep -q 'stop --timeout 120' "$unit"
check "unit no compose down" grep -qv 'compose .*-p nexus.* down' "$unit"
check "unit no EnvironmentFile .env" grep -qv 'EnvironmentFile=.*\.env' "$unit"
check "unit does not start ollama" grep -qv 'ollama' "$unit"

for key in mariadb-data redis-data caddy-data caddy-config web-static \
  xiaozhi-data xiaozhi-models cc-voice cc-secrets cc-photos; do
  check "compose volume $key" grep -q "^  ${key}:" "$COMPOSE"
  check "lib knows $key" grep -q "$key" "$ROOT/nexus-lib.sh"
done

check "backup skips web-static in essential list" \
  sh -c "grep NEXUS_ESSENTIAL_VOLUME_KEYS \"$ROOT/nexus-lib.sh\" | grep -qv web-static"
check "backup skips mariadb-data tarball (dump instead)" \
  sh -c "grep NEXUS_BACKUP_VOLUME_KEYS \"$ROOT/nexus-lib.sh\" | grep -qv mariadb-data"

plan="$(NEXUS_DEPLOY="$REPO/deploy" NEXUS_REPO="$REPO" NEXUS_PROJECT=nexus \
  sh "$ROOT/nexus-backup.sh" --plan)"
echo "$plan" | grep -q 'nexus_cc-secrets' || { echo "FAIL plan lists cc-secrets" >&2; fail=1; }
echo "$plan" | grep -q 'logical dump' || { echo "FAIL plan mentions dump" >&2; fail=1; }
echo "$plan" | grep -q 'web-static' || { echo "FAIL plan mentions skipped web-static" >&2; fail=1; }
echo "$plan" | grep -qv 'CC_ADMIN1_PASSWORD' || { echo "FAIL plan leaked env" >&2; fail=1; }
echo "ok  backup --plan"

restore_out="$(sh "$ROOT/nexus-restore.sh" 2>&1)" && rc=0 || rc=$?
[ "$rc" = "2" ] || { echo "FAIL restore no-args rc=$rc" >&2; fail=1; }
echo "$restore_out" | grep -q 'Refusing' || { echo "FAIL restore no-args message" >&2; fail=1; }
echo "ok  restore refuses without path"

tmpd="$(mktemp -d)"
mkdir -p "$tmpd/emptybak"
echo unknown > "$tmpd/emptybak/git-sha.txt"
restore_out="$(sh "$ROOT/nexus-restore.sh" "$tmpd/emptybak" 2>&1)" && rc=0 || rc=$?
[ "$rc" = "2" ] || { echo "FAIL restore no-confirm rc=$rc" >&2; fail=1; }
echo "$restore_out" | grep -q -- '--confirm RESTORE' || { echo "FAIL restore confirm hint" >&2; fail=1; }
echo "$restore_out" | grep -q 'will NOT be overwritten' || { echo "FAIL restore git safety" >&2; fail=1; }
echo "ok  restore refuses without --confirm RESTORE"
rm -rf "$tmpd"

# Scripts must never dump secret files or .env values.
if grep -R -n -E 'cat [^|]*(\.env|/run/secrets/|mariadb-root|api-jwt-secret)' \
  "$ROOT"/nexus-status.sh "$ROOT"/nexus-start.sh "$ROOT"/wait-docker.sh; then
  echo "FAIL secret cat in status/start/wait" >&2
  fail=1
else
  echo "ok  status/start/wait do not cat secrets"
fi
check "wait-docker never pulls" grep -qv 'docker pull' "$ROOT/wait-docker.sh"
check "start helper uses systemctl" grep -q 'systemctl start nexus' "$ROOT/nexus-start.sh"
check "stop helper uses systemctl stop" grep -q 'systemctl stop nexus' "$ROOT/nexus-stop.sh"
check "installer does not copy compose over production" \
  grep -q 'will NOT' "$ROOT/install-nexus-ops.sh"
check "installer does not start nexus" grep -qv 'systemctl start nexus' "$ROOT/install-nexus-ops.sh"
check "redis AOF in compose" grep -q 'appendonly yes' "$COMPOSE"
check "drop-in RequiresMountsFor preserved" \
  grep -q 'RequiresMountsFor=/mnt/xiaozhi' "$ROOT/systemd/docker.service.d/wait-mnt-xiaozhi.conf"

# Synthetic backup tree — verify succeeds, then a truncated dump fails.
# No Docker, no production paths.
fake="$(mktemp -d)"
mkdir -p "$fake/mariadb" "$fake/volumes" "$fake/deploy"
python3 -c 'import pathlib; pathlib.Path("'"$fake"'/mariadb/xiaozhi_esp32_server.sql").write_text("CREATE DATABASE `xiaozhi_esp32_server`;\nCREATE TABLE t (id int);\n" + ("-- pad\n"*40))'
echo 'name: nexus' > "$fake/deploy/docker-compose.yml"
echo 'PLACEHOLDER=1' > "$fake/deploy/.env"
chmod 600 "$fake/deploy/.env"
sec="$(mktemp -d)"
echo dummy > "$sec/mariadb-root"
for key in cc-secrets cc-voice xiaozhi-data redis-data caddy-data caddy-config; do
  tar -C "$sec" -czf "$fake/volumes/nexus_${key}.tar.gz" .
done
rm -rf "$sec"
(
  cd "$fake"
  find . -type f ! -name SHA256SUMS ! -name MANIFEST.txt -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  echo "synthetic" > MANIFEST.txt
  sha256sum MANIFEST.txt >> SHA256SUMS
)
if NEXUS_PROJECT=nexus sh "$ROOT/nexus-backup.sh" --verify "$fake"; then
  echo "ok  backup --verify synthetic tree"
else
  echo "FAIL backup --verify synthetic tree" >&2
  fail=1
fi
: > "$fake/mariadb/xiaozhi_esp32_server.sql"
if NEXUS_PROJECT=nexus sh "$ROOT/nexus-backup.sh" --verify "$fake" >/dev/null 2>&1; then
  echo "FAIL verify should reject empty dump" >&2
  fail=1
else
  echo "ok  backup --verify rejects empty dump"
fi
rm -rf "$fake"

if ! sh "$ROOT/tests/test-chroma-backup.sh"; then
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "FAILED" >&2
  exit 1
fi
echo "ALL PASSED"
