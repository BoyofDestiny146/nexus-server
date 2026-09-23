#!/bin/sh
# sudo nexus-status — concise production health. Never prints secrets or .env.
set -eu

LIB=""
if [ -f /usr/local/lib/nexus/nexus-lib.sh ]; then
  LIB=/usr/local/lib/nexus/nexus-lib.sh
elif [ -f "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-lib.sh" ]; then
  LIB="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-lib.sh"
else
  echo "nexus-status: nexus-lib.sh not found" >&2
  exit 1
fi
# shellcheck disable=SC1090
. "$LIB"

ok() { printf '  %-14s ok    %s\n' "$1" "$2"; }
bad() { printf '  %-14s FAIL  %s\n' "$1" "$2"; }
note() { printf '  %-14s %s    %s\n' "$1" "$2" "$3"; }

echo "=== Nexus status ==="
echo "time:    $(date -Iseconds 2>/dev/null || date)"
echo "host:    $(hostname) $(uname -m)"
echo

echo "-- mount --"
if command -v findmnt >/dev/null 2>&1 && findmnt -n "$NEXUS_MOUNT" >/dev/null 2>&1; then
  findmnt -n -o SOURCE,TARGET,FSTYPE,OPTIONS "$NEXUS_MOUNT" | awk '{print "  "$0}'
else
  bad mount "not mounted ($NEXUS_MOUNT)"
fi
if command -v df >/dev/null 2>&1; then
  df -h "$NEXUS_MOUNT" 2>/dev/null | awk 'NR==2 {printf "  disk          %s used, %s free of %s (%s)\n",$3,$4,$2,$5}'
fi
echo

echo "-- systemd --"
for u in containerd.service docker.service nexus.service ollama.service ollama-warm.service; do
  if systemctl cat "$u" >/dev/null 2>&1; then
    _act="$(systemctl is-active "$u" 2>/dev/null || true)"
    _en="$(systemctl is-enabled "$u" 2>/dev/null || true)"
    printf '  %-22s %-12s %s\n' "$u" "$_act" "$_en"
  else
    printf '  %-22s %-12s %s\n' "$u" "absent" "-"
  fi
done
echo

echo "-- compose $NEXUS_PROJECT ps -a --"
if docker info >/dev/null 2>&1; then
  if [ -f "$NEXUS_COMPOSE_FILE" ]; then
    nexus_compose ps -a || true
  else
    bad compose "$NEXUS_COMPOSE_FILE missing (not copying git compose)"
  fi
else
  bad docker "inactive / not usable"
fi
echo

echo "-- health --"
if docker info >/dev/null 2>&1 && [ -f "$NEXUS_COMPOSE_FILE" ]; then
  nexus_stack_ready_report
else
  bad health "skipped (docker/compose unavailable)"
fi
echo

echo "-- ollama (native, not a compose service) --"
if nexus_probe_http http://127.0.0.1:11434/api/version; then
  ok ollama "http://127.0.0.1:11434"
  if command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
    echo "  models:"
    curl -sf -m 4 http://127.0.0.1:11434/api/tags 2>/dev/null | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(0)
for m in d.get("models") or []:
    print("   -", m.get("name", "?"))
' || true
  fi
  if command -v ollama >/dev/null 2>&1; then
    echo "  loaded:"
    ollama ps 2>/dev/null | awk 'NR==1{next} NF{print "   - "$0}' || echo "   (none)"
  fi
else
  note ollama "DOWN" "not answering on :11434"
fi
echo

echo "-- git --"
echo "  repo     $NEXUS_REPO"
echo "  sha      $(nexus_git_sha)"
echo "  describe $(nexus_git_describe)"
echo
echo "(secrets and .env are not printed)"
