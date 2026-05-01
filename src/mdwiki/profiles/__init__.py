"""Corpus-aware profiles for ``mdwiki init``.

A profile pre-bakes a ``schema.md`` and a ``config.toml`` overlay tailored to a
specific corpus shape (initiative folders, transcript corpora, framework docs).
Profiles layer on top of the default schema rather than replacing it; the user
can edit ``.mdwiki/schema.md`` after init and the profile is just the seed.

Phase 1 ships only the default ``working-dir`` profile, which is byte-identical
to the legacy ``DEFAULT_SCHEMA``. Subsequent phases add ``initiative``,
``transcripts``, and ``framework``.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PROFILES_DIR: Path = Path(__file__).parent

#: Always-present default profile. Reserved name; does not require a directory
#: under ``src/mdwiki/profiles/`` because its content is the legacy
#: ``DEFAULT_SCHEMA`` from ``mdwiki.init``.
DEFAULT_PROFILE_NAME: str = "working-dir"


class UnknownProfileError(ValueError):
    """Raised when a profile name doesn't match a bundled or user-supplied profile."""


@dataclass(frozen=True)
class Profile:
    """A loaded mdwiki profile.

    Parameters
    ----------
    name : str
        Stable identifier (e.g. ``"initiative"``).
    schema_text : str
        Verbatim contents of the profile's ``schema.md``. Written to
        ``.mdwiki/schema.md`` at init time.
    config_overlay : dict
        Deep-merged into ``DEFAULT_CONFIG`` at init time. Profile values win on
        leaf conflict; missing keys fall through to the default.
    description : str
        One-line human description, surfaced by ``mdwiki init --help``.
    """

    name: str
    schema_text: str
    config_overlay: dict[str, Any] = field(default_factory=dict)
    description: str = ""


def load_profile(*, name: str) -> Profile:
    """Load a bundled profile by name.

    Parameters
    ----------
    name : str
        Profile identifier. Must match either the special ``working-dir``
        default or a directory under ``src/mdwiki/profiles/``.

    Returns
    -------
    Profile
        Loaded profile with ``schema_text`` populated from disk.

    Raises
    ------
    UnknownProfileError
        When ``name`` doesn't match any bundled profile. The error message
        lists the available profiles for actionable feedback.
    """
    if name == DEFAULT_PROFILE_NAME:
        # The default is special-cased so it's byte-identical to the legacy
        # init behavior. No on-disk directory required; this avoids drift if
        # someone edits the schema and forgets to update a profile copy.
        from mdwiki.init import DEFAULT_SCHEMA

        return Profile(
            name=DEFAULT_PROFILE_NAME,
            schema_text=DEFAULT_SCHEMA,
            config_overlay={},
            description="Generic working directory of mixed file types (default).",
        )

    profile_dir = _PROFILES_DIR / name
    if not profile_dir.is_dir() or not (profile_dir / "schema.md").is_file():
        available = ", ".join(list_profile_names())
        raise UnknownProfileError(
            f"Unknown profile: {name!r}. Available: {available}."
        )

    schema_text = (profile_dir / "schema.md").read_text()

    overlay_path = profile_dir / "config-overlay.toml"
    overlay = tomllib.loads(overlay_path.read_text()) if overlay_path.is_file() else {}

    description_path = profile_dir / "description.txt"
    description = description_path.read_text().strip() if description_path.is_file() else ""

    return Profile(
        name=name,
        schema_text=schema_text,
        config_overlay=overlay,
        description=description,
    )


def list_profile_names() -> list[str]:
    """Return every bundled profile name, sorted, including ``working-dir``.

    Returns
    -------
    list of str
        Sorted, deduplicated list of valid ``--profile`` arguments.
    """
    names: set[str] = {DEFAULT_PROFILE_NAME}
    if _PROFILES_DIR.is_dir():
        for child in _PROFILES_DIR.iterdir():
            if (
                child.is_dir()
                and not child.name.startswith("__")
                and (child / "schema.md").is_file()
            ):
                names.add(child.name)
    return sorted(names)


def deep_merge(*, base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overlay`` into ``base``. Overlay wins on leaf conflict.

    Parameters
    ----------
    base : dict
        Base dictionary; not mutated.
    overlay : dict
        Overlay dictionary; values win when both define the same key. When
        both ``base[k]`` and ``overlay[k]`` are dicts, recurse; otherwise
        ``overlay[k]`` replaces ``base[k]`` wholesale.

    Returns
    -------
    dict
        New dict with deep-merged contents. Neither ``base`` nor ``overlay``
        is mutated.
    """
    out: dict[str, Any] = dict(base)
    for key, overlay_val in overlay.items():
        base_val = out.get(key)
        if isinstance(base_val, dict) and isinstance(overlay_val, dict):
            out[key] = deep_merge(base=base_val, overlay=overlay_val)
        else:
            out[key] = overlay_val
    return out


__all__ = [
    "DEFAULT_PROFILE_NAME",
    "Profile",
    "UnknownProfileError",
    "deep_merge",
    "list_profile_names",
    "load_profile",
]
