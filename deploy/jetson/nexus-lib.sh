#!/bin/sh
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

# One-shot static export; Exited (0) is healthy and must not be treated as
# "still running" after nexus-stop.
NEXUS_ONESHOT_SERVICE="web"

NEXUS_HEALTH_TRIES="${NEXUS_HEALTH_TRIES:-60}"
NEXUS_HEALTH_SLEEP="${NEXUS_HEALTH_SLEEP:-2}"
NEXUS_LAST_HEALTH_MSG=""

nexus_need_root() {
  if [ "${NEXUS_ASSUME_ROOT:-}" = "1" ]; then
    return 0
  fi
  if [ "$(id -u)" != "0" ]; then
    nexus_die "run as root (sudo $0)"
  fi
}

nexus_probe_http() {
  _url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -sf -m 4 "$_url" >/dev/null 2>&1
  elif command -v wget >/dev/null 2>&1; then
    wget -q -T 4 -O /dev/null "$_url" >/dev/null 2>&1
  else
    return 2
  fi
}

nexus_compose_ps_lines() {
  nexus_compose ps -a --format '{{.Service}} {{.State}} {{.Status}}' 2>/dev/null || true
}

# Print persistent (non-web) services whose State is running. Return 0 if any.
nexus_persistent_running() {
  _names="$(nexus_compose_ps_lines | awk -v skip="$NEXUS_ONESHOT_SERVICE" '$1 != "" && $1 != skip && $2 == "running" {print $1}')"
  if [ -z "$_names" ]; then
    return 1
  fi
  printf '%s\n' "$_names"
  return 0
}

nexus_inspect_running() {
  docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null | grep -qx true
}

nexus_inspect_health() {
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$1" 2>/dev/null || true
}

# 0 healthy, 1 failed/not running, 2 running with no Health block (use probe).
nexus_cid_health() {
  _svc="$1"
  _cid="$(nexus_cid "$_svc" || true)"
  if [ -z "${_cid:-}" ]; then
    NEXUS_LAST_HEALTH_MSG="container missing"
    return 1
  fi
  if ! nexus_inspect_running "$_cid"; then
    NEXUS_LAST_HEALTH_MSG="not running"
    return 1
  fi
  _hs="$(nexus_inspect_health "$_cid")"
  case "$_hs" in
    healthy)
      NEXUS_LAST_HEALTH_MSG="healthy"
      return 0
      ;;
    unhealthy)
      NEXUS_LAST_HEALTH_MSG="unhealthy"
      return 1
      ;;
    starting)
      NEXUS_LAST_HEALTH_MSG="starting"
      return 1
      ;;
  esac
  NEXUS_LAST_HEALTH_MSG="running"
  return 2
}

nexus_health_api() {
  _st=0
  nexus_cid_health api || _st=$?
  [ "$_st" = "0" ] && return 0
  [ "$_st" = "1" ] && return 1
  _cid="$(nexus_cid api || true)"
  if [ -n "${_cid:-}" ] && docker exec "$_cid" wget -qO- -T 4 http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
    NEXUS_LAST_HEALTH_MSG="healthz"
    return 0
  fi
  NEXUS_LAST_HEALTH_MSG="healthz failed"
  return 1
}

nexus_health_xiaozhi() {
  _st=0
  nexus_cid_health xiaozhi-server || _st=$?
  [ "$_st" = "0" ] && return 0
  [ "$_st" = "1" ] && return 1
  _cid="$(nexus_cid xiaozhi-server || true)"
  if [ -n "${_cid:-}" ]; then
    if docker exec "$_cid" python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8003/xiaozhi/ota/', timeout=4).status==200 else 1)" >/dev/null 2>&1; then
      NEXUS_LAST_HEALTH_MSG="OTA (in-container)"
      return 0
    fi
    if docker exec "$_cid" python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8003/xiaozhi/ota/', timeout=4).status==200 else 1)" >/dev/null 2>&1; then
      NEXUS_LAST_HEALTH_MSG="OTA (in-container)"
      return 0
    fi
  fi
  if nexus_probe_http http://127.0.0.1:8003/xiaozhi/ota/; then
    NEXUS_LAST_HEALTH_MSG="OTA :8003"
    return 0
  fi
  NEXUS_LAST_HEALTH_MSG="OTA not healthy"
  return 1
}

nexus_health_piper() {
  _st=0
  nexus_cid_health piper-tts || _st=$?
  [ "$_st" = "0" ] && return 0
  [ "$_st" = "1" ] && return 1
  _cid="$(nexus_cid piper-tts || true)"
  if [ -n "${_cid:-}" ] && docker exec "$_cid" wget -qO- -T 4 http://127.0.0.1:5500/health >/dev/null 2>&1; then
    NEXUS_LAST_HEALTH_MSG="/health"
    return 0
  fi
  NEXUS_LAST_HEALTH_MSG="not healthy"
  return 1
}

nexus_health_caddy() {
  _st=0
  nexus_cid_health caddy || _st=$?
  [ "$_st" = "0" ] && return 0
  [ "$_st" = "1" ] && return 1
  _cid="$(nexus_cid caddy || true)"
  if [ -n "${_cid:-}" ] && docker exec "$_cid" wget -qO- -T 4 http://127.0.0.1:2019/config/ >/dev/null 2>&1; then
    NEXUS_LAST_HEALTH_MSG="admin (in-container)"
    return 0
  fi
  if nexus_probe_http http://127.0.0.1:2019/config/; then
    NEXUS_LAST_HEALTH_MSG="admin :2019"
    return 0
  fi
  NEXUS_LAST_HEALTH_MSG="admin API not healthy"
  return 1
}

nexus_health_web() {
  _line="$(nexus_compose_ps_lines | awk '$1=="web"{print; exit}')"
  if [ -z "$_line" ]; then
    NEXUS_LAST_HEALTH_MSG="container missing"
    return 1
  fi
  case "$_line" in
    *Exited\ \(0\)*|*exited\ \(0\)*)
      NEXUS_LAST_HEALTH_MSG="Exited (0) one-shot (normal)"
      return 0
      ;;
  esac
  NEXUS_LAST_HEALTH_MSG="$_line"
  return 1
}

nexus_health_mariadb() {
  _st=0
  nexus_cid_health mariadb || _st=$?
  [ "$_st" = "0" ] && return 0
  [ "$_st" = "1" ] && return 1
  _cid="$(nexus_cid mariadb || true)"
  if [ -n "${_cid:-}" ] && docker exec "$_cid" healthcheck.sh --su-mysql --connect --innodb_initialized >/dev/null 2>&1; then
    NEXUS_LAST_HEALTH_MSG="innodb"
    return 0
  fi
  NEXUS_LAST_HEALTH_MSG="not healthy"
  return 1
}

nexus_health_redis() {
  _st=0
  nexus_cid_health redis || _st=$?
  [ "$_st" = "0" ] && return 0
  [ "$_st" = "1" ] && return 1
  _cid="$(nexus_cid redis || true)"
  if [ -n "${_cid:-}" ] && docker exec "$_cid" redis-cli ping >/dev/null 2>&1; then
    NEXUS_LAST_HEALTH_MSG="PONG"
    return 0
  fi
  NEXUS_LAST_HEALTH_MSG="not healthy"
  return 1
}

nexus_health_mqtt() {
  _st=0
  nexus_cid_health xiaozhi-mqtt-gateway || _st=$?
  [ "$_st" = "0" ] && return 0
  [ "$_st" = "1" ] && return 1
  _cid="$(nexus_cid xiaozhi-mqtt-gateway || true)"
  if [ -n "${_cid:-}" ] && docker exec "$_cid" node -e "const n=require('net');const c=n.createConnection(1883,'127.0.0.1');c.on('connect',()=>{c.destroy();process.exit(0)});c.on('error',()=>process.exit(1))" >/dev/null 2>&1; then
    NEXUS_LAST_HEALTH_MSG="mqtt :1883"
    return 0
  fi
  NEXUS_LAST_HEALTH_MSG="not healthy"
  return 1
}

nexus_stack_ready() {
  nexus_health_api \
    && nexus_health_xiaozhi \
    && nexus_health_piper \
    && nexus_health_caddy \
    && nexus_health_web \
    && nexus_health_mariadb \
    && nexus_health_redis \
    && nexus_health_mqtt
}

nexus_health_line() {
  _n="$1"
  shift
  if "$@"; then
    printf '  %-14s ok    %s\n' "$_n" "$NEXUS_LAST_HEALTH_MSG"
  else
    printf '  %-14s FAIL  %s\n' "$_n" "$NEXUS_LAST_HEALTH_MSG"
  fi
}

nexus_stack_ready_report() {
  nexus_health_line api nexus_health_api
  nexus_health_line xiaozhi nexus_health_xiaozhi
  nexus_health_line piper nexus_health_piper
  nexus_health_line caddy nexus_health_caddy
  nexus_health_line web nexus_health_web
  nexus_health_line mariadb nexus_health_mariadb
  nexus_health_line redis nexus_health_redis
  nexus_health_line mqtt nexus_health_mqtt
}

nexus_wait_ready() {
  _i=0
  while [ "$_i" -lt "$NEXUS_HEALTH_TRIES" ]; do
    if nexus_stack_ready; then
      return 0
    fi
    _i=$((_i + 1))
    sleep "$NEXUS_HEALTH_SLEEP"
  done
  return 1
}

# Graceful stop. Never remove networks/volumes/images. Success only after
# persistent containers are not running. web Exited (0) is ignored.
nexus_do_stop() {
  if systemctl cat nexus.service >/dev/null 2>&1 && nexus_unit_active; then
    echo "nexus-stop: systemctl stop nexus"
    systemctl stop nexus.service
  elif systemctl cat nexus.service >/dev/null 2>&1; then
    echo "nexus-stop: nexus.service inactive (no ExecStop); will stop compose if needed"
  else
    echo "nexus-stop: nexus.service not installed"
  fi

  if nexus_persistent_running >/dev/null; then
    echo "nexus-stop: docker compose -p ${NEXUS_PROJECT} stop --timeout 120"
    nexus_compose stop --timeout 120
  fi

  _still="$(nexus_persistent_running || true)"
  if [ -n "${_still:-}" ]; then
    echo "nexus-stop: FAILED — still running:" >&2
    printf '%s\n' "$_still" >&2
    nexus_compose ps -a || true
    return 1
  fi
  echo "nexus-stop: containers stopped; volumes, networks, images, secrets retained"
  return 0
}
