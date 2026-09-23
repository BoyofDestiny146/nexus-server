#!/bin/sh
# sudo nexus-status — concise production health. Never prints secrets or .env.
set -eu

LIB=""
if [ -f /usr/local/lib/nexus/nexus-lib.sh ]; then
  LIB=/usr/local/lib/nexus/nexus-lib.sh
elif [ -f "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-lib.sh" ]; then
  LIB="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/nexus-lib.sh"
else
  echo "nexus-status: nexus-lib.sh not found" >&2
  exit 1
fi
# shellcheck disable=SC1090
. "$LIB"

ok() { printf '  %-14s ok    %s\n' "$1" "$2"; }
bad() { printf '  %-14s FAIL  %s\n' "$1" "$2"; }
note() { printf '  %-14s %s    %s\n' "$1" "$2" "$3"; }

probe_http() {
  _url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -sf -m 4 "$_url" >/dev/null 2>&1
  elif command -v wget >/dev/null 2>&1; then
    wget -q -T 4 -O /dev/null "$_url" >/dev/null 2>&1
  else
    return 2
  fi
}

echo "=== Nexus status ==="
echo "time:    $(date -Iseconds 2>/dev/null || date)"
echo "host:    $(hostname) $(uname -m)"
echo

echo "-- mount --"
if command -v findmnt >/dev/null 2>&1 && findmnt -n "$NEXUS_MOUNT" >/dev/null 2>&1; then
  findmnt -n -o SOURCE,TARGET,FSTYPE,OPTIONS "$NEXUS_MOUNT" | awk '{print "  "$0}'
else
  bad mount "not mounted ($NEXUS_MOUNT)"
fi
if command -v df >/dev/null 2>&1; then
  df -h "$NEXUS_MOUNT" 2>/dev/null | awk 'NR==2 {printf "  disk          %s used, %s free of %s (%s)\n",$3,$4,$2,$5}'
fi
echo

echo "-- systemd --"
for u in containerd.service docker.service nexus.service ollama.service ollama-warm.service; do
  if systemctl cat "$u" >/dev/null 2>&1; then
    _act="$(systemctl is-active "$u" 2>/dev/null || true)"
    _en="$(systemctl is-enabled "$u" 2>/dev/null || true)"
    printf '  %-22s %-12s %s\n' "$u" "$_act" "$_en"
  else
    printf '  %-22s %-12s %s\n' "$u" "absent" "-"
  fi
done
echo

echo "-- compose $NEXUS_PROJECT ps -a --"
if docker info >/dev/null 2>&1; then
  if [ -f "$NEXUS_COMPOSE_FILE" ]; then
    nexus_compose ps -a || true
  else
    bad compose "$NEXUS_COMPOSE_FILE missing (not copying git compose)"
  fi
else
  bad docker "inactive / not usable"
fi
echo

echo "-- health --"
if docker info >/dev/null 2>&1 && [ -f "$NEXUS_COMPOSE_FILE" ]; then
  _api="$(nexus_cid api || true)"
  if [ -n "${_api:-}" ]; then
    if docker exec "$_api" wget -qO- -T 4 http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
      ok api "healthz"
    else
      bad api "healthz failed"
    fi
  else
    bad api "container missing"
  fi

  if probe_http http://127.0.0.1:8003/xiaozhi/ota/; then
    ok xiaozhi "OTA :8003"
  else
    _xz="$(nexus_cid xiaozhi-server || true)"
    if [ -n "${_xz:-}" ] && docker exec "$_xz" python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8003/xiaozhi/ota/', timeout=4).status==200 else 1)" >/dev/null 2>&1; then
      ok xiaozhi "OTA (in-container)"
    else
      bad xiaozhi "OTA not healthy"
    fi
  fi

  _piper="$(nexus_cid piper-tts || true)"
  if [ -n "${_piper:-}" ] && docker exec "$_piper" wget -qO- -T 4 http://127.0.0.1:5500/health >/dev/null 2>&1; then
    ok piper "/health"
  else
    bad piper "not healthy"
  fi

  _caddy="$(nexus_cid caddy || true)"
  if probe_http http://127.0.0.1:2019/config/; then
    ok caddy "admin :2019"
  elif [ -n "${_caddy:-}" ] && docker exec "$_caddy" wget -qO- -T 4 http://127.0.0.1:2019/config/ >/dev/null 2>&1; then
    ok caddy "admin (in-container)"
  else
    bad caddy "admin API not healthy"
  fi

  _web_st="$(nexus_compose ps -a --format '{{.Service}} {{.Status}}' 2>/dev/null | awk '$1=="web"{print substr($0, index($0,$2))}')"
  case "$_web_st" in
    *Exited\ \(0\)*|*exited\ \(0\)*) ok web "Exited (0) one-shot (normal)" ;;
    *Up*|*running*) note web "WARN" "still running (copy should exit)" ;;
    "") bad web "container missing" ;;
    *) note web "INFO" "${_web_st:-unknown}" ;;
  esac
else
  bad health "skipped (docker/compose unavailable)"
fi
echo

echo "-- ollama (native, not a compose service) --"
if probe_http http://127.0.0.1:11434/api/version; then
  ok ollama "http://127.0.0.1:11434"
  if command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
    echo "  models:"
    curl -sf -m 4 http://127.0.0.1:11434/api/tags 2>/dev/null | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(0)
for m in d.get("models") or []:
    print("   -", m.get("name", "?"))
' || true
  fi
  if command -v ollama >/dev/null 2>&1; then
    echo "  loaded:"
    ollama ps 2>/dev/null | awk 'NR==1{next} NF{print "   - "$0}' || echo "   (none)"
  fi
else
  note ollama "DOWN" "not answering on :11434"
fi
echo

echo "-- git --"
echo "  repo     $NEXUS_REPO"
echo "  sha      $(nexus_git_sha)"
echo "  describe $(nexus_git_describe)"
echo
echo "(secrets and .env are not printed)"
