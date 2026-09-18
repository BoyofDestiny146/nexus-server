# careconnect-mqtt-gateway — MQTT broker + UDP audio + WS relay
# Build context: an upstream xiaozhi-mqtt-gateway checkout with these three files copied in (see README)
# Platform: linux/arm64
#
# Ports:
#   1883 — MQTT broker (plain TCP; watcher connects here)
#   8884 — UDP audio frames
#   8007 — Admin HTTP API (internal; mapped to 127.0.0.1 by compose)
#
# Runtime secrets / config injected via entrypoint:
#   MQTT_SIGNATURE_KEY_FILE → reads the file, exports MQTT_SIGNATURE_KEY
#   XIAOZHI_SERVER_WS       → written into config/mqtt.json production.chat_servers

FROM node:18-alpine

RUN addgroup -S mqttuser \
    && adduser -S -G mqttuser mqttuser \
    && apk add --no-cache tini

WORKDIR /app

# Copy dependency manifests first (layer cache)
COPY package.json package-lock.json ./

# Production deps only
RUN npm ci --omit=dev --no-audit --no-fund

# Copy application source (excluding node_modules via .dockerignore)
COPY . .

# Entrypoint: resolves secrets + writes runtime mqtt.json config, then execs node
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && chown -R mqttuser:mqttuser /app

USER mqttuser

EXPOSE 1883 8884/udp 8007

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "require('http').get('http://localhost:8007/health', r => process.exit(r.statusCode === 200 ? 0 : 1))" || exit 1

ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["node", "app.js"]
