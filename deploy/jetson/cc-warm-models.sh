#!/bin/sh
# cc-warm-models.sh — pin the primary Watcher chat model only.
#
# Run by ollama-warm.service on boot. Does NOT preload cc-llm / cc-vision /
# nomic-embed-text: those were keep_alive=-1 Forever and exhausted Jetson GPU
# memory so qwen2.5:3b could not load. Models stay installed for rollback.
#
# Vision and embeddings load on demand (OLLAMA_KEEP_ALIVE=5m).
O=http://127.0.0.1:11434
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
CHAT_MODEL="${CC_LLM_MODEL:-qwen2.5:3b}"

i=0; while [ $i -lt 40 ]; do
  curl -sf -m4 "$O/api/version" >/dev/null 2>&1 && break
  sleep 5; i=$((i + 1))
done

# Unload the old always-resident set so chat can allocate CUDA buffers.
# Do not `ollama rm` — rollback needs the blobs on disk.
for m in cc-llm cc-vision nomic-embed-text; do
  ollama stop "$m" >/dev/null 2>&1 || true
done

drop_caches() { sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true; }

warm_chat() {
  m=$1; i=0
  while [ $i -lt 20 ]; do
    ollama ps 2>/dev/null | grep -q "^$m" && return 0
    drop_caches
    curl -sf -m240 "$O/api/generate" \
      -d "{\"model\":\"$m\",\"prompt\":\"hi\",\"stream\":false,\"keep_alive\":-1}" \
      >/dev/null 2>&1
    sleep 8; i=$((i + 1))
  done
  return 1
}

drop_caches
warm_chat "$CHAT_MODEL"
echo "cc-warm-models: chat=$CHAT_MODEL resident=$(ollama ps 2>/dev/null | tail -n +2 | grep -c . || true)"
exit 0
