"""OllamaLLM / embeddings URL precedence: CC_LLM_MODEL and OLLAMA_BASE_URL over YAML."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.utils.ollama_env import (  # noqa: E402
    ON_DEMAND_KEEP_ALIVE,
    ollama_v1_url,
    resolve_ollama_base_url,
    resolve_ollama_model,
)

YAML_MODEL = "cc-llm"
QWEN = "qwen2.5:3b"
YAML_URL = "http://127.0.0.1:11434"
HOST_URL = "http://host-gateway:11434"


def test_cc_llm_model_overrides_yaml():
    model, source = resolve_ollama_model(
        YAML_MODEL, environ={"CC_LLM_MODEL": QWEN}
    )
    assert source == "CC_LLM_MODEL"
    assert model == QWEN


def test_yaml_model_used_when_env_absent():
    model, source = resolve_ollama_model(YAML_MODEL, environ={})
    assert source == "yaml"
    assert model == YAML_MODEL


def test_blank_cc_llm_model_falls_through_to_yaml():
    model, source = resolve_ollama_model(
        YAML_MODEL, environ={"CC_LLM_MODEL": "  "}
    )
    assert source == "yaml"
    assert model == YAML_MODEL


def test_ollama_base_url_overrides_yaml():
    url, source = resolve_ollama_base_url(
        YAML_URL, environ={"OLLAMA_BASE_URL": HOST_URL}
    )
    assert source == "OLLAMA_BASE_URL"
    assert url == HOST_URL


def test_yaml_base_url_used_when_env_absent():
    url, source = resolve_ollama_base_url(YAML_URL, environ={})
    assert source == "yaml"
    assert url == YAML_URL


def test_ollama_v1_url_appends_once():
    assert ollama_v1_url(HOST_URL) == HOST_URL + "/v1"
    assert ollama_v1_url(HOST_URL + "/v1") == HOST_URL + "/v1"


def test_on_demand_keep_alive_is_finite():
    assert ON_DEMAND_KEEP_ALIVE != -1
    assert ON_DEMAND_KEEP_ALIVE != "-1"


def test_warm_script_no_longer_pins_cc_llm_or_vision():
    src = (ROOT.parent / "deploy/jetson/cc-warm-models.sh").read_text()
    assert "qwen2.5:3b" in src
    assert "keep_alive" in src
    assert "warm cc-llm" not in src
    assert "warm cc-vision" not in src
    assert "ollama stop" in src
    assert "nomic-embed-text" in src  # stopped, not warmed


def test_ollama_dropin_keep_alive_is_not_forever():
    src = (ROOT.parent / "deploy/jetson/ollama-cc-memory.conf").read_text()
    env_lines = [
        ln for ln in src.splitlines() if ln.startswith("Environment=")
    ]
    joined = "\n".join(env_lines)
    assert 'OLLAMA_KEEP_ALIVE=5m' in joined
    assert 'OLLAMA_KEEP_ALIVE=-1' not in joined
