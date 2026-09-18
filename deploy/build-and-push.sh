#!/usr/bin/env bash
# build-and-push.sh — Build and push all careconnect arm64 images to the
#                     private registry on the build host.
#
# Usage: ./deploy/build-and-push.sh [--tag <tag>]
#
# Idempotent: re-running overwrites the same tags in the registry.
# Requires:   docker buildx builder named cc-arm64 (docker-container driver,
#             host networking, buildkitd.toml configured for http access to
#             localhost:5000). The builder MUST use --driver-opt network=host
#             so the buildkit container can reach the insecure registry on the build host.
#
#             If cc-arm64 is missing or misconfigured, recreate it:
#               cat > /tmp/buildkitd.toml << 'EOF'
#               [registry."localhost:5000"]
#                 http = true
#                 insecure = true
#               [registry."127.0.0.1:5000"]
#                 http = true
#                 insecure = true
#               EOF
#               docker buildx rm cc-arm64 || true
#               docker buildx create --name cc-arm64 --driver docker-container \
#                 --driver-opt network=host --config /tmp/buildkitd.toml --use
#               docker buildx inspect --bootstrap cc-arm64
#
# XIAOZHI-SERVER NOTE:
#   careconnect-xiaozhi-server is SKIPPED by this script.
#   It must be built natively on the Jetson (funasr/modelscope have no arm64
#   manylinux wheels; they need native C extension compilation).
#   See xiaozhi-server/Dockerfile for the native build instructions.

set -euo pipefail

REGISTRY="${REGISTRY:-localhost:5000}"
BUILDER="${BUILDER:-cc-arm64}"
PLATFORM="linux/arm64"
DATE_TAG="$(date +%Y%m%d)-arm64"
TAG="${1:-${DATE_TAG}}"

# Resolve the repo root relative to this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# Upstream xiaozhi-mqtt-gateway checkout with mqtt-gateway/{Dockerfile,.dockerignore,docker-entrypoint.sh} copied in
MQTT_GATEWAY_SRC="${MQTT_GATEWAY_SRC:-${REPO_ROOT}/mqtt-gateway/upstream}"

BUILD="docker buildx --builder ${BUILDER} build --platform ${PLATFORM} --push"

echo "=========================================="
echo " careconnect arm64 image build + push"
echo " registry:  ${REGISTRY}"
echo " builder:   ${BUILDER}"
echo " tags:      ${REGISTRY}/<image>:${TAG}"
echo "            ${REGISTRY}/<image>:latest"
echo "=========================================="

# ── 1. careconnect-api ────────────────────────────────────────────────────────
echo ""
echo "[1/5] careconnect-api  (context: api/)"
${BUILD} \
  -t "${REGISTRY}/careconnect-api:${TAG}" \
  -t "${REGISTRY}/careconnect-api:latest" \
  "${REPO_ROOT}/api"
echo "      PUSHED careconnect-api:${TAG}"

# ── 2. careconnect-web ────────────────────────────────────────────────────────
echo ""
echo "[2/5] careconnect-web  (context: web/)"
${BUILD} \
  -t "${REGISTRY}/careconnect-web:${TAG}" \
  -t "${REGISTRY}/careconnect-web:latest" \
  "${REPO_ROOT}/web"
echo "      PUSHED careconnect-web:${TAG}"

# ── 3. careconnect-caddy ──────────────────────────────────────────────────────
echo ""
echo "[3/5] careconnect-caddy  (context: deploy/  file: deploy/caddy.Dockerfile)"
${BUILD} \
  -f "${SCRIPT_DIR}/caddy.Dockerfile" \
  -t "${REGISTRY}/careconnect-caddy:${TAG}" \
  -t "${REGISTRY}/careconnect-caddy:latest" \
  "${SCRIPT_DIR}"
echo "      PUSHED careconnect-caddy:${TAG}"

# ── 4. careconnect-piper ──────────────────────────────────────────────────────
echo ""
echo "[4/5] careconnect-piper  (MULTI-ENGINE Piper+Kokoro+Edge — context: repo root, file: deploy/tts.Dockerfile)"
# NOTE: must be tts.Dockerfile (multi-engine, bakes scripts/careconnect_tts_server.py
# + the Kokoro model), NOT piper.Dockerfile (the old Piper-only server). The latter
# has no Kokoro, so the neural default voice 500s.
${BUILD} \
  -f "${SCRIPT_DIR}/tts.Dockerfile" \
  -t "${REGISTRY}/careconnect-piper:${TAG}" \
  -t "${REGISTRY}/careconnect-piper:latest" \
  "${REPO_ROOT}"
echo "      PUSHED careconnect-piper:${TAG}"

# ── 5. careconnect-mqtt-gateway ───────────────────────────────────────────────
echo ""
echo "[5/5] careconnect-mqtt-gateway  (context: ${MQTT_GATEWAY_SRC})"
${BUILD} \
  -t "${REGISTRY}/careconnect-mqtt-gateway:${TAG}" \
  -t "${REGISTRY}/careconnect-mqtt-gateway:latest" \
  "${MQTT_GATEWAY_SRC}"
echo "      PUSHED careconnect-mqtt-gateway:${TAG}"

# ── SKIPPED: careconnect-xiaozhi-server ──────────────────────────────────────
echo ""
echo "[SKIP] careconnect-xiaozhi-server — deferred to native Jetson build."
echo "       See xiaozhi-server/Dockerfile for instructions."

# ── Registry catalog ──────────────────────────────────────────────────────────
echo ""
echo "=========================================="
echo " Registry catalog"
echo "=========================================="
curl -s "http://${REGISTRY}/v2/_catalog" | python3 -m json.tool 2>/dev/null || \
  curl -s "http://${REGISTRY}/v2/_catalog"

echo ""
echo "Build complete. Tags pushed: :${TAG} and :latest"
