#!/usr/bin/env python3
"""Archive Chroma from a live XiaoZhi container without stopping it.

Production overlay runs as root, so ``~/.local/share/careconnect/chroma`` is
``/root/.local/share/careconnect/chroma``. The xiaozhiuser HOME path is also
probed. SQLite is copied with sqlite3.Connection.backup (online backup API)
so chroma.sqlite3 is consistent while the process is running.

Stdout is either a path (discover) or a tar stream (archive to -). Status
and errors go to stderr. Database rows are never printed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

CANDIDATES = (
    "/root/.local/share/careconnect/chroma",
    os.path.expanduser("~/.local/share/careconnect/chroma"),
    "/opt/xiaozhi-esp32-server/.local/share/careconnect/chroma",
)


def _prefix_path(path: str) -> str:
    prefix = os.environ.get("NEXUS_CHROMA_FS_PREFIX", "").rstrip("/")
    if prefix:
        return prefix + path
    return path


def _nonempty_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    for _root, _dirs, files in os.walk(path):
        if files:
            return True
    return False


def discover() -> Path | None:
    seen: list[str] = []
    for raw in CANDIDATES:
        path = Path(_prefix_path(raw))
        key = str(path)
        if key in seen:
            continue
        seen.append(key)
        if _nonempty_dir(path):
            return path
    extra = os.environ.get("NEXUS_CHROMA_EXTRA", "")
    if extra:
        path = Path(extra)
        if _nonempty_dir(path):
            return path
    return None


def _sqlite_backup(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    uri = "file:{}?mode=ro".format(src.as_posix())
    src_conn = sqlite3.connect(uri, uri=True, timeout=60.0)
    try:
        dst_conn = sqlite3.connect(str(dest), timeout=60.0)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def copy_consistent(src: Path, dest: Path) -> str:
    """Copy chroma tree into dest. Returns method label."""
    dest.mkdir(parents=True, exist_ok=True)
    method = "copy"
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        out_dir = dest if rel == Path(".") else dest / rel
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            if name.endswith(("-wal", "-shm")):
                continue
            sfile = Path(root) / name
            dfile = out_dir / name
            if name.endswith(".sqlite3") or name.endswith(".sqlite"):
                try:
                    _sqlite_backup(sfile, dfile)
                    method = "sqlite3.Connection.backup"
                    continue
                except sqlite3.Error as exc:
                    print("chroma: sqlite backup failed (%s); falling back to copy" % type(exc).__name__, file=sys.stderr)
            shutil.copy2(sfile, dfile)
    return method


def write_tar(src: Path, dest_tar: Path | None) -> None:
    staging = Path(tempfile.mkdtemp(prefix="nexus-chroma-"))
    try:
        copied = staging / "chroma"
        method = copy_consistent(src, copied)
        if not any(copied.rglob("*")):
            raise SystemExit("chroma: copy produced an empty tree")
        print("chroma: archived source=%s method=%s" % (src, method), file=sys.stderr)
        if dest_tar is None:
            with tarfile.open(fileobj=sys.stdout.buffer, mode="w:gz") as tf:
                tf.add(copied, arcname="chroma")
        else:
            dest_tar.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(dest_tar, mode="w:gz") as tf:
                tf.add(copied, arcname="chroma")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Live-consistent Chroma archive")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover")
    sub.add_parser("status")
    arch = sub.add_parser("archive")
    arch.add_argument("--path", default="")
    arch.add_argument("-o", "--output", default="-", help="tar.gz path or - for stdout")
    args = parser.parse_args()

    found = Path(args.path) if getattr(args, "path", "") else discover()

    if args.cmd == "discover":
        if found is None:
            return 2
        print(found.as_posix())
        return 0

    if args.cmd == "status":
        if found is None:
            print("present=no", file=sys.stderr)
            return 2
        print("present=yes")
        print("source=%s" % found.as_posix())
        return 0

    if found is None or not _nonempty_dir(found):
        print("chroma: directory missing or empty", file=sys.stderr)
        return 2
    out = None if args.output == "-" else Path(args.output)
    write_tar(found, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
