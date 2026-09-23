#!/bin/sh
# Focused Chroma backup tests. Does not touch production.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
fail=0

echo "=== chroma backup tests ==="

python3 -m py_compile "$ROOT/chroma-archive.py"

# Reproduce production layout: nonempty /root/... and empty xiaozhiuser HOME.
fs="$(mktemp -d)"
root_chroma="$fs/root/.local/share/careconnect/chroma"
opt_chroma="$fs/opt/xiaozhi-esp32-server/.local/share/careconnect/chroma"
mkdir -p "$root_chroma" "$opt_chroma"
python3 -c "
import sqlite3, pathlib
p = pathlib.Path('$root_chroma') / 'chroma.sqlite3'
c = sqlite3.connect(p)
c.execute('create table embeddings (id integer primary key, vec blob)')
c.execute('insert into embeddings (vec) values (?)', (b'secret-vector-bytes',))
c.commit()
c.close()
"
# WAL leftovers that must not be required in the archive
: > "$root_chroma/chroma.sqlite3-wal"
: > "$root_chroma/chroma.sqlite3-shm"

export NEXUS_CHROMA_FS_PREFIX="$fs"
found="$(python3 "$ROOT/chroma-archive.py" discover)"
unset NEXUS_CHROMA_FS_PREFIX
case "$found" in
  */root/.local/share/careconnect/chroma) echo "ok  discover prefers /root chroma over /opt" ;;
  *) echo "FAIL discover got $found" >&2; fail=1 ;;
esac

arch="$(mktemp -d)/chroma.tar.gz"
python3 "$ROOT/chroma-archive.py" archive --path "$root_chroma" -o "$arch"
tar -tzf "$arch" | grep -q 'chroma/chroma.sqlite3' || { echo "FAIL tar missing chroma.sqlite3" >&2; fail=1; }
tar -tzf "$arch" | grep -q 'chroma.sqlite3-wal' && { echo "FAIL tar included live WAL" >&2; fail=1; }
# Contents must not leak to stdout of archive (file only).
if python3 "$ROOT/chroma-archive.py" archive --path "$root_chroma" -o "$arch" 2>/tmp/chroma-archive.err | grep -q secret-vector-bytes; then
  echo "FAIL archive stdout leaked sqlite payload" >&2
  fail=1
else
  echo "ok  archive stdout has no sqlite payload"
fi
if grep -q secret-vector-bytes /tmp/chroma-archive.err; then
  echo "FAIL archive stderr leaked sqlite payload" >&2
  fail=1
else
  echo "ok  archive stderr has no sqlite payload"
fi
echo "ok  chroma.tar.gz created from live sqlite"

# Synthetic backup tree: chroma present=yes must verify; missing archive must fail.
fake="$(mktemp -d)"
mkdir -p "$fake/mariadb" "$fake/volumes" "$fake/deploy" "$fake/chroma"
python3 -c 'import pathlib; pathlib.Path("'"$fake"'/mariadb/xiaozhi_esp32_server.sql").write_text("CREATE DATABASE `xiaozhi_esp32_server`;\nCREATE TABLE t (id int);\n" + ("-- pad\n"*40))'
echo 'name: nexus' > "$fake/deploy/docker-compose.yml"
echo 'PLACEHOLDER=1' > "$fake/deploy/.env"
chmod 600 "$fake/deploy/.env"
sec="$(mktemp -d)"
echo dummy > "$sec/mariadb-root"
for key in cc-secrets cc-voice xiaozhi-data redis-data caddy-data caddy-config; do
  tar -C "$sec" -czf "$fake/volumes/nexus_${key}.tar.gz" .
done
rm -rf "$sec"
cp "$arch" "$fake/chroma/chroma.tar.gz"
printf '%s\n' \
  "present=yes" \
  "source=/root/.local/share/careconnect/chroma" \
  "archive=chroma/chroma.tar.gz" \
  "bytes=$(wc -c < "$fake/chroma/chroma.tar.gz" | tr -d ' ')" \
  "method=sqlite3.Connection.backup" \
  > "$fake/chroma/status.txt"
{
  echo "Nexus production backup"
  echo "== chroma =="
  cat "$fake/chroma/status.txt"
  echo "chroma/chroma.tar.gz"
} > "$fake/MANIFEST.txt"
(
  cd "$fake"
  find . -type f ! -name SHA256SUMS ! -name MANIFEST.txt -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum MANIFEST.txt >> SHA256SUMS
)
if NEXUS_PROJECT=nexus sh "$ROOT/nexus-backup.sh" --verify "$fake"; then
  echo "ok  verify succeeds when chroma archive present"
else
  echo "FAIL verify with chroma archive" >&2
  fail=1
fi

rm -f "$fake/chroma/chroma.tar.gz"
(
  cd "$fake"
  find . -type f ! -name SHA256SUMS ! -name MANIFEST.txt -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum MANIFEST.txt >> SHA256SUMS
)
if NEXUS_PROJECT=nexus sh "$ROOT/nexus-backup.sh" --verify "$fake" >/dev/null 2>&1; then
  echo "FAIL verify should reject missing chroma archive" >&2
  fail=1
else
  echo "ok  verify fails when chroma existed but archive is missing"
fi

check_root_probe() {
  grep -q '/root/.local/share/careconnect/chroma' "$ROOT/nexus-backup.sh"
}
if check_root_probe; then
  echo "ok  backup probes /root chroma path"
else
  echo "FAIL backup does not mention /root chroma path" >&2
  fail=1
fi
if grep -q 'test -d /opt/xiaozhi-esp32-server/.local/share/careconnect/chroma' "$ROOT/nexus-backup.sh"; then
  echo "FAIL backup still only tests xiaozhiuser HOME" >&2
  fail=1
else
  echo "ok  backup no longer only tests /opt/.../chroma"
fi

rm -rf "$fs" "$fake"
if [ "$fail" -ne 0 ]; then
  echo "CHROMA TESTS FAILED" >&2
  exit 1
fi
echo "CHROMA TESTS PASSED"
