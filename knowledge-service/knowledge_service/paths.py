"""Resolve source files under the shared /data volume. Never move them."""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from .settings import settings


def absolute_source_path(relative: str | None) -> Path:
    if not relative:
        raise HTTPException(status_code=400, detail="storagePath is required")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise HTTPException(status_code=400, detail="invalid storage path")
    root = Path(settings.source_dir).resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid storage path") from exc
    return path
