#!/bin/sh
# cc-warm-models.sh — warm all CareConnect Ollama models hot into the GPU and
# keep them resident. Run by ollama-warm.service on boot. Robust against the
# boot-time GPU contention (the docker stack loads ASR/VAD into the GPU at the
# same time, transiently OOMing the 11GB vision model): retries each model until
# it actually shows resident in `ollama ps`.
O=http://127.0.0.1:11434
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# wait for the ollama API (up to ~200s)
i=0; while [ $i -lt 40 ]; do
  curl -sf -m4 "$O/api/version" >/dev/null 2>&1 && break
  sleep 5; i=$((i + 1))
done

# (re)create the context-capped variants (idempotent; needed for a fresh box)
ollama create cc-llm    -f /etc/careconnect/cc-models/Modelfile.cc-llm    >/dev/null 2>&1
ollama create cc-vision -f /etc/careconnect/cc-models/Modelfile.cc-vision >/dev/null 2>&1

# Reclaim page cache so ollama sees the real free RAM. Loading the 11GB vision
# model from the SD card fills the cache, which transiently makes ollama refuse
# the next model (NvMap ENOMEM). Best-effort (runs as root under systemd).
drop_caches() { sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true; }

warm() {
  m=$1; ep=$2; i=0
  while [ $i -lt 20 ]; do
    ollama ps 2>/dev/null | grep -q "^$m" && return 0
    drop_caches
    if [ "$ep" = e ]; then
      curl -sf -m120 "$O/api/embeddings" -d "{\"model\":\"$m\",\"prompt\":\"w\",\"keep_alive\":-1}" >/dev/null 2>&1
    else
      curl -sf -m240 "$O/api/generate" -d "{\"model\":\"$m\",\"prompt\":\"hi\",\"stream\":false,\"keep_alive\":-1}" >/dev/null 2>&1
    fi
    sleep 8; i=$((i + 1))
  done
}

drop_caches
warm cc-vision g     # vision first (largest) for clean packing
warm cc-llm    g
warm nomic-embed-text e

echo "cc-warm-models: $(ollama ps 2>/dev/null | tail -n +2 | grep -c .) models resident"
exit 0
