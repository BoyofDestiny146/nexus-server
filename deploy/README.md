# CareConnect — Deploy Guide (Jetson AGX Orin)

Target: a Jetson AGX Orin (JetPack 6.1 / L4T r36.4) running the compose stack
in `deploy/docker-compose.yml`. All images are built **natively on the Jetson**
(arm64). Nothing is pulled from an external registry.

---

## 1. Host prerequisites (one time)

- Docker + NVIDIA Container Toolkit (ships with JetPack). `daemon.json`:

  ```json
  {
    "runtimes": {"nvidia": {"path": "nvidia-container-runtime", "args": []}},
    "insecure-registries": ["localhost:5000"]
  }
  ```
  Do **not** set `"iptables": false` (published ports stop binding after a reboot).

- A local registry so compose can pull by tag:
  `docker run -d -p 5000:5000 --restart=unless-stopped --name registry registry:2`

- Ollama installed natively (`ollama serve` on `0.0.0.0:11434`) with the models:

  ```sh
  ollama pull llama3.1:8b-instruct-q4_K_M
  ollama pull qwen2.5vl:3b
  ollama pull nomic-embed-text
  sudo mkdir -p /etc/careconnect/cc-models
  sudo cp deploy/jetson/Modelfile.cc-llm deploy/jetson/Modelfile.cc-vision /etc/careconnect/cc-models/
  ollama create cc-llm    -f /etc/careconnect/cc-models/Modelfile.cc-llm
  ollama create cc-vision -f /etc/careconnect/cc-models/Modelfile.cc-vision
  # keep all models resident + warm them on boot
  sudo install -m 644 deploy/jetson/ollama-cc-memory.conf /etc/systemd/system/ollama.service.d/cc-memory.conf
  sudo install -m 755 deploy/jetson/cc-warm-models.sh /usr/local/bin/cc-warm-models.sh
  sudo install -m 644 deploy/jetson/ollama-warm.service /etc/systemd/system/ollama-warm.service
  sudo systemctl daemon-reload && sudo systemctl enable --now ollama-warm.service
  ```

---

## 2. Build the images (on the Jetson)

```sh
git clone <this repo> ~/careconnect-src && cd ~/careconnect-src
REGISTRY=localhost:5000; TAG=$(date +%Y%m%d)

docker build -t $REGISTRY/careconnect-api:$TAG   -t $REGISTRY/careconnect-api:latest   api/
docker build -t $REGISTRY/careconnect-web:$TAG   -t $REGISTRY/careconnect-web:latest   web/
docker build -f deploy/caddy.Dockerfile -t $REGISTRY/careconnect-caddy:$TAG -t $REGISTRY/careconnect-caddy:latest deploy/
docker build -f deploy/tts.Dockerfile   -t $REGISTRY/careconnect-piper:$TAG -t $REGISTRY/careconnect-piper:latest .

# xiaozhi-server: full build first (torch/funasr compile natively, slow), then the
# source overlay for every later change (fast, no dependency rebuild).
docker build --network=host -t $REGISTRY/careconnect-xiaozhi-server:base xiaozhi-server/
docker build --network=host -f xiaozhi-server/Dockerfile.overlay \
  --build-arg BASE=$REGISTRY/careconnect-xiaozhi-server:base \
  -t $REGISTRY/careconnect-xiaozhi-server:$TAG -t $REGISTRY/careconnect-xiaozhi-server:latest xiaozhi-server/

# mqtt-gateway (LAN/MQTT transport only): upstream source + our container files
git clone https://github.com/xinnan-tech/xiaozhi-mqtt-gateway.git mqtt-gateway/upstream
git -C mqtt-gateway/upstream checkout 369e42f62fbae5af5734c2da19712c8318652bf5
cp mqtt-gateway/Dockerfile mqtt-gateway/.dockerignore mqtt-gateway/docker-entrypoint.sh mqtt-gateway/upstream/
docker build -t $REGISTRY/careconnect-mqtt-gateway:$TAG -t $REGISTRY/careconnect-mqtt-gateway:latest mqtt-gateway/upstream/

for i in api web caddy piper xiaozhi-server mqtt-gateway; do docker push $REGISTRY/careconnect-$i:latest; done
```

The xiaozhi-server image runs as root (the base image drops the `xiaozhiuser`
account); do not add a `USER` line to the overlay.

---

## 3. First boot

```sh
mkdir -p ~/careconnect-deploy && cp deploy/docker-compose.yml deploy/.env.example ~/careconnect-deploy/
cp -r deploy/scripts ~/careconnect-deploy/
cd ~/careconnect-deploy
cp .env.example .env            # set CC_ADMIN1_PASSWORD, CC_ADMIN2_PASSWORD, CC_TUNNEL_TOKEN, CC_OTA_TRANSPORT=websocket
./scripts/gen-secrets.sh        # creates the cc-secrets volume (6 random secret files)
docker compose pull
docker compose up -d
docker compose ps               # 8 services up / healthy within ~60 s
```

Admin accounts `admin1` / `admin2` are seeded by the api on startup from the
`.env` passwords. Schema is created by the api on first boot; SQL files in
`api/migrations/` are applied by hand if the schema is older than the code
(`docker compose exec mariadb mariadb -u root -p"$(…)" xiaozhi_esp32_server < …`).

Runtime config for the voice server is the copy in the `xiaozhi-data` volume
(`data/.config.yaml`); it shadows the file baked into the image. Edit it with
`docker compose exec xiaozhi-server vi data/.config.yaml` and restart the service.

ASR/VAD model weights go into the `xiaozhi-models` volume
(SenseVoiceSmall + silero VAD, see `xiaozhi-server/config.yaml` for paths).

---

## 4. Public ingress (Cloudflare Tunnel)

`deploy/scripts/cf-tunnel-setup.py` provisions the tunnel + proxied DNS records
idempotently. It reads `CF_API_TOKEN`, `CF_ZONE`, `CF_ACCOUNT_ID` from an env
file (`~/.config/careconnect/cloudflare.env` by default):

```sh
./scripts/cf-tunnel-setup.py setup --token-out ~/.config/careconnect/cf-tunnel-token
# put the emitted token into .env as CC_TUNNEL_TOKEN, then: docker compose up -d cloudflared
```

Hostname map: apex/www/app/api → caddy:443 (with `noTLSVerify` +
`originServerName`), `ota.` → xiaozhi-server:8003, `ws.` → xiaozhi-server:8000.

---

## 5. Devices

Flash the fleet OTA URL (and optionally WiFi) into a Watcher's NVS:

```sh
scripts/preprovision-watcher.sh backup
scripts/preprovision-watcher.sh provision --ssid <ssid> --password-file <file>
```

Serial target is the Watcher's second CDC interface (`/dev/ttyACM1` by default).
Stop ModemManager first if it grabs the port. Then add the device in the
dashboard (`/careconnect/patients/new`) by MAC.

---

## 6. Verify / operate

```sh
curl -s https://api.haizel.online/api/health
API_KEY=$(docker run --rm -v careconnect_cc-secrets:/s:ro alpine cat /s/api-client-key)
curl -s -H "X-API-Key: $API_KEY" https://api.haizel.online/api/v1/watchers

docker compose logs -f xiaozhi-server
docker compose restart api
```

Rollback = set `TAG=<previous>` in `.env`, `docker compose up -d`.
