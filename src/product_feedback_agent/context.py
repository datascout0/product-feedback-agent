"""Product-context ingestion: load Markdown/text strategy docs into sections.

Design notes (see docs/decisions.md for the broader architecture):
- document_id is derived from the source filename (not file content), so
  re-importing the same logical file always yields the same document_id
  even if the document's text is later edited.
- section_id is derived from document_id + section_index, so re-splitting
  an unchanged file always yields the same section_ids.
- Section text (and plain-text document text) is preserved exactly as it
  appears in the source file, with no leading/trailing trimming, so later
  retrieval can cite it verbatim.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from product_feedback_agent.schemas import ContextDocument, ContextSection

SUPPORTED_EXTENSIONS = {".md", ".txt"}

DOCUMENT_TYPE_BY_STEM = {
    "product_strategy": "strategy",
    "roadmap": "roadmap",
    "personas": "personas",
    "features": "features",
    "research_notes": "research",
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


class ContextIngestionError(Exception):
    """Raised when a product-context file cannot be processed."""


def load_context_file(path: str | Path) -> ContextDocument:
    """Load a UTF-8 Markdown or plain-text product-context file."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise ContextIngestionError(f"Unsupported file extension: {path.suffix!r}")

    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise ContextIngestionError(f"{path.name} is empty or contains only whitespace.")

    document_id = _generate_document_id(path.name)
    document_type = _infer_document_type(path.stem)

    if suffix == ".md":
        raw_sections = _split_markdown(content)
    else:
        raw_sections = [(None, content)]

    sections = [
        ContextSection(
            section_id=_generate_section_id(document_id, index),
            document_id=document_id,
            heading=heading,
            text=text,
            section_index=index,
        )
        for index, (heading, text) in enumerate(raw_sections)
    ]

    return ContextDocument(
        document_id=document_id,
        filename=path.name,
        document_type=document_type,
        sections=sections,
    )


def _split_markdown(content: str) -> list[tuple[str | None, str]]:
    """Split Markdown on headings (# through ######), each kept with its body."""
    sections: list[tuple[str | None, str]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in content.splitlines(keepends=True):
        match = _HEADING_RE.match(line.rstrip("\n"))
        if match:
            _flush_section(sections, current_heading, current_lines)
            current_heading = match.group(2).strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    _flush_section(sections, current_heading, current_lines)
    return sections


def _flush_section(
    sections: list[tuple[str | None, str]],
    heading: str | None,
    lines: list[str],
) -> None:
    text = "".join(lines)
    if text.strip():
        sections.append((heading, text))


def _generate_document_id(filename: str) -> str:
    digest = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:16]
    return f"doc_{digest}"


def _generate_section_id(document_id: str, section_index: int) -> str:
    return f"{document_id}_sec{section_index:03d}"


def _infer_document_type(stem: str) -> str:
    return DOCUMENT_TYPE_BY_STEM.get(stem.lower(), "context")
