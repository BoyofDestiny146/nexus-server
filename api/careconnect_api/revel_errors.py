"""Canonical Revel reason codes. Never include secrets or auth headers."""
from __future__ import annotations

from typing import Any

from .envelope import APIException

REVEL_NOT_CONFIGURED = "revel_not_configured"
REVEL_AUTH_FAILED = "revel_auth_failed"
UNMAPPED_PLAYER = "unmapped_player"
CONTROL_TABLE_NOT_CONFIGURED = "control_table_not_configured"
CONTROL_ROW_NOT_FOUND = "control_row_not_found"
AMBIGUOUS_CONTROL_ROW = "ambiguous_control_row"
MISSING_CONTROL_COLUMNS = "missing_control_columns"
REVEL_UNAVAILABLE = "revel_unavailable"
REVEL_WRITE_DISABLED = "revel_write_disabled"
REVEL_WRITE_FAILED = "revel_write_failed"

ALL_REASONS = frozenset(
    {
        REVEL_NOT_CONFIGURED,
        REVEL_AUTH_FAILED,
        UNMAPPED_PLAYER,
        CONTROL_TABLE_NOT_CONFIGURED,
        CONTROL_ROW_NOT_FOUND,
        AMBIGUOUS_CONTROL_ROW,
        MISSING_CONTROL_COLUMNS,
        REVEL_UNAVAILABLE,
        REVEL_WRITE_DISABLED,
        REVEL_WRITE_FAILED,
    }
)

REASON_LABELS: dict[str, str] = {
    REVEL_NOT_CONFIGURED: "Revel is not configured",
    REVEL_AUTH_FAILED: "Revel authentication failed",
    UNMAPPED_PLAYER: "No mapped Revel player",
    CONTROL_TABLE_NOT_CONFIGURED: "Control table is not configured",
    CONTROL_ROW_NOT_FOUND: "Control row was not found",
    AMBIGUOUS_CONTROL_ROW: "Multiple control rows matched",
    MISSING_CONTROL_COLUMNS: "Required control columns are missing",
    REVEL_UNAVAILABLE: "Revel is unavailable",
    REVEL_WRITE_DISABLED: "Revel execution disabled",
    REVEL_WRITE_FAILED: "Revel write failed",
}

FAILED_REASONS = ALL_REASONS - {REVEL_WRITE_DISABLED}
SKIPPED_REASONS = frozenset({REVEL_WRITE_DISABLED})


def reason_label(code: str | None) -> str | None:
    text = (code or "").strip()
    if not text:
        return None
    return REASON_LABELS.get(text, text)


def normalize_reason(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text in ALL_REASONS:
        return text
    lowered = text.casefold()
    if lowered in {"revel execution disabled", "execution disabled", "write disabled"}:
        return REVEL_WRITE_DISABLED
    if lowered in {"auth", "unauthorized", "forbidden"}:
        return REVEL_AUTH_FAILED
    if lowered in {"timeout", "transport"}:
        return REVEL_UNAVAILABLE
    if lowered in {"http_error", "malformed", "malformed_response", "revel_failed"}:
        return REVEL_WRITE_FAILED
    return text if len(text) <= 80 else text[:80]


def classify_api_exception(exc: APIException) -> str:
    data = exc.data if isinstance(exc.data, dict) else {}
    nested = normalize_reason(data.get("reason"))
    if nested in ALL_REASONS:
        return nested
    msg = (exc.msg or "").casefold()
    if exc.code in (401, 403) or "authentication failed" in msg:
        return REVEL_AUTH_FAILED
    if exc.code == 503 or "not configured" in msg:
        return REVEL_NOT_CONFIGURED
    if exc.code in (502, 504) or "timed out" in msg or "could not reach" in msg:
        return REVEL_UNAVAILABLE
    return REVEL_UNAVAILABLE


def controlled_read_payload(exc: APIException, *, kind: str) -> dict[str, Any]:
    """Fail-open read result. Never invents device, table, or row IDs."""
    reason = classify_api_exception(exc)
    payload: dict[str, Any] = {
        "ok": False,
        "reason": reason,
        "reasonLabel": reason_label(reason),
        "writesEnabled": False,
    }
    if kind == "devices":
        payload["devices"] = []
    elif kind == "tables":
        payload["tables"] = []
        payload["controlTableIdConfigured"] = False
    else:
        payload["table"] = None
        payload["rows"] = []
        payload["binding"] = None
    return payload
