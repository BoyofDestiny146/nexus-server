#!/bin/sh
# sudo nexus-start — start nexus.service, wait for the same health checks
# as nexus-status, then print status.
# --force restarts the unit (re-runs compose up -d --pull never).
set -eu

LIB=""
if [ -f /usr/local/lib/nexus/nexus-lib.sh ]; then
  LIB=/usr/local/lib/nexus/nexus-lib.sh
elif [ -f "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-lib.sh" ]; then
  LIB="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-lib.sh"
else
  echo "nexus-start: nexus-lib.sh not found" >&2
  exit 1
fi
# shellcheck disable=SC1090
. "$LIB"
nexus_need_root

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --help|-h)
      echo "Usage: sudo nexus-start [--force]"
      exit 0
      ;;
    *) nexus_die "unknown argument: $arg" ;;
  esac
done

if ! systemctl cat nexus.service >/dev/null 2>&1; then
  nexus_die "nexus.service not installed; run sudo deploy/jetson/install-nexus-ops.sh"
fi

if [ "$FORCE" = "1" ]; then
  echo "nexus-start: systemctl restart nexus"
  systemctl restart nexus.service
else
  echo "nexus-start: systemctl start nexus"
  systemctl start nexus.service
fi

echo "nexus-start: waiting for API / XiaoZhi / Piper / Caddy / web / DB health..."
ready=0
if nexus_wait_ready; then
  ready=1
else
  echo "nexus-start: timed out waiting for health (see checks below)" >&2
  nexus_stack_ready_report >&2 || true
fi

STATUS="${NEXUS_STATUS_BIN:-}"
if [ -z "$STATUS" ] && [ -x /usr/local/sbin/nexus-status ]; then
  STATUS=/usr/local/sbin/nexus-status
elif [ -z "$STATUS" ] && [ -x "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-status.sh" ]; then
  STATUS="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-status.sh"
fi
if [ -n "$STATUS" ]; then
  "$STATUS"
fi

[ "$ready" = "1" ]
