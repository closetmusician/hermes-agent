# ABOUTME: RED-first tests for factory/cost_trend.py (P4-e).
# ABOUTME: Covers REQ-01 (cost_per_merged_pr), REQ-02 (two-window trend direction),
# ABOUTME: REQ-03 (board/summary with real ledger + job data), REQ-04 (TDD, real
# ABOUTME: sqlite fixtures, trend-direction test inverted = fail).
"""
P4-e cost_trend tests — RED-first.

Three requirements exercised:
  REQ-01  cost_per_merged_pr(window) = total_spend / merged_pr_count; zero merges → None.
  REQ-02  trend across two windows → direction 'down'/'up'/'flat' + delta.
  REQ-03  board() returns a structured dict with real numbers from ledger + job store.
  REQ-04  the trend-direction test FAILS if the comparison is inverted (validated by
          testing 'down' when window_a > window_b, then confirming 'up' if inverted).

All fixtures use real sqlite (tmp_path) — no mocks.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


# ---------------------------------------------------------------------------
# Helpers to build a minimal JobStore + seed DONE jobs
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path):
    """Create a JobStore backed by a tmp sqlite file."""
    from factory.job_store import JobStore

    return JobStore(db_path=tmp_path / "jobs.db")


def _seed_done_job(
    store,
    *,
    cost_usd: float,
    updated_ts_ms: int,
    repo: str = "acme",
) -> str:
    """
    Enqueue a job and fast-forward it to DONE with known cost and updated_ts.

    updated_ts_ms controls which window the job falls into (the 'merged' timestamp
    is the job's updated_ts at DONE).  Bypasses full lifecycle by using the store's
    public transition() chain minimally.
    """
    import sqlite3

    jid = store.enqueue_job(
        {
            "repo": repo,
            "spec": "stub task",
            "kind": "quick",
            "worker": "claude",
            "model": "claude-sonnet",
            "budget_usd": cost_usd,
            "timeout_min": 5,
        }
    )
    # Drive through the required states to reach DONE.
    # Bypass cost checks — we set cost_so_far_usd directly below.
    store.transition(jid, "QUEUED", "RUNNING")
    store.transition(jid, "RUNNING", "TEST")
    store.transition(jid, "TEST", "REVIEW")
    store.transition(jid, "REVIEW", "AWAITING_APPROVAL")
    store.transition(jid, "AWAITING_APPROVAL", "MERGING")
    store.transition(jid, "MERGING", "DONE")

    # Stamp final cost_so_far_usd and the desired updated_ts directly in sqlite
    # (the public transition API doesn't expose cost; we write it here for testing).
    store._conn.execute(
        "UPDATE jobs SET cost_so_far_usd = ?, updated_ts = ? WHERE id = ?",
        (cost_usd, updated_ts_ms, jid),
    )
    store._conn.commit()
    return jid


def _make_ledger(tmp_path: Path):
    """Create a DailyBudgetLedger backed by a tmp sqlite file."""
    from factory.cost_stops import DailyBudgetLedger

    return DailyBudgetLedger(db_path=tmp_path / "ledger.db")


# ---------------------------------------------------------------------------
# Time-window helpers — two non-overlapping windows in epoch-ms
# ---------------------------------------------------------------------------

_DAY_MS = 86_400_000  # one day in milliseconds

# Window A: 7 days ago → 4 days ago
_NOW_MS = int(time.time() * 1000)
_WIN_A_START = _NOW_MS - 7 * _DAY_MS
_WIN_A_END = _NOW_MS - 4 * _DAY_MS
# Window B: 4 days ago → 1 day ago
_WIN_B_START = _NOW_MS - 4 * _DAY_MS
_WIN_B_END = _NOW_MS - 1 * _DAY_MS


# ---------------------------------------------------------------------------
# REQ-01: cost_per_merged_pr(window)
# ---------------------------------------------------------------------------


class TestCostPerMergedPr:
    """
    REQ-01 — a window with known spend and merged count → correct cost-per-PR.
    """

    def test_single_merged_pr_returns_correct_cost(self, tmp_path):
        """One job merged with $2.00 spend → cost_per_merged_pr == 2.0."""
        from factory.cost_trend import cost_per_merged_pr

        store = _make_store(tmp_path)
        ts = (_WIN_A_START + _WIN_A_END) // 2  # mid-window A
        _seed_done_job(store, cost_usd=2.0, updated_ts_ms=ts)

        result = cost_per_merged_pr(
            store,
            window_start_ms=_WIN_A_START,
            window_end_ms=_WIN_A_END,
        )
        assert result is not None
        assert abs(result - 2.0) < 1e-6

    def test_multiple_merged_prs_averages_correctly(self, tmp_path):
        """Three jobs ($1, $3, $2) → average $2.00."""
        from factory.cost_trend import cost_per_merged_pr

        store = _make_store(tmp_path)
        ts = (_WIN_A_START + _WIN_A_END) // 2
        for cost in (1.0, 3.0, 2.0):
            _seed_done_job(store, cost_usd=cost, updated_ts_ms=ts)

        result = cost_per_merged_pr(
            store,
            window_start_ms=_WIN_A_START,
            window_end_ms=_WIN_A_END,
        )
        assert result is not None
        assert abs(result - 2.0) < 1e-6

    def test_zero_merged_prs_returns_none_no_division_error(self, tmp_path):
        """
        REQ-01 escalate_if: window with 0 merged PRs must not divide by zero.
        Returns None (or a sentinel) — never raises ZeroDivisionError.
        """
        from factory.cost_trend import cost_per_merged_pr

        store = _make_store(tmp_path)
        # No jobs seeded — empty window.
        result = cost_per_merged_pr(
            store,
            window_start_ms=_WIN_A_START,
            window_end_ms=_WIN_A_END,
        )
        assert result is None

    def test_jobs_outside_window_excluded(self, tmp_path):
        """Jobs outside the requested window must not affect the result."""
        from factory.cost_trend import cost_per_merged_pr

        store = _make_store(tmp_path)
        # Inside window A
        ts_in = (_WIN_A_START + _WIN_A_END) // 2
        _seed_done_job(store, cost_usd=1.0, updated_ts_ms=ts_in)
        # Outside — before window A
        ts_out = _WIN_A_START - _DAY_MS
        _seed_done_job(store, cost_usd=99.0, updated_ts_ms=ts_out)

        result = cost_per_merged_pr(
            store,
            window_start_ms=_WIN_A_START,
            window_end_ms=_WIN_A_END,
        )
        assert result is not None
        assert abs(result - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# REQ-02: trend direction across two windows
# ---------------------------------------------------------------------------


class TestTrendDirection:
    """
    REQ-02 — two consecutive windows → direction 'down'/'up'/'flat' + delta.
    The RED gate: if window_a > window_b the direction must be 'down' (improving),
    NOT 'up'.  An inverted comparison would produce 'up' and the test fails.
    """

    def test_trend_down_when_window_b_cheaper(self, tmp_path):
        """Window A costs $3/PR, Window B costs $1/PR → direction 'down' (improving)."""
        from factory.cost_trend import trend

        store = _make_store(tmp_path)
        ts_a = (_WIN_A_START + _WIN_A_END) // 2
        ts_b = (_WIN_B_START + _WIN_B_END) // 2

        _seed_done_job(store, cost_usd=3.0, updated_ts_ms=ts_a)  # window A
        _seed_done_job(store, cost_usd=1.0, updated_ts_ms=ts_b)  # window B

        result = trend(
            store,
            window_a=((_WIN_A_START, _WIN_A_END)),
            window_b=((_WIN_B_START, _WIN_B_END)),
        )
        assert result["direction"] == "down", (
            f"Expected 'down' (B cheaper than A) but got {result['direction']!r}. "
            "If this is 'up' the comparison is inverted."
        )

    def test_trend_up_when_window_b_more_expensive(self, tmp_path):
        """Window A costs $1/PR, Window B costs $3/PR → direction 'up' (getting worse)."""
        from factory.cost_trend import trend

        store = _make_store(tmp_path)
        ts_a = (_WIN_A_START + _WIN_A_END) // 2
        ts_b = (_WIN_B_START + _WIN_B_END) // 2

        _seed_done_job(store, cost_usd=1.0, updated_ts_ms=ts_a)  # window A
        _seed_done_job(store, cost_usd=3.0, updated_ts_ms=ts_b)  # window B

        result = trend(
            store,
            window_a=(_WIN_A_START, _WIN_A_END),
            window_b=(_WIN_B_START, _WIN_B_END),
        )
        assert result["direction"] == "up"

    def test_trend_flat_when_equal(self, tmp_path):
        """Window A and B both $2/PR → direction 'flat', delta ≈ 0."""
        from factory.cost_trend import trend

        store = _make_store(tmp_path)
        ts_a = (_WIN_A_START + _WIN_A_END) // 2
        ts_b = (_WIN_B_START + _WIN_B_END) // 2

        _seed_done_job(store, cost_usd=2.0, updated_ts_ms=ts_a)
        _seed_done_job(store, cost_usd=2.0, updated_ts_ms=ts_b)

        result = trend(
            store,
            window_a=(_WIN_A_START, _WIN_A_END),
            window_b=(_WIN_B_START, _WIN_B_END),
        )
        assert result["direction"] == "flat"
        assert abs(result["delta"]) < 1e-6

    def test_result_contains_value_a_value_b_delta(self, tmp_path):
        """trend() must return value_a, value_b, direction, delta."""
        from factory.cost_trend import trend

        store = _make_store(tmp_path)
        ts_a = (_WIN_A_START + _WIN_A_END) // 2
        ts_b = (_WIN_B_START + _WIN_B_END) // 2
        _seed_done_job(store, cost_usd=4.0, updated_ts_ms=ts_a)
        _seed_done_job(store, cost_usd=2.0, updated_ts_ms=ts_b)

        result = trend(
            store,
            window_a=(_WIN_A_START, _WIN_A_END),
            window_b=(_WIN_B_START, _WIN_B_END),
        )
        for key in ("value_a", "value_b", "direction", "delta"):
            assert key in result, f"Missing key {key!r} in trend result"
        assert abs(result["value_a"] - 4.0) < 1e-6
        assert abs(result["value_b"] - 2.0) < 1e-6
        assert abs(result["delta"] - (-2.0)) < 1e-6

    def test_inversion_guard_direction_cannot_be_down_when_b_is_worse(self, tmp_path):
        """
        REQ-04 RED gate: if the direction for a worsening window is 'down', the
        trend comparison is inverted.  This test FAILS if the comparison is wrong.
        """
        from factory.cost_trend import trend

        store = _make_store(tmp_path)
        ts_a = (_WIN_A_START + _WIN_A_END) // 2
        ts_b = (_WIN_B_START + _WIN_B_END) // 2

        # B more expensive (factory getting worse → 'up'), never 'down'.
        _seed_done_job(store, cost_usd=1.0, updated_ts_ms=ts_a)
        _seed_done_job(store, cost_usd=5.0, updated_ts_ms=ts_b)

        result = trend(
            store,
            window_a=(_WIN_A_START, _WIN_A_END),
            window_b=(_WIN_B_START, _WIN_B_END),
        )
        assert result["direction"] != "down", (
            "direction='down' means cheaper — but B ($5) is more expensive than A ($1). "
            "The comparison is inverted."
        )
        assert result["direction"] == "up"

    def test_zero_merge_window_propagates_to_trend(self, tmp_path):
        """trend() with one empty window must not crash; value is None for that window."""
        from factory.cost_trend import trend

        store = _make_store(tmp_path)
        # Only window A has a job; window B is empty.
        ts_a = (_WIN_A_START + _WIN_A_END) // 2
        _seed_done_job(store, cost_usd=2.0, updated_ts_ms=ts_a)

        result = trend(
            store,
            window_a=(_WIN_A_START, _WIN_A_END),
            window_b=(_WIN_B_START, _WIN_B_END),
        )
        assert result["value_b"] is None
        # direction should reflect one side unavailable, not crash
        assert result["direction"] in ("unknown", "down", "up", "flat")


# ---------------------------------------------------------------------------
# REQ-03: board() returns real numbers
# ---------------------------------------------------------------------------


class TestBoard:
    """
    REQ-03 — board() returns a structured dict the briefing can render; numbers
    come from real ledger + job-store data (not placeholders).
    """

    def test_board_contains_required_keys(self, tmp_path):
        """board() must include value_a, value_b, direction, delta, window_a, window_b."""
        from factory.cost_trend import board

        store = _make_store(tmp_path)
        ts_a = (_WIN_A_START + _WIN_A_END) // 2
        ts_b = (_WIN_B_START + _WIN_B_END) // 2
        _seed_done_job(store, cost_usd=3.0, updated_ts_ms=ts_a)
        _seed_done_job(store, cost_usd=1.5, updated_ts_ms=ts_b)

        result = board(
            store,
            window_a=(_WIN_A_START, _WIN_A_END),
            window_b=(_WIN_B_START, _WIN_B_END),
        )
        for key in ("value_a", "value_b", "direction", "delta", "window_a", "window_b"):
            assert key in result, f"Missing key {key!r} in board output"

    def test_board_values_reflect_real_job_store_data(self, tmp_path):
        """
        board() values must match what cost_per_merged_pr returns for the same windows —
        not hardcoded or placeholder numbers.
        """
        from factory.cost_trend import board, cost_per_merged_pr

        store = _make_store(tmp_path)
        ts_a = (_WIN_A_START + _WIN_A_END) // 2
        ts_b = (_WIN_B_START + _WIN_B_END) // 2
        _seed_done_job(store, cost_usd=4.0, updated_ts_ms=ts_a)
        _seed_done_job(store, cost_usd=2.0, updated_ts_ms=ts_b)

        expected_a = cost_per_merged_pr(
            store, window_start_ms=_WIN_A_START, window_end_ms=_WIN_A_END
        )
        expected_b = cost_per_merged_pr(
            store, window_start_ms=_WIN_B_START, window_end_ms=_WIN_B_END
        )

        result = board(
            store,
            window_a=(_WIN_A_START, _WIN_A_END),
            window_b=(_WIN_B_START, _WIN_B_END),
        )
        assert abs(result["value_a"] - expected_a) < 1e-9
        assert abs(result["value_b"] - expected_b) < 1e-9

    def test_board_direction_matches_trend_direction(self, tmp_path):
        """board() direction field must agree with trend()."""
        from factory.cost_trend import board, trend

        store = _make_store(tmp_path)
        ts_a = (_WIN_A_START + _WIN_A_END) // 2
        ts_b = (_WIN_B_START + _WIN_B_END) // 2
        _seed_done_job(store, cost_usd=5.0, updated_ts_ms=ts_a)
        _seed_done_job(store, cost_usd=1.0, updated_ts_ms=ts_b)

        trend_result = trend(
            store,
            window_a=(_WIN_A_START, _WIN_A_END),
            window_b=(_WIN_B_START, _WIN_B_END),
        )
        board_result = board(
            store,
            window_a=(_WIN_A_START, _WIN_A_END),
            window_b=(_WIN_B_START, _WIN_B_END),
        )
        assert board_result["direction"] == trend_result["direction"]

    def test_board_empty_windows_no_crash(self, tmp_path):
        """board() with no DONE jobs in either window must not raise."""
        from factory.cost_trend import board

        store = _make_store(tmp_path)
        result = board(
            store,
            window_a=(_WIN_A_START, _WIN_A_END),
            window_b=(_WIN_B_START, _WIN_B_END),
        )
        assert result is not None
        assert result["value_a"] is None
        assert result["value_b"] is None
