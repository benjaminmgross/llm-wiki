"""Command-line entry point for ``mdwiki``.

Subcommands route to their respective modules. ``main`` always returns an int
exit code — never raises ``SystemExit`` — so it is callable from tests and from
embedded contexts.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from mdwiki import __version__
from mdwiki.discover import WikiNotFound, find_wiki
from mdwiki.init import NestedWikiError, init_wiki
from mdwiki.rebuild import RebuildError, rebuild_wiki
from mdwiki.source import find_matching_sources, format_disambiguation, format_source_info, get_source_info
from mdwiki.status import format_status, get_status


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and dispatch to the matching subcommand.

    Parameters
    ----------
    argv : Sequence[str], optional
        Command-line arguments excluding the program name. Defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        Exit code; 0 on success, non-zero on failure.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdwiki",
        description="Folder-local CLI that turns a directory of markdown into an LLM-maintained wiki.",
    )
    parser.add_argument("--version", action="version", version=f"mdwiki {__version__}")

    subparsers = parser.add_subparsers(dest="command", title="commands", metavar="<command>")

    init_p = subparsers.add_parser("init", help="Scaffold a wiki and register markdown sources as pending.")
    init_p.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        help="Folder to turn into a wiki (default: current directory).",
    )
    init_p.set_defaults(_handler=_cmd_init)

    status_p = subparsers.add_parser("status", help="Show pending/ingested counts, recent events, last lint.")
    status_p.set_defaults(_handler=_cmd_status)

    source_p = subparsers.add_parser("source", help="Inspect a registered source by hash or hash prefix.")
    source_p.add_argument("hash_prefix", help="Full source id or any unique prefix.")
    source_p.set_defaults(_handler=_cmd_source)

    rebuild_p = subparsers.add_parser("rebuild", help="Reconstruct .mdwiki/state.db from raw/.sources.json + wiki/log.md.")
    rebuild_p.set_defaults(_handler=_cmd_rebuild)

    return parser


def _cmd_init(args: argparse.Namespace) -> int:
    """Handler for ``mdwiki init``."""
    try:
        result = init_wiki(args.path)
    except NestedWikiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.message)
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    """Handler for ``mdwiki status``."""
    try:
        wiki_root = find_wiki()
    except WikiNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report = get_status(wiki_root)
    print(format_status(report, wiki_root=wiki_root))
    return 0


def _cmd_source(args: argparse.Namespace) -> int:
    """Handler for ``mdwiki source <hash_prefix>``."""
    try:
        wiki_root = find_wiki()
    except WikiNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    matches = find_matching_sources(wiki_root, args.hash_prefix)
    if not matches:
        print(f"error: no source matching prefix {args.hash_prefix!r}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(format_disambiguation(matches), file=sys.stderr)
        return 1
    print(format_source_info(get_source_info(wiki_root, matches[0])))
    return 0


def _cmd_rebuild(_args: argparse.Namespace) -> int:
    """Handler for ``mdwiki rebuild``."""
    try:
        result = rebuild_wiki()
    except WikiNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except RebuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.message)
    return 0
