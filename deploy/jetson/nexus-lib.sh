# Shared Nexus ops helpers. Sourced by nexus-status/start/stop/backup/restore.
# Never print secret file contents, .env values, or dump SQL to stdout.

# shellcheck disable=SC2034
NEXUS_MOUNT="${NEXUS_MOUNT:-/mnt/xiaozhi}"
NEXUS_REPO="${NEXUS_REPO:-/mnt/xiaozhi/nexus-server}"
NEXUS_DEPLOY="${NEXUS_DEPLOY:-/mnt/xiaozhi/nexus-deploy}"
NEXUS_PROJECT="${NEXUS_PROJECT:-nexus}"
NEXUS_BACKUP_ROOT="${NEXUS_BACKUP_ROOT:-/mnt/xiaozhi/backups}"
NEXUS_COMPOSE_FILE="${NEXUS_COMPOSE_FILE:-$NEXUS_DEPLOY/docker-compose.yml}"

# Named volume keys from deploy/docker-compose.yml. Runtime names are
# ${NEXUS_PROJECT}_<key> when using `docker compose -p nexus`.
NEXUS_VOLUME_KEYS="mariadb-data redis-data caddy-data caddy-config web-static xiaozhi-data xiaozhi-models cc-voice cc-secrets cc-photos"

# Backed up as volume tarballs. mariadb-data is omitted (logical dump is
# the source of truth). web-static is omitted (web one-shot recopies it).
NEXUS_BACKUP_VOLUME_KEYS="cc-secrets cc-voice xiaozhi-data redis-data caddy-data caddy-config cc-photos xiaozhi-models"

# Fail the backup if these volume tarballs are missing/empty.
NEXUS_ESSENTIAL_VOLUME_KEYS="cc-secrets cc-voice xiaozhi-data redis-data caddy-data caddy-config"

# Secret files expected inside cc-secrets (names only — never cat them).
NEXUS_SECRET_NAMES="mariadb-root mariadb-app api-jwt-secret api-internal-token api-client-key mqtt-signature-key integration-secret-key"

nexus_die() {
  echo "nexus: $*" >&2
  exit 1
}

nexus_need_root() {
  if [ "$(id -u)" != "0" ]; then
    nexus_die "run as root (sudo $0)"
  fi
}

nexus_vol() {
  printf '%s_%s' "$NEXUS_PROJECT" "$1"
}

nexus_compose() {
  # Do not pass systemd EnvironmentFile=.env. Compose reads .env from
  # --project-directory. Never `compose config` (interpolates secrets).
  docker compose -p "$NEXUS_PROJECT" --project-directory "$NEXUS_DEPLOY" "$@"
}

nexus_service_running() {
  docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null | grep -qx true
}

nexus_cid() {
  nexus_compose ps -a -q "$1" 2>/dev/null | head -n 1
}

nexus_git_sha() {
  if [ -d "$NEXUS_REPO/.git" ] && command -v git >/dev/null 2>&1; then
    git -C "$NEXUS_REPO" rev-parse HEAD 2>/dev/null || echo "unknown"
  else
    echo "unknown"
  fi
}

nexus_git_describe() {
  if [ -d "$NEXUS_REPO/.git" ] && command -v git >/dev/null 2>&1; then
    git -C "$NEXUS_REPO" describe --tags --always --dirty 2>/dev/null || nexus_git_sha
  else
    echo "unknown"
  fi
}

nexus_helper_image() {
  # Prefer an image already on the host. Never pull.
  _img=""
  for _img in alpine:3.19 alpine:3.20 alpine:latest redis:7.4.3-alpine mariadb:10.11.11; do
    if docker image inspect "$_img" >/dev/null 2>&1; then
      printf '%s\n' "$_img"
      return 0
    fi
  done
  return 1
}

nexus_unit_active() {
  systemctl is-active --quiet nexus.service 2>/dev/null
}
