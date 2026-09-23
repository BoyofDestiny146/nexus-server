"""Ensure deploy compose injects CC_LLM_MODEL into xiaozhi-server."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/scripts"))
from ensure_xiaozhi_llm_env import patch_compose  # noqa: E402

COMPOSE = ROOT / "deploy/docker-compose.yml"


def test_repo_compose_declares_cc_llm_model():
    text = COMPOSE.read_text()
    assert "  xiaozhi-server:" in text
    assert "CC_LLM_MODEL:" in text
    assert "qwen2.5:3b" in text
    assert "OLLAMA_BASE_URL:" in text
    assert "PIPER_URL:" in text
    assert "piper-tts:5500/v1/audio/speech" in text


def test_patcher_inserts_after_ollama_url():
    snippet = """
services:
  xiaozhi-server:
    image: example
    environment:
      OLLAMA_BASE_URL: "${CC_OLLAMA_URL:-http://host-gateway:11434}"
      PIPER_URL: "http://piper-tts:5500/v1/audio/speech"
  api:
    image: example
"""
    new, action = patch_compose(snippet)
    assert action == "inserted"
    assert 'CC_LLM_MODEL: "${CC_LLM_MODEL:-qwen2.5:3b}"' in new
    env = new.split("environment:", 1)[1].split("  api:", 1)[0]
    assert env.index("OLLAMA_BASE_URL") < env.index("CC_LLM_MODEL") < env.index("PIPER_URL")
    again, action2 = patch_compose(new)
    assert action2 == "unchanged"
    assert again == new


def test_patcher_inserts_missing_piper_url():
    snippet = """
services:
  xiaozhi-server:
    environment:
      OLLAMA_BASE_URL: "${CC_OLLAMA_URL:-http://host-gateway:11434}"
      CC_LLM_MODEL: "${CC_LLM_MODEL:-qwen2.5:3b}"
"""
    new, action = patch_compose(snippet)
    assert action == "inserted"
    assert 'PIPER_URL: "http://piper-tts:5500/v1/audio/speech"' in new
    assert new.count("PIPER_URL:") == 1


def test_patcher_does_not_touch_other_services():
    snippet = """
services:
  api:
    environment:
      OLLAMA_BASE_URL: "should-not-matter"
  xiaozhi-server:
    environment:
      OLLAMA_BASE_URL: "${CC_OLLAMA_URL:-http://host-gateway:11434}"
"""
    new, action = patch_compose(snippet)
    assert action == "inserted"
    api_block = new.split("  xiaozhi-server:", 1)[0]
    xz_block = new.split("  xiaozhi-server:", 1)[1]
    assert "CC_LLM_MODEL:" not in api_block
    llm_keys = [ln for ln in xz_block.splitlines() if ln.lstrip().startswith("CC_LLM_MODEL:")]
    assert len(llm_keys) == 1
