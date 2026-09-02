"""Page frontmatter metadata: ``title``, ``type``, ``created``, ``updated``, ``sources``, ``tags``.

Every page mdwiki writes carries these keys so a wiki is usable from Obsidian
and Dataview without post-processing. The managed keys are rendered in a fixed
order; any other key the page already had (including ``previous_hash``, which
``version_chain`` re-sets afterwards) is preserved verbatim after them. Dates
are ISO ``YYYY-MM-DD`` strings both on write and on read.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from mdwiki.version_chain import _FRONTMATTER_RE

MANAGED_KEYS: tuple[str, ...] = ("title", "type", "created", "updated", "sources", "tags")
_PLAIN_SCALAR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _./()+-]*")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_YAML_RESERVED: frozenset[str] = frozenset({"true", "false", "null", "yes", "no", "on", "off", "~"})
_H1_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)


def read_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return ``(metadata, body)``; ``metadata`` is ``{}`` when the page has no frontmatter block."""
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text
    try:
        loaded = yaml.safe_load(match.group("inner")) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(loaded, dict):
        return {}, text
    return {str(key): _normalize_value(value) for key, value in loaded.items()}, text[match.end() :]


def apply_page_metadata(
    content: str,
    *,
    path: str,
    kind: str,
    source_ids: Iterable[str],
    today: str | None = None,
) -> str:
    """Return ``content`` with the managed frontmatter keys set or refreshed.

    ``title`` and ``created`` are kept when already present; ``updated`` is
    always ``today``; ``sources`` is the union of existing and new ids;
    ``tags`` defaults to ``[kind]`` and is otherwise preserved.
    """
    today = today or datetime.now(tz=UTC).date().isoformat()
    existing, body = read_frontmatter(content)
    newline = "\r\n" if "\r\n" in content else "\n"

    sources = {str(s) for s in _as_list(existing.get("sources"))} | {str(s) for s in source_ids if s}
    tags = [str(t) for t in _as_list(existing.get("tags"))] or [kind]
    managed: dict[str, Any] = {
        "title": str(existing.get("title") or _title_from_body(body, path=path)),
        "type": kind,
        "created": str(existing.get("created") or today),
        "updated": today,
        "sources": sorted(sources),
        "tags": tags,
    }
    extras = {key: value for key, value in existing.items() if key not in MANAGED_KEYS}

    lines = [f"{key}: {_render(value)}" for key, value in managed.items()]
    for key, value in extras.items():
        lines.append(_render_extra(key, value))
    block = "---" + newline + newline.join(lines) + newline + "---" + newline
    return block + body


def _title_from_body(body: str, *, path: str) -> str:
    match = _H1_RE.search(body)
    if match:
        return match.group(1).strip()
    return Path(path).stem.replace("-", " ").title()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    return value


def _render(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_render_scalar(item) for item in value) + "]"
    return _render_scalar(value)


def _render_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    if _DATE_RE.fullmatch(text):
        return text
    if _PLAIN_SCALAR_RE.fullmatch(text) and text.lower() not in _YAML_RESERVED and not text.replace(".", "", 1).isdigit():
        return text
    return json.dumps(text, ensure_ascii=False)


def _render_extra(key: str, value: Any) -> str:
    if (
        isinstance(value, str | int | float | bool)
        or value is None
        or (isinstance(value, list) and all(isinstance(v, str | int | float) for v in value))
    ):
        return f"{key}: {_render(value) if value is not None else 'null'}"
    return str(yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True, default_flow_style=None)).rstrip("\n")
