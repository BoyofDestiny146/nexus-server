#!/usr/bin/env python3
"""Insert CC_LLM_MODEL into the xiaozhi-server service of a Compose file.

Used on the Orin deploy copy (e.g. /mnt/xiaozhi/nexus-deploy/docker-compose.yml)
which is not the git tree and will not pick up repo compose edits automatically.

Idempotent. Touches only the xiaozhi-server environment block:
  * preserves OLLAMA_BASE_URL
  * does not change image, TAG, or any other service
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_LINE = '      CC_LLM_MODEL: "${CC_LLM_MODEL:-qwen2.5:3b}"'
SERVICE_RE = re.compile(r"^  xiaozhi-server:\s*$")
NEXT_SERVICE_RE = re.compile(r"^  [A-Za-z0-9._-]+:\s*$")
LLM_LINE_RE = re.compile(r"^(\s*)CC_LLM_MODEL:\s*(.*)$")
OLLAMA_LINE_RE = re.compile(r"^(\s*)OLLAMA_BASE_URL:")


def patch_compose(text: str) -> tuple[str, str]:
    """Return (new_text, action) where action is inserted|updated|unchanged."""
    lines = text.splitlines(keepends=True)
    in_service = False
    llm_idx: int | None = None
    ollama_idx: int | None = None
    llm_indent = "      "

    for i, line in enumerate(lines):
        bare = line.split("\n", 1)[0]
        if SERVICE_RE.match(bare):
            in_service = True
            continue
        if in_service and NEXT_SERVICE_RE.match(bare) and not SERVICE_RE.match(bare):
            break
        if not in_service:
            continue
        m_llm = LLM_LINE_RE.match(bare)
        if m_llm:
            llm_idx = i
            llm_indent = m_llm.group(1)
        m_ol = OLLAMA_LINE_RE.match(bare)
        if m_ol:
            ollama_idx = i
            llm_indent = m_ol.group(1)

    if llm_idx is None and ollama_idx is None:
        raise SystemExit("xiaozhi-server OLLAMA_BASE_URL line not found; refusing to patch")

    desired = f'{llm_indent}CC_LLM_MODEL: "${{CC_LLM_MODEL:-qwen2.5:3b}}"'

    def with_newline(s: str, like: str) -> str:
        return s + ("\n" if like.endswith("\n") else "")

    if llm_idx is not None:
        current_val = LLM_LINE_RE.match(lines[llm_idx].rstrip("\n")).group(2).strip()
        if current_val in (
            '"${CC_LLM_MODEL:-qwen2.5:3b}"',
            "${CC_LLM_MODEL:-qwen2.5:3b}",
            '"qwen2.5:3b"',
            "qwen2.5:3b",
        ):
            return text, "unchanged"
        lines[llm_idx] = with_newline(desired, lines[llm_idx])
        return "".join(lines), "updated"

    insert_at = ollama_idx + 1
    lines.insert(insert_at, with_newline(desired, lines[ollama_idx]))
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
