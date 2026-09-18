"""Health-check router — pipeline hop diagnostics.

Exposes two endpoints (no auth required; read-only, no sensitive data):

  GET /api/health/checks
    Returns [{key, label, status, detail, latency_ms}, ...] for every hop
    in the voice pipeline. Probes run concurrently with a hard 5-second
    budget so the dashboard never hangs.

  GET /readyz
    Lightweight three-signal liveness: DB + Redis + Ollama.  Mounted at the
    root (not under /api) to stay outside the envelope middleware; monitoring
    tools get a raw boolean.

Hop order follows the physical path a voice turn travels:
  device → router/wifi → ota → mqtt → ws → vad/asr → llm → tts → db → redis → e2e
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import async_session_factory
from ..envelope import envelope
from ..pubsub import get_redis
from ..settings import settings


log = logging.getLogger("health")

router = APIRouter(prefix="/health", tags=["health"])

# Status constants
OK = "ok"
WARN = "warn"
FAIL = "fail"


def _hop(
    key: str,
    label: str,
    status: str,
    detail: str,
    latency_ms: float | None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
    }


# ── Individual probe coroutines ───────────────────────────────────────────────

async def _probe_device() -> dict[str, Any]:
    """Check the most-recently-seen watcher against the online window.
    Queries the DB directly — no external call.

    Uses `last_seen` (DateTime, set by heartbeat API) falling back to
    `last_connected_at` (set by xiaozhi-server on WS connect) for freshness.
    """
    import datetime as dt

    t0 = time.monotonic()
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                text(
                    "SELECT mac_address, agent_id, last_seen, last_connected_at "
                    "FROM ai_device "
                    "ORDER BY COALESCE(last_seen, last_connected_at) DESC "
                    "LIMIT 1"
                )
            )
            row = result.first()
        ms = (time.monotonic() - t0) * 1000

        if row is None:
            return _hop("device", "Device / Watcher", WARN, "no devices registered", ms)

        mac = row[0] or "?"
        agent_id = row[1]
        # Prefer heartbeat last_seen; fall back to WS last_connected_at
        ts: dt.datetime | None = row[2] or row[3]

        if ts is not None:
            # Both columns are naive UTC datetimes from the DB
            now_utc = dt.datetime.utcnow()
            age_s = (now_utc - ts).total_seconds()
        else:
            age_s = None

        window = settings.watcher_online_window_seconds
        if age_s is not None and age_s <= window:
            detail = f"{mac} online ({int(age_s)}s ago)"
            if agent_id:
                detail += f" — agent {agent_id}"
            return _hop("device", "Device / Watcher", OK, detail, ms)
        elif age_s is not None:
            detail = f"{mac} last seen {int(age_s)}s ago (window {window}s — offline)"
            return _hop("device", "Device / Watcher", WARN, detail, ms)
        else:
            return _hop("device", "Device / Watcher", WARN, f"{mac} — no heartbeat timestamp", ms)

    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("device", "Device / Watcher", FAIL, f"db error: {exc}", ms)


async def _probe_router() -> dict[str, Any]:
    """Router/Wi-Fi hop — not directly probeable from the server container.
    Reported as informational (warn) always."""
    return _hop(
        "router",
        "Router / Wi-Fi",
        WARN,
        "not probeable from server — check via watcher RSSI in heartbeat",
        None,
    )


async def _probe_ota(client: httpx.AsyncClient) -> dict[str, Any]:
    url = f"http://{settings.xiaozhi_server_host}:{settings.xiaozhi_ota_port}/xiaozhi/ota/"
    t0 = time.monotonic()
    try:
        r = await client.get(url)
        ms = (time.monotonic() - t0) * 1000
        if r.status_code < 500:
            return _hop("ota", "OTA Endpoint", OK, f"HTTP {r.status_code} — {url}", ms)
        return _hop("ota", "OTA Endpoint", FAIL, f"HTTP {r.status_code}", ms)
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("ota", "OTA Endpoint", FAIL, str(exc), ms)


async def _probe_mqtt() -> dict[str, Any]:
    """TCP-probe :1883 + optional admin API :8007."""
    t0 = time.monotonic()
    try:
        _r, _w = await asyncio.wait_for(
            asyncio.open_connection(settings.mqtt_host, settings.mqtt_port),
            timeout=settings.health_probe_timeout_s,
        )
        _w.close()
        ms = (time.monotonic() - t0) * 1000
        mqtt_detail = f"TCP:{settings.mqtt_port} reachable"

        # Try admin API — best-effort, don't fail the hop if it's absent
        try:
            admin_url = f"http://{settings.mqtt_host}:{settings.mqtt_admin_port}/"
            async with httpx.AsyncClient(timeout=1.0) as ac:
                ar = await ac.get(admin_url)
            mqtt_detail += f" | admin HTTP {ar.status_code}"
        except Exception:
            mqtt_detail += f" | admin :{settings.mqtt_admin_port} not available"

        return _hop("mqtt", "MQTT Gateway", OK, mqtt_detail, ms)
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("mqtt", "MQTT Gateway", FAIL, str(exc), ms)


async def _probe_ws(client: httpx.AsyncClient) -> dict[str, Any]:
    """HTTP probe of the xiaozhi-server WS port — a 404 or upgrade response
    means the server is up even if WS upgrade itself is refused by httpx."""
    url = f"http://{settings.xiaozhi_server_host}:{settings.xiaozhi_ws_port}/"
    t0 = time.monotonic()
    try:
        r = await client.get(url)
        ms = (time.monotonic() - t0) * 1000
        # A WS server returns 426/400/404 on plain GET — any non-5xx is up
        if r.status_code < 500:
            return _hop("ws", "XiaoZhi WS Server", OK, f"HTTP {r.status_code} on :{settings.xiaozhi_ws_port}", ms)
        return _hop("ws", "XiaoZhi WS Server", FAIL, f"HTTP {r.status_code}", ms)
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("ws", "XiaoZhi WS Server", FAIL, str(exc), ms)


async def _probe_vad_asr(client: httpx.AsyncClient) -> dict[str, Any]:
    """VAD/ASR — proxy through xiaozhi-server health. No dedicated endpoint;
    we verify the server is reachable and report what the OTA probe showed.
    A dedicated ASR model-loaded endpoint could be added to xiaozhi-server later."""
    url = f"http://{settings.xiaozhi_server_host}:{settings.xiaozhi_ota_port}/xiaozhi/ota/"
    t0 = time.monotonic()
    try:
        r = await client.get(url)
        ms = (time.monotonic() - t0) * 1000
        if r.status_code < 500:
            return _hop(
                "vad_asr",
                "VAD / ASR",
                OK,
                "xiaozhi-server reachable (no dedicated model-loaded probe available)",
                ms,
            )
        return _hop("vad_asr", "VAD / ASR", WARN, f"xiaozhi-server HTTP {r.status_code}", ms)
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("vad_asr", "VAD / ASR", FAIL, str(exc), ms)


async def _probe_llm(client: httpx.AsyncClient) -> dict[str, Any]:
    """Ollama: version check + model present + tiny generate latency."""
    t0 = time.monotonic()
    try:
        vr = await client.get(f"{settings.ollama_url}/api/version")
        ms_ver = (time.monotonic() - t0) * 1000
        if vr.status_code != 200:
            return _hop("llm", "LLM (Ollama)", FAIL, f"version HTTP {vr.status_code}", ms_ver)

        version = vr.json().get("version", "?")

        # Check model is present in tags
        tags_r = await client.get(f"{settings.ollama_url}/api/tags")
        target_model = settings.ollama_triage_model
        if tags_r.status_code == 200:
            names = [m.get("name", "") for m in tags_r.json().get("models", [])]
            model_ok = any(n.startswith(target_model.split(":")[0]) for n in names)
        else:
            model_ok = False

        if not model_ok:
            ms = (time.monotonic() - t0) * 1000
            return _hop(
                "llm",
                "LLM (Ollama)",
                WARN,
                f"ollama {version} up but {target_model} not found in /api/tags",
                ms,
            )

        # Tiny generate probe for round-trip latency
        t_gen = time.monotonic()
        gen_r = await client.post(
            f"{settings.ollama_url}/api/generate",
            json={"model": target_model, "prompt": "hi", "stream": False, "options": {"num_predict": 1}},
        )
        gen_ms = (time.monotonic() - t_gen) * 1000
        total_ms = (time.monotonic() - t0) * 1000

        if gen_r.status_code == 200:
            return _hop(
                "llm",
                "LLM (Ollama)",
                OK,
                f"ollama {version} | {target_model} present | generate {gen_ms:.0f}ms",
                total_ms,
            )
        return _hop(
            "llm",
            "LLM (Ollama)",
            WARN,
            f"ollama {version} up, {target_model} found, but generate HTTP {gen_r.status_code}",
            total_ms,
        )

    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("llm", "LLM (Ollama)", FAIL, str(exc), ms)


async def _probe_tts(client: httpx.AsyncClient) -> dict[str, Any]:
    """Multi-engine TTS server: /health endpoint + engine availability + synth latency.

    The TTS server exposes an "engines" object in its /health response:
      {"engines": {"piper": bool, "kokoro": bool, "edge": bool}, ...}

    Status rules:
      OK   — /health 200 AND (kokoro OR piper) is True, synth succeeded
      WARN — /health 200 but only edge is up, or synth returned non-2xx
      FAIL — /health not 200, or connection error
    """
    tts_base = settings.tts_url
    t0 = time.monotonic()
    try:
        hr = await client.get(f"{tts_base}/health")
        ms_health = (time.monotonic() - t0) * 1000
        if hr.status_code != 200:
            return _hop("tts", "TTS", FAIL, f"/health HTTP {hr.status_code}", ms_health)

        health_data: dict = {}
        try:
            health_data = hr.json()
        except Exception:
            pass

        # Parse engine availability from the /health response.
        engines: dict = health_data.get("engines", {}) if isinstance(health_data, dict) else {}
        kokoro_up = bool(engines.get("kokoro", False))
        piper_up = bool(engines.get("piper", False))
        edge_up = bool(engines.get("edge", False))

        engine_parts = []
        for name, up in (("kokoro", kokoro_up), ("piper", piper_up), ("edge", edge_up)):
            engine_parts.append(f"{name}:{'up' if up else 'down'}")
        engine_summary = " ".join(engine_parts) if engine_parts else "engines:unknown"

        # At least one local engine must be up; edge-only is degraded.
        local_engine_up = kokoro_up or piper_up

        # Tiny synth latency probe using whichever voice is the catalog default.
        t_synth = time.monotonic()
        synth_r = await client.post(
            f"{tts_base}/v1/audio/speech",
            json={"input": "ok", "voice": "kokoro:af_heart", "speed": "normal", "response_format": "wav"},
        )
        synth_ms = (time.monotonic() - t_synth) * 1000
        total_ms = (time.monotonic() - t0) * 1000

        synth_ok = synth_r.status_code in (200, 206)

        if local_engine_up and synth_ok:
            status = OK
            detail = f"healthy | {engine_summary} | synth {synth_ms:.0f}ms"
        elif not local_engine_up and edge_up:
            status = WARN
            detail = f"edge-only (kokoro+piper down) | {engine_summary} | synth {synth_ms:.0f}ms"
        elif local_engine_up and not synth_ok:
            status = WARN
            detail = f"{engine_summary} | synth HTTP {synth_r.status_code}"
        else:
            status = WARN
            detail = f"no local engines up | {engine_summary}"

        return _hop("tts", "TTS", status, detail, total_ms)

    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("tts", "TTS", FAIL, str(exc), ms)


async def _probe_db() -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        async with async_session_factory() as db:
            await db.execute(text("SELECT 1"))
        ms = (time.monotonic() - t0) * 1000
        return _hop("db", "Database (MariaDB)", OK, f"SELECT 1 ok — {settings.db_name}@{settings.db_host}", ms)
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("db", "Database (MariaDB)", FAIL, str(exc), ms)


async def _probe_redis() -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        r = await get_redis()
        await r.ping()
        ms = (time.monotonic() - t0) * 1000
        return _hop("redis", "Redis", OK, f"PING ok — {settings.redis_url}", ms)
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("redis", "Redis", FAIL, str(exc), ms)


# ── Main checks endpoint ──────────────────────────────────────────────────────

_CRITICAL_HOPS = {"ota", "ws", "llm", "tts", "db", "redis"}


@router.get("/checks", response_model=None)
async def health_checks() -> dict[str, Any]:
    """Run all pipeline hop probes concurrently and return structured results.

    Returns the envelope-wrapped list:
      {code: 0, msg: "success", data: {hops: [...], overall: "ok"|"warn"|"fail"}}

    Capped at settings.health_total_timeout_s (default 5s) — individual probes
    that exceed settings.health_probe_timeout_s are cancelled and reported as fail.
    """
    timeout = settings.health_probe_timeout_s
    total_timeout = settings.health_total_timeout_s

    # Single shared httpx client for all HTTP probes
    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [
            _probe_device(),
            _probe_router(),
            _probe_ota(client),
            _probe_mqtt(),
            _probe_ws(client),
            _probe_vad_asr(client),
            _probe_llm(client),
            _probe_tts(client),
            _probe_db(),
            _probe_redis(),
        ]

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=total_timeout,
            )
        except asyncio.TimeoutError:
            return envelope(
                {"hops": [], "overall": FAIL},
                code=0,
                msg="health checks timed out",
            )

    hops: list[dict[str, Any]] = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            # A probe coroutine raised an unhandled exception
            task_names = [
                "device", "router", "ota", "mqtt", "ws",
                "vad_asr", "llm", "tts", "db", "redis",
            ]
            key = task_names[i] if i < len(task_names) else f"hop_{i}"
            hops.append(_hop(key, key, FAIL, str(res), None))
        else:
            hops.append(res)

    # End-to-end: green only if all critical hops pass
    critical_failed = [h for h in hops if h["key"] in _CRITICAL_HOPS and h["status"] == FAIL]
    any_warn = any(h["status"] == WARN for h in hops if h["key"] in _CRITICAL_HOPS)

    if critical_failed:
        e2e_status = FAIL
        e2e_detail = f"{len(critical_failed)} critical hop(s) failed: {', '.join(h['key'] for h in critical_failed)}"
    elif any_warn:
        e2e_status = WARN
        e2e_detail = "all critical hops reachable with warnings"
    else:
        e2e_status = OK
        e2e_detail = "all critical hops passed"

    hops.append(_hop("e2e", "End-to-End", e2e_status, e2e_detail, None))

    overall = FAIL if critical_failed else (WARN if any_warn else OK)
    return envelope({"hops": hops, "overall": overall})
