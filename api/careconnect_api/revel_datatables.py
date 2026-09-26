"""Read-only Revel Data Table discovery.

Queries are official GraphQL documents from
https://developer.reveldigital.com/graphql/ — never concatenated with caller
input. Mutations are documented here for later phases and are never POSTed.

REST shape (official, not called from this module):
  GET    /datatables
  GET    /datatables/{tableId}
  GET    /datatables/{tableId}/rows
  GET    /datatables/{tableId}/rows/{rowId}
  PUT    /datatables/{tableId}/rows/{rowId}   # partial row update — not called
  POST   /datatables/{tableId}/rows           # insert — not called

Player propagation (Revel support article 43852295364493): API updates push
to players in real time; no template republish. Offline devices fetch the
latest row on reconnect. Cache TTL is a table field (``cacheTtlSeconds``).
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from .envelope import APIException
from .revel_client import (
    AUTH_HEADER,
    key_shape,
    sanitize_revel_api_key,
    url_host,
    _safe_revel_error,
)
from .revel_signage import (
    load_revel_api_key,
    load_revel_signage_settings,
    resolve_graphql_url,
)

log = logging.getLogger("revel_datatables")

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_TABLE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_PAGE_MAX = 100

# Official list query. Docs: GraphQL dataTables(pageSize).
DATATABLES_QUERY = (
    "query DataTablesList($pageSize: Int) { "
    "dataTables(pageSize: $pageSize) { "
    "data { id name description columnCount rowCount updatedAt } "
    "continuationToken } }"
)

# Official table definition including columns.
DATATABLE_QUERY = (
    "query DataTableDef($tableId: String!) { "
    "dataTable(tableId: $tableId) { "
    "id name description rowCount cacheTtlSeconds "
    "columns { id name key type required sortable options default } } }"
)

# Official row list. Caller cannot supply filter/sort GraphQL.
DATATABLE_ROWS_QUERY = (
    "query DataTableRows($tableId: String!, $pageSize: Int) { "
    "dataTableRows(tableId: $tableId, pageSize: $pageSize) { "
    "data { id sortOrder data updatedAt } "
    "totalCount continuationToken } }"
)

ALLOWED_QUERIES = frozenset(
    {DATATABLES_QUERY, DATATABLE_QUERY, DATATABLE_ROWS_QUERY}
)

# Documented write operations — names only. Never sent.
DOCUMENTED_WRITE_OPERATIONS = (
    "createDataTable",
    "updateDataTable",
    "deleteDataTable",
    "createDataTableRow",
    "updateDataTableRow",
    "deleteDataTableRow",
    "batchCreateDataTableRows",
    "batchDeleteDataTableRows",
    "importDataTableRows",
    "reorderDataTableRows",
    "rollbackDataTableRow",
    "sendDeviceCommand",
)

# GraphQL create row as documented. Not executed.
DOCUMENTED_CREATE_ROW = (
    "mutation { createDataTableRow(input: { tableId: \"table-id\", "
    "data: { } }) { success row { id data updatedAt } error } }"
)

# REST partial update as documented. Not executed.
DOCUMENTED_REST_UPDATE_ROW = "PUT /datatables/{tableId}/rows/{rowId}"


def normalize_table_id(raw: Any) -> str:
    text = str(raw or "").strip()
    if not _TABLE_ID_RE.match(text):
        raise APIException(400, "invalid data table id")
    return text


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        AUTH_HEADER: api_key,
    }


def _auth_failed(status: int, body: str | None, *, key: str, url: str) -> APIException:
    revel_error = _safe_revel_error(body)
    log.warning(
        "revel datatable auth failed status=%s key_present=%s key_length=%s "
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
        data={"ok": False, "tables": [], "revelStatus": status},
    )


async def graphql_read(
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    """POST an allowlisted read query. Rejects mutations and unknown documents."""
    if query not in ALLOWED_QUERIES:
        raise APIException(400, "unsupported Revel query")
    if "mutation" in query.casefold():
        raise APIException(400, "Revel mutations are disabled")
    key = sanitize_revel_api_key(api_key) if api_key is not None else load_revel_api_key()
    if not key:
        raise APIException(
            503,
            "Revel API key is not configured. Install the Developer API key at "
            "REVEL_API_KEY_FILE (never auto-generated).",
            data={"ok": False},
        )
    url = resolve_graphql_url()
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            resp = await client.post(url, headers=_headers(key), json=payload)
    except httpx.HTTPError:
        log.warning("revel datatable graphql transport failed host=%s", url_host(url))
        raise APIException(502, "Could not reach Revel", data={"ok": False}) from None

    if resp.status_code in (401, 403):
        raise _auth_failed(resp.status_code, resp.text, key=key, url=url)
    if resp.status_code >= 400:
        log.warning(
            "revel datatable graphql failed status=%s host=%s revel_error=%s",
            resp.status_code,
            url_host(url),
            _safe_revel_error(resp.text),
        )
        raise APIException(502, "Could not read Revel data tables", data={"ok": False})

    try:
        body = resp.json()
    except Exception:
        log.warning("revel datatable graphql returned non-json host=%s", url_host(url))
        raise APIException(502, "Could not read Revel data tables", data={"ok": False}) from None

    if not isinstance(body, dict):
        raise APIException(502, "Could not read Revel data tables", data={"ok": False})
    errors = body.get("errors")
    if errors:
        count = len(errors) if isinstance(errors, list) else 1
        log.warning("revel datatable graphql errors count=%s host=%s", count, url_host(url))
        raise APIException(502, "Could not read Revel data tables", data={"ok": False})
    data = body.get("data")
    if not isinstance(data, dict):
        raise APIException(502, "Could not read Revel data tables", data={"ok": False})
    return data


def _public_table(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    table_id = str(raw.get("id") or "").strip()
    if not table_id:
        return None
    try:
        table_id = normalize_table_id(table_id)
    except APIException:
        return None
    out: dict[str, Any] = {
        "id": table_id,
        "name": str(raw.get("name") or table_id)[:128],
        "description": str(raw.get("description") or "")[:512] or None,
        "columnCount": raw.get("columnCount"),
        "rowCount": raw.get("rowCount"),
        "updatedAt": raw.get("updatedAt"),
        "cacheTtlSeconds": raw.get("cacheTtlSeconds"),
    }
    columns = raw.get("columns")
    if isinstance(columns, list):
        out["columns"] = [
            {
                "id": str(col.get("id") or ""),
                "name": str(col.get("name") or ""),
                "key": str(col.get("key") or ""),
                "type": str(col.get("type") or ""),
                "required": bool(col.get("required")) if col.get("required") is not None else None,
                "sortable": bool(col.get("sortable")) if col.get("sortable") is not None else None,
                "options": col.get("options"),
                "default": col.get("default"),
            }
            for col in columns
            if isinstance(col, dict)
        ]
    return out


def _public_row(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    row_id = str(raw.get("id") or "").strip()
    if not row_id:
        return None
    data = raw.get("data")
    if data is not None and not isinstance(data, dict):
        data = None
    return {
        "id": row_id[:128],
        "sortOrder": raw.get("sortOrder"),
        "data": data,
        "updatedAt": raw.get("updatedAt"),
    }


def _page_size(raw: int | None) -> int:
    if raw is None:
        return 20
    return max(1, min(int(raw), _PAGE_MAX))


async def list_data_tables(*, page_size: int | None = None) -> dict[str, Any]:
    data = await graphql_read(
        DATATABLES_QUERY,
        {"pageSize": _page_size(page_size)},
    )
    block = data.get("dataTables") if isinstance(data.get("dataTables"), dict) else {}
    rows = block.get("data") if isinstance(block, dict) else None
    tables: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for item in rows:
            pub = _public_table(item)
            if pub:
                tables.append(pub)
    cfg = load_revel_signage_settings()
    control = (cfg.control_table_id or "").strip() or None
    if control:
        try:
            control = normalize_table_id(control)
        except APIException:
            control = None
    for table in tables:
        table["isControlTable"] = bool(control) and table["id"] == control
    log.info("revel datatable list tables=%s", len(tables))
    return {
        "ok": True,
        "tables": tables,
        "continuationToken": (block or {}).get("continuationToken") if isinstance(block, dict) else None,
        "controlTableIdConfigured": bool(control),
    }


async def get_data_table(table_id: str, *, page_size: int | None = None) -> dict[str, Any]:
    tid = normalize_table_id(table_id)
    definition = await graphql_read(DATATABLE_QUERY, {"tableId": tid})
    raw_table = definition.get("dataTable")
    table = _public_table(raw_table)
    if table is None:
        raise APIException(404, "Revel data table not found", data={"ok": False})
    rows_payload = await graphql_read(
        DATATABLE_ROWS_QUERY,
        {"tableId": tid, "pageSize": _page_size(page_size)},
    )
    block = rows_payload.get("dataTableRows") if isinstance(rows_payload.get("dataTableRows"), dict) else {}
    raw_rows = block.get("data") if isinstance(block, dict) else None
    rows: list[dict[str, Any]] = []
    if isinstance(raw_rows, list):
        for item in raw_rows:
            pub = _public_row(item)
            if pub:
                rows.append(pub)
    cfg = load_revel_signage_settings()
    control = (cfg.control_table_id or "").strip() or None
    table["isControlTable"] = bool(control) and table["id"] == control
    log.info("revel datatable get columns=%s rows=%s", len(table.get("columns") or []), len(rows))
    return {
        "ok": True,
        "table": table,
        "rows": rows,
        "totalCount": block.get("totalCount") if isinstance(block, dict) else None,
        "continuationToken": block.get("continuationToken") if isinstance(block, dict) else None,
        "writesEnabled": False,
    }
