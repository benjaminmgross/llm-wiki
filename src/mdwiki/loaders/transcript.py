"""Transcript loader — converts VTT, SRT, and Fathom-style markdown transcripts
to ``## Speaker [HH:MM:SS]\\n<utterance>`` markdown so the chunker can split
on speaker turns.

Detected by extension (``.vtt`` / ``.srt``) or by the substring ``transcript``
in the filename for markdown variants. Returns an empty string when the
content can't be parsed into recognizable turns; the caller (init's
``empty_load_skipped`` path) skips such files rather than corrupting the
wiki with non-transcript content under a transcript-shaped filename.
"""

from __future__ import annotations

import re
from pathlib import Path

from mdwiki.loaders.base import Loader

_TRANSCRIPT_SUFFIXES: frozenset[str] = frozenset({".vtt", ".srt"})

# Cue-line timestamp matchers.
# VTT: "00:00:00.000 --> 00:00:05.000"
# SRT: "00:00:00,000 --> 00:00:05,000"
_VTT_CUE_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2})[.,]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}[.,]\d{3}\s*$",
    re.MULTILINE,
)
# Fathom-style markdown speaker block: "**Sarah** [00:00:00]"
_FATHOM_BLOCK_RE = re.compile(
    r"^\*\*([A-Za-z][A-Za-z .'\-]{0,40})\*\*\s+\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*$",
    re.MULTILINE,
)
# Inline speaker prefix used by VTT/SRT cue text: "Sarah: utterance"
_SPEAKER_PREFIX_RE = re.compile(r"^([A-Z][A-Za-z .'\-]{0,40}):\s+(.*)$", re.DOTALL)


class TranscriptLoader(Loader):
    """Convert VTT / SRT / Fathom markdown transcripts to speaker-turn H2 markdown."""

    name: str = "TranscriptLoader"

    def can_handle(self, path: Path) -> bool:
        suffix = path.suffix.lower()
        if suffix in _TRANSCRIPT_SUFFIXES:
            return True
        # Markdown transcripts are claimed by filename hint only; plain `.md`
        # without ``transcript`` in the name continues to route to MarkdownLoader.
        if suffix == ".md" and "transcript" in path.stem.lower():
            return True
        return False

    def load_to_markdown(self, path: Path) -> str:
        suffix = path.suffix.lower()
        body = path.read_text(encoding="utf-8")

        if suffix in {".vtt", ".srt"}:
            return _convert_cue_format(body=body)
        if suffix == ".md":
            return _convert_fathom_md(body=body)
        return ""


def _convert_cue_format(*, body: str) -> str:
    """Parse a VTT or SRT body into ``## Speaker [HH:MM:SS]`` markdown.

    Strips the WEBVTT header and SRT cue indices; pairs each timestamp range
    with the lines that follow until the next blank or next cue. Speaker
    extraction is best-effort: lines starting with ``Name: text`` get the
    name promoted into the H2; otherwise the H2 is ``## Unknown [HH:MM:SS]``.
    """
    cue_matches = list(_VTT_CUE_RE.finditer(body))
    if not cue_matches:
        return ""

    sections: list[str] = []
    for i, match in enumerate(cue_matches):
        start_time = match.group(1)
        cue_text_start = match.end()
        cue_text_end = cue_matches[i + 1].start() if i + 1 < len(cue_matches) else len(body)

        cue_text = body[cue_text_start:cue_text_end].strip()
        # Drop the SRT trailing index line (digit on its own line) before the next cue.
        cue_text = re.sub(r"\n\s*\d+\s*$", "", cue_text)

        if not cue_text:
            continue

        speaker, utterance = _split_speaker(cue_text=cue_text)
        sections.append(f"## {speaker} [{start_time}]\n\n{utterance.strip()}\n")

    return "\n".join(sections)


def _convert_fathom_md(*, body: str) -> str:
    """Parse a Fathom-style markdown transcript into ``## Speaker [timestamp]`` blocks.

    Fathom emits blocks like::

        **Sarah** [00:00:00]
        Hello team.

    We promote each block to ``## Sarah [00:00:00]`` followed by the body.
    Returns ``""`` if no Fathom blocks are detected so the caller can skip
    files that match the filename hint but aren't actually transcripts.
    """
    matches = list(_FATHOM_BLOCK_RE.finditer(body))
    if not matches:
        return ""

    sections: list[str] = []
    for i, match in enumerate(matches):
        speaker = match.group(1).strip()
        timestamp = match.group(2)
        block_start = match.end()
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        utterance = body[block_start:block_end].strip()
        if not utterance:
            continue
        sections.append(f"## {speaker} [{timestamp}]\n\n{utterance}\n")

    return "\n".join(sections)


def _split_speaker(*, cue_text: str) -> tuple[str, str]:
    """Split ``cue_text`` into ``(speaker, utterance)`` if it begins with a speaker prefix.

    Falls back to ``("Unknown", cue_text)`` when no speaker prefix is detected.
    """
    match = _SPEAKER_PREFIX_RE.match(cue_text)
    if match:
        return match.group(1).strip(), match.group(2)
    return "Unknown", cue_text
