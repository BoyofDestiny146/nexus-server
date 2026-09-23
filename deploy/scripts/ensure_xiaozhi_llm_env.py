#!/usr/bin/env python3
"""Ensure xiaozhi-server env keys exist in a Compose file.

Used on the Orin deploy copy (e.g. /mnt/xiaozhi/nexus-deploy/docker-compose.yml)
which is not the git tree and will not pick up repo compose edits automatically.

Idempotent. Touches only the xiaozhi-server environment block:
  * CC_LLM_MODEL (default qwen2.5:3b)
  * PIPER_URL    (piper-tts:5500/v1/audio/speech)
  * preserves OLLAMA_BASE_URL
  * does not change image, TAG, or any other service
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SERVICE_RE = re.compile(r"^  xiaozhi-server:\s*$")
NEXT_SERVICE_RE = re.compile(r"^  [A-Za-z0-9._-]+:\s*$")
OLLAMA_LINE_RE = re.compile(r"^(\s*)OLLAMA_BASE_URL:")
KEY_LINE_RE = re.compile(r"^(\s*)([A-Za-z0-9_]+):\s*(.*)$")

REQUIRED = (
    ("CC_LLM_MODEL", '"${CC_LLM_MODEL:-qwen2.5:3b}"'),
    ("PIPER_URL", '"http://piper-tts:5500/v1/audio/speech"'),
)

ACCEPTABLE = {
    "CC_LLM_MODEL": {
        '"${CC_LLM_MODEL:-qwen2.5:3b}"',
        "${CC_LLM_MODEL:-qwen2.5:3b}",
        '"qwen2.5:3b"',
        "qwen2.5:3b",
    },
    "PIPER_URL": {
        '"http://piper-tts:5500/v1/audio/speech"',
        "http://piper-tts:5500/v1/audio/speech",
    },
}


def patch_compose(text: str) -> tuple[str, str]:
    """Return (new_text, action) where action is inserted|updated|unchanged."""
    lines = text.splitlines(keepends=True)
    in_service = False
    ollama_idx: int | None = None
    indent = "      "
    found: dict[str, int] = {}

    for i, line in enumerate(lines):
        bare = line.split("\n", 1)[0]
        if SERVICE_RE.match(bare):
            in_service = True
            continue
        if in_service and NEXT_SERVICE_RE.match(bare) and not SERVICE_RE.match(bare):
            break
        if not in_service:
            continue
        m_ol = OLLAMA_LINE_RE.match(bare)
        if m_ol:
            ollama_idx = i
            indent = m_ol.group(1)
        m_key = KEY_LINE_RE.match(bare)
        if m_key and m_key.group(2) in dict(REQUIRED):
            found[m_key.group(2)] = i
            indent = m_key.group(1)

    if ollama_idx is None and not found:
        raise SystemExit("xiaozhi-server OLLAMA_BASE_URL line not found; refusing to patch")

    def with_newline(s: str, like: str) -> str:
        return s + ("\n" if like.endswith("\n") else "")

    actions: list[str] = []
    insert_at = (ollama_idx + 1) if ollama_idx is not None else (max(found.values()) + 1)
    shift = 0

    for key, value in REQUIRED:
        desired = f"{indent}{key}: {value}"
        idx = found.get(key)
        if idx is None:
            like = lines[ollama_idx] if ollama_idx is not None else lines[insert_at - 1]
            lines.insert(insert_at + shift, with_newline(desired, like))
            shift += 1
            actions.append("inserted")
            continue
        current = KEY_LINE_RE.match(lines[idx + shift].rstrip("\n")).group(3).strip()
        if current in ACCEPTABLE[key]:
            continue
        lines[idx + shift] = with_newline(desired, lines[idx + shift])
        actions.append("updated")

    if not actions:
        return text, "unchanged"
    if "updated" in actions and "inserted" not in actions:
        return "".join(lines), "updated"
    return "".join(lines), "inserted"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("compose_file", type=Path, help="Path to docker-compose.yml")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    path: Path = args.compose_file
    original = path.read_text()
    new_text, action = patch_compose(original)
    print(f"{path}: {action}")
    if action != "unchanged" and not args.dry_run:
        path.write_text(new_text)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
