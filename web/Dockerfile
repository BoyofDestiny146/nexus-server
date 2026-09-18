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

USER appuser

# The compose command overrides this:
#   command: ["/bin/sh", "-c", "cp -a /app/out/. /srv/web/ && echo 'web: static export copied'"]
# Provide a sensible default in case someone runs this image standalone.
CMD ["/bin/sh", "-c", "echo 'careconnect-web: run with the compose command to copy static files'"]
