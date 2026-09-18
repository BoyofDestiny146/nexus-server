# careconnect-tts — multi-engine TTS HTTP server (Piper + Kokoro + Edge)
# Build context: careconnect/  (repo root)  Platform: linux/arm64
# Replaces the old piper-only image. No torch (onnxruntime only) → arm64-friendly.
#
# Engines:
#   kokoro:<name>  local neural, 24 kHz native (DEFAULT, offline)  e.g. kokoro:af_heart
#   edge:<name>    Microsoft neural (needs internet)               e.g. edge:en-US-AvaNeural
#   piper:<name>   local                                           e.g. piper:en_US-hfc_female-medium
#
# HTTP: GET /health, GET /voices, POST /v1/audio/speech {input,voice,speed}

FROM python:3.12-slim AS assets
WORKDIR /assets
ARG PIPER_BASE=https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US
ARG KOKORO_BASE=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
RUN apt-get update && apt-get install -y --no-install-recommends wget ca-certificates && rm -rf /var/lib/apt/lists/* \
    && mkdir -p piper kokoro \
    && wget -q "${PIPER_BASE}/amy/medium/en_US-amy-medium.onnx"            -O piper/en_US-amy-medium.onnx \
    && wget -q "${PIPER_BASE}/amy/medium/en_US-amy-medium.onnx.json"       -O piper/en_US-amy-medium.onnx.json \
    && wget -q "${PIPER_BASE}/hfc_female/medium/en_US-hfc_female-medium.onnx"      -O piper/en_US-hfc_female-medium.onnx \
    && wget -q "${PIPER_BASE}/hfc_female/medium/en_US-hfc_female-medium.onnx.json" -O piper/en_US-hfc_female-medium.onnx.json \
    && wget -q "${KOKORO_BASE}/kokoro-v1.0.onnx" -O kokoro/kokoro-v1.0.onnx \
    && wget -q "${KOKORO_BASE}/voices-v1.0.bin"  -O kokoro/voices-v1.0.bin

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg espeak-ng libsndfile1 wget ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r ttsuser && useradd -r -g ttsuser -d /app -s /sbin/nologin ttsuser
WORKDIR /app
# No torch here, so numpy is unpinned — kokoro-onnx needs numpy>=2 (the 1.26 pin
# is only required by the torch-based xiaozhi-server, a different image).
RUN pip install --no-cache-dir \
    "piper-tts==1.4.2" \
    "kokoro-onnx>=0.4.9" \
    "edge-tts>=7.2.8" \
    "soundfile>=0.12" \
    "fastapi>=0.115" "uvicorn[standard]>=0.30" "pydantic>=2.0"

COPY --from=assets /assets/piper  /voices
COPY --from=assets /assets/kokoro /kokoro
# TTS server (from careconnect/scripts/careconnect_tts_server.py)
COPY scripts/careconnect_tts_server.py .
RUN chown -R ttsuser:ttsuser /app /voices /kokoro
USER ttsuser

ENV PIPER_VOICE_DIR=/voices
ENV KOKORO_DIR=/kokoro
ENV CC_TTS_DEFAULT_VOICE=kokoro:af_heart
EXPOSE 5500
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -qO- http://localhost:5500/health || exit 1
CMD ["uvicorn", "careconnect_tts_server:app", "--host", "0.0.0.0", "--port", "5500"]
