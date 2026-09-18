# careconnect-xiaozhi-server — XiaoZhi ESP32 voice server
# Build context: xiaozhi-server/
# Platform: linux/arm64
#
# ============================================================================
# DEFERRED: This image MUST be built natively on the Jetson AGX Orin.
#
# Why: The requirements include:
#   - torch==2.2.2    (82 MB aarch64 wheel — feasible, but slow under QEMU)
#   - funasr==1.2.3   (sdist only — C/C++ extensions, breaks under QEMU emulation)
#   - modelscope==1.23.2 (sdist only)
#   - torchaudio==2.2.2
#
# funasr and modelscope have no manylinux aarch64 wheels; they require native
# compilation. Under QEMU (linux/arm64 cross-build on x86_64), the C extension
# build either hangs or produces incorrect binaries.
#
# Native build on Jetson (one-time):
#   ssh <jetson-host>
#   mkdir /tmp/cc-xiaozhi-ctx && cd /tmp/cc-xiaozhi-ctx
#   # copy the xiaozhi-server/ directory here (then delete after build)
#   docker build --network=host \
#     -t localhost:5000/careconnect-xiaozhi-server:20260530-arm64 \
#     -t localhost:5000/careconnect-xiaozhi-server:latest \
#     /tmp/cc-xiaozhi-ctx/
#   docker push localhost:5000/careconnect-xiaozhi-server:20260530-arm64
#   docker push localhost:5000/careconnect-xiaozhi-server:latest
#   rm -rf /tmp/cc-xiaozhi-ctx
# ============================================================================

FROM python:3.10-slim AS builder

WORKDIR /build

# System packages needed for native extension builds
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc g++ make cmake git \
        libopus-dev libffi-dev \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install \
        -r requirements.txt

# ── Runtime image ─────────────────────────────────────────────────────────────
FROM python:3.10-slim

WORKDIR /opt/xiaozhi-esp32-server

# Runtime system deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libopus0 ffmpeg wget \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r xiaozhiuser \
    && useradd -r -g xiaozhiuser -d /opt/xiaozhi-esp32-server -s /sbin/nologin xiaozhiuser

COPY --from=builder /install /usr/local

# Application source (careconnect-specific; no upstream config.yaml baked in —
# it must be provided via the xiaozhi-data volume or env override at runtime)
COPY . .

# The config/ directory ships with config.yaml (defaults). Runtime overrides
# go to data/.config.yaml (mounted volume: xiaozhi-data:/app/data)
# Ensure the data mount point exists
RUN mkdir -p /opt/xiaozhi-esp32-server/data \
    && chown -R xiaozhiuser:xiaozhiuser /opt/xiaozhi-esp32-server

USER xiaozhiuser

EXPOSE 8000 8003

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD wget -qO- http://localhost:8003/xiaozhi/ota/ || exit 1

CMD ["python", "app.py"]
