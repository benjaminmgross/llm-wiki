"""Tests for the TranscriptLoader (Phase 3).

Detects VTT, SRT, and Fathom-format markdown transcripts; normalizes each to
``## Speaker [HH:MM:SS]\\n<utterance>`` markdown so the chunker can split per
speaker turn.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.loaders.transcript import TranscriptLoader


@pytest.mark.unit
def test_can_handle_vtt() -> None:
    loader = TranscriptLoader()
    assert loader.can_handle(Path("meeting.vtt"))


@pytest.mark.unit
def test_can_handle_srt() -> None:
    loader = TranscriptLoader()
    assert loader.can_handle(Path("subtitle.srt"))


@pytest.mark.unit
def test_can_handle_transcript_md_filename() -> None:
    """A markdown file with -transcript suffix is claimed by TranscriptLoader."""
    loader = TranscriptLoader()
    assert loader.can_handle(Path("2026-04-30-call-transcript.md"))


@pytest.mark.unit
def test_does_not_handle_plain_markdown() -> None:
    """Plain .md files without 'transcript' in the filename go to MarkdownLoader."""
    loader = TranscriptLoader()
    assert not loader.can_handle(Path("doc.md"))


@pytest.mark.unit
def test_load_vtt_produces_speaker_turn_h2(tmp_path: Path) -> None:
    """A VTT cue with 'Speaker: utterance' becomes '## Speaker [HH:MM:SS]'."""
    vtt = tmp_path / "meeting.vtt"
    vtt.write_text(
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:05.000\n"
        "Sarah: Hello team and welcome to the meeting today.\n\n"
        "00:00:05.000 --> 00:00:10.000\n"
        "Bob: Thanks Sarah, glad to be here.\n"
    )

    md = TranscriptLoader().load_to_markdown(vtt)

    assert "## Sarah [00:00:00]" in md
    assert "Hello team and welcome" in md
    assert "## Bob [00:00:05]" in md
    assert "Thanks Sarah" in md


@pytest.mark.unit
def test_load_srt_produces_speaker_turn_h2(tmp_path: Path) -> None:
    """SRT format with speaker prefix gets normalized like VTT."""
    srt = tmp_path / "meeting.srt"
    srt.write_text(
        "1\n"
        "00:00:00,000 --> 00:00:05,000\n"
        "Sarah: Hello team and welcome.\n\n"
        "2\n"
        "00:00:05,000 --> 00:00:10,000\n"
        "Bob: Thanks Sarah glad to be here.\n"
    )

    md = TranscriptLoader().load_to_markdown(srt)

    assert "## Sarah [00:00:00]" in md
    assert "## Bob [00:00:05]" in md


@pytest.mark.unit
def test_load_fathom_md_passes_speaker_blocks_through(tmp_path: Path) -> None:
    """Fathom-style markdown transcripts (already speaker-prefixed) get H2-promoted."""
    fathom = tmp_path / "fathom-transcript.md"
    fathom.write_text(
        "# Meeting transcript\n\n"
        "**Sarah** [00:00:00]\n"
        "Hello team and welcome.\n\n"
        "**Bob** [00:00:05]\n"
        "Thanks Sarah glad to be here.\n"
    )

    md = TranscriptLoader().load_to_markdown(fathom)

    # Fathom blocks become H2-prefixed for chunker compatibility.
    assert "## Sarah [00:00:00]" in md
    assert "Hello team and welcome" in md
    assert "## Bob [00:00:05]" in md


@pytest.mark.unit
def test_load_returns_empty_string_on_unrecognized_content(tmp_path: Path) -> None:
    """A file that claims to be a transcript but has no recognizable structure returns ''."""
    weird = tmp_path / "weird-transcript.md"
    weird.write_text("Some random text with no speaker turns and no timestamps.\n")

    md = TranscriptLoader().load_to_markdown(weird)

    # Empty signals to init "loader couldn't extract" — caller skips the file
    # rather than corrupting the wiki with non-transcript content under a
    # transcript-shaped filename.
    assert md.strip() == ""
