#!/bin/sh
# Install systemd drop-ins so containerd + docker wait for /mnt/xiaozhi.
#
# The Orin keeps Docker Root Dir at /mnt/xiaozhi/docker-data on /dev/sda1.
# If those daemons start before the mount is active they open an empty
# directory on the root filesystem and all Nexus images appear "missing"
# until `systemctl restart containerd docker`.
#
# This script:
#   * inspects how /mnt/xiaozhi is mounted (fstab vs systemd mount unit)
#   * installs drop-ins (RequiresMountsFor=/mnt/xiaozhi, After=mnt-xiaozhi.mount)
#   * runs daemon-reload
#   * does NOT restart docker/containerd, does NOT prune, does NOT change
#     data-root, does NOT reboot
#
# Usage (on the Orin, from this repo):
#   sudo deploy/jetson/install-docker-mount-order.sh --status
#   sudo deploy/jetson/install-docker-mount-order.sh
#   sudo deploy/jetson/install-docker-mount-order.sh --restart   # optional, now
set -eu

MOUNTPOINT="/mnt/xiaozhi"
UNIT_NAME="mnt-xiaozhi.mount"
SRC_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
DROPIN_SRC="${SRC_DIR}/systemd"

DO_INSTALL=1
DO_RESTART=0
DO_STATUS=0

for arg in "$@"; do
  case "$arg" in
    --status) DO_STATUS=1; DO_INSTALL=0 ;;
    --restart) DO_RESTART=1 ;;
    --help|-h)
      sed -n '2,24p' "$0"
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing command: $1" >&2; exit 1; }
}

need_cmd systemctl
need_cmd awk

echo "=== /mnt/xiaozhi mount source ==="
if command -v findmnt >/dev/null 2>&1 && findmnt -n "$MOUNTPOINT" >/dev/null 2>&1; then
  findmnt -n -o SOURCE,TARGET,FSTYPE,OPTIONS "$MOUNTPOINT" || true
else
  echo "(not currently a mountpoint — expected before first boot-with-disk, or disk missing)"
fi

echo
echo "=== /etc/fstab ==="
if [ -r /etc/fstab ] && grep -E -v '^[[:space:]]*#' /etc/fstab | grep -q -F "$MOUNTPOINT"; then
  grep -E -v '^[[:space:]]*#' /etc/fstab | grep -F "$MOUNTPOINT"
  if grep -E -v '^[[:space:]]*#' /etc/fstab | grep -F "$MOUNTPOINT" | grep -q nofail; then
    echo "NOTE: fstab has nofail. local-fs.target will NOT wait for this disk;"
    echo "      the drop-ins (RequiresMountsFor) are what make docker wait."
  fi
else
  echo "(no /etc/fstab line for ${MOUNTPOINT})"
fi

echo
echo "=== systemd ${UNIT_NAME} ==="
if systemctl cat "$UNIT_NAME" >/dev/null 2>&1; then
  systemctl show -p FragmentPath,Where,What,LoadState,ActiveState,Result "$UNIT_NAME" || true
else
  echo "(no ${UNIT_NAME} unit loaded)"
  echo "If /mnt/xiaozhi is not in fstab, add a UUID fstab line so systemd"
  echo "generates ${UNIT_NAME}. Do not hard-code /dev/sda1 (name can change)."
fi

echo
echo "=== docker data-root (must stay under ${MOUNTPOINT}) ==="
if command -v docker >/dev/null 2>&1; then
  docker info 2>/dev/null | awk -F': ' '/Docker Root Dir/{print $2}' || echo "(docker info failed)"
else
  echo "(docker not in PATH)"
fi

install_dropin() {
  unit="$1"
  src="${DROPIN_SRC}/${unit}.d/wait-mnt-xiaozhi.conf"
  dest="/etc/systemd/system/${unit}.d/wait-mnt-xiaozhi.conf"
  if [ ! -f "$src" ]; then
    echo "missing drop-in source: $src" >&2
    exit 1
  fi
  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    echo "SKIP ${unit}: unit not installed on this host"
    return 0
  fi
  mkdir -p "/etc/systemd/system/${unit}.d"
  install -m 644 "$src" "$dest"
  echo "installed $dest"
}

if [ "$DO_INSTALL" = "1" ]; then
  if [ "$(id -u)" != "0" ]; then
    echo "install must run as root" >&2
    exit 1
  fi
  echo
  echo "=== installing drop-ins ==="
  install_dropin containerd.service
  install_dropin docker.service
  systemctl daemon-reload
  echo "daemon-reload done (docker/containerd not restarted)"
fi

echo
echo "=== effective dependencies ==="
for unit in containerd.service docker.service; do
  if systemctl cat "$unit" >/dev/null 2>&1; then
    echo "--- $unit ---"
    systemctl show -p After,Requires,RequiresMountsFor "$unit" || true
  fi
done

if [ "$DO_RESTART" = "1" ]; then
  if [ "$(id -u)" != "0" ]; then
    echo "--restart must run as root" >&2
    exit 1
  fi
  echo
  echo "=== restarting containerd then docker (optional) ==="
  systemctl restart containerd
  systemctl restart docker
  echo "restarted"
fi

echo
echo "Done. Next reboot should order: ${MOUNTPOINT} → containerd → docker → Nexus (unless-stopped)."
echo "Do not prune images/volumes. Do not change Docker Root Dir."
