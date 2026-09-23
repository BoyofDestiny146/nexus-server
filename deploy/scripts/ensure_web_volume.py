#!/usr/bin/env python3
"""Ensure the compose ``web`` service can copy into nexus_web-static.

Used on the Orin deploy copy (e.g. /mnt/xiaozhi/nexus-deploy/docker-compose.yml)
which is not the git tree and must NOT be overwritten by repo compose
(production Caddy is 80/443; repo compose still has 18180/18443).

Idempotent. Touches only the ``web`` service:
  * user: "0:0"
  * command copies as root then chowns to appuser (no chmod 777)

Does not modify Caddy ports, image tags, CC_LLM_MODEL, PIPER_URL,
OLLAMA_BASE_URL, or any other service.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SERVICE_RE = re.compile(r"^  web:\s*$")
NEXT_SERVICE_RE = re.compile(r"^  [A-Za-z0-9._-]+:\s*$")

USER_LINE = 'user: "0:0"'
CANONICAL_SCRIPT = (
    "chown appuser:appuser /srv/web && "
    "cp -a /app/out/. /srv/web/ && "
    "chown -R appuser:appuser /srv/web && "
    "echo 'web: static export copied'"
)
COMMAND_LINE = f'command: ["/bin/sh", "-c", "{CANONICAL_SCRIPT}"]'

_USER_OK = {'"0:0"', "'0:0'", "0:0"}


def _with_newline(s: str, like: str) -> str:
    return s + ("\n" if like.endswith("\n") else "")


def _service_bounds(lines: list[str]) -> tuple[int, int]:
    start = None
    for i, line in enumerate(lines):
        bare = line.split("\n", 1)[0]
        if SERVICE_RE.match(bare):
            start = i
            continue
        if start is not None and NEXT_SERVICE_RE.match(bare) and not SERVICE_RE.match(bare):
            return start, i
    if start is None:
        raise SystemExit("web service not found; refusing to patch")
    return start, len(lines)


def _body_indent(lines: list[str], start: int, end: int) -> str:
    for i in range(start + 1, end):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s+)\S", raw)
        if m:
            return m.group(1)
    return "    "


def _key_name(line: str, indent: str) -> str | None:
    if not line.startswith(indent):
        return None
    rest = line[len(indent) :]
    if rest.startswith(" ") or rest.startswith("\t") or rest.startswith("-"):
        return None
    m = re.match(r"^([A-Za-z0-9._-]+|<<):", rest)
    return m.group(1) if m else None


def _key_spans(lines: list[str], start: int, end: int, indent: str) -> dict[str, tuple[int, int]]:
    """Map service-level key -> [start, end) line indices."""
    found: list[tuple[str, int]] = []
    for i in range(start + 1, end):
        name = _key_name(lines[i].split("\n", 1)[0], indent)
        if name is not None:
            found.append((name, i))
    spans: dict[str, tuple[int, int]] = {}
    for i, (name, idx) in enumerate(found):
        stop = found[i + 1][1] if i + 1 < len(found) else end
        spans[name] = (idx, stop)
    return spans


def _user_ok(line: str) -> bool:
    if ":" not in line:
        return False
    return line.split(":", 1)[1].strip() in _USER_OK


def _command_ok(block: str) -> bool:
    compact = re.sub(r"\s+", " ", block)
    return (
        "chown appuser:appuser /srv/web" in compact
        and "cp -a /app/out/. /srv/web/" in compact
        and "chown -R appuser:appuser /srv/web" in compact
        and "web: static export copied" in compact
    )


def patch_compose(text: str) -> tuple[str, str]:
    """Return (new_text, action) where action is inserted|updated|unchanged."""
    lines = text.splitlines(keepends=True)
    start, end = _service_bounds(lines)
    indent = _body_indent(lines, start, end)
    spans = _key_spans(lines, start, end, indent)
    actions: list[str] = []

    if "user" in spans:
        u0, u1 = spans["user"]
        if u1 - u0 != 1 or not _user_ok(lines[u0]):
            like = lines[u0]
            del lines[u0:u1]
            lines.insert(u0, _with_newline(f"{indent}{USER_LINE}", like))
            actions.append("updated")
            end += 1 - (u1 - u0)
            spans = _key_spans(lines, start, end, indent)
    else:
        if "<<" in spans:
            insert_at = spans["<<"][1]
        elif "image" in spans:
            insert_at = spans["image"][1]
        else:
            insert_at = start + 1
        like = lines[insert_at - 1] if insert_at > 0 else "\n"
        lines.insert(insert_at, _with_newline(f"{indent}{USER_LINE}", like))
        actions.append("inserted")
        end += 1
        spans = _key_spans(lines, start, end, indent)

    if "command" in spans:
        c0, c1 = spans["command"]
        block = "".join(lines[c0:c1])
        if not _command_ok(block):
            like = lines[c0]
            del lines[c0:c1]
            lines.insert(c0, _with_newline(f"{indent}{COMMAND_LINE}", like))
            actions.append("updated")
            end += 1 - (c1 - c0)
            spans = _key_spans(lines, start, end, indent)
    else:
        insert_at = spans["user"][1] if "user" in spans else start + 1
        like = lines[insert_at - 1] if insert_at > 0 else "\n"
        lines.insert(insert_at, _with_newline(f"{indent}{COMMAND_LINE}", like))
        actions.append("inserted")

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
