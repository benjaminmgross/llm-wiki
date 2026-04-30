"""PDF loader — extracts text via ``pypdf`` with optional vision fallback for scanned PDFs.

Default behavior: per-page text extraction joined with a horizontal rule. If extraction
returns empty text (image-only / scanned PDFs), logs a warning and returns empty
unless ``vision_fallback=True`` was passed; with vision fallback enabled, the loader
renders each page to PNG via ``pypdfium2`` and forwards each rendered page to the
provider's ``describe_image`` method.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from pypdf import PdfReader

from mdwiki.llm.base import Provider
from mdwiki.loaders.base import Loader

logger = logging.getLogger(__name__)

_PAGE_SEPARATOR: str = "\n\n---\n\n"
_VISION_RENDER_DPI: float = 150.0


class PdfLoader(Loader):
    """Read a PDF and emit one markdown document with one section per page.

    Parameters
    ----------
    provider : Provider, optional
        Required only when ``vision_fallback=True``. Used to call ``describe_image``
        on each rendered page when text extraction returns empty.
    vision_fallback : bool, optional
        When True, fall back to vision OCR via ``provider.describe_image`` if pypdf's
        text extraction returns empty. Default False (matches v1.0 behavior).
    """

    name: str = "PdfLoader"

    def __init__(self, provider: Provider | None = None, *, vision_fallback: bool = False) -> None:
        self._provider = provider
        self._vision_fallback = vision_fallback

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def load_to_markdown(self, path: Path) -> str:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        joined = _PAGE_SEPARATOR.join(p.strip() for p in pages if p.strip())
        if joined:
            return joined + "\n"

        if self._vision_fallback and self._provider is not None:
            return self._vision_extract(path)

        logger.warning(
            "PdfLoader: no text extracted from %s — likely a scanned/image PDF; "
            "set [loaders.pdf].vision_fallback = true in config to OCR via the LLM.",
            path,
        )
        return ""

    def _vision_extract(self, path: Path) -> str:
        """Render each PDF page to PNG and forward each to ``provider.describe_image``."""
        # Lazy import — pypdfium2 is a hot import (~80ms) and not always needed.
        import pypdfium2 as pdfium

        if self._provider is None:
            raise RuntimeError("PdfLoader vision fallback requires a provider")

        descriptions: list[str] = []
        document = pdfium.PdfDocument(str(path))
        try:
            for page_index, page in enumerate(document, start=1):
                pil_image = page.render(scale=_VISION_RENDER_DPI / 72.0).to_pil()
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                pil_image.save(tmp_path, format="PNG")
                try:
                    desc = self._provider.describe_image(tmp_path).strip()
                    if desc:
                        descriptions.append(f"## Page {page_index}\n\n{desc}")
                finally:
                    tmp_path.unlink(missing_ok=True)
        finally:
            document.close()

        if not descriptions:
            logger.warning("PdfLoader vision fallback: provider returned empty for every page in %s", path)
            return ""
        return _PAGE_SEPARATOR.join(descriptions) + "\n"
