#!/bin/sh
# sudo nexus-stop — same graceful stop as `systemctl stop nexus`.
# Does not shut down the Jetson.
set -eu

LIB=""
if [ -f /usr/local/lib/nexus/nexus-lib.sh ]; then
  LIB=/usr/local/lib/nexus/nexus-lib.sh
elif [ -f "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-lib.sh" ]; then
  LIB="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-lib.sh"
else
  echo "nexus-stop: nexus-lib.sh not found" >&2
  exit 1
fi
# shellcheck disable=SC1090
. "$LIB"
nexus_need_root

if systemctl cat nexus.service >/dev/null 2>&1; then
  echo "nexus-stop: systemctl stop nexus"
  systemctl stop nexus.service
else
  echo "nexus-stop: nexus.service not installed; compose stop --timeout 120"
  [ -f "$NEXUS_COMPOSE_FILE" ] || nexus_die "$NEXUS_COMPOSE_FILE missing"
  nexus_compose stop --timeout 120
fi
echo "nexus-stop: containers stopped; volumes, networks, images, secrets retained"
