"""Cost guard — daily-USD spend tracking and budget enforcement.

Every LLM call appends a row to ``state.db.cost_ledger``. ``check_budget``
sums today's spend and raises ``CostBudgetExceededError`` when the configured
daily cap is exceeded, unless an override flag (the future ``--no-cost-guard``
CLI flag) is passed.

Borrowed from openclaw-wiki-lancedb's ``scripts/wiki-cost-guard.js`` pattern
(see research doc), adapted to live in mdwiki's existing sqlite state.

Phase 5 ships these primitives. Provider-side auto-recording (calling
``record_cost`` from ``llm/anthropic.py`` and ``llm/openai_compatible.py``
after every ``complete()``) and the CLI flag wiring are documented follow-ups.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime


class CostBudgetExceededError(Exception):
    """Raised by ``check_budget`` when today's spend exceeds the configured cap."""


def record_cost(
    *,
    conn: sqlite3.Connection,
    operation: str,
    cost_usd: float,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    source_id: str | None = None,
    ts: float | None = None,
) -> None:
    """Insert one row into ``cost_ledger``.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open connection to ``state.db``. Caller is responsible for committing.
    operation : str
        Short label for what was billed (``"ingest"``, ``"query"``,
        ``"synthesize"``, ``"bootstrap_batch"``, ``"embed"``, etc.).
    cost_usd : float
        Estimated USD cost for this single LLM call. Negative or zero values
        are accepted (free-tier endpoints, embedding models the user pays
        nothing for) but discouraged.
    tokens_in : int, optional
        Input token count, when known.
    tokens_out : int, optional
        Output token count, when known.
    source_id : str, optional
        The 12-char source id this call ingested, when applicable.
    ts : float, optional
        Unix timestamp; defaults to ``time.time()``.
    """
    conn.execute(
        "INSERT INTO cost_ledger (ts, operation, tokens_in, tokens_out, cost_usd, source_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            ts if ts is not None else time.time(),
            operation,
            tokens_in,
            tokens_out,
            cost_usd,
            source_id,
        ),
    )


def today_spent_usd(*, conn: sqlite3.Connection, now: float | None = None) -> float:
    """Return total USD spent today (since local midnight).

    Parameters
    ----------
    conn : sqlite3.Connection
        Open connection to ``state.db``.
    now : float, optional
        Override for "now" (defaults to ``time.time()``). Useful for tests.

    Returns
    -------
    float
        Sum of ``cost_ledger.cost_usd`` for rows whose ``ts`` is after the
        most recent local midnight. Returns ``0.0`` when no rows match.
    """
    now = now if now is not None else time.time()
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM cost_ledger WHERE ts >= ?",
        (midnight,),
    ).fetchone()
    return float(row["total"])


def check_budget(
    *,
    conn: sqlite3.Connection,
    daily_budget_usd: float | None,
    override: bool,
) -> None:
    """Raise ``CostBudgetExceededError`` when today's spend exceeds the cap.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open connection to ``state.db``.
    daily_budget_usd : float or None
        The configured cap. ``None`` means no cap (always proceeds).
    override : bool
        When ``True``, the cap is bypassed regardless of spend (the
        ``--no-cost-guard`` CLI flag).

    Raises
    ------
    CostBudgetExceededError
        When ``daily_budget_usd`` is set, ``override`` is False, and today's
        spend equals or exceeds the cap.
    """
    if daily_budget_usd is None or override:
        return
    spent = today_spent_usd(conn=conn)
    if spent >= daily_budget_usd:
        raise CostBudgetExceededError(
            f"Daily LLM cost cap reached: spent ${spent:.2f} of ${daily_budget_usd:.2f} budget. "
            "Pass --no-cost-guard to override, or raise [cost_guard].daily_budget_usd in .mdwiki/config.toml."
        )
