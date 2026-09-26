"""Nexus-system Revel Digital client — Phase 1 read-only device discovery.

This is separate from per-client Revel credentials in ``cc_client_integration``.
The LLM / external callers never supply GraphQL text, REST paths, or command
names. The only GraphQL document this module will POST is ``DEVICES_QUERY``.

Writes (Data Table rows, ``sendDeviceCommand``) are not implemented here.
Data Table discovery lives in ``revel_datatables`` (read-only).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .envelope import APIException
from .revel_client import (
    AUTH_HEADER,
    key_shape,
    sanitize_revel_api_key,
    url_host,
    _safe_revel_error,
)

log = logging.getLogger("revel_signage")

DEFAULT_API_BASE = "https://api.reveldigital.com"
DEFAULT_GRAPHQL_URL = "https://api.reveldigital.com/graphql"
DEFAULT_API_KEY_FILE = Path("/run/secrets/revel-api-key")

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_DEVICE_LIMIT = 100
_META_DROP = {
    "apiKey",
    "api_key",
    "secret",
    "registrationKey",
    "registration_key",
    "registrationKeySet",
}

# Official Revel GraphQL ``device`` query (Devices_View). Allowlisted constant —
# never concatenated with request input.
# Docs: https://developer.reveldigital.com/graphql/
DEVICES_QUERY = (
    "{ device(limit: "
    + str(_DEVICE_LIMIT)
    + ") { id name isOnline tags "
    "deviceType { name manufacturer } "
    "pingData { cpuUsage memoryUsage diskUsage playerVersion ipAddress timestamp } "
    "location { city state country latitude longitude } } }"
)


class RevelSignageSettings(BaseSettings):
    """Nexus-system Revel config from ``REVEL_*`` (also accepts ``CC_REVEL_*``).

    The API key file is read-only and is never created by this process.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        populate_by_name=True,
        env_file=None,
    )

    api_base: str = Field(
        default=DEFAULT_API_BASE,
        validation_alias=AliasChoices("REVEL_API_BASE", "CC_REVEL_API_BASE", "api_base"),
    )
    graphql_url: str = Field(
        default=DEFAULT_GRAPHQL_URL,
        validation_alias=AliasChoices(
            "REVEL_GRAPHQL_URL", "CC_REVEL_GRAPHQL_URL", "graphql_url"
        ),
    )
    api_key_file: Path = Field(
        default=DEFAULT_API_KEY_FILE,
        validation_alias=AliasChoices(
            "REVEL_API_KEY_FILE", "CC_REVEL_API_KEY_FILE", "api_key_file"
        ),
    )
    control_table_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "REVEL_CONTROL_TABLE_ID", "CC_REVEL_CONTROL_TABLE_ID", "control_table_id"
        ),
    )
    default_device_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "REVEL_DEFAULT_DEVICE_ID",
            "CC_REVEL_DEFAULT_DEVICE_ID",
            "default_device_id",
        ),
    )


def load_revel_signage_settings() -> RevelSignageSettings:
    return RevelSignageSettings()


def load_revel_api_key(path: Path | None = None) -> str:
    """Read the developer API key from ``REVEL_API_KEY_FILE``. Never creates it."""
    key_path = Path(path) if path is not None else load_revel_signage_settings().api_key_file
    try:
        if not key_path.is_file():
            return ""
        return sanitize_revel_api_key(key_path.read_text(encoding="utf-8"))
    except OSError:
        log.warning("revel signage api key file unreadable")
        return ""


def _require_https_url(raw: str, *, name: str) -> str:
    text = (raw or "").strip()
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise APIException(400, f"{name} must be an https URL without credentials")
    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme}://{parsed.netloc}{path.rstrip('/')}{query}"


def resolve_graphql_url(cfg: RevelSignageSettings | None = None) -> str:
    cfg = cfg or load_revel_signage_settings()
    explicit = (cfg.graphql_url or "").strip()
    if explicit:
        return _require_https_url(explicit, name="REVEL_GRAPHQL_URL")
    base = _require_https_url(cfg.api_base or DEFAULT_API_BASE, name="REVEL_API_BASE")
    return f"{base}/graphql"


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        AUTH_HEADER: api_key,
    }


def _status(online: Any) -> str:
    if online is True:
        return "online"
    if online is False:
        return "offline"
    if isinstance(online, str):
        low = online.strip().casefold()
        if low in {"online", "offline", "unknown"}:
            return low
        if low in {"true", "1", "yes"}:
            return "online"
        if low in {"false", "0", "no"}:
            return "offline"
    return "unknown"


def _public_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    tags = raw.get("tags")
    if isinstance(tags, list):
        meta["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    elif isinstance(tags, str) and tags.strip():
        meta["tags"] = [p.strip() for p in tags.replace(",", "\n").split("\n") if p.strip()]

    device_type = raw.get("deviceType")
    if isinstance(device_type, dict):
        meta["deviceType"] = {
            "name": device_type.get("name"),
            "manufacturer": device_type.get("manufacturer"),
        }

    ping = raw.get("pingData")
    if isinstance(ping, dict):
        meta["pingData"] = {
            key: ping.get(key)
            for key in (
                "cpuUsage",
                "memoryUsage",
                "diskUsage",
                "playerVersion",
                "ipAddress",
                "timestamp",
            )
            if key in ping
        }

    location = raw.get("location")
    if isinstance(location, dict):
        meta["location"] = {
            key: location.get(key)
            for key in ("city", "state", "country", "latitude", "longitude")
            if key in location
        }

    for dropped in _META_DROP:
        meta.pop(dropped, None)
    return meta


def normalize_signage_device(raw: Any) -> dict[str, Any] | None:
    """Normalize a Revel device into ``{id, name, status, metadata}``."""
    if not isinstance(raw, dict):
        return None
    device_id = str(raw.get("id") or raw.get("deviceId") or "").strip()
    if not _DEVICE_ID_RE.match(device_id):
        return None
    name = str(raw.get("name") or raw.get("deviceName") or device_id).strip()[:128]
    return {
        "id": device_id,
        "name": name or device_id,
        "status": _status(raw.get("isOnline") if "isOnline" in raw else raw.get("status")),
        "metadata": _public_metadata(raw),
    }


def _auth_failed(status: int, body: str | None, *, key: str, url: str) -> APIException:
    revel_error = _safe_revel_error(body)
    log.warning(
        "revel signage auth failed status=%s key_present=%s key_length=%s "
        "key_shape=%s header_name=%s query_api_key=%s host=%s revel_error=%s",
        status,
        bool(key),
        len(key),
        key_shape(key),
        AUTH_HEADER,
        False,
        url_host(url),
        revel_error or "",
    )
    return APIException(
        401,
        "Revel authentication failed. Use the Developer API key from "
        "Revel Account → Developer API, not a device registration key.",
        data={"ok": False, "devices": [], "revelStatus": status},
    )


async def list_signage_devices(
    *,
    api_key: str | None = None,
    graphql_url: str | None = None,
    key_file: Path | None = None,
) -> list[dict[str, Any]]:
    """POST the allowlisted ``device`` query. Never logs the API key."""
    key = sanitize_revel_api_key(api_key) if api_key is not None else load_revel_api_key(key_file)
    if not key:
        raise APIException(
            503,
            "Revel API key is not configured. Install the Developer API key at "
            "REVEL_API_KEY_FILE (never auto-generated).",
            data={"ok": False, "devices": []},
        )
    url = graphql_url or resolve_graphql_url()
    headers = _headers(key)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            resp = await client.post(url, headers=headers, json={"query": DEVICES_QUERY})
    except httpx.HTTPError:
        log.warning("revel signage graphql transport failed host=%s", url_host(url))
        raise APIException(
            502,
            "Could not reach Revel",
            data={"ok": False, "devices": []},
        ) from None

    if resp.status_code in (401, 403):
        raise _auth_failed(resp.status_code, resp.text, key=key, url=url)
    if resp.status_code >= 400:
        log.warning(
            "revel signage graphql failed status=%s host=%s revel_error=%s",
            resp.status_code,
            url_host(url),
            _safe_revel_error(resp.text),
        )
        raise APIException(
            502,
            "Could not list Revel devices",
            data={"ok": False, "devices": []},
        )

    try:
        payload = resp.json()
    except Exception:
        log.warning("revel signage graphql returned non-json host=%s", url_host(url))
        raise APIException(
            502,
            "Could not list Revel devices",
            data={"ok": False, "devices": []},
        ) from None

    if not isinstance(payload, dict):
        raise APIException(
            502,
            "Could not list Revel devices",
            data={"ok": False, "devices": []},
        )

    errors = payload.get("errors")
    if errors:
        count = len(errors) if isinstance(errors, list) else 1
        log.warning("revel signage graphql errors count=%s host=%s", count, url_host(url))
        raise APIException(
            502,
            "Could not list Revel devices",
            data={"ok": False, "devices": []},
        )

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    rows = data.get("device") if isinstance(data, dict) else None
    out: list[dict[str, Any]] = []
    skipped = 0
    if isinstance(rows, list):
        for item in rows:
            norm = normalize_signage_device(item)
            if norm:
                out.append(norm)
            else:
                skipped += 1
    log.info(
        "revel signage list method=POST host=%s query=device_list devices=%s skipped=%s",
        url_host(url),
        len(out),
        skipped,
    )
    return out
