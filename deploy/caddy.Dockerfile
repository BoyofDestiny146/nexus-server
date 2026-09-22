# careconnect-caddy — Caddy 2 with the hazel.online Caddyfile baked in
# Build context: deploy/
# Platform: linux/arm64

FROM caddy:2.9.1-alpine

# Bake in the production Caddyfile.
# Phase 5: to swap to real ACME, rebuild with an updated Caddyfile.hazel
# (replace `tls internal` block) — no other change needed.
COPY Caddyfile.nexus /etc/caddy/Caddyfile

# Verify Caddyfile parses before shipping the image.
# `caddy validate` exits non-zero on syntax errors.
RUN caddy validate --config /etc/caddy/Caddyfile

EXPOSE 80 443 2019
