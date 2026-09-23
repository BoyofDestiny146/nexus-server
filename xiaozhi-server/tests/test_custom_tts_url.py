"""CustomTTS endpoint precedence: CC_TTS_URL > PIPER_URL > YAML url.

Loads ``resolve_custom_tts_url`` without importing TTSProviderBase (torch /
funasr / opus).
"""
from __future__ import annotations

import ast
from pathlib import Path


def _load_resolve_fn():
    src = (
        Path(__file__).resolve().parents[1] / "core/providers/tts/custom.py"
    ).read_text()
    tree = ast.parse(src)
    wanted = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_custom_tts_url":
            wanted = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(wanted)
            break
    assert wanted is not None, "resolve_custom_tts_url not found in custom.py"
    ns: dict = {}
    exec(compile(wanted, "custom.py", "exec"), ns)
    return ns["resolve_custom_tts_url"]


resolve_custom_tts_url = _load_resolve_fn()

YAML_URL = "http://127.0.0.1:5500/v1/audio/speech"
PIPER_URL = "http://piper-tts:5500/v1/audio/speech"
CC_TTS_URL = "http://piper-tts:5500/override"


def test_cc_tts_url_overrides_everything():
    url, source = resolve_custom_tts_url(
        YAML_URL,
        environ={"CC_TTS_URL": CC_TTS_URL, "PIPER_URL": PIPER_URL},
    )
    assert source == "CC_TTS_URL"
    assert url == CC_TTS_URL


def test_piper_url_used_when_cc_tts_url_absent():
    url, source = resolve_custom_tts_url(
        YAML_URL,
        environ={"PIPER_URL": PIPER_URL},
    )
    assert source == "PIPER_URL"
    assert url == PIPER_URL


def test_yaml_used_when_neither_env_var_is_set():
    url, source = resolve_custom_tts_url(YAML_URL, environ={})
    assert source == "yaml"
    assert url == YAML_URL


def test_blank_env_vars_fall_through_to_yaml():
    url, source = resolve_custom_tts_url(
        YAML_URL,
        environ={"CC_TTS_URL": "  ", "PIPER_URL": ""},
    )
    assert source == "yaml"
    assert url == YAML_URL
