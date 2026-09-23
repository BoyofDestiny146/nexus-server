#!/bin/sh
# sudo nexus-restore — deliberately safe. Will not restore from a bare command.
#
# Usage:
#   sudo nexus-restore /path/to/backup
#       Print the recorded Git SHA and restore plan. Exit 2. No writes.
#   sudo nexus-restore /path/to/backup --confirm RESTORE
#       Stop Nexus, restore deploy files, secrets, volumes, MariaDB dump,
#       and systemd units/drop-ins. Does NOT overwrite /mnt/xiaozhi/nexus-server.
#       Does NOT start the full stack (run nexus-start afterwards).
set -eu

LIB=""
if [ -f /usr/local/lib/nexus/nexus-lib.sh ]; then
  LIB=/usr/local/lib/nexus/nexus-lib.sh
elif [ -f "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-lib.sh" ]; then
  LIB="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-lib.sh"
else
  echo "nexus-restore: nexus-lib.sh not found" >&2
  exit 1
fi
# shellcheck disable=SC1090
. "$LIB"

BACKUP=""
CONFIRM=""
APPLY_CHROMA=0

while [ $# -gt 0 ]; do
  case "$1" in
    --confirm)
      CONFIRM="${2:-}"
      shift 2
      ;;
    --apply-chroma)
      APPLY_CHROMA=1
      shift
      ;;
    --help|-h)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    --*)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
    *)
      if [ -z "$BACKUP" ]; then
        BACKUP="$1"
        shift
      else
        echo "unexpected argument: $1" >&2
        exit 2
      fi
      ;;
  esac
done

if [ -z "$BACKUP" ]; then
  echo "Usage: sudo nexus-restore /path/to/backup [--confirm RESTORE]" >&2
  echo "       sudo nexus-restore /path/to/backup --apply-chroma --confirm RESTORE" >&2
  echo "Refusing to restore without an explicit backup path." >&2
  exit 2
fi

BACKUP="$(CDPATH= cd -- "$BACKUP" && pwd)" || nexus_die "backup path not found"

print_plan() {
  echo "=== Nexus restore plan (no changes yet) ==="
  echo "backup:   $BACKUP"
  echo "deploy:   $NEXUS_DEPLOY   (WILL be overwritten if confirmed)"
  echo "repo:     $NEXUS_REPO     (will NOT be overwritten)"
  if [ -f "$BACKUP/git-sha.txt" ]; then
    echo "git SHA:  $(cat "$BACKUP/git-sha.txt")"
    echo
    echo "Check out that commit in $NEXUS_REPO before rebuilding images:"
    echo "  cd $NEXUS_REPO && git fetch && git checkout $(cat "$BACKUP/git-sha.txt")"
  else
    echo "git SHA:  unknown (git-sha.txt missing)"
  fi
  echo
  echo "Would restore:"
  echo "  - $NEXUS_DEPLOY from backup/deploy (compose, .env, scripts)"
  echo "  - named volumes from backup/volumes/*.tar.gz (secrets, voice, data, redis, caddy, photos, models)"
  echo "  - MariaDB logical dump into the running mariadb container"
  echo "  - systemd units/drop-ins from backup/systemd into /etc/systemd/system"
  echo
  echo "Would NOT:"
  echo "  - modify $NEXUS_REPO"
  echo "  - docker pull / prune / volume rm"
  echo "  - start the full Nexus stack (use sudo nexus-start after)"
  echo "  - restore $(nexus_vol web-static) (web one-shot recopies it)"
  echo "  - restore $(nexus_vol mariadb-data) (dump is source of truth)"
  echo
  echo "To proceed (DESTRUCTIVE):"
  echo "  sudo nexus-restore $BACKUP --confirm RESTORE"
}

if [ "$CONFIRM" != "RESTORE" ]; then
  print_plan
  echo "Refusing: pass --confirm RESTORE to apply." >&2
  exit 2
fi

nexus_need_root

if [ "$APPLY_CHROMA" = "1" ]; then
  [ -s "$BACKUP/chroma/chroma.tar.gz" ] || nexus_die "no chroma archive in backup"
  xz="$(nexus_cid xiaozhi-server || true)"
  [ -n "${xz:-}" ] || nexus_die "xiaozhi-server is not running; start Nexus first"
  echo "nexus-restore: extracting chroma into xiaozhi-server"
  docker exec "$xz" mkdir -p /opt/xiaozhi-esp32-server/.local/share/careconnect
  docker exec -i "$xz" tar -C /opt/xiaozhi-esp32-server/.local/share/careconnect -xzf - \
    < "$BACKUP/chroma/chroma.tar.gz"
  echo "nexus-restore: chroma applied"
  exit 0
fi
BACKUP_TOOL=""
if [ -x /usr/local/sbin/nexus-backup ]; then
  BACKUP_TOOL=/usr/local/sbin/nexus-backup
elif [ -x "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-backup.sh" ]; then
  BACKUP_TOOL="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-backup.sh"
else
  nexus_die "nexus-backup not found (needed to verify archive)"
fi
"$BACKUP_TOOL" --verify "$BACKUP"

HELPER="$(nexus_helper_image)" || nexus_die "no local helper image; will not pull"

echo "nexus-restore: stopping Nexus"
if systemctl cat nexus.service >/dev/null 2>&1; then
  systemctl stop nexus.service || true
else
  nexus_compose stop --timeout 120 || true
fi

echo "nexus-restore: restoring $NEXUS_DEPLOY (not the git repo)"
mkdir -p "$NEXUS_DEPLOY"
if command -v rsync >/dev/null 2>&1; then
  rsync -a "$BACKUP/deploy"/ "$NEXUS_DEPLOY"/
else
  tar -C "$BACKUP/deploy" -cf - . | tar -C "$NEXUS_DEPLOY" -xf -
fi
if [ -f "$NEXUS_DEPLOY/.env" ]; then
  chmod 600 "$NEXUS_DEPLOY/.env"
fi

echo "nexus-restore: restoring named volumes"
for archive in "$BACKUP/volumes"/*.tar.gz; do
  [ -f "$archive" ] || continue
  base="$(basename "$archive" .tar.gz)"
  case "$base" in
    *_web-static|*_mariadb-data)
      echo "  skip $base"
      continue
      ;;
  esac
  docker volume create "$base" >/dev/null
  docker run --rm --network none --pull never \
    -v "$base":/dst \
    -v "$archive":/in.tar.gz:ro \
    "$HELPER" tar -C /dst -xzf /in.tar.gz
  echo "  restored $base"
done

echo "nexus-restore: importing MariaDB dump"
nexus_compose up -d --pull never --no-build mariadb
i=0
healthy=0
while [ "$i" -lt 60 ]; do
  cid="$(nexus_cid mariadb || true)"
  if [ -n "${cid:-}" ] && docker exec "$cid" healthcheck.sh --su-mysql --connect --innodb_initialized >/dev/null 2>&1; then
    healthy=1
    break
  fi
  i=$((i + 1))
  sleep 2
done
[ "$healthy" = "1" ] || nexus_die "mariadb did not become healthy"
cid="$(nexus_cid mariadb)"
dump="$(find "$BACKUP/mariadb" -type f -name '*.sql' | head -n 1)"
[ -n "$dump" ] || nexus_die "dump missing after verify?"
# stdin is the SQL; password stays inside the container.
docker exec -i "$cid" sh -c \
  'export MYSQL_PWD; MYSQL_PWD=$(cat /run/secrets/mariadb-root); exec mysql --default-character-set=utf8mb4' \
  < "$dump"
echo "nexus-restore: database imported"
nexus_compose stop --timeout 120 mariadb

echo "nexus-restore: restoring systemd units (daemon-reload, not starting Nexus)"
if [ -f "$BACKUP/systemd/nexus.service" ]; then
  install -m 644 "$BACKUP/systemd/nexus.service" /etc/systemd/system/nexus.service
fi
if [ -f "$BACKUP/systemd/docker.service.d/wait-mnt-xiaozhi.conf" ]; then
  mkdir -p /etc/systemd/system/docker.service.d
  install -m 644 "$BACKUP/systemd/docker.service.d/wait-mnt-xiaozhi.conf" \
    /etc/systemd/system/docker.service.d/wait-mnt-xiaozhi.conf
fi
if [ -f "$BACKUP/systemd/containerd.service.d/wait-mnt-xiaozhi.conf" ]; then
  mkdir -p /etc/systemd/system/containerd.service.d
  install -m 644 "$BACKUP/systemd/containerd.service.d/wait-mnt-xiaozhi.conf" \
    /etc/systemd/system/containerd.service.d/wait-mnt-xiaozhi.conf
fi
systemctl daemon-reload

echo
echo "nexus-restore: complete"
echo "Recorded Git SHA: $(cat "$BACKUP/git-sha.txt" 2>/dev/null || echo unknown)"
echo "Check out that SHA in $NEXUS_REPO if images must match this backup."
echo "Then: sudo systemctl enable nexus && sudo nexus-start"
if [ -s "$BACKUP/chroma/chroma.tar.gz" ]; then
  echo "Chroma archive present. After XiaoZhi is up:"
  echo "  sudo nexus-restore $BACKUP --apply-chroma --confirm RESTORE"
fi
