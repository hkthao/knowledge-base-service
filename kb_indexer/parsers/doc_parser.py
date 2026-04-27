"""Doc parser: chunks Markdown natively, falls through to Docling for
PDF/Word/HTML when the optional `docling` package is installed.

Output is a list of `DocChunk` dicts, each carrying enough payload that
`indexing.index_doc` can write them to Qdrant + Neo4j the same way code
chunks are handled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Default chunk target — measured in characters, not tokens. Code chunks
# are bounded by AST nodes; doc chunks need an explicit window.
DEFAULT_CHUNK_CHARS = 1200
DEFAULT_OVERLAP_CHARS = 150


@dataclass
class DocChunk:
    chunk_index: int
    title: str | None  # nearest preceding heading
    content: str
    line_start: int
    line_end: int


def parse_file(file_path: str) -> list[DocChunk]:
    suffix = Path(file_path).suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        return _chunk_markdown(Path(file_path).read_text(encoding="utf-8"))
    if suffix in {".pdf", ".docx", ".html"}:
        return _chunk_via_docling(file_path)
    raise ValueError(f"Unsupported doc type: {file_path}")


# ── Markdown ──────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _chunk_markdown(text: str) -> list[DocChunk]:
    """Split by headings, then by character window within each section.

    Heading hierarchy isn't tracked explicitly — the nearest heading on
    each chunk is enough context for retrieval, and avoids a complex tree
    walk that would over-segment short docs.
    """
    sections = _split_by_heading(text)
    chunks: list[DocChunk] = []
    chunk_index = 0
    for section_title, section_lines, line_start in sections:
        section_text = "\n".join(section_lines).strip()
        if not section_text:
            continue
        for window_start, window in _window(section_text, DEFAULT_CHUNK_CHARS, DEFAULT_OVERLAP_CHARS):
            window_lines = window.count("\n") + 1
            chunks.append(DocChunk(
                chunk_index=chunk_index,
                title=section_title,
                content=window,
                line_start=line_start + _line_offset(section_text, window_start),
                line_end=line_start + _line_offset(section_text, window_start) + window_lines - 1,
            ))
            chunk_index += 1
    return chunks


def _split_by_heading(text: str) -> list[tuple[str | None, list[str], int]]:
    sections: list[tuple[str | None, list[str], int]] = []
    current_title: str | None = None
    current_lines: list[str] = []
    current_start = 1

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        match = _HEADING_RE.match(raw_line)
        if match:
            if current_lines or current_title is not None:
                sections.append((current_title, current_lines, current_start))
            current_title = match.group(2).strip()
            current_lines = []
            current_start = lineno + 1
            continue
        current_lines.append(raw_line)

    if current_lines or current_title is not None:
        sections.append((current_title, current_lines, current_start))
    return sections


def _window(text: str, size: int, overlap: int):
    if size <= 0:
        yield 0, text
        return
    step = max(1, size - overlap)
    pos = 0
    while pos < len(text):
        yield pos, text[pos:pos + size]
        if pos + size >= len(text):
            break
        pos += step


def _line_offset(text: str, char_offset: int) -> int:
    return text[:char_offset].count("\n")


# ── Docling fallback (PDF / DOCX / HTML) ──────────────────────────────

def _chunk_via_docling(file_path: str) -> list[DocChunk]:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise RuntimeError(
            f"Parsing {file_path} requires the optional `docling` package. "
            "Install with: pip install docling"
        ) from exc

    converter = DocumentConverter()
    result = converter.convert(file_path)
    markdown = result.document.export_to_markdown()
    return _chunk_markdown(markdown)
