"""Native-batch cost guard — daily-USD receipt tracking and enforcement.

Native batch bootstrap appends an estimated-upper-bound receipt to
``state.db.cost_ledger``. ``check_budget`` sums today's receipts and raises
``CostBudgetExceededError`` when the configured daily cap would be exceeded,
unless the explicit ``--no-cost-guard`` override is passed.

Borrowed from openclaw-wiki-lancedb's ``scripts/wiki-cost-guard.js`` pattern
(see research doc), adapted to live in mdwiki's existing sqlite state.

Synchronous ingest, query, synthesis, and vision calls do not yet record costs
or enforce this cap; that provider-side wiring remains a documented follow-up.
"""

from __future__ import annotations

import math
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
    projected_cost_usd: float = 0.0,
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
    projected_cost_usd : float, default=0.0
        Estimated incremental spend for the operation about to start.

    Raises
    ------
    CostBudgetExceededError
        When ``daily_budget_usd`` is set, ``override`` is False, and today's
        spend plus the projected operation would exceed the cap.
    """
    if not math.isfinite(projected_cost_usd) or projected_cost_usd < 0:
        raise CostBudgetExceededError("Projected native-batch cost must be finite and non-negative.")
    if daily_budget_usd is not None and (not math.isfinite(daily_budget_usd) or daily_budget_usd <= 0):
        raise CostBudgetExceededError("Configured native-batch daily budget must be finite and greater than zero.")
    if daily_budget_usd is None or override:
        return
    spent = today_spent_usd(conn=conn)
    if not math.isfinite(spent) or spent < 0:
        raise CostBudgetExceededError("Recorded native-batch spend must be finite and non-negative.")
    projected_total = spent + projected_cost_usd
    if spent >= daily_budget_usd or projected_total > daily_budget_usd:
        raise CostBudgetExceededError(
            f"Daily LLM cost cap reached: spent ${spent:.2f}; projected operation ${projected_cost_usd:.2f}; "
            f"projected total ${projected_total:.2f} exceeds ${daily_budget_usd:.2f} budget. "
            "Pass --no-cost-guard to override, or raise [cost_guard].daily_budget_usd in .mdwiki/config.toml."
        )
