"""Best-effort live Watcher session close + per-device RAG forget.

Used by the internal HTTP control path so CareConnect unbind/delete can drop
a live WebSocket and Chroma collection without touching firmware, Wi-Fi, OTA,
or MCP.

This module must only close the matching socket and delete MAC-keyed memory.
It must not send any application payload on the connection.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger("device_session")

_HEX = re.compile(r"[^0-9a-fA-F]")
_ROLE_SAFE = re.compile(r"[^A-Za-z0-9_-]")

DEFAULT_CHROMA_PATH = os.path.expanduser("~/.local/share/careconnect/chroma")


def stripped_hex(raw: str | None) -> str:
    return _HEX.sub("", raw or "").lower()


def identity_keys(*values: str | None) -> set[str]:
    """Comparable forms of a Watcher MAC / device-id / canonical PK."""
    keys: set[str] = set()
    for raw in values:
        if raw is None:
            continue
        s = str(raw).strip()
        if not s:
            continue
        keys.add(s)
        keys.add(s.lower())
        keys.add(s.upper())
        hx = stripped_hex(s)
        if hx:
            keys.add(hx)
            keys.add(hx.upper())
            keys.add(f"watcher-{hx[:24]}")
            if len(hx) >= 12:
                colon = ":".join(hx[i : i + 2] for i in range(0, min(len(hx), 12), 2))
                keys.add(colon)
                keys.add(colon.upper())
                keys.add(colon.lower())
        lower = s.lower()
        if lower.startswith("watcher-"):
            keys.add(lower)
            rest = lower[len("watcher-") :]
            if rest:
                keys.add(rest)
                keys.add(stripped_hex(rest))
    return {k for k in keys if k}


def _sanitize_role_id(role_id: str) -> str:
    return _ROLE_SAFE.sub("_", str(role_id))[:60] or "default"


def chroma_collection_names(*role_ids: str | None) -> set[str]:
    """Chroma collection names that could be keyed to this physical Watcher."""
    names: set[str] = set()
    for key in identity_keys(*role_ids):
        names.add(f"agent_{_sanitize_role_id(key)}")
    return names


def resolve_chroma_path(config: Optional[Dict[str, Any]] = None) -> str:
    block: Dict[str, Any] = {}
    if isinstance(config, dict):
        memory = config.get("Memory") or {}
        if isinstance(memory, dict):
            raw = memory.get("mem_local_vector") or {}
            if isinstance(raw, dict):
                block = raw
    path = block.get("path") if block else None
    return os.path.expanduser(path or DEFAULT_CHROMA_PATH)


def forget_device_collections(
    *,
    mac: str | None = None,
    device_id: str | None = None,
    persist_path: str | None = None,
    chroma_client: Any = None,
    config: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Delete per-device mem_local_vector collections. Best-effort."""
    names = chroma_collection_names(mac, device_id)
    if not names:
        return []

    client = chroma_client
    if client is None:
        path = persist_path or resolve_chroma_path(config)
        if not os.path.isdir(path):
            log.info("device memory forget: no chroma directory at %s", path)
            return []
        try:
            import chromadb  # lazy: not required to close a live socket

            client = chromadb.PersistentClient(path=path)
        except Exception as exc:
            log.warning("device memory forget: cannot open chroma (%s): %s", path, exc)
            return []

    existing: set[str] = set()
    try:
        listed = client.list_collections()
        existing = {
            str(getattr(item, "name", item) if not isinstance(item, str) else item)
            for item in (listed or [])
            if item
        }
    except Exception:
        existing = set()

    removed: List[str] = []
    for name in sorted(names):
        if existing and name not in existing:
            continue
        try:
            client.delete_collection(name)
            removed.append(name)
        except Exception as exc:
            log.debug("device memory forget: skip %s: %s", name, exc)
    if removed:
        log.info(
            "device memory forgot collections=%s mac=%r device_id=%r",
            removed,
            mac,
            device_id,
        )
    return removed


def handler_matches(handler: Any, *ids: str | None) -> bool:
    conn_id = getattr(handler, "device_id", None)
    if not conn_id:
        return False
    wanted = identity_keys(*ids)
    if not wanted:
        return False
    return bool(identity_keys(conn_id) & wanted)


async def close_handler_websocket(handler: Any) -> None:
    """Close the live socket only. Never send a control payload."""
    ws = getattr(handler, "websocket", None)
    if ws is None:
        return
    await ws.close()


async def close_live_sessions(
    ws_server: Any,
    *,
    mac: str | None = None,
    device_id: str | None = None,
) -> int:
    """Find ``WebSocketServer.active_connections`` and close matches.

    Failures on a single socket are logged and skipped so the caller can
    still finish the server-side unbind/delete.
    """
    if ws_server is None:
        return 0
    conns: Iterable[Any] = getattr(ws_server, "active_connections", None) or ()
    closed = 0
    for handler in list(conns):
        if not handler_matches(handler, mac, device_id):
            continue
        try:
            await close_handler_websocket(handler)
            closed += 1
        except Exception as exc:
            log.warning(
                "live Watcher close failed device_id=%r: %s",
                getattr(handler, "device_id", None),
                exc,
            )
    return closed
