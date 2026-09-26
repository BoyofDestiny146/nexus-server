"""Revel Data Table row write — REST PUT only, gated by REVEL_EXECUTE_ENABLED.

Official Swagger ``UpdateRowRequest`` (additionalProperties: false):

    PUT /datatables/{tableId}/rows/{rowId}
    { "data": { "<column key>": <value> } }

Column keys are taken from the live table schema. This module never invents
keys, never sends GraphQL mutations, and never calls sendDeviceCommand.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from .envelope import APIException
from .revel_client import (
    AUTH_HEADER,
    RevelMutationDisabled,
    key_shape,
    sanitize_revel_api_key,
    url_host,
    _safe_revel_error,
)
from .revel_datatables import get_data_table, normalize_table_id
from .revel_errors import (
    AMBIGUOUS_CONTROL_ROW,
    CONTROL_ROW_NOT_FOUND,
    CONTROL_TABLE_NOT_CONFIGURED,
    controlled_read_payload,
    reason_label,
)
from .revel_signage import (
    DEFAULT_API_BASE,
    _require_https_url,
    load_revel_api_key,
    load_revel_signage_settings,
)

log = logging.getLogger("revel_write")

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_ROW_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# Internal display fields → candidate column keys. A candidate is used only
# when it exists on the live table. Names/labels are never used as keys.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "device_key": ("device_key", "deviceKey", "DeviceKey", "device-key"),
    "screen": ("screen", "Screen"),
    "title": ("title", "Title"),
    "message": ("message", "Message"),
    "image_url": ("image_url", "imageUrl", "ImageUrl", "image"),
    "priority": ("priority", "Priority"),
    "expires_at": ("expires_at", "expiresAt", "ExpiresAt"),
    "updated_at": ("updated_at", "updatedAt", "UpdatedAt"),
}

REQUIRED_FIELDS = ("device_key", "screen", "title", "message")
OPTIONAL_FIELDS = ("image_url", "priority", "expires_at", "updated_at")
STRING_TYPES = {
    "",
    "string",
    "text",
    "richtext",
    "hidden",
    "select",
    "url",
    "date",
    "time",
}

# Incremented only immediately before an HTTP PUT. Reads do not count.
revel_puts_attempted = 0


def revel_execute_enabled() -> bool:
    """Server env only. Callers cannot flip this."""
    return bool(load_revel_signage_settings().revel_execute_enabled)


def normalize_row_id(raw: Any) -> str:
    text = str(raw or "").strip()
    if not _ROW_ID_RE.match(text):
        raise APIException(400, "invalid data table row id")
    return text


def configured_control_table_id() -> str | None:
    raw = (load_revel_signage_settings().control_table_id or "").strip()
    if not raw:
        return None
    return normalize_table_id(raw)


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bind_column_keys(columns: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Map internal fields onto exact live ``column.key`` values.

    Missing required keys are reported; no key is invented.
    """
    discovered: dict[str, dict[str, Any]] = {}
    for col in columns or []:
        if not isinstance(col, dict):
            continue
        key = str(col.get("key") or "").strip()
        if not key:
            continue
        discovered[key] = col
    discovered_cf = {k.casefold(): k for k in discovered}

    mapping: dict[str, str] = {}
    types: dict[str, str] = {}
    missing: list[str] = []
    for field, aliases in FIELD_ALIASES.items():
        actual = None
        for alias in aliases:
            if alias in discovered:
                actual = alias
                break
            folded = discovered_cf.get(alias.casefold())
            if folded:
                actual = folded
                break
        if actual is None:
            if field in REQUIRED_FIELDS:
                missing.append(field)
            continue
        mapping[field] = actual
        types[field] = str((discovered[actual] or {}).get("type") or "")
    return {
        "mapping": mapping,
        "types": types,
        "missingRequired": missing,
        "discoveredKeys": sorted(discovered),
    }


def select_control_row(
    rows: list[dict[str, Any]] | None,
    *,
    device_key: str,
    device_key_column: str,
) -> dict[str, Any]:
    """Exactly one row whose data[device_key_column] equals the stored key."""
    matches: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        value = str(data.get(device_key_column) or "").strip()
        if value == device_key:
            matches.append(row)
    if not matches:
        return {"ok": False, "reason": CONTROL_ROW_NOT_FOUND, "row": None}
    if len(matches) > 1:
        return {"ok": False, "reason": AMBIGUOUS_CONTROL_ROW, "row": None}
    return {"ok": True, "reason": None, "row": matches[0]}


def _coerce(field: str, value: Any, col_type: str) -> Any:
    kind = (col_type or "").strip().casefold()
    if field == "priority" and kind in {"number", "int", "integer", "float"}:
        return int(value)
    if kind in {"boolean", "bool"}:
        return bool(value)
    if value is None:
        return None
    return str(value)


def build_update_payload(
    *,
    display_state: dict[str, Any],
    binding: dict[str, Any],
    written_at: str | None = None,
) -> dict[str, Any]:
    """Allowlisted ``{data: ...}`` only. Never a passthrough of the request."""
    mapping: dict[str, str] = dict(binding.get("mapping") or {})
    types: dict[str, str] = dict(binding.get("types") or {})
    stamp = written_at or _iso_now()
    internals = {
        "device_key": display_state.get("deviceKey"),
        "screen": display_state.get("screen"),
        "title": display_state.get("title"),
        "message": display_state.get("message"),
        "image_url": display_state.get("imageUrl"),
        "priority": display_state.get("priority"),
        "expires_at": display_state.get("expiresAt"),
        "updated_at": stamp,
    }
    data: dict[str, Any] = {}
    for field, column in mapping.items():
        raw = internals.get(field)
        if raw is None and field in OPTIONAL_FIELDS:
            continue
        col_type = types.get(field) or ""
        if field == "image_url" and col_type.strip().casefold() in {"media", "file"}:
            continue
        data[column] = _coerce(field, raw, col_type)
    return {"data": data}


def write_plan(
    *,
    table_id: str,
    row_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "method": "PUT",
        "path": f"/datatables/{table_id}/rows/{row_id}",
        "body": payload,
    }


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        AUTH_HEADER: api_key,
    }


def _api_base() -> str:
    cfg = load_revel_signage_settings()
    return _require_https_url(cfg.api_base or DEFAULT_API_BASE, name="REVEL_API_BASE")


async def fetch_control_table() -> dict[str, Any]:
    table_id = configured_control_table_id()
    if not table_id:
        raise APIException(400, "REVEL_CONTROL_TABLE_ID is not configured")
    return await get_data_table(table_id, page_size=100)


async def update_data_table_row(
    *,
    table_id: str,
    row_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """One allowlisted REST PUT. Never called while execute is off."""
    global revel_puts_attempted
    if not revel_execute_enabled():
        raise RevelMutationDisabled("Revel execution disabled")
    tid = normalize_table_id(table_id)
    rid = normalize_row_id(row_id)
    if not isinstance(payload, dict) or set(payload.keys()) != {"data"}:
        raise APIException(500, "invalid Revel write payload")
    data = payload.get("data")
    if not isinstance(data, dict) or not data:
        raise APIException(500, "invalid Revel write payload")
    key = load_revel_api_key()
    if not key:
        raise APIException(503, "Revel API key is not configured")
    url = f"{_api_base()}/datatables/{tid}/rows/{rid}"
    revel_puts_attempted += 1
    log.info(
        "revel datatable put host=%s path=/datatables/%s/rows/%s keys=%s",
        url_host(url),
        tid,
        rid,
        sorted(str(k) for k in data),
    )
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            resp = await client.put(url, headers=_headers(key), json={"data": data})
    except httpx.TimeoutException:
        log.warning("revel datatable put timeout host=%s", url_host(url))
        raise APIException(504, "Revel write timed out", data={"reason": "timeout"}) from None
    except httpx.HTTPError:
        log.warning("revel datatable put transport failed host=%s", url_host(url))
        raise APIException(502, "Could not reach Revel", data={"reason": "transport"}) from None

    if resp.status_code in (401, 403):
        log.warning(
            "revel datatable put auth failed status=%s key_present=%s key_length=%s "
            "key_shape=%s header_name=%s query_api_key=%s host=%s revel_error=%s",
            resp.status_code,
            bool(key),
            len(key),
            key_shape(key),
            AUTH_HEADER,
            False,
            url_host(url),
            _safe_revel_error(resp.text),
        )
        raise APIException(401, "Revel authentication failed", data={"reason": "auth"})
    if resp.status_code < 200 or resp.status_code >= 300:
        log.warning(
            "revel datatable put failed status=%s host=%s revel_error=%s",
            resp.status_code,
            url_host(url),
            _safe_revel_error(resp.text),
        )
        raise APIException(
            502,
            "Revel write failed",
            data={"reason": "http_error", "revelStatus": resp.status_code},
        )
    try:
        body = resp.json()
    except Exception as exc:
        raise APIException(502, "malformed Revel write response", data={"reason": "malformed"}) from exc
    if not isinstance(body, dict) or not str(body.get("id") or "").strip():
        raise APIException(502, "malformed Revel write response", data={"reason": "malformed"})
    return {
        "id": str(body.get("id")),
        "updatedAt": body.get("updatedAt"),
        "sortOrder": body.get("sortOrder"),
    }


async def inspect_control_table() -> dict[str, Any]:
    """Read-only binding report. Does not PUT."""
    table_id = configured_control_table_id()
    if not table_id:
        return {
            "ok": False,
            "executeEnabled": revel_execute_enabled(),
            "writesEnabled": False,
            "reason": CONTROL_TABLE_NOT_CONFIGURED,
            "reasonLabel": reason_label(CONTROL_TABLE_NOT_CONFIGURED),
            "table": None,
            "binding": None,
            "rows": [],
        }
    try:
        fetched = await fetch_control_table()
    except APIException as exc:
        payload = controlled_read_payload(exc, kind="table")
        payload["executeEnabled"] = revel_execute_enabled()
        return payload
    table = fetched.get("table") if isinstance(fetched.get("table"), dict) else {}
    rows = fetched.get("rows") if isinstance(fetched.get("rows"), list) else []
    binding = bind_column_keys(table.get("columns") if isinstance(table, dict) else [])
    return {
        "ok": True,
        "executeEnabled": revel_execute_enabled(),
        "writesEnabled": revel_execute_enabled(),
        "reason": None,
        "table": {
            "id": table.get("id"),
            "name": table.get("name"),
            "rowCount": table.get("rowCount") if table.get("rowCount") is not None else len(rows),
            "columns": table.get("columns") or [],
        },
        "binding": binding,
        "rows": [
            {"id": r.get("id"), "data": r.get("data")}
            for r in rows
            if isinstance(r, dict)
        ],
    }
