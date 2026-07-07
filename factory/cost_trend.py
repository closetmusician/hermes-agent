# ABOUTME: Cost-per-merged-PR trend board for the Fable factory (P4-e).
# ABOUTME: Reads DONE jobs from the job store and computes average AI spend per
# ABOUTME: merged PR over two caller-supplied time windows. Emits direction
# ABOUTME: (down/up/flat/unknown) so the morning briefing shows a trend arrow.
# ABOUTME: No AI, no mocks — plain SQLite reads from real job-store data.
"""
Cost-per-merged-PR trend: two windows → spend-per-PR values + direction.

Design: docs/plans/harness/fable/p4/P4-design.md §6 (cost trend, P4-5).

Three public functions:

  cost_per_merged_pr(store, window_start_ms, window_end_ms)
    → float | None
    Sum of cost_so_far_usd for DONE jobs whose updated_ts falls inside the window,
    divided by the count of such jobs.  Returns None when the window has zero
    merged PRs (no division by zero).

  trend(store, window_a, window_b)
    → dict with {value_a, value_b, direction, delta}
    Computes cost_per_merged_pr for both windows and derives the direction:
      'down'  — factory is getting cheaper (value_b < value_a)
      'up'    — factory is getting more expensive (value_b > value_a)
      'flat'  — no material change (|delta| < FLAT_THRESHOLD_USD)
      'unknown' — at least one window has no merged PRs (cannot compare)

  board(store, window_a, window_b)
    → dict with {value_a, value_b, direction, delta, window_a, window_b}
    Structured summary the briefing can render directly.  Window bounds are
    included so the caller can label the columns.

All three accept an optional ``flat_threshold_usd`` keyword (default FLAT_THRESHOLD_USD)
to control when equal-ish costs are rounded to 'flat'.

Time windows are (start_ms, end_ms) epoch-millisecond tuples, inclusive on both ends.
The 'merged' timestamp is the job's updated_ts at DONE (the last write — the moment
the supervisor transitioned it to DONE, which is when the merge completed).
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# A per-PR difference below this amount is treated as 'flat' (noise floor).
# $0.10 = one dime — meaningful at the $1–$5/PR scale, small enough not to hide
# real improvements.
FLAT_THRESHOLD_USD: float = 0.10

# Type alias for a window expressed as (start_ms, end_ms) epoch-millisecond ints.
Window = Tuple[int, int]


def cost_per_merged_pr(
    store: Any,
    *,
    window_start_ms: int,
    window_end_ms: int,
) -> Optional[float]:
    """
    Compute average AI spend per merged PR for all DONE jobs inside the window.

    Purpose: the north-star number for the trend board.  A 'merged PR' is any job
    that reached state='DONE' with updated_ts in [window_start_ms, window_end_ms].
    The cost used is cost_so_far_usd — the actual recorded spend (set by the
    supervisor as the worker reports token usage via worker_cost.record_job_spend).

    Usage:
        cpm = cost_per_merged_pr(store, window_start_ms=t0, window_end_ms=t1)
        if cpm is None:
            print("no merges in this window")

    Gotchas:
      * Returns None — not 0.0 — when no DONE jobs exist in the window.
        0.0 is ambiguous (could mean 0 spend) while None is unambiguous.
      * window bounds are inclusive on both ends (>=, <=).
      * uses the store's underlying sqlite connection directly for an efficient
        single-query aggregation; does not pull all rows into Python.
    """
    # Single aggregate query: SUM(cost_so_far_usd) and COUNT(*) for DONE jobs
    # in the window.  Both come from the same row scan so they are always
    # consistent with each other (no TOCTOU between separate queries).
    row = store._conn.execute(
        """
        SELECT
            COALESCE(SUM(cost_so_far_usd), 0.0) AS total_usd,
            COUNT(*) AS merged_count
          FROM jobs
         WHERE state = 'DONE'
           AND updated_ts >= ?
           AND updated_ts <= ?
        """,
        (window_start_ms, window_end_ms),
    ).fetchone()

    merged_count = row[1]
    if merged_count == 0:
        # Zero merged PRs: returning None (not 0.0) to distinguish "no data"
        # from "zero cost" — the caller decides how to display this.
        return None

    total_usd = row[0]
    return total_usd / merged_count


def trend(
    store: Any,
    *,
    window_a: Window,
    window_b: Window,
    flat_threshold_usd: float = FLAT_THRESHOLD_USD,
) -> Dict[str, Any]:
    """
    Compute the trend direction between two consecutive time windows.

    Purpose: tells the owner whether the factory is getting cheaper (down),
    more expensive (up), unchanged (flat), or whether one window lacks data
    (unknown).  'down' always means window_b is cheaper than window_a —
    the natural reading of "the factory improved" over time.

    Usage:
        r = trend(store, window_a=(t0, t1), window_b=(t2, t3))
        print(r["direction"], r["delta"])

    Gotchas:
      * direction = 'down'  → value_b < value_a  (improving — CHEAPER)
      * direction = 'up'    → value_b > value_a  (worsening — MORE EXPENSIVE)
      * direction = 'flat'  → |delta| < flat_threshold_usd
      * direction = 'unknown' → at least one window returned None (no merges)
      * delta = value_b - value_a (negative = down, positive = up)
    """
    value_a = cost_per_merged_pr(
        store, window_start_ms=window_a[0], window_end_ms=window_a[1]
    )
    value_b = cost_per_merged_pr(
        store, window_start_ms=window_b[0], window_end_ms=window_b[1]
    )

    if value_a is None or value_b is None:
        # One or both windows have no merged PRs — cannot compute a meaningful
        # direction.  Report 'unknown' with the partial values so the briefing
        # can say e.g. "window A: $2.00/PR, window B: no merges yet".
        return {
            "value_a": value_a,
            "value_b": value_b,
            "direction": "unknown",
            "delta": None,
        }

    # delta = B - A: negative means cheaper (down), positive means more expensive (up).
    # This is the ONE canonical comparison point.  Reversing it would make 'down'
    # mean 'more expensive' — the inversion-guard test catches that.
    delta = value_b - value_a

    if abs(delta) < flat_threshold_usd:
        direction = "flat"
    elif delta < 0:
        direction = "down"   # B cheaper than A → improving
    else:
        direction = "up"     # B more expensive than A → worsening

    return {
        "value_a": value_a,
        "value_b": value_b,
        "direction": direction,
        "delta": delta,
    }


def board(
    store: Any,
    *,
    window_a: Window,
    window_b: Window,
    flat_threshold_usd: float = FLAT_THRESHOLD_USD,
) -> Dict[str, Any]:
    """
    Produce the full trend board dict the morning briefing renders.

    Purpose: the structured summary (REQ-03) that the briefing inlines as the
    cost-trend section.  Adds the window bounds to the trend result so the
    caller can label columns ("this week" vs "last week") without knowing the
    timestamps themselves.

    Usage:
        b = board(store, window_a=(t0, t1), window_b=(t2, t3))
        # render: f"A: ${b['value_a']:.2f}/PR  B: ${b['value_b']:.2f}/PR  {b['direction']} ↕${abs(b['delta']):.2f}"

    Gotchas:
      * All values are real numbers from the job store — never placeholders.
      * value_a / value_b are None (not 0.0) when their window has no merges.
      * window_a / window_b are echoed back as (start_ms, end_ms) tuples.
    """
    t = trend(store, window_a=window_a, window_b=window_b, flat_threshold_usd=flat_threshold_usd)
    return {
        "value_a": t["value_a"],
        "value_b": t["value_b"],
        "direction": t["direction"],
        "delta": t["delta"],
        "window_a": window_a,
        "window_b": window_b,
    }
