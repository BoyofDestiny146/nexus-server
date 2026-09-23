#!/bin/sh
# Install Nexus production ops (systemd unit + helpers). Does not start
# Nexus, does not reboot, does not copy git compose over production.
#
#   sudo deploy/jetson/install-nexus-ops.sh
#   sudo deploy/jetson/install-nexus-ops.sh --status
#   sudo deploy/jetson/install-nexus-ops.sh --no-enable
set -eu

SRC="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DO_INSTALL=1
DO_ENABLE=1
DO_STATUS=0

for arg in "$@"; do
  case "$arg" in
    --status) DO_STATUS=1; DO_INSTALL=0 ;;
    --no-enable) DO_ENABLE=0 ;;
    --help|-h)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

echo "=== source ==="
echo "  $SRC"

echo
echo "=== production paths (not modified by --status) ==="
echo "  repo     /mnt/xiaozhi/nexus-server"
echo "  deploy   /mnt/xiaozhi/nexus-deploy"
echo "  project  nexus"
echo "  compose  /mnt/xiaozhi/nexus-deploy/docker-compose.yml"
echo
echo "This installer will NOT:"
echo "  copy $SRC/../docker-compose.yml over production compose"
echo "  change Caddy ports, .env, Watcher firmware, Qwen, Piper, or images"
echo "  start/stop/reboot Nexus"
echo "  prune Docker"

status_one() {
  path="$1"
  if [ -e "$path" ]; then
    echo "  present  $path"
  else
    echo "  missing  $path"
  fi
}

echo
echo "=== installed units / helpers ==="
status_one /etc/systemd/system/nexus.service
status_one /usr/local/lib/nexus/wait-docker.sh
status_one /usr/local/lib/nexus/nexus-lib.sh
status_one /usr/local/sbin/nexus-status
status_one /usr/local/sbin/nexus-start
status_one /usr/local/sbin/nexus-stop
status_one /usr/local/sbin/nexus-backup
status_one /usr/local/sbin/nexus-restore
status_one /etc/systemd/system/docker.service.d/wait-mnt-xiaozhi.conf
status_one /etc/systemd/system/containerd.service.d/wait-mnt-xiaozhi.conf
if command -v systemctl >/dev/null 2>&1 && systemctl cat nexus.service >/dev/null 2>&1; then
  echo "  nexus.service  $(systemctl is-enabled nexus.service 2>/dev/null || echo unknown) $(systemctl is-active nexus.service 2>/dev/null || true)"
fi

if [ "$DO_INSTALL" != "1" ]; then
  exit 0
fi

if [ "$(id -u)" != "0" ]; then
  echo "install must run as root" >&2
  exit 1
fi

for f in nexus.service wait-docker.sh nexus-lib.sh \
  nexus-status.sh nexus-start.sh nexus-stop.sh \
  nexus-backup.sh nexus-restore.sh; do
  [ -f "$SRC/$f" ] || { echo "missing $SRC/$f" >&2; exit 1; }
done

echo
echo "=== installing ==="
install -d -m 755 /usr/local/lib/nexus
install -d -m 755 /usr/local/sbin
install -m 644 "$SRC/nexus.service" /etc/systemd/system/nexus.service
install -m 755 "$SRC/wait-docker.sh" /usr/local/lib/nexus/wait-docker.sh
install -m 644 "$SRC/nexus-lib.sh" /usr/local/lib/nexus/nexus-lib.sh
install -m 755 "$SRC/nexus-status.sh" /usr/local/sbin/nexus-status
install -m 755 "$SRC/nexus-start.sh" /usr/local/sbin/nexus-start
install -m 755 "$SRC/nexus-stop.sh" /usr/local/sbin/nexus-stop
install -m 755 "$SRC/nexus-backup.sh" /usr/local/sbin/nexus-backup
install -m 755 "$SRC/nexus-restore.sh" /usr/local/sbin/nexus-restore

# Drop-ins are installed by install-docker-mount-order.sh. Do not overwrite
# a production drop-in that already exists; copy only if missing.
if [ ! -f /etc/systemd/system/docker.service.d/wait-mnt-xiaozhi.conf ]; then
  echo "NOTE: docker mount-order drop-in missing. Install with:"
  echo "  sudo $SRC/install-docker-mount-order.sh"
fi

systemctl daemon-reload
if [ "$DO_ENABLE" = "1" ]; then
  systemctl enable nexus.service
  echo "enabled nexus.service (not started)"
else
  echo "skipped enable (--no-enable)"
fi

echo
echo "Done. Next: sudo nexus-backup && sudo nexus-status"
echo "Do not reboot until a known-good backup exists."
