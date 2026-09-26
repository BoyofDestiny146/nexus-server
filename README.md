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
| `api/` | FastAPI backend: auth, RBAC, patients/devices, client API (`X-API-Key`), triage scheduler, WebSocket push, Knowledge Fabric metadata. Tests in `api/tests`. | `careconnect-api` |
| `knowledge-service/` | Retrieval sidecar: extract, chunk, embed (`nomic-embed-text`), Qdrant index/search. | `careconnect-knowledge` |
| `web/` | Next.js 14 dashboard, built as a static export (`basePath /careconnect`) and served by Caddy. | `careconnect-web` |
| `xiaozhi-server/` | Voice server (fork of xiaozhi-esp32-server) with the CareConnect changes: per-patient persona from the DB, chat persistence + dashboard notify, multi-engine TTS, per-device voice, voice-triggered camera + vision, reminders, English-only pipeline, optional authorized Knowledge grounding (`CC_XIAOZHI_KNOWLEDGE_ENABLED`, default off). `data/.config.yaml` is the runtime config. | `careconnect-xiaozhi-server` |
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
xiaozhi-server, api, knowledge-service --> Ollama on the host (:11434): cc-llm (chat), cc-vision (camera), nomic-embed-text
api --> knowledge-service:8090 --> Qdrant (nexus_knowledge)
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

## Nexus Revel discovery (Phase 1)

Nexus can list Revel Digital players with a **read-only**, allowlisted GraphQL
query. Callers cannot supply GraphQL or trigger writes. Later phases will map
strict display intents onto a Data Table row; that code is not in this slice.

Required on the API container:

| Variable | Default | Notes |
|---|---|---|
| `REVEL_API_BASE` | `https://api.reveldigital.com` | HTTPS only |
| `REVEL_GRAPHQL_URL` | `https://api.reveldigital.com/graphql` | HTTPS only |
| `REVEL_API_KEY_FILE` | `/run/secrets/revel-api-key` | Developer API key **file**. Never auto-created. Header: `X-RevelDigital-ApiKey` |
| `REVEL_CONTROL_TABLE_ID` | empty | Loaded for later phases; unused in Phase 1 |
| `REVEL_DEFAULT_DEVICE_ID` | empty | Loaded for later phases; unused in Phase 1 |

Install the key into `cc-secrets` (do not put it in `.env`), then re-run
`deploy/scripts/gen-secrets.sh` so the API `appuser` can read it.

```sh
TOKEN=$(docker run --rm -v careconnect_cc-secrets:/s:ro alpine cat /s/api-internal-token)
docker compose -f deploy/docker-compose.yml exec -T api wget -qO- \
  --header="X-Internal-Token: ${TOKEN}" \
  http://127.0.0.1:8080/api/internal/revel/devices
```

## Nexus Revel observability (Phase 2A)

The client discussion header (Client detail, next to Live) shows a
server-generated Revel badge. Click it for a diagnostic panel. Timeline
`REVEL DISPLAY EVENT` rows are `chatType` 3 system events only — the UI
never invents ENABLED/MANUAL/OFF copy or success results.

```sh
TOKEN=$(docker run --rm -v careconnect_cc-secrets:/s:ro alpine cat /s/api-internal-token)
docker compose exec -T api wget -qO- \
  --header="X-Internal-Token: ${TOKEN}" \
  http://127.0.0.1:8080/api/internal/revel/status/$AGENT_ID
```

## Nexus Revel display state (Phase 2B)

Display writes are still **disabled** until Phase 2C is deliberately enabled
(`REVEL_EXECUTE_ENABLED`, default false). Keyword voice matching still uses
the separate `EXECUTE_ENABLED = False` constant in `revel_config.py`.

Player targeting uses `cc_revel_player_map` (agent → frozen `deviceKey` →
immutable Revel `deviceId`). Names, slugs, tags, and AI text are not write
keys. Callers cannot supply `revelDeviceId`, `screen`, GraphQL, or Revel
commands.

V1 intents: `SHOW_HOME`, `RETURN_HOME`, `SHOW_APPOINTMENT_REMINDER`,
`SHOW_MEDICATION_REMINDER`, `SHOW_CARE_ALERT`, `SHOW_SENSOR_ALERT`.

Read-only Data Table discovery (same `X-Internal-Token`):

```sh
TOKEN=$(docker run --rm -v careconnect_cc-secrets:/s:ro alpine cat /s/api-internal-token)
docker compose exec -T api wget -qO- \
  --header="X-Internal-Token: ${TOKEN}" \
  http://127.0.0.1:8080/api/internal/revel/datatables
```

Apply `api/migrations/020_revel_player_map.sql` (and 021) with
`deploy/scripts/apply-revel-player-map.sh` on existing databases. See Phase 2C.

## Nexus Revel table binding + write path (Phase 2C)

Live Data Table PUTs stay **off**. Only the API process env
`REVEL_EXECUTE_ENABLED` (default `false`, compose
`${REVEL_EXECUTE_ENABLED:-false}`) can enable them. Frontend, LLM, chat, and
API request bodies cannot flip the flag. A generic `EXECUTE_ENABLED` env var
does not enable Revel writes.

When the flag is false the display path still:

1. Validates the V1 intent
2. Resolves the stored player map (`unmapped_player` if none was operator-selected)
3. Loads `REVEL_CONTROL_TABLE_ID` and the live table schema/rows
4. Binds columns to exact live `column.key` values (labels are never keys)
5. Selects the unique row where `data[device_key] == cc_revel_player_map.device_key`
6. Builds `PUT /datatables/{tableId}/rows/{rowId}` `{ "data": { ...allowlisted keys } }`
7. Records `result=skipped`, `reason=Revel execution disabled`
8. Does **not** issue the PUT, GraphQL mutation, or `sendDeviceCommand`

REST write (documented Swagger, used only when the flag is true):

```
PUT https://api.reveldigital.com/datatables/{tableId}/rows/{rowId}
Header: X-RevelDigital-ApiKey
Body:   { "data": { "<live-column-key>": <value>, ... } }
```

Row targeting errors (no write in either case):

- zero matching rows → `control_row_not_found`
- two or more matching rows → `ambiguous_control_row`

### Safe migration (existing MariaDB, no drop/recreate)

```sh
# from the repo root, against the running compose MariaDB
chmod +x deploy/scripts/apply-revel-player-map.sh
./deploy/scripts/apply-revel-player-map.sh
```

Equivalent one-liners (database `xiaozhi_esp32_server`, additive only):

```sh
docker compose -f deploy/docker-compose.yml exec -T mariadb sh -c \
  'mariadb -u root -p"$(cat /run/secrets/mariadb-root)" "$MARIADB_DATABASE"' \
  < api/migrations/020_revel_player_map.sql

docker compose -f deploy/docker-compose.yml exec -T mariadb sh -c \
  'mariadb -u root -p"$(cat /run/secrets/mariadb-root)" "$MARIADB_DATABASE"' \
  < api/migrations/021_revel_player_control_row.sql

docker compose -f deploy/docker-compose.yml exec -T mariadb sh -c \
  'mariadb -u root -p"$(cat /run/secrets/mariadb-root)" "$MARIADB_DATABASE"' \
  < api/migrations/020_021_revel_player_map.verify.sql
```

### Operator-selected test player (do not auto-bind)

List discovered players, then persist **one** immutable id the operator chose.
Do not pick the first device, a name, a slug, or a tag.

```sh
TOKEN=$(docker run --rm -v careconnect_cc-secrets:/s:ro alpine cat /s/api-internal-token)
docker compose -f deploy/docker-compose.yml exec -T api wget -qO- \
  --header="X-Internal-Token: ${TOKEN}" \
  http://127.0.0.1:8080/api/internal/revel/devices

# After the operator copies one `id` from that list:
# PUT /api/agent/{agentId}/integrations/revel
# { "deviceId": "<immutable Revel id>", "deviceName": "<discovered name>" }
```

### Control table discovery (read-only)

Set `REVEL_CONTROL_TABLE_ID` on the API container, then:

```sh
./deploy/scripts/revel-control-inspect.sh
# or:
docker compose -f deploy/docker-compose.yml exec -T api wget -qO- \
  --header="X-Internal-Token: ${TOKEN}" \
  http://127.0.0.1:8080/api/internal/revel/control
```

### Skip-path display request (first live test later)

Keep `REVEL_EXECUTE_ENABLED=false`. This validates mapping and records SKIPPED:

```sh
TOKEN=$(docker run --rm -v careconnect_cc-secrets:/s:ro alpine cat /s/api-internal-token)
docker compose -f deploy/docker-compose.yml exec -T api wget -qO- \
  --header="X-Internal-Token: ${TOKEN}" \
  --header="Content-Type: application/json" \
  --post-data='{"agentId":"<AGENT_ID>","intent":"SHOW_HOME","source":"internal"}' \
  http://127.0.0.1:8080/api/internal/revel/display
```

Eventual first live PUT (do **not** run until an operator sets the env and
recreates the API container):

```sh
# deploy/.env  →  REVEL_EXECUTE_ENABLED=true
# docker compose -f deploy/docker-compose.yml up -d --force-recreate api
# then the same POST as above
# immediately set REVEL_EXECUTE_ENABLED=false and recreate api again
```

Timeline copy when skipped:

```
REVEL DISPLAY EVENT
Intent: …
Screen: …
Player: …
Result: SKIPPED
Reason: Revel execution disabled
```

`SENT` is recorded only after the backend confirms a 2xx Revel PUT.
