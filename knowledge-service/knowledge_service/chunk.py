"""Turn extracted units into overlapping retrieval chunks."""
from __future__ import annotations

import re
from typing import Any

TARGET_CHARS = 1400
OVERLAP_CHARS = 200
MIN_CHARS = 8

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _clean_line(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _paragraphs(text: str) -> list[str]:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    blocks = re.split(r"\n\s*\n", raw)
    paras: list[str] = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if len(lines) == 1:
            paras.append(lines[0])
            continue
        # Slide bullets / short lines stay as their own paragraphs.
        if all(len(line) <= 180 for line in lines):
            paras.extend(lines)
        else:
            paras.append("\n".join(lines))
    return paras


def _sentences(text: str) -> list[str]:
    cleaned = _clean_line(text)
    if not cleaned:
        return []
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(cleaned) if p.strip()]
    return parts or [cleaned]


def _hard_split(text: str) -> list[str]:
    cleaned = _clean_line(text)
    if len(cleaned) <= TARGET_CHARS:
        return [cleaned] if cleaned else []
    parts: list[str] = []
    start = 0
    length = len(cleaned)
    while start < length:
        end = min(length, start + TARGET_CHARS)
        if end < length:
            window = cleaned[start:end]
            cut = max(window.rfind(". "), window.rfind("? "), window.rfind("! "), window.rfind(" "))
            if cut >= TARGET_CHARS // 3:
                end = start + cut + 1
        piece = cleaned[start:end].strip()
        if piece:
            parts.append(piece)
        if end >= length:
            break
        start = max(end - OVERLAP_CHARS, start + 1)
    return parts


def _split_overflow(text: str) -> list[str]:
    if len(text) <= TARGET_CHARS:
        return [text]
    packed: list[str] = []
    buf = ""
    for sentence in _sentences(text):
        if len(sentence) > TARGET_CHARS:
            if buf:
                packed.append(buf.strip())
                buf = ""
            packed.extend(_hard_split(sentence))
            continue
        candidate = f"{buf} {sentence}".strip() if buf else sentence
        if len(candidate) <= TARGET_CHARS:
            buf = candidate
            continue
        if buf:
            packed.append(buf.strip())
        buf = sentence
    if buf:
        packed.append(buf.strip())
    return packed or [text]


def _pack_paragraphs(paras: list[str]) -> list[str]:
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for para in paras:
        pieces = _split_overflow(para) if len(para) > TARGET_CHARS else [para]
        for piece in pieces:
            extra = len(piece) + (1 if buf else 0)
            if buf and size + extra > TARGET_CHARS:
                chunks.append("\n".join(buf).strip())
                buf = [piece]
                size = len(piece)
            else:
                buf.append(piece)
                size += extra
    if buf:
        chunks.append("\n".join(buf).strip())
    return [c for c in chunks if c]


def chunk_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep slide/page/heading boundaries; split long units with overlap.

    Never merges unrelated slides. Target size is a fallback after slide,
    heading, paragraph, then sentence splits.
    """
    out: list[dict[str, Any]] = []
    for unit in units:
        text = (unit.get("text") or "").strip()
        notes = (unit.get("notes") or "").strip()
        if not text and notes:
            text = notes
            notes = ""
        pieces = _pack_paragraphs(_paragraphs(text)) if text else []
        if not pieces and text:
            pieces = [text]
        meta = {
            "pageNumber": unit.get("pageNumber"),
            "slideNumber": unit.get("slideNumber"),
            "sectionTitle": unit.get("sectionTitle"),
            "kindHint": unit.get("kindHint"),
        }
        for piece in pieces:
            if not piece.strip():
                continue
            if len(piece.strip()) < MIN_CHARS and not meta.get("sectionTitle"):
                continue
            item = {"text": piece.strip(), **meta}
            out.append(item)
        if notes and notes not in pieces and notes != text:
            for piece in _pack_paragraphs(_paragraphs(notes)) or [notes]:
                if not piece.strip():
                    continue
                out.append({"text": piece.strip(), **meta, "kindHint": meta.get("kindHint") or "narrative"})
    return out
