#!/bin/sh
# careconnect-web init-container entrypoint.
#
# Named volume nexus_web-static is created as root:root 755. The image used
# to USER appuser, so `cp /app/out/. /srv/web/` failed with Permission denied
# after a volume recreate. Start as root, make the mount writable by appuser
# (chown, not chmod 777), then run the compose copy command.
set -eu

if [ "$(id -u)" = "0" ] && [ -d /srv/web ]; then
  chown appuser:appuser /srv/web
fi

exec "$@"
