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
from mdwiki.doctor import format_report, run_doctor
from mdwiki.ingest import IngestError, ingest_source
from mdwiki.init import NestedWikiError, init_wiki
from mdwiki.llm import UnknownProviderError
from mdwiki.llm.anthropic import MissingAPIKeyError
from mdwiki.query import QueryError, query_wiki
from mdwiki.rebuild import RebuildError, rebuild_wiki
from mdwiki.source import find_matching_sources, format_disambiguation, format_source_info, get_source_info
from mdwiki.status import format_status, get_status
from mdwiki.synthesize import SynthesisError, synthesize_auto, synthesize_topic
from mdwiki.undo import UndoError, undo_last


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

    doctor_p = subparsers.add_parser("doctor", help="Check provider config and ping the LLM API.")
    doctor_p.set_defaults(_handler=_cmd_doctor)

    ingest_p = subparsers.add_parser("ingest", help="Ingest one source through the LLM into the wiki.")
    ingest_p.add_argument("source", help="Source id (12-char hash or unique prefix) or original_path of a registered source.")
    ingest_p.add_argument("--yes", "-y", action="store_true", help="Apply the LLM plan without confirmation (verifies quotes regardless).")
    ingest_p.set_defaults(_handler=_cmd_ingest)

    query_p = subparsers.add_parser("query", help="Ask a question; get a cited answer drawn from existing wiki pages.")
    query_p.add_argument("question", help="The natural-language question to answer.")
    query_p.add_argument("--file", action="store_true", help="File the answer as a synthesis page (wiki/syntheses/<slug>.md).")
    query_p.add_argument("--yes", "-y", action="store_true", help="Skip the confirmation prompt when --file is set.")
    query_p.set_defaults(_handler=_cmd_query)

    undo_p = subparsers.add_parser("undo", help="Roll back the last N applied transactions (file writes + DB rows).")
    undo_p.add_argument("n", nargs="?", type=int, default=1, help="Number of transactions to undo (default 1).")
    undo_p.set_defaults(_handler=_cmd_undo)

    syn_p = subparsers.add_parser(
        "synthesize",
        help="Produce a synthesis page from a topic, or auto-discover cluster opportunities.",
    )
    syn_group = syn_p.add_mutually_exclusive_group(required=True)
    syn_group.add_argument("topic", nargs="?", help="Explicit topic for the synthesis page.")
    syn_group.add_argument("--auto", action="store_true", help="Walk the cross-ref graph and propose syntheses for each cluster.")
    syn_p.add_argument("--yes", "-y", action="store_true", help="Apply (or apply-all in --auto) without prompting.")
    syn_p.set_defaults(_handler=_cmd_synthesize)

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


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Handler for ``mdwiki ingest <source> [--yes]``."""
    try:
        wiki_root = find_wiki()
    except WikiNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        result = ingest_source(wiki_root, args.source, yes=args.yes)
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except MissingAPIKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except UnknownProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.message)
    return 0 if result.applied else 0  # rejection is not an error


def _cmd_query(args: argparse.Namespace) -> int:
    """Handler for ``mdwiki query <question> [--file] [--yes]``."""
    try:
        wiki_root = find_wiki()
    except WikiNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        result = query_wiki(wiki_root, args.question, file=args.file, yes=args.yes)
    except QueryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except MissingAPIKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except UnknownProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(result.answer)
    if result.cited_pages:
        print("\nCited pages:")
        for path in result.cited_pages:
            print(f"  - {path}")
    if result.filed_path:
        print(f"\nFiled as synthesis page: {result.filed_path}")
    return 0


def _cmd_undo(args: argparse.Namespace) -> int:
    """Handler for ``mdwiki undo [N]``."""
    try:
        wiki_root = find_wiki()
    except WikiNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        result = undo_last(wiki_root, n=args.n)
    except UndoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.message)
    return 0


def _cmd_synthesize(args: argparse.Namespace) -> int:
    """Handler for ``mdwiki synthesize <topic>`` and ``mdwiki synthesize --auto``."""
    try:
        wiki_root = find_wiki()
    except WikiNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        if args.auto:
            results = synthesize_auto(wiki_root, yes=args.yes)
            if not results:
                print("No qualifying cross-ref clusters found (need ≥3 mutually-linked pages).")
                return 0
            applied = sum(1 for r in results if r.applied)
            print(f"Auto synthesis: {len(results)} cluster(s) evaluated, {applied} applied.")
            for r in results:
                print(f"  - {r.topic_or_title}: {r.message}")
                if r.filed_path:
                    print(f"      filed: {r.filed_path}")
            return 0

        result = synthesize_topic(wiki_root, args.topic, yes=args.yes)
        print(result.message)
        if result.filed_path:
            print(f"Filed: {result.filed_path}")
        return 0
    except SynthesisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except MissingAPIKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except UnknownProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _cmd_doctor(_args: argparse.Namespace) -> int:
    """Handler for ``mdwiki doctor``."""
    try:
        wiki_root = find_wiki()
    except WikiNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        report = run_doctor(wiki_root)
    except MissingAPIKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except UnknownProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_report(report))
    return 0 if report.api_ok else 1
