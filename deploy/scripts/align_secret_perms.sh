#!/bin/sh
# align_secret_perms.sh — create-if-missing integration-secret-key and give it
# the same UID/GID/mode as an already-readable API secret.
#
# Why this cannot be alpine root:root 0600: cc-secrets is mounted :ro at
# /run/secrets, and careconnect-api then drops root via `gosu appuser`. The
# entrypoint cannot chmod a read-only mount. api-client-key and
# api-internal-token already work for that runtime user; copy their ownership
# instead of guessing the image's appuser.
#
# Usage (volume mounted at /s, or pass the secrets directory):
#   sh align_secret_perms.sh
#   sh align_secret_perms.sh /path/to/secrets
#
# Never prints the Fernet key. Never overwrites a non-empty key. Re-applies
# ownership/mode on keep so an existing key stays readable across deploys.
# Never world-writable (chmod o-w after cloning).
set -eu

DIR="${1:-/s}"
DEST="${DIR}/integration-secret-key"
NAME="integration-secret-key"

if [ ! -d "$DIR" ]; then
  echo "align_secret_perms: secrets dir not found: $DIR" >&2
  exit 1
fi

REF=""
for cand in api-client-key api-internal-token api-jwt-secret; do
  if [ -s "${DIR}/${cand}" ]; then
    REF="${DIR}/${cand}"
    break
  fi
done

if [ -s "$DEST" ]; then
  action="keep   "
else
  umask 077
  tmp="${DEST}.tmp.$$"
  # Fernet key = urlsafe-base64 of 32 random bytes (padding kept, no newline).
  head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n' > "$tmp"
  mv "$tmp" "$DEST"
  action="created"
fi

if [ -n "$REF" ]; then
  # busybox-safe clone of uid/gid/mode. Numeric ids so alpine need not have appuser.
  chown "$(stat -c '%u:%g' "$REF")" "$DEST"
  chmod "$(stat -c '%a' "$REF")" "$DEST"
else
  chmod 600 "$DEST"
fi

# Strip world-write even if the reference was world-writable. Do not strip
# world-read: production refs may be 0644 and still be the working model.
chmod o-w "$DEST"

mode="$(stat -c '%a' "$DEST")"
other=$((mode % 10))
if [ $((other & 2)) -ne 0 ]; then
  echo "align_secret_perms: refusing world-writable $NAME mode=$mode" >&2
  exit 1
fi

uid="$(stat -c '%u' "$DEST")"
gid="$(stat -c '%g' "$DEST")"
echo "${action} ${NAME} uid=${uid} gid=${gid} mode=${mode}"
