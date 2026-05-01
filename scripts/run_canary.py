"""Canary runner — exercise mdwiki against a real folder and emit a scorecard.

Usage::

    python scripts/run_canary.py --profile=initiative --corpus=~/dev/my-command-center/key-initiatives/capital-raise --label=capital-raise [--live]

Modes:

- **structural** (default; no ``--live`` flag): runs ``mdwiki init --profile=<x>``
  and inspects the resulting state without invoking the LLM. Verifies the
  profile loads, files register correctly, the schema is the expected one,
  and the right loaders fire. Free.

- **live** (``--live`` flag): runs ``mdwiki init`` then ``mdwiki ingest --pending``,
  which calls the LLM. Costs real money. Computes the full Karpathy-style
  scorecard (page counts by kind, cross-ref density, orphan count, coverage,
  quote-anchor success rate).

Outputs a markdown scorecard file at ``thoughts/research/canary-<label>-<mode>.md``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run mdwiki against a canary corpus and emit a scorecard.")
    parser.add_argument("--profile", required=True, help="Profile name to init the canary with.")
    parser.add_argument("--corpus", required=True, type=Path, help="Path to a real folder to copy + canary.")
    parser.add_argument("--label", required=True, help="Short label for the scorecard filename.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run live LLM ingest after init (COSTS REAL MONEY). Default: structural only.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "thoughts" / "research",
        help="Directory where the scorecard markdown lands (default: thoughts/research/).",
    )
    parser.add_argument(
        "--keep-working-copy",
        action="store_true",
        help="Preserve the /tmp working copy after the run (default: deleted to avoid leaving proprietary corpus copies on disk).",
    )
    args = parser.parse_args(argv)

    corpus_src = args.corpus.expanduser().resolve()
    if not corpus_src.is_dir():
        print(f"error: corpus not found at {corpus_src}", file=sys.stderr)
        return 1

    workdir = Path(tempfile.mkdtemp(prefix=f"mdwiki-canary-{args.label}-"))
    canary_root = workdir / args.label
    print(f"[canary] copying corpus to {canary_root} ...")
    shutil.copytree(corpus_src, canary_root)

    mdwiki = REPO_ROOT / ".venv" / "bin" / "mdwiki"
    if not mdwiki.is_file():
        print(f"error: mdwiki binary not found at {mdwiki}", file=sys.stderr)
        return 1

    # Step 1: init.
    print(f"[canary] running: mdwiki init --profile={args.profile} {canary_root}")
    init_start = time.time()
    proc = subprocess.run(
        [str(mdwiki), "init", "--profile", args.profile, str(canary_root)],
        capture_output=True,
        text=True,
    )
    init_secs = time.time() - init_start
    if proc.returncode != 0:
        print(f"error: init failed:\n{proc.stderr}", file=sys.stderr)
        return 1
    init_message = proc.stdout.strip()
    print(f"[canary] init: {init_message}")

    # Always compute the structural scorecard (cheap; works even pre-ingest).
    structural = _structural_scorecard(canary_root=canary_root, profile=args.profile, label=args.label)
    structural["init_seconds"] = round(init_secs, 2)
    structural["init_message"] = init_message

    live: dict | None = None
    if args.live:
        print("[canary] live mode: running mdwiki ingest --pending --yes (costs real money) ...")
        ingest_start = time.time()
        proc = subprocess.run(
            [str(mdwiki), "ingest", "--pending", "--yes"],
            capture_output=True,
            text=True,
            cwd=canary_root,
        )
        ingest_secs = time.time() - ingest_start
        if proc.returncode != 0:
            print(f"warning: ingest exited non-zero ({proc.returncode}):\n{proc.stderr}", file=sys.stderr)
        live = _live_scorecard(canary_root=canary_root)
        live["ingest_seconds"] = round(ingest_secs, 2)
        live["ingest_stdout_tail"] = "\n".join(proc.stdout.splitlines()[-20:])
        # Redact verbatim source content from the stderr tail. Quote-anchor
        # failure messages include the LLM's proposed quote text, which
        # often contains content from the proprietary source. We keep the
        # failure CATEGORY (e.g. "quote too short", "quote not found in
        # source") but drop the quoted text itself.
        live["ingest_stderr_tail"] = (
            _redact_quotes_from_stderr("\n".join(proc.stderr.splitlines()[-20:]))
            if proc.stderr
            else ""
        )

    args.out.mkdir(parents=True, exist_ok=True)
    mode = "live" if args.live else "structural"
    md_path = args.out / f"canary-{args.label}-{mode}.md"
    md_path.write_text(_render_scorecard(structural=structural, live=live))
    print(f"[canary] scorecard written to: {md_path}")

    if args.keep_working_copy:
        print(f"[canary] working copy preserved at: {canary_root} (delete when done)")
    else:
        # Default: delete the working copy. The canary copies the source
        # corpus byte-for-byte, including potentially proprietary content;
        # leaving it in /tmp risks leaks. Pass --keep-working-copy to inspect.
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"[canary] working copy deleted: {workdir}")
    return 0


def _structural_scorecard(*, canary_root: Path, profile: str, label: str) -> dict:
    """Inspect the post-init state without invoking the LLM."""
    db = canary_root / ".mdwiki" / "state.db"
    schema_path = canary_root / ".mdwiki" / "schema.md"

    sources_count = 0
    by_loader: dict[str, int] = {}
    by_status: dict[str, int] = {}
    if db.is_file():
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            sources_count = conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
            for row in conn.execute("SELECT status, COUNT(*) AS n FROM sources GROUP BY status"):
                by_status[row["status"]] = row["n"]

    sidecar = canary_root / "raw" / ".sources.json"
    if sidecar.is_file():
        sidecar_data = json.loads(sidecar.read_text())
        for entry in sidecar_data.values():
            loader = entry.get("loader", "unknown")
            by_loader[loader] = by_loader.get(loader, 0) + 1

    schema_size = schema_path.stat().st_size if schema_path.is_file() else 0

    return {
        "label": label,
        "profile": profile,
        "canary_root": str(canary_root),
        "sources_registered": sources_count,
        "sources_by_status": by_status,
        "sources_by_loader": by_loader,
        "schema_bytes": schema_size,
    }


def _live_scorecard(*, canary_root: Path) -> dict:
    """Inspect post-ingest state."""
    db = canary_root / ".mdwiki" / "state.db"
    pages_by_kind: dict[str, int] = {}
    backref_count = 0
    sources_ingested = 0
    sources_failed = 0
    coverage_pct = 0.0
    cross_ref_total = 0
    if db.is_file():
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute("SELECT kind, COUNT(*) AS n FROM pages GROUP BY kind"):
                pages_by_kind[row["kind"]] = row["n"]
            backref_count = conn.execute("SELECT COUNT(*) AS n FROM backrefs").fetchone()["n"]
            sources_ingested = conn.execute(
                "SELECT COUNT(*) AS n FROM sources WHERE status = 'ingested'"
            ).fetchone()["n"]
            sources_failed = conn.execute(
                "SELECT COUNT(*) AS n FROM sources WHERE status = 'failed'"
            ).fetchone()["n"]
            covered = conn.execute(
                "SELECT COUNT(DISTINCT s.id) AS n FROM sources s "
                "JOIN backrefs b ON b.source_id = s.id WHERE s.status = 'ingested'"
            ).fetchone()["n"]
            if sources_ingested > 0:
                coverage_pct = 100.0 * covered / sources_ingested

    # Count cross-refs by parsing wiki/*.md for [text](*.md) links.
    import re

    link_re = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)")
    wiki_dir = canary_root / "wiki"
    if wiki_dir.is_dir():
        for page in wiki_dir.rglob("*.md"):
            cross_ref_total += sum(1 for _ in link_re.finditer(page.read_text()))

    avg_refs = round(cross_ref_total / max(sum(pages_by_kind.values()), 1), 2)

    return {
        "pages_by_kind": pages_by_kind,
        "total_pages": sum(pages_by_kind.values()),
        "backrefs": backref_count,
        "sources_ingested": sources_ingested,
        "sources_failed": sources_failed,
        "coverage_pct": round(coverage_pct, 1),
        "cross_refs_total": cross_ref_total,
        "avg_cross_refs_per_page": avg_refs,
    }


def _render_scorecard(*, structural: dict, live: dict | None) -> str:
    lines: list[str] = []
    lines.append(f"# Canary scorecard — {structural['label']}")
    lines.append("")
    lines.append(f"- **Profile:** `{structural['profile']}`")
    lines.append(f"- **Mode:** {'live (LLM ingest)' if live else 'structural (no LLM)'}")
    lines.append(f"- **Canary root:** `{structural['canary_root']}`")
    lines.append(f"- **Init time:** {structural['init_seconds']}s")
    lines.append(f"- **Init message:** {structural['init_message']}")
    lines.append("")

    lines.append("## Structural metrics")
    lines.append(f"- **Sources registered:** {structural['sources_registered']}")
    lines.append(f"- **Schema size:** {structural['schema_bytes']} bytes")
    if structural["sources_by_status"]:
        lines.append("- **Sources by status:**")
        for status, n in sorted(structural["sources_by_status"].items()):
            lines.append(f"  - `{status}`: {n}")
    if structural["sources_by_loader"]:
        lines.append("- **Sources by loader:**")
        for loader, n in sorted(structural["sources_by_loader"].items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  - `{loader}`: {n}")
    lines.append("")

    if live is None:
        lines.append("## Live metrics")
        lines.append("Not run. Re-invoke with `--live` to execute LLM ingest and compute the full scorecard.")
        lines.append("")
        return "\n".join(lines)

    lines.append("## Live metrics (post-ingest)")
    lines.append(f"- **Ingest time:** {live['ingest_seconds']}s")
    lines.append(f"- **Sources ingested:** {live['sources_ingested']}")
    lines.append(f"- **Sources failed:** {live['sources_failed']}")
    lines.append(f"- **Coverage:** {live['coverage_pct']}% (sources with ≥1 backref)")
    lines.append(f"- **Total wiki pages:** {live['total_pages']}")
    if live["pages_by_kind"]:
        lines.append("- **Pages by kind:**")
        for kind, n in sorted(live["pages_by_kind"].items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  - `{kind}`: {n}")
    lines.append(f"- **Total cross-refs:** {live['cross_refs_total']}")
    lines.append(f"- **Avg cross-refs per page:** {live['avg_cross_refs_per_page']}")
    lines.append(f"- **Backrefs (claim citations):** {live['backrefs']}")
    lines.append("")
    lines.append("### Ingest stdout tail")
    lines.append("```")
    lines.append(live.get("ingest_stdout_tail", ""))
    lines.append("```")
    if live.get("ingest_stderr_tail"):
        lines.append("### Ingest stderr tail")
        lines.append("```")
        lines.append(live["ingest_stderr_tail"])
        lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _redact_quotes_from_stderr(text: str) -> str:
    """Strip verbatim source quotes from quote-anchor failure messages.

    Quote-anchor failures emit lines like::

        quote not found in source: '<the proprietary text the LLM proposed>'

    The text inside the single quotes is verbatim source content (or what the
    LLM thought was source content). We replace it with a redaction marker so
    canary scorecards in shared repos don't leak proprietary fragments.
    """
    import re

    return re.sub(
        r"quote not found in source: '[^']*'",
        "quote not found in source: '<REDACTED quote text>'",
        text,
    )


if __name__ == "__main__":
    sys.exit(main())
