# careconnect-api — FastAPI backend
# Build context: api/
# Platform: linux/arm64

FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools needed for bcrypt / cryptography / pydantic-core wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# Install only runtime deps (no [dev] extras)
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "sqlalchemy[asyncio]>=2.0" \
        "aiomysql>=0.2" \
        "pydantic>=2.0" \
        "pydantic-settings>=2.0" \
        "pyjwt>=2.8" \
        "bcrypt>=4.0" \
        "httpx>=0.27" \
        "redis>=5.0" \
        "apscheduler>=3.10" \
        "pyyaml>=6.0" \
        "cryptography>=42.0" \
        "icalendar>=6.0" \
        "python-dateutil>=2.9" \
        "python-multipart>=0.0.9"

# ── Runtime image ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends wget gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r appuser \
    && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && mkdir -p /data \
    && chmod 0777 /data

WORKDIR /app

COPY --from=builder /install /usr/local

# Copy application source
COPY careconnect_api/ ./careconnect_api/
COPY migrations/ ./migrations/
COPY docker-entrypoint.sh /docker-entrypoint.sh

RUN chown -R appuser:appuser /app \
    && chmod 0755 /docker-entrypoint.sh

# Entrypoint starts as root so it can chmod the shared cc-voice volume, then
# drops to appuser via gosu. Do not set USER appuser here.
ENTRYPOINT ["/docker-entrypoint.sh"]

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD wget -qO- http://localhost:8080/healthz || exit 1

CMD ["uvicorn", "careconnect_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
