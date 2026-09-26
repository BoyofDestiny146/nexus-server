"""Extract text units from Phase 2 source types. Images stay metadata-only."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .chunk import chunk_units
from .paths import absolute_source_path

log = logging.getLogger("knowledge-service")

IMAGE_TYPES = frozenset({"image"})
TEXT_TYPES = frozenset({"txt", "markdown", "text"})


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    return data.decode("utf-8", errors="replace")


def _unit(
    text: str,
    *,
    page_number: int | None = None,
    slide_number: int | None = None,
    section_title: str | None = None,
    kind_hint: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "text": text,
        "pageNumber": page_number,
        "slideNumber": slide_number,
        "sectionTitle": section_title,
    }
    if kind_hint:
        row["kindHint"] = kind_hint
    if notes:
        row["notes"] = notes
    return row


def extract_units(source_type: str, path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    kind = (source_type or "").strip().lower()
    if kind in IMAGE_TYPES:
        return [], {
            "parser": None,
            "skipped": "image",
            "reason": "Images are metadata-only until a vision parser is added",
            "filename": path.name,
        }
    if kind == "markdown":
        return _extract_markdown(path)
    if kind in TEXT_TYPES:
        text = _read_text(path)
        return (
            [_unit(text)],
            {"parser": "utf8", "characters": len(text)},
        )
    if kind == "pdf":
        return _extract_pdf(path)
    if kind == "docx":
        return _extract_docx(path)
    if kind == "pptx":
        return _extract_pptx(path)
    raise HTTPException(status_code=400, detail=f"unsupported sourceType: {source_type}")


def _extract_pdf(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    units: list[dict[str, Any]] = []
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        units.append(_unit(text, page_number=index, section_title=f"Page {index}"))
    return units, {"parser": "pypdf", "pages": len(reader.pages), "textPages": len(units)}


def _extract_docx(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from docx import Document

    doc = Document(str(path))
    units: list[dict[str, Any]] = []
    heading: str | None = None
    buffer: list[str] = []
    heading_count = 0
    table_count = 0

    def flush() -> None:
        text = "\n".join(part for part in buffer if part.strip()).strip()
        buffer.clear()
        if not text:
            return
        units.append(_unit(text, section_title=heading))

    for para in doc.paragraphs:
        style = (para.style.name if para.style is not None else "") or ""
        text = (para.text or "").strip()
        if style.startswith("Heading"):
            flush()
            heading = text or heading
            heading_count += 1
            continue
        if text:
            buffer.append(text)
    flush()

    for table in doc.tables:
        table_count += 1
        rows: list[str] = []
        for row in table.rows:
            cells = [((cell.text or "").strip()) for cell in row.cells]
            line = " | ".join(cell for cell in cells if cell)
            if line:
                rows.append(line)
        text = "\n".join(rows).strip()
        if text:
            units.append(_unit(text, section_title=heading or "Table", kind_hint="table"))
    return units, {
        "parser": "python-docx",
        "paragraphUnits": len(units) - table_count,
        "tables": table_count,
        "headings": heading_count,
    }


def _extract_markdown(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    text = _read_text(path)
    units: list[dict[str, Any]] = []
    heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        buffer.clear()
        if not body and not heading:
            return
        units.append(_unit(body or heading or "", section_title=heading))

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            flush()
            heading = stripped.lstrip("#").strip() or heading
            continue
        buffer.append(line)
    flush()
    if not units and text.strip():
        units.append(_unit(text.strip()))
    return units, {"parser": "markdown", "characters": len(text), "headings": sum(1 for u in units if u.get("sectionTitle"))}


def _shape_paragraphs(shape) -> list[str]:
    if not getattr(shape, "has_text_frame", False):
        return []
    frame = shape.text_frame
    if frame is None:
        return []
    parts: list[str] = []
    for para in frame.paragraphs:
        text = (para.text or "").strip()
        if text:
            parts.append(text)
    return parts


def _placeholder_type(shape):
    if not getattr(shape, "is_placeholder", False):
        return None
    try:
        return shape.placeholder_format.type
    except Exception:
        return None


def _dedupe_exact(parts: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        key = " ".join((part or "").split())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(part.strip())
    return out


def _extract_pptx(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

    skip_placeholders = {
        PP_PLACEHOLDER.DATE,
        PP_PLACEHOLDER.FOOTER,
        PP_PLACEHOLDER.HEADER,
        PP_PLACEHOLDER.SLIDE_NUMBER,
    }
    title_placeholders = {
        PP_PLACEHOLDER.TITLE,
        PP_PLACEHOLDER.CENTER_TITLE,
        PP_PLACEHOLDER.VERTICAL_TITLE,
    }

    pres = Presentation(str(path))
    units: list[dict[str, Any]] = []
    for index, slide in enumerate(pres.slides, start=1):
        title = ""
        subtitle = ""
        body: list[str] = []
        tables: list[str] = []
        title_shape = getattr(slide.shapes, "title", None)
        if title_shape is not None:
            title = "\n".join(_shape_paragraphs(title_shape)).strip()

        for shape in slide.shapes:
            if title_shape is not None and shape == title_shape:
                continue
            ph = _placeholder_type(shape)
            if ph in skip_placeholders:
                continue
            if getattr(shape, "shape_type", None) == MSO_SHAPE_TYPE.TABLE or (
                ph == PP_PLACEHOLDER.TABLE and getattr(shape, "has_table", False)
            ):
                table = shape.table
                rows: list[str] = []
                for row in table.rows:
                    cells = [((cell.text or "").strip()) for cell in row.cells]
                    line = " | ".join(cell for cell in cells if cell)
                    if line:
                        rows.append(line)
                text = "\n".join(rows).strip()
                if text:
                    tables.append(text)
                continue
            paras = _shape_paragraphs(shape)
            if not paras:
                continue
            joined = "\n".join(paras).strip()
            if ph in title_placeholders and not title:
                title = joined
                continue
            if ph == PP_PLACEHOLDER.SUBTITLE and not subtitle:
                subtitle = joined
                continue
            if title and joined == title:
                continue
            if subtitle and joined == subtitle:
                continue
            body.extend(paras)

        body = _dedupe_exact([p for p in body if p != title and p != subtitle])
        notes = ""
        if slide.has_notes_slide:
            frame = slide.notes_slide.notes_text_frame
            notes = (frame.text or "").strip() if frame is not None else ""

        section = title or f"Slide {index}"
        body_text = "\n".join(body).strip()
        if subtitle and subtitle not in body_text:
            body_text = "\n".join(p for p in [subtitle, body_text] if p).strip()
        if not body_text and title:
            body_text = title
        if body_text:
            units.append(_unit(body_text, slide_number=index, section_title=section))
        for table_text in _dedupe_exact(tables):
            if table_text == body_text:
                continue
            units.append(
                _unit(table_text, slide_number=index, section_title=section, kind_hint="table")
            )
        if notes and notes not in {body_text, title, subtitle}:
            units.append(_unit(notes, slide_number=index, section_title=section, kind_hint="narrative"))
    return units, {
        "parser": "python-pptx",
        "slides": len(list(pres.slides)),
        "textSlides": len({u["slideNumber"] for u in units}),
        "units": len(units),
    }


def extract_file(source_type: str, storage_path: str) -> dict[str, Any]:
    path = absolute_source_path(storage_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="source file not found")
    units, metadata = extract_units(source_type, path)
    character_count = int(metadata.get("characters") or 0)
    if character_count <= 0:
        character_count = sum(len((unit.get("text") or "")) for unit in units)
    metadata["characterCount"] = character_count
    metadata["sourceType"] = source_type
    metadata["storagePath"] = storage_path
    metadata["unitCount"] = len(units)
    log.info(
        "extracted type=%s path=%s units=%s chars=%s",
        source_type,
        storage_path,
        len(units),
        character_count,
    )
    return {
        "units": units,
        "metadata": metadata,
        "characterCount": character_count,
        "unitCount": len(units),
    }


def chunks_from_units(units: list[dict[str, Any]]) -> dict[str, Any]:
    chunks = chunk_units(units)
    for index, chunk in enumerate(chunks):
        chunk["chunkIndex"] = index
        chunk["contentHash"] = content_hash(chunk["text"])
    return {"chunks": chunks, "chunkCount": len(chunks)}


def extract_chunks(source_type: str, storage_path: str) -> dict[str, Any]:
    extracted = extract_file(source_type, storage_path)
    chunked = chunks_from_units(list(extracted.get("units") or []))
    metadata = dict(extracted.get("metadata") or {})
    metadata["chunkCount"] = chunked["chunkCount"]
    log.info(
        "chunked type=%s path=%s chunks=%s",
        source_type,
        storage_path,
        chunked["chunkCount"],
    )
    return {
        "chunks": chunked["chunks"],
        "units": extracted.get("units") or [],
        "metadata": metadata,
        "chunkCount": chunked["chunkCount"],
        "characterCount": extracted.get("characterCount") or 0,
    }
