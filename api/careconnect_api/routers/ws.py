"""WebSocket fan-out for chat turns + assessment updates.

Wire layout
-----------

Client (the Next.js dashboard's Client Detail view) opens::

    wss://host/ws/agent/{agent_id}?token=<jwt>

The handshake is *not* a normal HTTP request — browser ``WebSocket`` can't
set ``Authorization`` headers, so we accept the JWT in the query string
instead. The token is validated against :func:`careconnect_api.auth.decode_token`
and the user's RBAC is checked with
:func:`careconnect_api.rbac.assert_can_access_agent` before the
``websocket.accept()`` ever fires.

After the handshake we send a ``hello`` envelope with the server time
(handy for client-side clock skew checks) then enter the fan-out loop:

* Subscribe to the agent's two pub/sub channels:
  - ``cc:agent:{agent_id}:chat``       — emitted by the bridge after each
    chat-history insert via ``/api/internal/notify/chat-turn``
  - ``cc:agent:{agent_id}:assessment`` — emitted by the triage runner when
    a new ``ai_medical_assessment`` row lands
* Forward every Redis message verbatim to the WebSocket — the Redis
  publisher already wraps payloads in ``{type, payload}``.
* Heartbeat every 25 s with ``{type:"ping"}`` to keep idle proxies open
  and to detect dead clients (a failed send raises and we tear down).
* On client disconnect we cancel the reader/writer pair, unsubscribe, and
  close the PubSub object.

The envelope middleware ignores non-/api routes, so /ws/* responses (the
HTTP 101 upgrade) pass through untouched.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from ..auth import CurrentUser, decode_token
from ..db import async_session_factory
from ..models import SysUser
from ..pubsub import assessment_channel, chat_channel, get_redis
from ..rbac import assert_can_access_agent


log = logging.getLogger("ws")

router = APIRouter(tags=["ws"])


HEARTBEAT_INTERVAL_S = 25.0


# ---------- handshake helpers ----------

async def _resolve_user(token: str) -> CurrentUser:
    """Decode the JWT and load the user row, mirroring :func:`auth.get_current_user`
    but without FastAPI's Header-based dep (we got the token from the query
    string, not a Bearer header)."""
    payload = decode_token(token)  # raises jwt.PyJWTError on bad/expired
    user_id = int(payload["sub"])
    async with async_session_factory() as session:
        user = (
            await session.execute(select(SysUser).where(SysUser.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError("user not found")
        return CurrentUser(id=user.id, username=user.username, role=int(user.super_admin or 0))


async def _check_access(user: CurrentUser, agent_id: str) -> None:
    """Open a fresh session for the RBAC check so we don't hold one for the
    entire WS lifetime."""
    async with async_session_factory() as session:
        await assert_can_access_agent(session, user, agent_id)


# ---------- the endpoint ----------

@router.websocket("/ws/agent/{agent_id}")
async def agent_ws(
    websocket: WebSocket,
    agent_id: str,
    token: str | None = Query(default=None),
):
    """Per-agent fan-out subscriber. One WS per browser tab; multiple tabs
    open the same agent independently (Redis ``PUBLISH`` delivers to every
    subscriber so this fans out for free)."""
    # ----- handshake auth (BEFORE accept) -----
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="missing token")
        return

    try:
        user = await _resolve_user(token)
    except jwt.ExpiredSignatureError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="token expired")
        return
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        log.info("ws auth reject agent=%s: %s", agent_id, exc)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid token")
        return

    try:
        await _check_access(user, agent_id)
    except Exception as exc:
        # rbac raises APIException(403); also catch unexpected DB errors so
        # we don't leak a half-open WS.
        log.info("ws rbac reject user=%s agent=%s: %s", user.username, agent_id, exc)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="forbidden")
        return

    await websocket.accept()
    log.info("ws accept user=%s role=%s agent=%s", user.username, user.role_name, agent_id)

    # ----- subscribe -----
    redis_client = await get_redis()
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(chat_channel(agent_id), assessment_channel(agent_id))

    # Hello envelope so the client knows the WS is fully wired (subscribe is
    # async — without this the client can't tell "still connecting" from
    # "connected but agent is just quiet").
    try:
        await websocket.send_json({
            "type": "hello",
            "payload": {
                "agentId": agent_id,
                "serverTs": int(time.time() * 1000),
                "channels": [chat_channel(agent_id), assessment_channel(agent_id)],
            },
        })
    except Exception:
        # Client vanished between accept() and the hello send.
        with contextlib.suppress(Exception):
            await pubsub.aclose()
        return

    # ----- fan-out loop -----
    stop_event = asyncio.Event()

    async def reader() -> None:
        """Forward Redis messages to the WS. Each message from PubSub is
        already a JSON-encoded ``{type, payload}`` envelope from
        :mod:`careconnect_api.pubsub`, so we send it through ``send_text``
        verbatim — no double-encoding."""
        try:
            async for msg in pubsub.listen():
                if stop_event.is_set():
                    break
                if msg.get("type") != "message":
                    # 'subscribe' / 'unsubscribe' acks — ignore
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                if not isinstance(data, str):
                    continue
                await websocket.send_text(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.info("ws reader exit agent=%s: %s", agent_id, exc)
            stop_event.set()

    async def heartbeat() -> None:
        """Periodic ping — also our liveness probe. ``send_json`` on a dead
        socket raises, which trips ``stop_event`` and tears the loop down."""
        try:
            while not stop_event.is_set():
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
                if stop_event.is_set():
                    break
                await websocket.send_json({
                    "type": "ping",
                    "payload": {"serverTs": int(time.time() * 1000)},
                })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.info("ws heartbeat exit agent=%s: %s", agent_id, exc)
            stop_event.set()

    async def client_watcher() -> None:
        """Drain client-sent frames so a Disconnect is noticed promptly. The
        dashboard doesn't speak back to us today, but anything the client
        sends still has to be receive()'d for Starlette to surface the
        disconnect event."""
        try:
            while not stop_event.is_set():
                # receive() raises WebSocketDisconnect when the client closes
                await websocket.receive_text()
        except WebSocketDisconnect:
            stop_event.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.info("ws client_watcher exit agent=%s: %s", agent_id, exc)
            stop_event.set()

    reader_task = asyncio.create_task(reader())
    heartbeat_task = asyncio.create_task(heartbeat())
    watcher_task = asyncio.create_task(client_watcher())

    try:
        # Wait until any of the three exits (disconnect, send error, etc.)
        await stop_event.wait()
    finally:
        for t in (reader_task, heartbeat_task, watcher_task):
            t.cancel()
        for t in (reader_task, heartbeat_task, watcher_task):
            with contextlib.suppress(BaseException):
                await t
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(chat_channel(agent_id), assessment_channel(agent_id))
        with contextlib.suppress(Exception):
            await pubsub.aclose()
        with contextlib.suppress(Exception):
            await websocket.close()
        log.info("ws close user=%s agent=%s", user.username, agent_id)
