#!/bin/sh
# sudo nexus-backup — consistent production backup. Never prints secrets.
#
# Usage:
#   sudo nexus-backup
#   sudo nexus-backup --plan
#   sudo nexus-backup --skip-models
#   sudo nexus-backup --verify /path/to/backup
set -eu

LIB=""
if [ -f /usr/local/lib/nexus/nexus-lib.sh ]; then
  LIB=/usr/local/lib/nexus/nexus-lib.sh
elif [ -f "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-lib.sh" ]; then
  LIB="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-lib.sh"
else
  echo "nexus-backup: nexus-lib.sh not found" >&2
  exit 1
fi
# shellcheck disable=SC1090
. "$LIB"

umask 077
PLAN=0
VERIFY_PATH=""
SKIP_MODELS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-models) SKIP_MODELS=1; shift ;;
    --plan) PLAN=1; shift ;;
    --verify)
      VERIFY_PATH="${2:-}"
      [ -n "$VERIFY_PATH" ] || { echo "--verify requires a path" >&2; exit 2; }
      shift 2
      ;;
    --help|-h)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

verify_backup_dir() {
  d="$1"
  err=0
  [ -d "$d" ] || { echo "verify: not a directory: $d" >&2; return 1; }
  [ -f "$d/MANIFEST.txt" ] || { echo "verify: MANIFEST.txt missing" >&2; err=1; }
  [ -f "$d/SHA256SUMS" ] || { echo "verify: SHA256SUMS missing" >&2; err=1; }
  if [ -f "$d/SHA256SUMS" ]; then
    if ! (cd "$d" && sha256sum -c SHA256SUMS --quiet); then
      echo "verify: checksum mismatch" >&2
      err=1
    fi
  fi
  dump=""
  dump="$(find "$d/mariadb" -type f -name '*.sql' 2>/dev/null | head -n 1 || true)"
  if [ -z "$dump" ] || [ ! -s "$dump" ]; then
    echo "verify: MariaDB dump missing or empty" >&2
    err=1
  else
    if ! grep -q -E 'CREATE DATABASE|CREATE TABLE' "$dump"; then
      echo "verify: MariaDB dump does not look like a logical dump" >&2
      err=1
    fi
    bytes="$(wc -c < "$dump" | tr -d ' ')"
    if [ "$bytes" -lt 200 ]; then
      echo "verify: MariaDB dump too small ($bytes bytes)" >&2
      err=1
    fi
  fi
  [ -f "$d/deploy/docker-compose.yml" ] || { echo "verify: deploy/docker-compose.yml missing" >&2; err=1; }
  [ -f "$d/deploy/.env" ] || { echo "verify: deploy/.env missing" >&2; err=1; }
  secrets=""
  secrets="$(find "$d/volumes" -type f -name '*cc-secrets*.tar.gz' 2>/dev/null | head -n 1 || true)"
  if [ -z "$secrets" ] || [ ! -s "$secrets" ]; then
    echo "verify: cc-secrets archive missing" >&2
    err=1
  else
    if ! tar -tzf "$secrets" >/dev/null 2>&1; then
      echo "verify: cc-secrets archive not readable" >&2
      err=1
    fi
  fi
  for key in $NEXUS_ESSENTIAL_VOLUME_KEYS; do
    f="$d/volumes/$(nexus_vol "$key").tar.gz"
    if [ ! -s "$f" ]; then
      echo "verify: essential volume archive missing: $key" >&2
      err=1
    fi
  done
  [ "$err" = "0" ]
}

if [ -n "$VERIFY_PATH" ]; then
  verify_backup_dir "$VERIFY_PATH"
  echo "verify: ok $VERIFY_PATH"
  exit 0
fi

echo "=== Nexus backup plan ==="
echo "project:   $NEXUS_PROJECT"
echo "deploy:    $NEXUS_DEPLOY"
echo "repo:      $NEXUS_REPO"
echo "dest root: $NEXUS_BACKUP_ROOT"
echo "compose:   $NEXUS_COMPOSE_FILE"
echo
echo "MariaDB:   logical dump from running mariadb container (required)"
echo "deploy:    copy of $NEXUS_DEPLOY (compose, .env, scripts) — contents not printed"
echo "volumes (tar.gz, project prefix ${NEXUS_PROJECT}_):"
for key in $NEXUS_BACKUP_VOLUME_KEYS; do
  extra=""
  [ "$key" = "xiaozhi-models" ] && [ "$SKIP_MODELS" = "1" ] && extra=" [SKIP --skip-models]"
  echo "  - $(nexus_vol "$key")$extra"
done
echo "skipped volumes:"
echo "  - $(nexus_vol mariadb-data)  (logical dump is source of truth)"
echo "  - $(nexus_vol web-static)    (web one-shot recopies from image)"
echo "chroma:    xiaozhi-server ~/.local/share/careconnect/chroma if present"
echo "systemd:   nexus.service + docker/containerd mount-order drop-ins if installed"
echo "inventory: git SHA, compose images/digests, ollama tags"
echo "NOT included: Docker image layers (reproducible), Watcher firmware"
if [ "$PLAN" = "1" ]; then
  exit 0
fi

nexus_need_root
[ -f "$NEXUS_COMPOSE_FILE" ] || nexus_die "$NEXUS_COMPOSE_FILE missing (will not copy git compose over production)"
docker info >/dev/null 2>&1 || nexus_die "Docker not usable"
mkdir -p "$NEXUS_BACKUP_ROOT"
chmod 700 "$NEXUS_BACKUP_ROOT"

LOCK="$NEXUS_BACKUP_ROOT/.nexus-backup.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  nexus_die "another backup is running ($LOCK)"
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PARTIAL="$NEXUS_BACKUP_ROOT/nexus-${STAMP}.partial"
FINAL="$NEXUS_BACKUP_ROOT/nexus-${STAMP}"
FAILED="$NEXUS_BACKUP_ROOT/nexus-${STAMP}.failed"

fail_keep_last_good() {
  echo "nexus-backup: FAILED — previous LAST_GOOD (if any) was not modified" >&2
  if [ -d "$PARTIAL" ]; then
    rm -rf "$FAILED"
    mv "$PARTIAL" "$FAILED"
    echo "nexus-backup: incomplete tree moved to $FAILED" >&2
  fi
  exit 1
}

mkdir -m 700 "$PARTIAL"
for sub in mariadb volumes chroma systemd docker ollama deploy; do
  mkdir -m 700 "$PARTIAL/$sub"
done

echo "nexus-backup: writing $PARTIAL"

# --- git ---
SHA="$(nexus_git_sha)"
printf '%s\n' "$SHA" > "$PARTIAL/git-sha.txt"
{
  echo "repo=$NEXUS_REPO"
  echo "describe=$(nexus_git_describe)"
  echo "branch=$(git -C "$NEXUS_REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
} > "$PARTIAL/git-info.txt"

# --- host ---
{
  echo "hostname=$(hostname)"
  echo "arch=$(uname -m)"
  echo "kernel=$(uname -r)"
  echo "stamp_utc=$STAMP"
  echo "mount=$(findmnt -n -o SOURCE,FSTYPE "$NEXUS_MOUNT" 2>/dev/null || echo unknown)"
} > "$PARTIAL/host.txt"

# --- deploy copy (includes .env; do not print it) ---
if command -v rsync >/dev/null 2>&1; then
  rsync -a --exclude '.git/' "$NEXUS_DEPLOY"/ "$PARTIAL/deploy/"
else
  tar -C "$NEXUS_DEPLOY" --exclude '.git' -cf - . | tar -C "$PARTIAL/deploy" -xf -
fi
if [ -f "$PARTIAL/deploy/.env" ]; then
  chmod 600 "$PARTIAL/deploy/.env"
fi
[ -f "$PARTIAL/deploy/docker-compose.yml" ] || fail_keep_last_good

# --- MariaDB dump ---
MARIA_CID="$(nexus_cid mariadb || true)"
[ -n "${MARIA_CID:-}" ] || { echo "mariadb container not running" >&2; fail_keep_last_good; }
DB_NAME="$(docker exec "$MARIA_CID" printenv MARIADB_DATABASE 2>/dev/null || echo xiaozhi_esp32_server)"
DB_NAME="$(printf '%s' "$DB_NAME" | tr -d '\r')"
DUMP="$PARTIAL/mariadb/${DB_NAME}.sql"
if ! docker exec "$MARIA_CID" sh -c \
  'export MYSQL_PWD; MYSQL_PWD=$(cat /run/secrets/mariadb-root); exec mysqldump --single-transaction --quick --routines --events --triggers --hex-blob --add-drop-database --default-character-set=utf8mb4 --databases "$MARIADB_DATABASE"' \
  > "$DUMP"; then
  echo "mysqldump failed" >&2
  fail_keep_last_good
fi
chmod 600 "$DUMP"
printf '%s\n' "$DB_NAME" > "$PARTIAL/mariadb/database-name.txt"
wc -c < "$DUMP" | tr -d ' ' > "$PARTIAL/mariadb/dump-bytes.txt"

# --- volumes ---
HELPER="$(nexus_helper_image)" || { echo "no local helper image (alpine/redis/mariadb) — will not pull" >&2; fail_keep_last_good; }
echo "$HELPER" > "$PARTIAL/docker/helper-image.txt"
for key in $NEXUS_BACKUP_VOLUME_KEYS; do
  if [ "$key" = "xiaozhi-models" ] && [ "$SKIP_MODELS" = "1" ]; then
    echo "skip $key" >> "$PARTIAL/volumes/SKIPPED.txt"
    continue
  fi
  vol="$(nexus_vol "$key")"
  if ! docker volume inspect "$vol" >/dev/null 2>&1; then
    echo "volume missing: $vol" >&2
    case " $NEXUS_ESSENTIAL_VOLUME_KEYS " in
      *" $key "*) fail_keep_last_good ;;
      *) echo "skip missing optional $vol" >> "$PARTIAL/volumes/SKIPPED.txt"; continue ;;
    esac
  fi
  out="$PARTIAL/volumes/${vol}.tar.gz"
  if ! docker run --rm --network none --pull never \
    -v "$vol":/src:ro \
    -v "$PARTIAL/volumes":/dst \
    "$HELPER" tar -C /src -czf "/dst/${vol}.tar.gz" .; then
    echo "volume tar failed: $vol" >&2
    fail_keep_last_good
  fi
  chmod 600 "$out"
done

# --- chroma from running xiaozhi-server (not a named volume today) ---
XZ_CID="$(nexus_cid xiaozhi-server || true)"
if [ -n "${XZ_CID:-}" ]; then
  if docker exec "$XZ_CID" sh -c 'test -d /opt/xiaozhi-esp32-server/.local/share/careconnect/chroma'; then
    docker exec "$XZ_CID" tar -C /opt/xiaozhi-esp32-server/.local/share/careconnect -czf - chroma \
      > "$PARTIAL/chroma/chroma.tar.gz" || true
    if [ -s "$PARTIAL/chroma/chroma.tar.gz" ]; then
      chmod 600 "$PARTIAL/chroma/chroma.tar.gz"
    fi
  fi
fi

# --- systemd ---
copy_if() {
  src="$1"
  dest="$2"
  if [ -f "$src" ]; then
    mkdir -p "$(dirname "$dest")"
    install -m 644 "$src" "$dest"
  fi
}
copy_if /etc/systemd/system/nexus.service "$PARTIAL/systemd/nexus.service"
copy_if /etc/systemd/system/docker.service.d/wait-mnt-xiaozhi.conf \
  "$PARTIAL/systemd/docker.service.d/wait-mnt-xiaozhi.conf"
copy_if /etc/systemd/system/containerd.service.d/wait-mnt-xiaozhi.conf \
  "$PARTIAL/systemd/containerd.service.d/wait-mnt-xiaozhi.conf"
copy_if /etc/systemd/system/ollama-warm.service "$PARTIAL/systemd/ollama-warm.service"
copy_if /etc/systemd/system/mnt-xiaozhi.mount "$PARTIAL/systemd/mnt-xiaozhi.mount"

# --- docker inventory (no image layers) ---
nexus_compose ps -a > "$PARTIAL/docker/compose-ps.txt" 2>&1 || true
nexus_compose images > "$PARTIAL/docker/compose-images.txt" 2>&1 || true
: > "$PARTIAL/docker/inspect.txt"
for cid in $(nexus_compose ps -aq 2>/dev/null || true); do
  docker inspect -f '{{.Name}} image={{.Config.Image}} id={{.Image}}' "$cid" \
    >> "$PARTIAL/docker/inspect.txt" || true
done
: > "$PARTIAL/docker/digests.txt"
# Unique image IDs currently used by the project.
for cid in $(nexus_compose ps -aq 2>/dev/null || true); do
  docker inspect -f '{{.Image}}' "$cid"
done | sort -u | while read -r imgid; do
  [ -n "$imgid" ] || continue
  docker image inspect -f '{{join .RepoTags ","}} {{join .RepoDigests ","}} {{.Id}}' "$imgid" \
    >> "$PARTIAL/docker/digests.txt" || true
done

# --- ollama inventory ---
if curl -sf -m 4 http://127.0.0.1:11434/api/tags > "$PARTIAL/ollama/tags.json" 2>/dev/null; then
  :
else
  echo '{"error":"ollama not reachable"}' > "$PARTIAL/ollama/tags.json"
fi
if command -v ollama >/dev/null 2>&1; then
  ollama list > "$PARTIAL/ollama/list.txt" 2>/dev/null || true
  ollama ps > "$PARTIAL/ollama/ps.txt" 2>/dev/null || true
fi

# --- checksums then manifest ---
(
  cd "$PARTIAL"
  find . -type f ! -name SHA256SUMS ! -name MANIFEST.txt -print0 \
    | sort -z \
    | xargs -0 sha256sum > SHA256SUMS
)

{
  echo "Nexus production backup"
  echo "timestamp_utc=$STAMP"
  echo "git_sha=$SHA"
  echo "git_describe=$(nexus_git_describe)"
  echo "host=$(hostname) arch=$(uname -m)"
  echo "project=$NEXUS_PROJECT"
  echo "deploy=$NEXUS_DEPLOY"
  echo "repo=$NEXUS_REPO"
  echo "database=$DB_NAME dump=mariadb/${DB_NAME}.sql bytes=$(cat "$PARTIAL/mariadb/dump-bytes.txt")"
  echo "helper_image=$HELPER"
  echo "skip_models=$SKIP_MODELS"
  echo
  echo "== compose images =="
  cat "$PARTIAL/docker/compose-images.txt"
  echo
  echo "== image digests =="
  cat "$PARTIAL/docker/digests.txt"
  echo
  echo "== volumes included =="
  ls -1 "$PARTIAL/volumes"
  echo
  echo "== ollama =="
  if command -v python3 >/dev/null 2>&1; then
    python3 -c '
import json,sys
p=sys.argv[1]
try:
    d=json.load(open(p))
except Exception as e:
    print("unreadable:", e)
    sys.exit(0)
if "error" in d:
    print(d.get("error"))
else:
    for m in d.get("models") or []:
        print(m.get("name", "?"))
' "$PARTIAL/ollama/tags.json"
  fi
  echo
  echo "== files =="
  (cd "$PARTIAL" && find . -type f | sort)
  echo
  echo "== checksums =="
  cat "$PARTIAL/SHA256SUMS"
  echo
  echo "Secrets and .env are in this tree with mode 0600/0700 and are not listed by value."
} > "$PARTIAL/MANIFEST.txt"

# checksum MANIFEST separately so verify can still check everything else first,
# then include MANIFEST in a second pass.
(
  cd "$PARTIAL"
  sha256sum MANIFEST.txt >> SHA256SUMS
)

if ! verify_backup_dir "$PARTIAL"; then
  fail_keep_last_good
fi

mv "$PARTIAL" "$FINAL"
ln -sfn "$(basename "$FINAL")" "$NEXUS_BACKUP_ROOT/LAST_GOOD"
chmod 700 "$FINAL"
echo "nexus-backup: ok $FINAL"
echo "nexus-backup: LAST_GOOD -> $(basename "$FINAL")"
echo "git SHA: $SHA"
