"""PDF loader — extracts text via ``pypdf`` and joins pages with a horizontal rule.

If extraction returns no text (image-only / scanned PDFs), logs a warning and returns
empty string. ``init_wiki`` interprets empty as "skip this source" — the user can
re-ingest with vision OCR enabled (Phase 5) once that ships.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader

from mdwiki.loaders.base import Loader

logger = logging.getLogger(__name__)

_PAGE_SEPARATOR: str = "\n\n---\n\n"


class PdfLoader(Loader):
    """Read a PDF and emit one markdown document with one section per page."""

    name: str = "PdfLoader"

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def load_to_markdown(self, path: Path) -> str:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        joined = _PAGE_SEPARATOR.join(p.strip() for p in pages if p.strip())
        if not joined:
            logger.warning(
                "PdfLoader: no text extracted from %s — likely a scanned/image PDF; "
                "enable vision_fallback in config (Phase 5) to OCR via the LLM.",
                path,
            )
            return ""
        return joined + "\n"
