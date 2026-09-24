"""Health-check router — pipeline hop diagnostics.

Exposes two endpoints (no auth required; read-only, no sensitive data):

  GET /api/health/checks
    Returns [{key, label, status, detail, latency_ms}, ...] for every hop
    in the voice pipeline. Probes run concurrently with a hard timeout so
    the dashboard never hangs.

  GET /readyz
    Lightweight three-signal liveness: DB + Redis + Ollama.  Mounted at the
    root (not under /api) to stay outside the envelope middleware.

Hop order follows the physical path a voice turn travels:
  device → router/wifi → ota → mqtt → ws → vad/asr → llm → tts → db → redis → e2e

Status vocabulary:
  ok   — confirmed healthy
  warn — degraded or expected component unavailable (e.g. Watcher offline)
  fail — this hop's service failed
  info — not directly probeable; never counts toward End-to-End
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from ..db import async_session_factory
from ..envelope import envelope
from ..pubsub import get_redis
from ..settings import settings


router = APIRouter(prefix="/health", tags=["health"])

OK = "ok"
WARN = "warn"
FAIL = "fail"
INFO = "info"

# Voice-pipeline hops: a FAIL here makes End-to-End fail.
# API itself is implied (this endpoint is served by it).
_CRITICAL_HOPS = frozenset({"ws", "llm", "tts", "db"})

# Service-down is real but not a voice-pipeline outage.
# MQTT is LAN-only (remote Watchers use WebSocket). OTA is provisioning.
# Redis is dashboard live-push. VAD/ASR has no model-loaded endpoint.
# Those hops fail their own row but do not fail End-to-End.

_LLM_LABELS = {
    "qwen2.5:3b": "LLM — Qwen 2.5 3B",
    "qwen2.5:7b": "LLM — Qwen 2.5 7B",
}


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


def human_age(seconds: float | None) -> str:
    """24474s → '6h 47m ago'. None → 'never'."""
    if seconds is None:
        return "never"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        m = s // 60
        return f"{m}m ago"
    if s < 86400:
        h = s // 3600
        m = (s % 3600) // 60
        return f"{h}h {m}m ago" if m else f"{h}h ago"
    d = s // 86400
    h = (s % 86400) // 3600
    return f"{d}d {h}h ago" if h else f"{d}d ago"


def llm_hop_label(model: str) -> str:
    mid = (model or "").strip()
    return _LLM_LABELS.get(mid, f"LLM — {mid or 'unconfigured'}")


def llm_short_name(model: str) -> str:
    mid = (model or "").strip()
    label = _LLM_LABELS.get(mid)
    if label:
        return label.replace("LLM — ", "", 1)
    return mid or "unconfigured"


def model_is_listed(names: list[str], target: str) -> bool:
    """True if Ollama /api/tags includes the configured model id.

    Matches exact ``qwen2.5:3b`` and quantized variants ``qwen2.5:3b-q4_K_M``.
    Does not treat ``qwen2.5:7b`` as a hit for ``qwen2.5:3b``.
    """
    want = (target or "").strip()
    if not want:
        return False
    for raw in names:
        name = (raw or "").strip()
        if not name:
            continue
        if name == want or name.startswith(want + "-"):
            return True
        if name.startswith(want + ":"):
            return True
    return False


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _age_seconds(ts: Any, now: datetime) -> float | None:
    ts_dt = _as_dt(ts)
    if ts_dt is None:
        return None
    if ts_dt.tzinfo is not None and now.tzinfo is None:
        ts_dt = ts_dt.replace(tzinfo=None)
    elif ts_dt.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    return (now - ts_dt).total_seconds()


# ── Individual probe coroutines ───────────────────────────────────────────────

async def _probe_device() -> dict[str, Any]:
    """Count registered Watchers. Offline is warn, not a Nexus failure.

    Liveness is COALESCE(last_seen, last_connected_at) vs the online window.
    W1-A firmware does not POST heartbeat; WS last_connected_at is the fallback.
    """
    t0 = time.monotonic()
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                text(
                    "SELECT mac_address, agent_id, last_seen, last_connected_at, rssi "
                    "FROM ai_device"
                )
            )
            rows = result.fetchall()
        ms = (time.monotonic() - t0) * 1000
        now = datetime.utcnow()
        window = settings.watcher_online_window_seconds
        registered = len(rows)
        if registered == 0:
            return _hop("device", "Device / Watcher", WARN, "No Watchers registered", ms)

        online = 0
        freshest: tuple[float, Any] | None = None
        for row in rows:
            ts = row[2] or row[3]
            age = _age_seconds(ts, now)
            if age is not None and age <= window:
                online += 1
            if age is not None and (freshest is None or age < freshest[0]):
                freshest = (age, row)

        count = f"{online} online / {registered} registered"
        if online > 0:
            return _hop("device", "Device / Watcher", OK, count, ms)

        last = f"last seen {human_age(freshest[0])}" if freshest else "no last-seen timestamp"
        return _hop(
            "device",
            "Device / Watcher",
            WARN,
            f"Offline — {count}; {last}",
            ms,
        )
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("device", "Device / Watcher", FAIL, f"db error: {type(exc).__name__}", ms)


async def _probe_router() -> dict[str, Any]:
    """Not probeable from the API container. RSSI is device-side telemetry."""
    t0 = time.monotonic()
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                text(
                    "SELECT rssi, last_seen, last_connected_at FROM ai_device "
                    "WHERE rssi IS NOT NULL "
                    "ORDER BY COALESCE(last_seen, last_connected_at) DESC "
                    "LIMIT 1"
                )
            )
            row = result.first()
        ms = (time.monotonic() - t0) * 1000
        if row is not None and row[0] is not None:
            return _hop(
                "router",
                "Router / Wi-Fi",
                INFO,
                f"Device-side signal check — Wi-Fi signal: {int(row[0])} dBm",
                ms,
            )
        return _hop(
            "router",
            "Router / Wi-Fi",
            INFO,
            "Device-side signal check — not currently measurable from Nexus",
            None,
        )
    except Exception:
        return _hop(
            "router",
            "Router / Wi-Fi",
            INFO,
            "Device-side signal check — not currently measurable from Nexus",
            None,
        )


async def _probe_ota(client: httpx.AsyncClient) -> dict[str, Any]:
    """Internal :8003 is authoritative. Public hostname is display-only."""
    url = f"http://{settings.xiaozhi_server_host}:{settings.xiaozhi_ota_port}/xiaozhi/ota/"
    public = (settings.ota_public_url or "").rstrip("/") + "/"
    t0 = time.monotonic()
    try:
        r = await client.get(url)
        ms = (time.monotonic() - t0) * 1000
        extra = f" · public {public}" if public.startswith("https://") else ""
        if r.status_code < 500:
            return _hop(
                "ota",
                "OTA Endpoint",
                OK,
                f"internal :{settings.xiaozhi_ota_port} ok{extra}",
                ms,
            )
        return _hop("ota", "OTA Endpoint", FAIL, f"internal HTTP {r.status_code}", ms)
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("ota", "OTA Endpoint", FAIL, f"internal unreachable ({type(exc).__name__})", ms)


async def _probe_mqtt() -> dict[str, Any]:
    """TCP :1883 only. The gateway has no /health and GET / is a 404."""
    t0 = time.monotonic()
    try:
        _r, _w = await asyncio.wait_for(
            asyncio.open_connection(settings.mqtt_host, settings.mqtt_port),
            timeout=settings.health_probe_timeout_s,
        )
        _w.close()
        ms = (time.monotonic() - t0) * 1000
        return _hop(
            "mqtt",
            "MQTT Gateway",
            OK,
            f"broker :{settings.mqtt_port} reachable",
            ms,
        )
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop(
            "mqtt",
            "MQTT Gateway",
            FAIL,
            f"broker :{settings.mqtt_port} unreachable ({type(exc).__name__})",
            ms,
        )


async def _probe_ws(client: httpx.AsyncClient) -> dict[str, Any]:
    """Plain GET on :8000 hits websockets process_request → 'Server is running'."""
    url = f"http://{settings.xiaozhi_server_host}:{settings.xiaozhi_ws_port}/"
    t0 = time.monotonic()
    try:
        r = await client.get(url)
        ms = (time.monotonic() - t0) * 1000
        body = (r.text or "").strip()
        if r.status_code == 200:
            return _hop(
                "ws",
                "XiaoZhi WS Server",
                OK,
                f"handshake ok — {body or 'HTTP 200'} (:{settings.xiaozhi_ws_port})",
                ms,
            )
        if r.status_code < 500:
            return _hop(
                "ws",
                "XiaoZhi WS Server",
                WARN,
                f"HTTP {r.status_code} on :{settings.xiaozhi_ws_port} (WS process up)",
                ms,
            )
        return _hop("ws", "XiaoZhi WS Server", FAIL, f"HTTP {r.status_code}", ms)
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("ws", "XiaoZhi WS Server", FAIL, f"unreachable ({type(exc).__name__})", ms)


async def _probe_vad_asr(client: httpx.AsyncClient) -> dict[str, Any]:
    """VAD/ASR live inside the XiaoZhi WS process. No model-loaded endpoint."""
    url = f"http://{settings.xiaozhi_server_host}:{settings.xiaozhi_ws_port}/"
    t0 = time.monotonic()
    try:
        r = await client.get(url)
        ms = (time.monotonic() - t0) * 1000
        if r.status_code < 500:
            return _hop(
                "vad_asr",
                "VAD / ASR",
                OK,
                "Service reachable — Silero VAD / Sherpa ASR model-specific probe unavailable",
                ms,
            )
        return _hop("vad_asr", "VAD / ASR", WARN, f"XiaoZhi HTTP {r.status_code}", ms)
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("vad_asr", "VAD / ASR", FAIL, f"service unreachable ({type(exc).__name__})", ms)


async def _probe_llm(client: httpx.AsyncClient) -> dict[str, Any]:
    """Conversational model via host Ollama. Never probes the triage model."""
    base = (settings.ollama_url or "").rstrip("/")
    model = (settings.llm_model or "").strip()
    label = llm_hop_label(model)
    t0 = time.monotonic()
    if not base or not model:
        return _hop("llm", label, FAIL, "LLM runtime unconfigured", 0)

    try:
        vr = await client.get(f"{base}/api/version")
        if vr.status_code != 200:
            ms = (time.monotonic() - t0) * 1000
            return _hop("llm", label, FAIL, "LLM runtime unreachable", ms)

        tags_r = await client.get(f"{base}/api/tags")
        names: list[str] = []
        if tags_r.status_code == 200:
            try:
                names = [m.get("name", "") for m in (tags_r.json().get("models") or [])]
            except Exception:
                names = []
        if not model_is_listed(names, model):
            ms = (time.monotonic() - t0) * 1000
            return _hop(
                "llm",
                label,
                FAIL,
                f"Ollama reachable; {model} unavailable",
                ms,
            )

        gen_ms = None
        try:
            t_gen = time.monotonic()
            gen_r = await client.post(
                f"{base}/api/generate",
                json={
                    "model": model,
                    "prompt": "Reply with the single word OK.",
                    "stream": False,
                    "options": {"num_predict": 1},
                },
                timeout=2.0,
            )
            gen_ms = (time.monotonic() - t_gen) * 1000
            total_ms = (time.monotonic() - t0) * 1000
            if gen_r.status_code == 200:
                return _hop(
                    "llm",
                    label,
                    OK,
                    f"{llm_short_name(model)} reachable via Ollama",
                    total_ms,
                )
            return _hop(
                "llm",
                label,
                WARN,
                f"Ollama reachable; {model} listed, generate HTTP {gen_r.status_code}",
                total_ms,
            )
        except Exception:
            total_ms = (time.monotonic() - t0) * 1000
            extra = f" ({gen_ms:.0f}ms)" if gen_ms is not None else ""
            return _hop(
                "llm",
                label,
                WARN,
                f"Ollama reachable; {model} listed, no reply yet{extra}",
                total_ms,
            )
    except Exception:
        ms = (time.monotonic() - t0) * 1000
        return _hop("llm", label, FAIL, "LLM runtime unreachable", ms)


async def _probe_tts(client: httpx.AsyncClient) -> dict[str, Any]:
    """Multi-engine TTS at CC_TTS_URL (piper-tts:5500). Edge-only is warn."""
    tts_base = (settings.tts_url or "").rstrip("/")
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

        engines: dict = health_data.get("engines", {}) if isinstance(health_data, dict) else {}
        kokoro_up = bool(engines.get("kokoro", False))
        piper_up = bool(engines.get("piper", False))
        edge_up = bool(engines.get("edge", False))

        engine_parts = []
        for name, up in (("kokoro", kokoro_up), ("piper", piper_up), ("edge", edge_up)):
            engine_parts.append(f"{name}:{'up' if up else 'down'}")
        engine_summary = " ".join(engine_parts) if engine_parts else "engines:unknown"
        local_engine_up = kokoro_up or piper_up

        if kokoro_up:
            voice = "kokoro:af_heart"
        elif piper_up:
            voice = "piper:en_US-hfc_female-medium"
        else:
            voice = "edge:en-US-AvaNeural"

        t_synth = time.monotonic()
        synth_r = await client.post(
            f"{tts_base}/v1/audio/speech",
            json={"input": "ok", "voice": voice, "speed": "normal", "response_format": "wav"},
        )
        synth_ms = (time.monotonic() - t_synth) * 1000
        total_ms = (time.monotonic() - t0) * 1000
        synth_ok = synth_r.status_code in (200, 206)

        if local_engine_up and synth_ok:
            status = OK
            detail = f"healthy | {engine_summary} | synth {synth_ms:.0f}ms"
        elif not local_engine_up and edge_up:
            status = WARN
            detail = f"edge-only (kokoro+piper down) | {engine_summary}"
        elif local_engine_up and not synth_ok:
            status = WARN
            detail = f"{engine_summary} | synth HTTP {synth_r.status_code}"
        else:
            status = WARN
            detail = f"no local engines up | {engine_summary}"

        return _hop("tts", "TTS", status, detail, total_ms)
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("tts", "TTS", FAIL, f"unreachable ({type(exc).__name__})", ms)


async def _probe_db() -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        async with async_session_factory() as db:
            await db.execute(text("SELECT 1"))
        ms = (time.monotonic() - t0) * 1000
        return _hop(
            "db",
            "Database (MariaDB)",
            OK,
            f"SELECT 1 ok — {settings.db_name}@{settings.db_host}",
            ms,
        )
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("db", "Database (MariaDB)", FAIL, type(exc).__name__, ms)


def _redis_target(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "redis"
        port = parsed.port or 6379
        return f"{host}:{port}"
    except Exception:
        return "redis"


async def _probe_redis() -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        r = await get_redis()
        await r.ping()
        ms = (time.monotonic() - t0) * 1000
        return _hop("redis", "Redis", OK, f"PING ok — {_redis_target(settings.redis_url)}", ms)
    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        return _hop("redis", "Redis", FAIL, type(exc).__name__, ms)


# ── Main checks endpoint ──────────────────────────────────────────────────────

_TASK_KEYS = [
    "device", "router", "ota", "mqtt", "ws",
    "vad_asr", "llm", "tts", "db", "redis",
]


def _e2e_and_overall(hops: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    critical_failed = [h for h in hops if h["key"] in _CRITICAL_HOPS and h["status"] == FAIL]
    critical_warn = [h for h in hops if h["key"] in _CRITICAL_HOPS and h["status"] == WARN]
    non_info = [h for h in hops if h["status"] != INFO]

    if critical_failed:
        e2e_status = FAIL
        e2e_detail = (
            f"{len(critical_failed)} critical hop(s) failed: "
            + ", ".join(h["key"] for h in critical_failed)
        )
    elif critical_warn:
        e2e_status = WARN
        e2e_detail = "critical hops reachable with warnings"
    else:
        e2e_status = OK
        e2e_detail = "all critical hops passed"

    any_fail = any(h["status"] == FAIL for h in non_info)
    any_warn = any(h["status"] == WARN for h in non_info)
    if critical_failed:
        overall = FAIL
    elif any_fail or any_warn:
        overall = WARN
    else:
        overall = OK
    return _hop("e2e", "End-to-End", e2e_status, e2e_detail, None), overall


@router.get("/checks", response_model=None)
async def health_checks() -> dict[str, Any]:
    """Run all pipeline hop probes concurrently and return structured results."""
    timeout = settings.health_probe_timeout_s
    total_timeout = settings.health_total_timeout_s

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
            key = _TASK_KEYS[i] if i < len(_TASK_KEYS) else f"hop_{i}"
            hops.append(_hop(key, key, FAIL, type(res).__name__, None))
        else:
            hops.append(res)

    e2e, overall = _e2e_and_overall(hops)
    hops.append(e2e)
    return envelope({"hops": hops, "overall": overall})
