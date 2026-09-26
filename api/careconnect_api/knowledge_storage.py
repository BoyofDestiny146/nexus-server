"""Knowledge source files on the existing CareConnect /data volume.

Compose already mounts named volume ``cc-voice`` at ``/data`` on the API
container (same path as ``voice_config.json``). Source bytes live under
``/data/knowledge-sources/{kb_id}/{source_id}/source{ext}``.

They are never served by Caddy. Future RAG reads ``storage_path`` from
``cc_knowledge_source`` and the same directory; the UI does not need to change.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .envelope import APIException
from .settings import settings

log = logging.getLogger("knowledge")

MAX_SOURCE_BYTES = 25 * 1024 * 1024

SOURCE_TYPES = ("pdf", "docx", "pptx", "txt", "markdown", "image", "text")
TEXT_SOURCE_TYPES = frozenset({"txt", "markdown", "text"})
STATUSES = ("uploaded", "pending", "processing", "ready", "failed", "disabled")

_TYPE_EXTS: dict[str, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "docx": (".docx",),
    "pptx": (".pptx",),
    "txt": (".txt",),
    "markdown": (".md", ".markdown"),
    "image": (".png", ".jpg", ".jpeg", ".webp", ".gif"),
    "text": (".txt",),
}
_TYPE_MIME: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
    "markdown": "text/markdown",
    "image": "application/octet-stream",
    "text": "text/plain",
}
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def knowledge_source_root() -> Path:
    return Path(settings.knowledge_source_dir)


def validate_source_type(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if value not in SOURCE_TYPES:
        raise APIException(400, f"sourceType must be one of: {', '.join(SOURCE_TYPES)}")
    return value


def validate_status(raw: str | None, *, default: str = "uploaded") -> str:
    value = (raw or default).strip().lower() or default
    if value not in STATUSES:
        raise APIException(400, f"status must be one of: {', '.join(STATUSES)}")
    return value


def extension_for(source_type: str, filename: str | None) -> str:
    allowed = _TYPE_EXTS[source_type]
    raw = (filename or "").strip()
    ext = Path(raw).suffix.lower() if raw else ""
    if source_type == "text":
        return ".txt"
    if ext in allowed:
        return ext
    if source_type == "image" and not ext:
        return ".png"
    return allowed[0]


def mime_for(source_type: str, ext: str) -> str:
    if source_type == "image":
        return _IMAGE_MIME.get(ext.lower(), "application/octet-stream")
    return _TYPE_MIME[source_type]


def display_filename(name: str, ext: str) -> str:
    stem = _SAFE_NAME.sub("-", (name or "source").strip())[:80].strip(".-") or "source"
    if not ext.startswith("."):
        ext = f".{ext}"
    return f"{stem}{ext}"


def relative_source_path(knowledge_base_id: int, source_id: int, ext: str) -> str:
    if not ext.startswith("."):
        ext = f".{ext}"
    return f"{int(knowledge_base_id)}/{int(source_id)}/source{ext}"


def absolute_source_path(relative: str | None) -> Path:
    if not relative:
        raise APIException(404, "source file not found")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise APIException(400, "invalid storage path")
    root = knowledge_source_root().resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise APIException(400, "invalid storage path") from exc
    return path


def write_source_bytes(
    knowledge_base_id: int,
    source_id: int,
    ext: str,
    content: bytes,
    *,
    previous_relative: str | None = None,
) -> str:
    if len(content) > MAX_SOURCE_BYTES:
        raise APIException(400, f"file exceeds {MAX_SOURCE_BYTES} bytes")
    relative = relative_source_path(knowledge_base_id, source_id, ext)
    dest = absolute_source_path(relative)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(content)
    tmp.replace(dest)
    if previous_relative and previous_relative != relative:
        try:
            old = absolute_source_path(previous_relative)
            if old.is_file():
                old.unlink()
        except APIException:
            pass
        except OSError as exc:
            log.warning("could not remove replaced source %s: %s", previous_relative, exc)
    log.info(
        "knowledge source wrote kb=%s source=%s bytes=%s path=%s",
        knowledge_base_id,
        source_id,
        len(content),
        relative,
    )
    return relative


def delete_source_file(relative: str | None) -> None:
    if not relative:
        return
    try:
        path = absolute_source_path(relative)
    except APIException:
        return
    try:
        if path.is_file():
            path.unlink()
        parent = path.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError as exc:
        log.warning("could not delete source file %s: %s", relative, exc)


def read_text_preview(relative: str | None, *, limit: int = 400_000) -> str:
    if not relative:
        return ""
    try:
        path = absolute_source_path(relative)
    except APIException:
        return ""
    if not path.is_file():
        return ""
    try:
        data = path.read_bytes()[:limit]
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")
