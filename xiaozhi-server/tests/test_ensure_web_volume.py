"""Idempotent web-service patcher for the Orin deploy compose copy.

Must not overwrite production Caddy 80/443 with the repo's 18180/18443.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/scripts"))
from ensure_web_volume import patch_compose, CANONICAL_SCRIPT  # noqa: E402

COMPOSE = ROOT / "deploy/docker-compose.yml"

PRODUCTION_STYLE = """\
name: nexus
x-logging: &logging
  logging:
    driver: json-file

services:
  caddy:
    image: localhost:5000/careconnect-caddy:20260920
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - web-static:/srv/web:ro
    depends_on:
      web:
        condition: service_completed_successfully
  web:
    image: localhost:5000/careconnect-web:20260920
    <<: *logging
    command: ["/bin/sh", "-c", "cp -a /app/out/. /srv/web/ && echo 'web: static export copied'"]
    restart: "no"
    volumes:
      - web-static:/srv/web
  xiaozhi-server:
    image: localhost:5000/careconnect-xiaozhi-server:20260920
    environment:
      OLLAMA_BASE_URL: "http://host-gateway:11434"
      CC_LLM_MODEL: "${CC_LLM_MODEL:-qwen2.5:3b}"
      PIPER_URL: "http://piper-tts:5500/v1/audio/speech"
  api:
    image: localhost:5000/careconnect-api:20260920
"""


def _caddy_ports(text: str) -> list[str]:
    block = text.split("  caddy:", 1)[1].split("  web:", 1)[0]
    return [ln for ln in block.splitlines() if "80:80" in ln or "443:443" in ln or "18180" in ln or "18443" in ln]


def test_production_style_gets_user_and_chown_command():
    new, action = patch_compose(PRODUCTION_STYLE)
    assert action == "inserted"
    web = new.split("  web:", 1)[1].split("  xiaozhi-server:", 1)[0]
    assert 'user: "0:0"' in web
    assert CANONICAL_SCRIPT in web
    assert "chown appuser:appuser /srv/web" in web
    assert "chown -R appuser:appuser /srv/web" in web


def test_production_style_keeps_caddy_80_443_and_udp():
    new, _action = patch_compose(PRODUCTION_STYLE)
    ports = _caddy_ports(new)
    assert any('"80:80"' in p for p in ports)
    assert any('"443:443"' in p for p in ports)
    assert any("443:443/udp" in p for p in ports)
    assert not any("18180" in p for p in ports)
    assert not any("18443" in p for p in ports)
    orig_caddy = PRODUCTION_STYLE.split("  caddy:", 1)[1].split("  web:", 1)[0]
    new_caddy = new.split("  caddy:", 1)[1].split("  web:", 1)[0]
    assert orig_caddy == new_caddy


def test_production_style_does_not_touch_other_services_or_tags():
    new, _action = patch_compose(PRODUCTION_STYLE)
    assert "localhost:5000/careconnect-web:20260920" in new
    assert "localhost:5000/careconnect-caddy:20260920" in new
    assert "localhost:5000/careconnect-xiaozhi-server:20260920" in new
    assert "localhost:5000/careconnect-api:20260920" in new
    orig_xz = PRODUCTION_STYLE.split("  xiaozhi-server:", 1)[1]
    new_xz = new.split("  xiaozhi-server:", 1)[1]
    assert orig_xz == new_xz
    assert 'OLLAMA_BASE_URL: "http://host-gateway:11434"' in new_xz
    assert 'CC_LLM_MODEL: "${CC_LLM_MODEL:-qwen2.5:3b}"' in new_xz
    assert 'PIPER_URL: "http://piper-tts:5500/v1/audio/speech"' in new_xz


def test_patcher_is_idempotent_on_production_style():
    once, action1 = patch_compose(PRODUCTION_STYLE)
    assert action1 == "inserted"
    twice, action2 = patch_compose(once)
    assert action2 == "unchanged"
    assert twice == once


def test_updates_existing_wrong_user_and_command():
    snippet = """\
services:
  web:
    image: keep-this-tag:abc
    user: "100:101"
    command: ["/bin/sh", "-c", "cp -a /app/out/. /srv/web/"]
    restart: "no"
  caddy:
    ports:
      - "80:80"
"""
    new, action = patch_compose(snippet)
    assert action == "updated"
    web = new.split("  web:", 1)[1].split("  caddy:", 1)[0]
    assert 'user: "0:0"' in web
    assert 'user: "100:101"' not in web
    assert CANONICAL_SCRIPT in web
    assert "keep-this-tag:abc" in web
    assert '"80:80"' in new
    again, action2 = patch_compose(new)
    assert action2 == "unchanged"
    assert again == new


def test_replaces_multiline_command_list():
    snippet = """\
services:
  web:
    image: x
    command:
      - /bin/sh
      - -c
      - cp -a /app/out/. /srv/web/
    restart: "no"
  api:
    image: y
"""
    new, action = patch_compose(snippet)
    assert action in ("inserted", "updated")
    web = new.split("  web:", 1)[1].split("  api:", 1)[0]
    assert 'user: "0:0"' in web
    assert CANONICAL_SCRIPT in web
    assert web.count("command:") == 1
    assert "- /bin/sh" not in web


def test_repo_compose_already_ok_is_unchanged():
    text = COMPOSE.read_text()
    new, action = patch_compose(text)
    assert action == "unchanged"
    assert new == text
    assert "18180:80" in new
    assert "18443:443" in new
