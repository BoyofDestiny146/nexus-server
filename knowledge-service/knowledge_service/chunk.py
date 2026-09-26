"""Turn extracted units into overlapping retrieval chunks."""
from __future__ import annotations

from typing import Any

TARGET_CHARS = 1400
OVERLAP_CHARS = 200
MIN_CHARS = 40


def _split_long(text: str) -> list[str]:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= TARGET_CHARS:
        return [cleaned] if len(cleaned) >= MIN_CHARS or cleaned else ([] if not cleaned else [cleaned])
    parts: list[str] = []
    start = 0
    length = len(cleaned)
    while start < length:
        end = min(length, start + TARGET_CHARS)
        if end < length:
            window = cleaned[start:end]
            cut = max(window.rfind(". "), window.rfind("? "), window.rfind("! "), window.rfind("\n"))
            if cut >= TARGET_CHARS // 3:
                end = start + cut + 1
        piece = cleaned[start:end].strip()
        if piece:
            parts.append(piece)
        if end >= length:
            break
        start = max(end - OVERLAP_CHARS, start + 1)
    return parts


def chunk_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep slide/page boundaries; split long units with overlap."""
    out: list[dict[str, Any]] = []
    for unit in units:
        text = (unit.get("text") or "").strip()
        if not text:
            continue
        pieces = _split_long(text)
        if not pieces and text:
            pieces = [text]
        for piece in pieces:
            if not piece.strip():
                continue
            out.append(
                {
                    "text": piece.strip(),
                    "pageNumber": unit.get("pageNumber"),
                    "slideNumber": unit.get("slideNumber"),
                    "sectionTitle": unit.get("sectionTitle"),
                }
            )
    return out
