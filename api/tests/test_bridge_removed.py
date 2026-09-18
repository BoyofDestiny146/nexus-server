"""Tests verifying W1-B bridge code has been removed (Task 3).

These are structural/contract tests — they verify that:
- The unbound-recent endpoint is gone (no more bridge-log reading)
- settings no longer have bridge_log_unit / unbound_recent_window_minutes
- xiaozhi_ingest_enabled defaults to True (W1-A is the only path now)
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_unbound_recent_endpoint_removed(client: AsyncClient):
    """GET /api/device/unbound-recent should no longer be a valid API endpoint.

    The endpoint used to return 401 (auth required) when the route existed.
    Now that the route is removed, the URL either:
    - 404s (no matching route)
    - 405s (matches a pattern for a different HTTP method — e.g. DELETE /device/{id})

    Either way, the code should NOT be 401, which would indicate the
    unbound-recent route still exists and is rejecting unauthenticated callers.
    A 401 specifically would mean the route IS registered (auth fails first).
    """
    resp = await client.get("/api/device/unbound-recent")
    data = resp.json()
    # 401 would mean the unbound-recent route still exists (auth check fires).
    # 404 or 405 both confirm it's no longer a dedicated endpoint.
    assert data["code"] != 401, (
        f"Got code=401 — the unbound-recent route appears to still be registered "
        f"(auth dependency fires before handler). Full response: {data}"
    )
    assert data["code"] in (404, 405), (
        f"Expected 404 or 405 (route removed), got code={data['code']}"
    )


def test_settings_no_bridge_log_unit():
    """settings should NOT have bridge_log_unit or unbound_recent_window_minutes."""
    from careconnect_api.settings import Settings
    model_fields = Settings.model_fields
    assert "bridge_log_unit" not in model_fields, (
        "bridge_log_unit should have been removed from Settings"
    )
    assert "unbound_recent_window_minutes" not in model_fields, (
        "unbound_recent_window_minutes should have been removed from Settings"
    )


def test_settings_xiaozhi_ingest_enabled_default_true():
    """xiaozhi_ingest_enabled should default to True now that W1-A is the only path."""
    from careconnect_api.settings import Settings
    s = Settings()
    assert s.xiaozhi_ingest_enabled is True, (
        f"xiaozhi_ingest_enabled default should be True, got {s.xiaozhi_ingest_enabled}"
    )
