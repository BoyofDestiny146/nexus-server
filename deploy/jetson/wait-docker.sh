#!/bin/sh
# ExecStartPre for nexus.service. Fail if the SSD is missing or Docker is
# not actually usable yet. Does not pull, build, prune, or start Ollama.
set -eu

MOUNT="${NEXUS_MOUNT:-/mnt/xiaozhi}"
DEPLOY="${NEXUS_DEPLOY:-/mnt/xiaozhi/nexus-deploy}"
TRIES="${NEXUS_DOCKER_WAIT_TRIES:-60}"
SLEEP_S="${NEXUS_DOCKER_WAIT_SLEEP:-2}"

if [ ! -d "$MOUNT" ]; then
  echo "nexus: $MOUNT does not exist" >&2
  exit 1
fi
if command -v findmnt >/dev/null 2>&1; then
  if ! findmnt -n "$MOUNT" >/dev/null 2>&1; then
    echo "nexus: $MOUNT is not a mount point" >&2
    exit 1
  fi
fi
if [ ! -d "$DEPLOY" ]; then
  echo "nexus: deploy directory missing: $DEPLOY" >&2
  exit 1
fi
if [ ! -f "$DEPLOY/docker-compose.yml" ]; then
  echo "nexus: $DEPLOY/docker-compose.yml missing (will not copy from git)" >&2
  exit 1
fi

i=0
while [ "$i" -lt "$TRIES" ]; do
  if docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    exit 0
  fi
  i=$((i + 1))
  sleep "$SLEEP_S"
done

echo "nexus: Docker not usable after ${TRIES} tries" >&2
exit 1
