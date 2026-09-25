"""Read-only Revel Digital HTTP client.

Mutations are compiled in but refused until execute is explicitly enabled
after discovery approval. Voice/command code must not call ``apply_device_tags``.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .envelope import APIException
from .revel_config import DEFAULT_API_BASE, EXECUTE_ENABLED, tags_from_devices

log = logging.getLogger("revel_client")

_TIMEOUT_S = 15.0


class RevelMutationDisabled(RuntimeError):
    """Raised if a caller tries to mutate Revel while execute is off."""


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-RevelDigital-ApiKey": api_key,
    }


def _base(url: str | None) -> str:
    return (url or DEFAULT_API_BASE).rstrip("/")


def _normalize_tags(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    if isinstance(raw, str):
        parts = raw.replace(",", "\n").split("\n")
        return [p.strip() for p in parts if p.strip()]
    return []


def _normalize_device(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    device_id = str(raw.get("id") or raw.get("deviceId") or "").strip()
    if not device_id:
        return None
    name = str(raw.get("name") or raw.get("deviceName") or device_id).strip()
    online = raw.get("isOnline")
    if online is None:
        online = raw.get("is_online")
    if online is None:
        online = raw.get("online")
    if not isinstance(online, bool):
        online = None
    return {
        "id": device_id,
        "name": name,
        "isOnline": online,
        "tags": _normalize_tags(raw.get("tags")),
        "registrationKeySet": bool(raw.get("registrationKey") or raw.get("registration_key")),
    }


def _extract_device_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "devices", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict) and isinstance(val.get("device"), list):
                return val["device"]
        if isinstance(payload.get("device"), list):
            return payload["device"]
    return []


async def list_devices(api_key: str, api_base_url: str | None = None) -> list[dict[str, Any]]:
    """GET /devices then GraphQL ``device`` fallback. Never logs the API key."""
    if not api_key.strip():
        raise APIException(400, "Revel API key is missing")
    base = _base(api_base_url)
    headers = _headers(api_key.strip())
    devices: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(f"{base}/devices", headers=headers)
    except httpx.HTTPError:
        log.warning("revel list devices transport failed host=%s", url_host(base))
        raise APIException(502, "Could not reach Revel")
    if resp.status_code in (401, 403):
        log.warning("revel auth failed status=%s", resp.status_code)
        raise APIException(401, "Revel rejected the API key")
    if resp.status_code >= 400:
        log.warning("revel list devices failed status=%s", resp.status_code)
        raise APIException(502, "Could not list Revel devices")
    try:
        payload = resp.json()
    except Exception:
        payload = None
    for item in _extract_device_list(payload):
        norm = _normalize_device(item)
        if norm:
            devices.append(norm)

    if not devices:
        gql = await _graphql_devices(api_key, base)
        devices = gql

    log.info("revel discover devices=%s host=%s", len(devices), url_host(base))
    return devices


async def _graphql_devices(api_key: str, base: str) -> list[dict[str, Any]]:
    query = (
        "{ device(limit: 50) { id name isOnline tags "
        "pingData { timestamp } } }"
    )
    headers = _headers(api_key.strip())
    url = f"{base}/graphql"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(url, headers=headers, json={"query": query})
    except httpx.HTTPError:
        log.warning("revel graphql devices transport failed host=%s", url_host(base))
        return []
    if resp.status_code >= 400:
        log.warning("revel graphql devices failed status=%s", resp.status_code)
        return []
    try:
        payload = resp.json()
    except Exception:
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("device") if isinstance(data, dict) else None
    out: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for item in rows:
            norm = _normalize_device(item)
            if norm:
                out.append(norm)
    return out


async def get_device(
    api_key: str, device_id: str, api_base_url: str | None = None
) -> dict[str, Any] | None:
    """GET /devices/{id} — read-only verification helper. Not used to mutate."""
    if not api_key.strip() or not device_id.strip():
        return None
    base = _base(api_base_url)
    headers = _headers(api_key.strip())
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(f"{base}/devices/{device_id.strip()}", headers=headers)
    except httpx.HTTPError:
        log.warning("revel get device transport failed")
        return None
    if resp.status_code >= 400:
        log.warning("revel get device failed status=%s", resp.status_code)
        return None
    try:
        return _normalize_device(resp.json())
    except Exception:
        return None


def apply_device_tags(
    api_key: str,
    device_id: str,
    tags: list[str],
    api_base_url: str | None = None,
) -> None:
    """Intentionally disabled. Do not call from the voice path."""
    _ = (api_key, device_id, tags, api_base_url)
    if not EXECUTE_ENABLED:
        raise RevelMutationDisabled("revel mutations are disabled")
    raise RevelMutationDisabled("revel mutations are disabled")


def url_host(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).netloc or "unknown"
    except Exception:
        return "unknown"


def discover_summary(devices: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "deviceCount": len(devices),
        "devices": devices,
        "tags": tags_from_devices(devices),
    }
