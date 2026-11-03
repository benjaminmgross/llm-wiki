"""Capture command orchestration independent of immutable source storage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mdwiki import ingest
from mdwiki.capture import CaptureIngestError, CaptureInput, CaptureRecord, export_capture, get_capture, register_capture


@dataclass(frozen=True, kw_only=True)
class CaptureAddResult:
    """Registration and optional configured-provider ingestion outcome."""

    record: CaptureRecord
    ingest_result: ingest.IngestResult | None


def add_capture(
    *, wiki_root: Path, capture: CaptureInput, no_ingest: bool = False, export_path: Path | None = None, yes: bool = False
) -> CaptureAddResult:
    """Register, optionally export, and ingest through the configured provider."""
    record = register_capture(wiki_root=wiki_root, capture=capture)
    if export_path is not None:
        export_capture(wiki_root=wiki_root, source_id=record.source_id, path=export_path)
    if no_ingest:
        return CaptureAddResult(record=get_capture(wiki_root=wiki_root, source_id=record.source_id), ingest_result=None)

    try:
        result = ingest.ingest_source(wiki_root, record.source_id, yes=yes)
    except Exception as exc:
        raise CaptureIngestError(source_id=record.source_id, detail=str(exc)) from exc
    return CaptureAddResult(record=get_capture(wiki_root=wiki_root, source_id=record.source_id), ingest_result=result)
