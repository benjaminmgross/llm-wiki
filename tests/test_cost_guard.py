"""Tests for native-batch receipt tracking and daily-budget enforcement."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mdwiki.cost_guard import (
    CostBudgetExceededError,
    check_budget,
    record_cost,
    today_spent_usd,
)
from mdwiki.state import connect, init_db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh state.db with cost_ledger schema."""
    db = tmp_path / "state.db"
    init_db(db)
    return db


@pytest.mark.unit
def test_record_cost_inserts_row(db_path: Path) -> None:
    with connect(db_path) as conn:
        record_cost(
            conn=conn,
            operation="ingest",
            tokens_in=1000,
            tokens_out=500,
            cost_usd=0.05,
        )
        conn.commit()
        rows = conn.execute("SELECT operation, tokens_in, tokens_out, cost_usd FROM cost_ledger").fetchall()
    assert len(rows) == 1
    assert rows[0]["operation"] == "ingest"
    assert rows[0]["tokens_in"] == 1000
    assert rows[0]["tokens_out"] == 500
    assert rows[0]["cost_usd"] == pytest.approx(0.05)


@pytest.mark.unit
def test_today_spent_usd_sums_today_only(db_path: Path) -> None:
    """Spend recorded today is summed; older spend is excluded."""
    now = time.time()
    yesterday = now - (24 * 3600 + 60)  # 24h+1min ago

    with connect(db_path) as conn:
        # Two rows today.
        record_cost(conn=conn, operation="ingest", cost_usd=0.10, ts=now)
        record_cost(conn=conn, operation="query", cost_usd=0.02, ts=now)
        # One row yesterday.
        record_cost(conn=conn, operation="ingest", cost_usd=5.00, ts=yesterday)
        conn.commit()

        today = today_spent_usd(conn=conn)

    assert today == pytest.approx(0.12)


@pytest.mark.unit
def test_check_budget_passes_under_cap(db_path: Path) -> None:
    """When today's spend is below the cap, ``check_budget`` returns silently."""
    with connect(db_path) as conn:
        record_cost(conn=conn, operation="ingest", cost_usd=0.05)
        conn.commit()

        # Should not raise.
        check_budget(conn=conn, daily_budget_usd=1.00, override=False)


@pytest.mark.unit
def test_check_budget_raises_over_cap(db_path: Path) -> None:
    """When today's spend exceeds the cap and override=False, raise ``CostBudgetExceededError``."""
    with connect(db_path) as conn:
        record_cost(conn=conn, operation="ingest", cost_usd=2.50)
        conn.commit()

        with pytest.raises(CostBudgetExceededError) as excinfo:
            check_budget(conn=conn, daily_budget_usd=1.00, override=False)
    assert "2.5" in str(excinfo.value) or "2.50" in str(excinfo.value)
    assert "1.0" in str(excinfo.value) or "1.00" in str(excinfo.value)


@pytest.mark.unit
def test_check_budget_raises_when_spend_equals_cap(db_path: Path) -> None:
    with connect(db_path) as conn:
        record_cost(conn=conn, operation="ingest", cost_usd=1.00)
        conn.commit()

        with pytest.raises(CostBudgetExceededError):
            check_budget(conn=conn, daily_budget_usd=1.00, override=False)


@pytest.mark.unit
def test_check_budget_raises_when_spend_plus_projection_exceeds_cap(db_path: Path) -> None:
    with connect(db_path) as conn:
        record_cost(conn=conn, operation="bootstrap_batch_estimate", cost_usd=0.75)
        conn.commit()

        with pytest.raises(CostBudgetExceededError, match="projected total"):
            check_budget(
                conn=conn,
                daily_budget_usd=1.00,
                override=False,
                projected_cost_usd=0.30,
            )


@pytest.mark.unit
def test_check_budget_override_bypasses_cap(db_path: Path) -> None:
    """``override=True`` lets the caller proceed past the cap (the ``--no-cost-guard`` flag)."""
    with connect(db_path) as conn:
        record_cost(conn=conn, operation="ingest", cost_usd=10.00)
        conn.commit()

        # Should not raise even though we're 10x over the cap.
        check_budget(conn=conn, daily_budget_usd=1.00, override=True)


@pytest.mark.unit
def test_check_budget_no_cap_configured_is_silent(db_path: Path) -> None:
    """``daily_budget_usd=None`` means no cap — never raises."""
    with connect(db_path) as conn:
        record_cost(conn=conn, operation="ingest", cost_usd=999.00)
        conn.commit()

        check_budget(conn=conn, daily_budget_usd=None, override=False)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("daily_budget_usd", "projected_cost_usd"),
    [(float("nan"), 0.1), (float("inf"), 0.1), (1.0, float("nan")), (1.0, float("inf"))],
)
def test_check_budget_rejects_nonfinite_values(
    db_path: Path,
    daily_budget_usd: float,
    projected_cost_usd: float,
) -> None:
    with connect(db_path) as conn, pytest.raises(CostBudgetExceededError, match="finite"):
        check_budget(
            conn=conn,
            daily_budget_usd=daily_budget_usd,
            override=False,
            projected_cost_usd=projected_cost_usd,
        )
