"""Per-source rejection memory.

Persists ingest plan rejections so the LLM can be reminded of prior failures
when re-ingesting the same source. Borrowed from openclaw-wiki-lancedb's
``.olw/rejections.json`` pattern (`scripts/wiki-refine.js:127-152` in the
research doc), adapted to live in mdwiki's existing sqlite state.

Phase 5 ships these primitives. Wiring into the user prompt at ingest time
(injecting the last-N reasons above the source body) is a documented
follow-up.
"""

from __future__ import annotations

import sqlite3
import time


def record_rejection(
    *,
    conn: sqlite3.Connection,
    source_id: str,
    reason: str,
    verdict: str | None = None,
    ts: float | None = None,
) -> None:
    """Insert one row into ``rejections``.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open connection to ``state.db``. Caller is responsible for committing.
    source_id : str
        The 12-char source id whose ingest plan was rejected.
    reason : str
        Human-readable explanation, ideally one sentence; this string is
        injected verbatim into future ingest prompts so favor specificity
        over verbosity.
    verdict : str, optional
        The LLM's verdict (``"low-quality"``, ``"out-of-scope"``,
        ``"duplicate-of:<page>"``) when the rejection was verdict-driven,
        or ``"quote-failed"`` when the plan was rejected by the quote-anchor
        verifier.
    ts : float, optional
        Unix timestamp; defaults to ``time.time()``.
    """
    conn.execute(
        "INSERT INTO rejections (source_id, reason, verdict, ts) VALUES (?, ?, ?, ?)",
        (source_id, reason, verdict, ts if ts is not None else time.time()),
    )


def last_n_reasons(*, conn: sqlite3.Connection, source_id: str, n: int = 5) -> list[str]:
    """Return the most-recent-first list of rejection reasons for ``source_id``.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open connection to ``state.db``.
    source_id : str
        The 12-char source id.
    n : int, default 5
        Maximum number of reasons to return.

    Returns
    -------
    list of str
        Reasons ordered most-recent-first. Empty list if no rejections exist.
    """
    rows = conn.execute(
        "SELECT reason FROM rejections WHERE source_id = ? ORDER BY ts DESC, id DESC LIMIT ?",
        (source_id, n),
    ).fetchall()
    return [row["reason"] for row in rows]
