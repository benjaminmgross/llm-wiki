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

import anthropic

from mdwiki import __version__
from mdwiki.discover import WikiNotFound, find_wiki
from mdwiki.doctor import format_report, run_doctor
from mdwiki.ingest import IngestError, ingest_many, ingest_source
from mdwiki.init import NestedWikiError, init_wiki
from mdwiki.lint import lint_wiki
from mdwiki.llm import UnknownProviderError
from mdwiki.llm.anthropic import MissingAPIKeyError
from mdwiki.query import QueryError, query_wiki
from mdwiki.rebuild import RebuildError, rebuild_wiki
from mdwiki.rebuild_log import rebuild_log
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
        # argparse uses SystemExit("...") with a string code for parse errors;
        # 2 is the canonical argparse exit code for usage failure.
        return exc.code if isinstance(exc.code, int) else 2

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
    bootstrap_group = init_p.add_mutually_exclusive_group()
    bootstrap_group.add_argument(
        "--bootstrap",
        action="store_true",
        help="After init, immediately ingest every pending source (chains `ingest --pending --yes`).",
    )
    bootstrap_group.add_argument(
        "--bootstrap-batch",
        action="store_true",
        dest="bootstrap_batch",
        help="After init, submit every pending source to the Anthropic Batch API (~50%% cheaper, ~1h ETA).",
    )
    init_p.set_defaults(_handler=_cmd_init)

    status_p = subparsers.add_parser("status", help="Show pending/ingested counts, recent events, last lint.")
    status_p.set_defaults(_handler=_cmd_status)

    source_p = subparsers.add_parser("source", help="Inspect a registered source by hash or hash prefix.")
    source_p.add_argument("hash_prefix", help="Full source id or any unique prefix.")
    source_p.set_defaults(_handler=_cmd_source)

    rebuild_p = subparsers.add_parser("rebuild", help="Reconstruct .mdwiki/state.db from raw/.sources.json + wiki/log.md.")
    rebuild_p.set_defaults(_handler=_cmd_rebuild)

    rebuild_log_p = subparsers.add_parser(
        "rebuild-log",
        help="Regenerate wiki/log.md from the events table (recovery if a write was lost).",
    )
    rebuild_log_p.set_defaults(_handler=_cmd_rebuild_log)

    doctor_p = subparsers.add_parser("doctor", help="Check provider config and ping the LLM API.")
    doctor_p.set_defaults(_handler=_cmd_doctor)

    ingest_p = subparsers.add_parser("ingest", help="Ingest one source — or every pending / every source — through the LLM into the wiki.")
    ingest_p.add_argument("source", nargs="?", help="Source id (12-char hash or unique prefix) or original_path. Omit when using --all or --pending.")
    bulk = ingest_p.add_mutually_exclusive_group()
    bulk.add_argument("--all", action="store_true", dest="all_sources", help="Re-ingest every registered source (re-processes already-ingested sources too).")
    bulk.add_argument("--pending", action="store_true", help="Ingest every source whose status is still 'pending'.")
    ingest_p.add_argument("--yes", "-y", action="store_true", help="Apply LLM plans without confirmation (default ON for --all/--pending).")
    ingest_p.set_defaults(_handler=_cmd_ingest)

    query_p = subparsers.add_parser("query", help="Ask a question; get a cited answer drawn from existing wiki pages.")
    query_p.add_argument("question", help="The natural-language question to answer.")
    query_p.add_argument("--file", action="store_true", help="File the answer as a synthesis page (wiki/syntheses/<slug>.md).")
    query_p.add_argument("--yes", "-y", action="store_true", help="Skip the confirmation prompt when --file is set.")
    query_p.set_defaults(_handler=_cmd_query)

    lint_p = subparsers.add_parser("lint", help="Health-check the wiki: broken refs, orphans, stale pages, coverage gaps.")
    lint_p.set_defaults(_handler=_cmd_lint)

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
    """Handler for ``mdwiki init [--bootstrap | --bootstrap-batch]``."""
    try:
        result = init_wiki(args.path)
    except NestedWikiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.message)

    if result.files_registered == 0:
        return 0

    if getattr(args, "bootstrap_batch", False):
        return _run_bootstrap_batch(args.path.resolve())

    if not args.bootstrap:
        return 0

    print(f"\n--- bootstrap: ingesting {result.files_registered} pending source(s) ---\n")
    try:
        ingest_results = ingest_many(
            args.path.resolve(),
            scope="pending",
            yes=True,
            on_progress=lambda i, total, path: print(f"[{i}/{total}] {path}"),
            on_failure=lambda path, exc: print(f"  ! failed: {path}: {exc}", file=sys.stderr),
        )
    except (MissingAPIKeyError, UnknownProviderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (anthropic.AuthenticationError, anthropic.NotFoundError) as exc:
        print(_fatal_api_error_message(exc), file=sys.stderr)
        return 1
    applied = sum(1 for r in ingest_results if r.applied)
    print(f"\nBootstrap done: {applied} of {len(ingest_results)} applied.")
    return 0


def _run_bootstrap_batch(wiki_root: Path) -> int:
    """Submit every pending source to Anthropic's Batch API (~50% off, ~1h ETA)."""
    from mdwiki.bootstrap import bootstrap_batch

    print("\n--- bootstrap-batch: building batch request ---\n")
    try:
        result = bootstrap_batch(
            wiki_root,
            yes=False,
            on_status=lambda status, ok, total: print(f"  [batch status: {status} — {ok}/{total} succeeded]"),
            on_progress=lambda i, total, cid: print(f"  [{i}/{total}] applying {cid}..."),
        )
    except (MissingAPIKeyError, UnknownProviderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (anthropic.AuthenticationError, anthropic.NotFoundError) as exc:
        print(_fatal_api_error_message(exc), file=sys.stderr)
        return 1

    if result.submitted == 0:
        print("Bootstrap batch aborted (no submission).")
        return 0
    print(
        f"\nBootstrap batch done: applied {result.applied} of {result.submitted} "
        f"({result.failed} failed, {result.skipped} verdict-skip)."
    )
    return 0 if result.failed == 0 else 1


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


def _cmd_rebuild_log(_args: argparse.Namespace) -> int:
    """Handler for ``mdwiki rebuild-log`` — regenerate wiki/log.md from events."""
    try:
        wiki_root = find_wiki()
    except WikiNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    result = rebuild_log(wiki_root)
    print(f"Regenerated {result.log_path} ({result.lines_written} line(s) from events table).")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Handler for ``mdwiki ingest <source> | --all | --pending``."""
    try:
        wiki_root = find_wiki()
    except WikiNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.all_sources or args.pending:
        if args.source is not None:
            print("error: <source> cannot be combined with --all/--pending.", file=sys.stderr)
            return 2
        scope = "all" if args.all_sources else "pending"
        try:
            results = ingest_many(
                wiki_root,
                scope=scope,
                yes=True,
                on_progress=lambda i, total, path: print(f"[{i}/{total}] {path}"),
                on_failure=lambda path, exc: print(f"  ! failed: {path}: {exc}", file=sys.stderr),
            )
        except (MissingAPIKeyError, UnknownProviderError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except (anthropic.AuthenticationError, anthropic.NotFoundError) as exc:
            print(_fatal_api_error_message(exc), file=sys.stderr)
            return 1
        applied = sum(1 for r in results if r.applied)
        print(f"\nDone: {applied} of {len(results)} applied.")
        return 0

    if args.source is None:
        print("error: provide a <source> or use --all / --pending.", file=sys.stderr)
        return 2

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
    except (anthropic.AuthenticationError, anthropic.NotFoundError) as exc:
        print(_fatal_api_error_message(exc), file=sys.stderr)
        return 1
    print(result.message)
    return 0  # rejection is not an error


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


def _cmd_lint(_args: argparse.Namespace) -> int:
    """Handler for ``mdwiki lint``."""
    try:
        wiki_root = find_wiki()
    except WikiNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report = lint_wiki(wiki_root)
    if not report.findings:
        print("Lint: clean — no findings.")
        return 0
    print(f"Lint: {len(report.findings)} finding(s) " + ", ".join(f"{kind}={count}" for kind, count in sorted(report.findings_by_kind.items())))
    print()
    for kind in sorted(report.findings_by_kind):
        print(f"## {kind}")
        for finding in report.findings:
            if finding.kind == kind:
                print(f"  - {finding.page_path}: {finding.message}")
        print()
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


def _fatal_api_error_message(exc: Exception) -> str:
    """Render a user-friendly message for fatal Anthropic API errors.

    These errors (auth/not-found) signal a config problem: a bad API key, a
    typo'd model id, or insufficient permissions. They're identical for every
    source, so ``ingest_many`` propagates them rather than swallowing per-source
    failures and the CLI prints one clear "fix your config" message.
    """
    if isinstance(exc, anthropic.AuthenticationError):
        return (
            f"error: Anthropic API rejected the credentials ({exc}). "
            "Fix the ANTHROPIC_API_KEY env var (or your provider config) and re-run."
        )
    if isinstance(exc, anthropic.NotFoundError):
        return (
            f"error: Anthropic API returned 'not found' ({exc}). "
            "Likely cause: the model id in .mdwiki/config.toml ([llm] model = ...) "
            "is misspelled or has been retired. Update it and re-run."
        )
    return f"error: {exc}"
