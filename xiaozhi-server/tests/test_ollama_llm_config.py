"""OllamaLLM config precedence: CC_LLM_MODEL / OLLAMA_BASE_URL over YAML.

Loads resolvers from ollama.py without importing OpenAI / LLMProviderBase.
"""
from __future__ import annotations

import ast
from pathlib import Path


def _load_fns():
    src = (
        Path(__file__).resolve().parents[1]
        / "core/providers/llm/ollama/ollama.py"
    ).read_text()
    tree = ast.parse(src)
    wanted = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in (
            "resolve_ollama_model",
            "resolve_ollama_base_url",
        ):
            wanted.append(node)
    assert {n.name for n in wanted} == {
        "resolve_ollama_model",
        "resolve_ollama_base_url",
    }
    mod = ast.Module(body=wanted, type_ignores=[])
    ast.fix_missing_locations(mod)
    ns: dict = {}
    exec(compile(mod, "ollama.py", "exec"), ns)
    return ns["resolve_ollama_model"], ns["resolve_ollama_base_url"]


resolve_ollama_model, resolve_ollama_base_url = _load_fns()

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
