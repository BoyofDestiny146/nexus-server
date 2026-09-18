# CareConnect

Voice companion + caregiver dashboard for older adults, built around the SenseCAP
Watcher (ESP32-S3) and a self-hosted server stack. This repository is the exact
source of the stack running on the production NVIDIA Jetson AGX Orin
(snapshot as deployed 2026-07-27).

Public entry points (via a Cloudflare Tunnel, no inbound ports):

| Host | Backend | Purpose |
|---|---|---|
| `haizel.online`, `www.`, `app.`, `api.` | caddy:443 | Dashboard SPA (`/careconnect/…`), REST API (`/api/*`), client API (`/api/v1/*`), dashboard WebSocket (`/ws/*`) |
| `ota.haizel.online` | xiaozhi-server:8003 | Device OTA / server-config fetch, vision endpoint |
| `ws.haizel.online` | xiaozhi-server:8000 | Device voice WebSocket |

## Repository layout

| Path | What it is | Image |
|---|---|---|
| `api/` | FastAPI backend: auth, RBAC, patients/devices, client API (`X-API-Key`), triage scheduler, WebSocket push. Tests in `api/tests`. | `careconnect-api` |
| `web/` | Next.js 14 dashboard, built as a static export (`basePath /careconnect`) and served by Caddy. | `careconnect-web` |
| `xiaozhi-server/` | Voice server (fork of xiaozhi-esp32-server) with the CareConnect changes: per-patient persona from the DB, chat persistence + dashboard notify, multi-engine TTS, per-device voice, voice-triggered camera + vision, reminders, English-only pipeline. `data/.config.yaml` is the runtime config. | `careconnect-xiaozhi-server` |
| `mqtt-gateway/` | Container files for the upstream `xiaozhi-mqtt-gateway` (used only for LAN/MQTT transport; remote devices use WebSocket). | `careconnect-mqtt-gateway` |
| `deploy/` | `docker-compose.yml`, `Caddyfile.hazel`, `.env.example`, Dockerfiles for caddy + TTS, Ollama host files (`deploy/jetson/`), Cloudflare tunnel provisioner, secrets bootstrap. | `careconnect-caddy`, `careconnect-piper` |
| `scripts/` | `careconnect_tts_server.py` (TTS HTTP server baked into the TTS image) and `preprovision-watcher.sh` (flash WiFi + OTA URL into a Watcher's NVS). | |

## Runtime architecture

```
Watcher (ESP32-S3)  --wss--> cloudflared --> xiaozhi-server:8000  (voice: VAD, ASR, LLM, TTS)
                    --https-> cloudflared --> xiaozhi-server:8003  (OTA config, vision)
Browser             --https-> cloudflared --> caddy:443 --> /srv/web (dashboard)
                                                       --> api:8080 (REST + WS)
xiaozhi-server --> mariadb (chat history, agents, devices)   --> api (notify chat-turn)
xiaozhi-server --> piper-tts:5500 (Kokoro / Piper / Edge TTS)
xiaozhi-server, api --> Ollama on the host (:11434): cc-llm (chat), cc-vision (camera), nomic-embed-text
```

Ollama runs natively on the Jetson (GPU), not in compose. Model variants and the
boot warm-up unit live in `deploy/jetson/`.

## Deploying

See `deploy/README.md` for the full build + first-boot procedure. Short version:

1. Build the six images on the Jetson (arm64, native) and tag them into a local registry.
2. `deploy/scripts/gen-secrets.sh` once, then fill `deploy/.env` from `.env.example`
   (admin passwords, tunnel token, domain).
3. `docker compose up -d` from the deploy directory.
4. Provision each Watcher with `scripts/preprovision-watcher.sh provision --ssid … --password-file …`.

## Notes

- Device identity is the Watcher's MAC. A device can also carry a client-supplied
  `clientDeviceId`, editable in the dashboard, and looked up via
  `GET /api/v1/watcher/by-client-id/{id}/status` (X-API-Key).
- Device "online" state is derived from the last conversation timestamp (the
  firmware does not send heartbeats), window `CC_WATCHER_ONLINE_WINDOW_SECONDS`.
- Wake word on the Watcher is `Jarvis` (firmware WakeNet); the assistant's name is Haizel.
