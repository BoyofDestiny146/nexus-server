#!/bin/sh
# Regression tests for nexus-stop / nexus-start. No production Docker.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
fail=0

echo "=== start/stop regression ==="

MOCK="$(mktemp -d)"
BIN="$MOCK/bin"
mkdir -p "$BIN" "$MOCK/health"

write_ps() {
  : > "$MOCK/ps"
  if [ -f "$MOCK/running" ]; then
    while IFS= read -r svc; do
      [ -n "$svc" ] || continue
      echo "$svc running Up (healthy)" >> "$MOCK/ps"
    done < "$MOCK/running"
  fi
  echo "web exited Exited (0) 2 hours ago" >> "$MOCK/ps"
}

reset_healthy_stack() {
  printf '%s\n' api caddy mariadb piper-tts redis xiaozhi-mqtt-gateway xiaozhi-server > "$MOCK/running"
  write_ps
  for svc in api caddy mariadb piper-tts redis xiaozhi-mqtt-gateway xiaozhi-server; do
    echo healthy > "$MOCK/health/$svc"
  done
  : > "$MOCK/log"
  echo 1 > "$MOCK/stop_clears"
  rm -f "$MOCK/unit_active"
}

cat > "$BIN/docker" <<'EOF'
#!/bin/sh
MOCK_DIR="${MOCK_DIR:-}"
log() { printf '%s\n' "$*" >> "$MOCK_DIR/log"; }

if [ "$1" = "info" ]; then
  exit 0
fi

if [ "$1" = "inspect" ]; then
  fmt="" cid=""
  shift
  while [ $# -gt 0 ]; do
    case "$1" in
      -f) fmt=$2; shift 2 ;;
      *) cid=$1; shift ;;
    esac
  done
  svc=${cid#cid-}
  case "$fmt" in
    *Running*)
      if grep -qx "$svc" "$MOCK_DIR/running" 2>/dev/null; then echo true; else echo false; fi
      ;;
    *Health*)
      if [ -f "$MOCK_DIR/health/$svc" ]; then cat "$MOCK_DIR/health/$svc"; fi
      ;;
  esac
  exit 0
fi

if [ "$1" = "exec" ]; then
  exit 1
fi

if [ "$1" != "compose" ]; then
  exit 0
fi
shift
cmd=""
quiet=0
timeout=""
extra=""
while [ $# -gt 0 ]; do
  case "$1" in
    -p) shift 2 ;;
    --project-directory) shift 2 ;;
    -a) shift ;;
    -q) quiet=1; shift ;;
    --format) shift 2 ;;
    --timeout) timeout=$2; shift 2 ;;
    --timeout=*) timeout=${1#--timeout=}; shift ;;
    stop|ps|up|images|down) cmd=$1; shift ;;
    *) extra=$1; shift ;;
  esac
done

if [ "$cmd" = "down" ]; then
  log "BUG compose down"
  exit 1
fi

if [ "$cmd" = "stop" ]; then
  log "compose-stop timeout=${timeout:-}"
  if [ "$(cat "$MOCK_DIR/stop_clears" 2>/dev/null || echo 0)" = "1" ]; then
    : > "$MOCK_DIR/running"
    echo "web exited Exited (0) 2 hours ago" > "$MOCK_DIR/ps"
  fi
  exit 0
fi

if [ "$cmd" = "ps" ]; then
  if [ "$quiet" = "1" ]; then
    svc=$extra
    if [ "$svc" = "web" ]; then
      echo "cid-web"
      exit 0
    fi
    if grep -qx "$svc" "$MOCK_DIR/running" 2>/dev/null; then
      echo "cid-$svc"
    fi
    exit 0
  fi
  cat "$MOCK_DIR/ps"
  exit 0
fi
exit 0
EOF
chmod +x "$BIN/docker"

cat > "$BIN/systemctl" <<'EOF'
#!/bin/sh
MOCK_DIR="${MOCK_DIR:-}"
log() { printf '%s\n' "$*" >> "$MOCK_DIR/log"; }
case "$1" in
  cat)
    [ -f "$MOCK_DIR/unit_installed" ] || exit 1
    exit 0
    ;;
  is-active)
    if [ -f "$MOCK_DIR/unit_active" ]; then exit 0; fi
    echo inactive
    exit 3
    ;;
  stop)
    log systemctl-stop
    rm -f "$MOCK_DIR/unit_active"
    if [ "$(cat "$MOCK_DIR/stop_clears" 2>/dev/null || echo 0)" = "1" ]; then
      : > "$MOCK_DIR/running"
      echo "web exited Exited (0) 2 hours ago" > "$MOCK_DIR/ps"
    fi
    exit 0
    ;;
  start)
    log systemctl-start
    touch "$MOCK_DIR/unit_active"
    exit 0
    ;;
  restart)
    log systemctl-restart
    touch "$MOCK_DIR/unit_active"
    exit 0
    ;;
  is-enabled)
    echo enabled
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
EOF
chmod +x "$BIN/systemctl"

export MOCK_DIR="$MOCK"
export PATH="$BIN:/usr/bin:/bin"
export NEXUS_ASSUME_ROOT=1
export NEXUS_HEALTH_TRIES=2
export NEXUS_HEALTH_SLEEP=0
export NEXUS_STATUS_BIN=/bin/true
export NEXUS_PROJECT=nexus
export NEXUS_DEPLOY="$MOCK/deploy"
mkdir -p "$NEXUS_DEPLOY"
echo 'name: nexus' > "$NEXUS_DEPLOY/docker-compose.yml"
touch "$MOCK/unit_installed"

# 1) inactive unit + compose running → compose stop, success
reset_healthy_stack
if sh "$ROOT/nexus-stop.sh" > "$MOCK/out" 2>&1; then
  if grep -q compose-stop "$MOCK/log" && grep -q 'containers stopped' "$MOCK/out"; then
    if [ -s "$MOCK/running" ]; then
      echo "FAIL inactive-stop left containers running" >&2
      fail=1
    else
      echo "ok  inactive unit + running compose → compose stop, containers down"
    fi
  else
    echo "FAIL inactive-stop did not compose stop" >&2
    cat "$MOCK/out" >&2
    fail=1
  fi
else
  echo "FAIL inactive-stop exit $?" >&2
  cat "$MOCK/out" >&2
  fail=1
fi
if grep -q 'BUG compose down' "$MOCK/log"; then
  echo "FAIL compose down was invoked" >&2
  fail=1
fi
if grep -q systemctl-stop "$MOCK/log"; then
  echo "FAIL inactive path should not systemctl stop" >&2
  fail=1
fi

# 2) active unit → systemctl stop
reset_healthy_stack
touch "$MOCK/unit_active"
if sh "$ROOT/nexus-stop.sh" > "$MOCK/out" 2>&1; then
  if grep -q systemctl-stop "$MOCK/log" && grep -q 'containers stopped' "$MOCK/out"; then
    echo "ok  active unit → systemctl stop"
  else
    echo "FAIL active-stop log: $(cat "$MOCK/log")" >&2
    fail=1
  fi
else
  echo "FAIL active-stop nonzero" >&2
  cat "$MOCK/out" >&2
  fail=1
fi

# 3) containers remain running → failure, no success line
reset_healthy_stack
echo 0 > "$MOCK/stop_clears"
touch "$MOCK/unit_active"
if sh "$ROOT/nexus-stop.sh" > "$MOCK/out" 2>&1; then
  echo "FAIL stop returned 0 while containers still running" >&2
  fail=1
else
  if grep -q 'containers stopped' "$MOCK/out"; then
    echo "FAIL printed success while containers running" >&2
    fail=1
  elif grep -q 'FAILED' "$MOCK/out"; then
    echo "ok  remaining containers → stop fails"
  else
    echo "FAIL expected FAILED message" >&2
    cat "$MOCK/out" >&2
    fail=1
  fi
fi

# 4) healthy stack → nexus-start succeeds (inspect healthy, web Exited 0)
reset_healthy_stack
touch "$MOCK/unit_installed"
if sh "$ROOT/nexus-start.sh" > "$MOCK/out" 2>&1; then
  if grep -q systemctl-start "$MOCK/log"; then
    echo "ok  healthy API/XiaoZhi/Piper/Caddy + web Exited(0) → start succeeds"
  else
    echo "FAIL start did not systemctl start" >&2
    fail=1
  fi
else
  echo "FAIL start nonzero on healthy stack" >&2
  cat "$MOCK/out" >&2
  fail=1
fi

# 5) genuine failed health → start fails
reset_healthy_stack
echo unhealthy > "$MOCK/health/api"
if sh "$ROOT/nexus-start.sh" > "$MOCK/out" 2>&1; then
  echo "FAIL start succeeded with unhealthy api" >&2
  fail=1
else
  if grep -q 'timed out waiting for health' "$MOCK/out"; then
    echo "ok  genuine failed health → start fails"
  else
    echo "FAIL start fail message missing" >&2
    cat "$MOCK/out" >&2
    fail=1
  fi
fi

# Host curl-only would miss in-container health; inspect healthy must be enough
# even if exec/curl fail (docker exec stub exits 1).
reset_healthy_stack
if sh "$ROOT/nexus-start.sh" > "$MOCK/out" 2>&1; then
  echo "ok  inspect healthy succeeds without host curl/exec"
else
  echo "FAIL inspect-healthy path still timed out" >&2
  cat "$MOCK/out" >&2
  fail=1
fi

rm -rf "$MOCK"
if [ "$fail" -ne 0 ]; then
  echo "START/STOP TESTS FAILED" >&2
  exit 1
fi
echo "START/STOP TESTS PASSED"
