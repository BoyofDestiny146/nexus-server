"""Canonical portal hostname: nexus.warehouse-13.biz; care.nexus is a 308 alias."""
from __future__ import annotations

import re
from pathlib import Path

from careconnect_api.partner_push import is_self_push_url
from careconnect_api.settings import Settings, settings

ROOT = Path(__file__).resolve().parents[2]
CADDY = (ROOT / "deploy" / "Caddyfile.nexus").read_text()
ENV_EXAMPLE = (ROOT / "deploy" / ".env.example").read_text()
PARTNER_PUSH = (ROOT / "api" / "careconnect_api" / "partner_push.py").read_text()
SETTINGS_SRC = (ROOT / "api" / "careconnect_api" / "settings.py").read_text()
RUNNER_SRC = (ROOT / "api" / "careconnect_api" / "triage" / "runner.py").read_text()
ENGINE_SRC = (ROOT / "api" / "careconnect_api" / "assessment_engine" / "engine.py").read_text()
SALES_SRC = (ROOT / "api" / "careconnect_api" / "assessment_engine" / "sales_runner.py").read_text()
REVEL_WRITE = (ROOT / "api" / "careconnect_api" / "revel_write.py").read_text()

CANONICAL = "nexus.warehouse-13.biz"
LEGACY = "care.nexus.warehouse-13.biz"
CANONICAL_HTTPS = f"https://{CANONICAL}"
REDIR_308 = f"redir {CANONICAL_HTTPS}{{uri}} 308"


def _site_block(src: str, label: str) -> str:
    match = re.search(rf"(?m)^{re.escape(label)} \{{", src)
    assert match, f"missing site block {label!r}"
    start = match.start()
    depth = 0
    for i, ch in enumerate(src[match.end() - 1 :], start=match.end() - 1):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unclosed site block {label!r}")


def test_caddy_canonical_host_owns_portal_handlers():
    portal = _site_block(CADDY, CANONICAL)
    assert "handle /api/v1/*" in portal
    assert "handle /api/*" in portal
    assert "handle /ws/*" in portal
    assert "handle /photos/*" in portal
    assert "handle /careconnect/knowledge/*" in portal
    assert "handle /*" in portal
    assert "reverse_proxy api:8080" in portal
    assert "try_files {path} {path}/index.html /patients/_/index.html" in portal
    assert "redir @root /careconnect/login 302" in portal
    assert REDIR_308 not in portal


def test_caddy_http_and_legacy_hosts_redirect_to_https_canonical():
    http_canonical = _site_block(CADDY, f"http://{CANONICAL}")
    http_legacy = _site_block(CADDY, f"http://{LEGACY}")
    https_legacy = _site_block(CADDY, LEGACY)
    for block, label in (
        (http_canonical, "http canonical"),
        (http_legacy, "http legacy"),
        (https_legacy, "https legacy"),
    ):
        assert REDIR_308 in block, label
        assert "{uri}" in block, label
        assert " 301" not in block, label
        assert "reverse_proxy" not in block, label
        assert "handle /api" not in block, label
        assert "file_server" not in block, label


def test_caddy_ws_and_ota_hosts_are_unchanged():
    ota = _site_block(CADDY, "ota.nexus.warehouse-13.biz")
    ws = _site_block(CADDY, "ws.nexus.warehouse-13.biz")
    assert "reverse_proxy xiaozhi-server:8003" in ota
    assert "redir" not in ota
    assert "handle /" not in ota
    assert "reverse_proxy xiaozhi-server:8000" in ws
    assert "versions 1.1" in ws
    assert "handle /api" not in ws
    assert CANONICAL_HTTPS not in ota
    assert CANONICAL_HTTPS not in ws


def test_default_portal_base_url_is_canonical_nexus():
    assert Settings.model_fields["portal_base_url"].default == CANONICAL_HTTPS
    assert settings.portal_base_url.rstrip("/") == CANONICAL_HTTPS
    assert 'portal_base_url: str = "https://nexus.warehouse-13.biz"' in SETTINGS_SRC


def test_partner_push_treats_canonical_and_legacy_as_same_host():
    canonical = f"{CANONICAL_HTTPS}/api/v1/integrations/careconnect/ingest"
    legacy = f"https://{LEGACY}/api/v1/integrations/careconnect/ingest"
    external = "https://careconnect.example.org/api/v1/integrations/careconnect/ingest"
    assert is_self_push_url(canonical)
    assert is_self_push_url(legacy)
    assert is_self_push_url(canonical, portal_base=CANONICAL_HTTPS)
    assert is_self_push_url(legacy, portal_base=CANONICAL_HTTPS)
    assert is_self_push_url(legacy, portal_base=f"https://{LEGACY}")
    assert not is_self_push_url(external)
    assert not is_self_push_url(external, portal_base=CANONICAL_HTTPS)
    assert '"nexus.warehouse-13.biz"' in PARTNER_PUSH
    assert '"care.nexus.warehouse-13.biz"' in PARTNER_PUSH


def test_hostname_migration_does_not_retune_assessment_or_revel():
    assert '"options": {"num_predict": 300, "temperature": 0.2}' in RUNNER_SRC
    assert "push_assessment_best_effort" in RUNNER_SRC
    assert "await run_for_agent(db, agent_id, for_date)" in ENGINE_SRC
    assert "revel_write" not in ENGINE_SRC
    assert "sendDeviceCommand" not in SALES_SRC
    assert "REVEL_EXECUTE_ENABLED" in REVEL_WRITE
    assert "REVEL_EXECUTE_ENABLED=false" in ENV_EXAMPLE
    assert ENV_EXAMPLE.splitlines().count("REVEL_EXECUTE_ENABLED=false") >= 1
