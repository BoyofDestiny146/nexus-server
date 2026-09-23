#!/bin/sh
# sudo nexus-stop — graceful compose stop. Does not shut down the Jetson.
# If nexus.service is inactive, systemd will not run ExecStop; we then stop
# compose directly. Never remove networks or volumes.
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
nexus_do_stop
