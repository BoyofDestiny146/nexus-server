# careconnect-web — Next.js 14 static export
# Build context: web/
# Platform: linux/arm64
#
# Pattern: multi-stage build → tiny final image that is used as an init
# container. On startup it copies /app/out/ into the shared web-static volume
# that Caddy mounts at /srv/web, then exits.

FROM node:20-alpine AS builder

WORKDIR /app

# Copy dependency manifests first (layer cache)
COPY package.json package-lock.json ./

# Install deps (including devDeps — needed for Next.js build)
RUN npm ci --no-audit --no-fund

# Copy source
COPY . .

# Build static export — produces out/
# NEXT_PUBLIC_* vars can be overridden at build time via --build-arg if needed.
# Defaults: hazel.online (set in lib/serverConfig.ts)
RUN npm run build

# ── Tiny init-container image ─────────────────────────────────────────────────
FROM alpine:3.19

RUN addgroup -S appuser && adduser -S -G appuser appuser

# Copy only the static export
COPY --from=builder /app/out /app/out

RUN chown -R appuser:appuser /app

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod 0755 /docker-entrypoint.sh

# Must start as root so the entrypoint can chown the named volume (created
# root:root 755). Do not USER appuser here — that was the Permission denied
# on cp into /srv/web after a volume recreate.
ENTRYPOINT ["/docker-entrypoint.sh"]

# Compose overrides this with the copy command; keep a safe default.
CMD ["/bin/sh", "-c", "echo 'careconnect-web: run with the compose command to copy static files'"]
