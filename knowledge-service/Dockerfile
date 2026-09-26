# careconnect-knowledge — extract / chunk / embed / Qdrant REST
# Build context: knowledge-service/
# Platform: linux/arm64

FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "pydantic>=2.0" \
        "pydantic-settings>=2.0" \
        "httpx>=0.27" \
        "pypdf>=5.0" \
        "python-docx>=1.1" \
        "python-pptx>=1.0"

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r appuser \
    && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

COPY --from=builder /install /usr/local
COPY knowledge_service/ ./knowledge_service/

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
    CMD wget -qO- http://localhost:8090/healthz || exit 1

CMD ["uvicorn", "knowledge_service.main:app", "--host", "0.0.0.0", "--port", "8090"]
