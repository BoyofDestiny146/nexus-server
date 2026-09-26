"""careconnect API entry point.

Mounts:
  - /healthz                         (raw, no envelope)
  - /api/user/*, /api/agent/*, ...   (all wrapped in {code, msg, data})
  - /ws/agent/{id}                   (WebSocket; envelope skipped)

Run:
  conda activate xiaozhi
  python -m uvicorn careconnect_api.main:app --host 0.0.0.0 --port 8080
Or:
  systemctl --user start careconnect-api.service
"""
from __future__ import annotations

import contextlib
import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from . import __version__
from .envelope import (
    APIException,
    EnvelopeMiddleware,
    api_exception_handler,
    envelope,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from .bootstrap_root import seed_two_admins
from .db import async_session_factory
from .pubsub import close_redis, get_redis
from .scheduler import start_scheduler, stop_scheduler
from .settings import settings


logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("api")


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup / shutdown. Phase 5 wires APScheduler in for the daily triage
    job; Phase 6 adds the Redis client used by the WebSocket fan-out."""
    log.info("careconnect-api starting (version=%s, port=%d)", __version__, settings.port)
    # Eagerly read secrets so misconfig fails at boot, not at first request
    _ = settings.jwt_secret
    _ = settings.internal_token
    _ = settings.client_api_key
    log.info("secrets loaded; db=%s redis=%s", settings.db_name, settings.redis_url)
    # Seed the two default admin accounts on first boot (idempotent).
    try:
        async with async_session_factory() as db:
            await seed_two_admins(db)
    except Exception as exc:
        log.warning("admin seed failed (DB may not be ready yet): %s", exc)
    start_scheduler()
    # Force Redis connect so a misconfigured URL fails at boot instead of at
    # the first /ws/agent/* handshake. PING throws if the server is down.
    redis_client = await get_redis()
    try:
        await redis_client.ping()
        log.info("redis connected (%s)", settings.redis_url)
    except Exception as exc:
        log.warning("redis ping failed at startup (%s) — pubsub will reconnect lazily", exc)
    try:
        yield
    finally:
        stop_scheduler()
        await close_redis()
        log.info("careconnect-api shutting down")


app = FastAPI(
    title="careconnect API",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.add_middleware(EnvelopeMiddleware)
app.add_exception_handler(APIException, api_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)


@app.get("/healthz")
def healthz():
    """Liveness probe — raw JSON, NOT wrapped in envelope (so monitoring
    can hit it without parsing)."""
    return {"ok": True, "service": "careconnect-api", "version": __version__}


# Routers — registered as we build phases out
from .routers import user as user_router  # noqa: E402
from .routers import agent as agent_router  # noqa: E402
from .routers import device as device_router  # noqa: E402
from .routers import assessment as assessment_router  # noqa: E402
from .routers import admin as admin_router  # noqa: E402
from .routers import onboard as onboard_router  # noqa: E402
from .routers import internal as internal_router  # noqa: E402
from .routers import ws as ws_router  # noqa: E402
from .routers import public as public_router  # noqa: E402
from .routers import health as health_router  # noqa: E402
from .routers import voice as voice_router  # noqa: E402
from .routers import integrations as integrations_router  # noqa: E402
from .routers import partner as partner_router  # noqa: E402
from .routers import knowledge as knowledge_router  # noqa: E402

app.include_router(user_router.router, prefix="/api")
app.include_router(agent_router.router, prefix="/api")
app.include_router(device_router.router, prefix="/api")
app.include_router(assessment_router.router, prefix="/api")
app.include_router(admin_router.router, prefix="/api")
app.include_router(onboard_router.router, prefix="/api")
app.include_router(internal_router.router, prefix="/api")
# Public client-facing API — X-API-Key auth, no JWT/RBAC. Mounted at /api/v1/*.
app.include_router(public_router.router, prefix="/api")
# CareConnect partner M2M — X-Client-Id + X-Client-Secret. Not dashboard JWT.
app.include_router(partner_router.router, prefix="/api")
# WebSocket router mounted at root — /ws/* is intentionally outside /api so
# the envelope middleware skips it (websocket upgrades aren't JSON anyway).
app.include_router(ws_router.router)
app.include_router(health_router.router, prefix="/api")
app.include_router(voice_router.router, prefix="/api")
app.include_router(integrations_router.router, prefix="/api")
app.include_router(knowledge_router.router, prefix="/api")


@app.get("/readyz")
async def readyz():
    """Lightweight readiness probe: DB + Redis + Ollama.

    Returns raw JSON (not envelope-wrapped) so monitoring tools can consume it
    without parsing the envelope shape.  HTTP 200 = ready; HTTP 503 = not ready.
    """
    import httpx
    from sqlalchemy import text
    from .pubsub import get_redis as _get_redis

    checks: dict[str, str] = {}
    all_ok = True

    # DB
    try:
        async with async_session_factory() as db:
            await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"fail: {exc}"
        all_ok = False

    # Redis
    try:
        r = await _get_redis()
        await r.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"fail: {exc}"
        all_ok = False

    # Ollama
    try:
        async with httpx.AsyncClient(timeout=3.0) as ac:
            vr = await ac.get(f"{settings.ollama_url}/api/version")
        if vr.status_code == 200:
            checks["ollama"] = f"ok ({vr.json().get('version', '?')})"
        else:
            checks["ollama"] = f"fail: HTTP {vr.status_code}"
            all_ok = False
    except Exception as exc:
        checks["ollama"] = f"fail: {exc}"
        all_ok = False

    status_code = 200 if all_ok else 503
    from fastapi.responses import JSONResponse
    return JSONResponse(
        {"ok": all_ok, "checks": checks, "service": "careconnect-api"},
        status_code=status_code,
    )
