# Nexus production runbook (Jetson Orin)

Paths:

| Role | Path |
|---|---|
| Git repo | `/mnt/xiaozhi/nexus-server` |
| Production compose + `.env` | `/mnt/xiaozhi/nexus-deploy` |
| Compose project | `nexus` (`docker compose -p nexus`) |
| SSD | `/mnt/xiaozhi` (Docker Root Dir `/mnt/xiaozhi/docker-data`) |
| Backups | `/mnt/xiaozhi/backups/` |

Do **not** copy `deploy/docker-compose.yml` from git over production compose. Production Caddy is **80/443**. The git file still publishes 18180/18443.

Ollama/Qwen stay native (`ollama.service` + `ollama-warm.service`). `nexus.service` does not start a second Ollama.

---

## Normal operation

```sh
sudo nexus-status
sudo nexus-backup                 # /mnt/xiaozhi/backups/nexus-<UTC>/
sudo nexus-stop                   # systemd stop if active, else compose stop; verifies containers are down
sudo nexus-start                  # start unit, wait for health, print status
sudo nexus-start --force          # systemctl restart nexus (still --pull never)
```

`web` **Exited (0)** is the static-export one-shot. That is healthy.

---

## Safe server shutdown

```sh
sudo nexus-backup
sudo nexus-status                 # confirm LAST_GOOD exists
sudo systemctl stop nexus         # or: sudo nexus-stop
sudo shutdown -h now
```

`nexus.service` uses `docker compose -p nexus stop`, not `down`. Networks, named volumes, MariaDB files, secrets, images, and compose stay on disk. `sudo nexus-stop` stops through systemd when the unit is **active**; if the unit is inactive but containers are still Up, it runs the same `compose stop --timeout 120` and fails unless they are actually down (`web` Exited (0) is OK).

`RequiresMountsFor=/mnt/xiaozhi` keeps the SSD mounted until Nexus has stopped.

---

## Restart / reboot

`nexus.service` is enabled for `multi-user.target`. After a reboot the order is:

1. `/mnt/xiaozhi` mounted (`mnt-xiaozhi.mount`)
2. containerd, then Docker (existing mount-order drop-ins — preserve them)
3. `nexus.service` waits until `docker info` works, then  
   `docker compose -p nexus up -d --pull never --no-build`

Ollama warmup is independent and must be left alone.

```sh
sudo reboot          # after a successful backup
# after boot:
sudo nexus-status
```

---

## First install of this package (does not start the stack)

From the git checkout (do **not** replace production compose):

```sh
cd /mnt/xiaozhi/nexus-server
git fetch
git checkout nexus-cursor-update   # or the commit that added this package
git pull

sudo deploy/jetson/install-docker-mount-order.sh --status
# only if drop-ins are missing:
# sudo deploy/jetson/install-docker-mount-order.sh

sudo deploy/jetson/install-nexus-ops.sh --status
sudo deploy/jetson/install-nexus-ops.sh
sudo nexus-backup
sudo nexus-status
```

`install-nexus-ops.sh` enables `nexus.service` but does **not** start it. Existing `unless-stopped` containers keep running.

First backup:

```sh
sudo nexus-backup
ls -l /mnt/xiaozhi/backups/LAST_GOOD
```

---

## Controlled start/stop/reboot test (after a known-good backup)

```sh
sudo nexus-backup
sudo nexus-stop
sudo nexus-status          # compose services should be Exit/Stopped; web Exited (0) is still fine
sudo nexus-start
sudo nexus-status          # api / xiaozhi / piper / caddy ok

# optional reboot test
sudo nexus-backup
sudo reboot
# after boot
sudo nexus-status
```

---

## Disaster recovery

1. **Mount the SSD** (fstab UUID, not a hardcoded `/dev/sda1`):
   ```sh
   sudo mount /mnt/xiaozhi
   findmnt /mnt/xiaozhi
   ```
2. **Docker/containerd mount-order drop-ins** (do not change Docker Root Dir):
   ```sh
   sudo /mnt/xiaozhi/nexus-server/deploy/jetson/install-docker-mount-order.sh
   sudo systemctl restart containerd
   sudo systemctl restart docker
   ```
3. **Restore production deployment** (explicit confirm required):
   ```sh
   sudo nexus-restore /mnt/xiaozhi/backups/LAST_GOOD
   # read the printed Git SHA, then:
   sudo nexus-restore /mnt/xiaozhi/backups/LAST_GOOD --confirm RESTORE
   ```
   This restores `/mnt/xiaozhi/nexus-deploy`, secrets, persistent volumes, MariaDB dump, and systemd units. It does **not** overwrite the git repo.
4. **Checkout the recorded Git SHA** (images/code match the backup):
   ```sh
   cd /mnt/xiaozhi/nexus-server
   git fetch
   git checkout <SHA from MANIFEST / restore output>
   ```
   Rebuild/retags only if the recorded image digests are missing. Do not `docker compose pull` at boot.
5. **Secrets** are already in volume `nexus_cc-secrets` after restore. Do not `cat` them.
6. **Chroma** (Watcher vector memory) is **not** a named volume. Production
   XiaoZhi runs as **root**, so it lives at
   `/root/.local/share/careconnect/chroma` inside `xiaozhi-server` (not
   `/opt/xiaozhi-esp32-server/.local/...`). After Nexus is up:
   ```sh
   sudo nexus-restore /mnt/xiaozhi/backups/LAST_GOOD --apply-chroma --confirm RESTORE
   ```
7. **Enable and start Nexus**:
   ```sh
   sudo systemctl enable nexus
   sudo nexus-start
   sudo nexus-status
   ```
8. **Validate**: API `healthz`, XiaoZhi OTA `:8003`, Piper `/health`, Caddy admin `:2019`, `web` Exited (0), Ollama `:11434`, disk free on `/mnt/xiaozhi`.

---

## Backup contents

Timestamped directory `/mnt/xiaozhi/backups/nexus-<UTC>/` mode `0700`:

- `MANIFEST.txt`, `SHA256SUMS`, `git-sha.txt`
- `deploy/` — production compose + `.env` (mode 0600, values not logged)
- `mariadb/<database>.sql` — `mysqldump --single-transaction`
- `volumes/nexus_<name>.tar.gz` — `cc-secrets`, `cc-voice`, `xiaozhi-data`, `redis-data`, `caddy-data`, `caddy-config`, `cc-photos`, `xiaozhi-models`
- `chroma/chroma.tar.gz` from the live XiaoZhi container (`/root/.local/share/careconnect/chroma`, sqlite online backup)
- `systemd/` — `nexus.service` + docker/containerd drop-ins
- `docker/` — image names/tags/digests (not image layers)
- `ollama/` — tag inventory

Skipped on purpose: Docker image tarballs, `nexus_web-static` (one-shot recopies), `nexus_mariadb-data` (logical dump is canonical).

A failed backup is renamed `*.failed`. `LAST_GOOD` is only updated after verification.

---

## Restore safety

```sh
sudo nexus-restore                         # exit 2, no writes
sudo nexus-restore /path/to/backup         # prints SHA + plan, exit 2, no writes
sudo nexus-restore /path/to/backup --confirm RESTORE   # destructive apply
```

---

## Named volumes (`docker compose -p nexus`)

| Volume | Why |
|---|---|
| `nexus_cc-secrets` | MariaDB passwords, JWT, API keys, MQTT HMAC, Fernet |
| `nexus_mariadb-data` | InnoDB files (backed up via dump, not tar) |
| `nexus_redis-data` | Redis AOF (dashboard pub/sub survives restart) |
| `nexus_caddy-data` / `nexus_caddy-config` | TLS/ACME |
| `nexus_xiaozhi-data` | `.config.yaml` + runtime data |
| `nexus_xiaozhi-models` | SenseVoice / Silero weights |
| `nexus_cc-voice` | per-device voice map |
| `nexus_cc-photos` | Watcher camera stills |
| `nexus_web-static` | dashboard export (not backed up) |
| Chroma (in-container) | `/root/.local/share/careconnect/chroma` | `chroma/chroma.tar.gz` |
