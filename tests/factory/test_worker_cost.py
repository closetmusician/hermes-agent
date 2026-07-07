# ABOUTME: RED-first test suite for factory/worker_cost.py + the OpenRouter $2
# ABOUTME: sub-ceiling in factory/cost_stops.py (P4-b, reviews F5/F6/F8). Real SQLite
# ABOUTME: DailyBudgetLedger, no mocks on the CAS. Proves: two-CAS never exceeds $5
# ABOUTME: nightly NOR $2 openrouter under concurrency; resume re-spend counted as
# ABOUTME: real spend; reconciliation vs a synthetic PROVIDER figure within tolerance.
"""
Tests for factory/worker_cost.py + cost_stops two-CAS sub-ceiling.

RED → GREEN protocol: committed before worker_cost.py and the composite-PK
migration exist.

Coverage:
  * worker_cost.usd_for(...) prices claude/codex/openrouter token usage.
  * resume re-spend is counted as REAL spend (F5), not just reservation returned.
  * reconciliation compares the ledger to a SYNTHETIC provider figure computed
    OUTSIDE the ledger, within a defined TOLERANCE (F8); the test would CATCH drift.
  * reserve_openrouter is a two-CAS in ONE transaction: never exceeds $5 nightly NOR
    $2 openrouter, concurrent fan-out; a breaching reserve rolls BOTH rows back (F6).
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from factory.cost_stops import DailyBudgetLedger


def _tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "jobs.db"


# ---------------------------------------------------------------------------
# REQ-04 — worker_cost pricing + resume re-spend counted
# ---------------------------------------------------------------------------


def test_usd_for_prices_each_provider() -> None:
    """worker_cost.usd_for returns a positive cost that scales with token counts."""
    from factory.worker_cost import usd_for

    small = usd_for("claude", "claude-sonnet", input_tok=1_000, output_tok=1_000)
    big = usd_for("claude", "claude-sonnet", input_tok=10_000, output_tok=10_000)
    assert big > small > 0
    # Each provider prices independently and non-negatively.
    for worker, model in (("claude", "claude-sonnet"), ("codex", "gpt-5"),
                          ("openrouter", "zhipuai/glm-5")):
        assert usd_for(worker, model, input_tok=1_000, output_tok=500) > 0


def test_usd_for_unknown_model_uses_conservative_fallback() -> None:
    """An unpriced model does not crash and does not price at zero (fail toward
    counting spend, never toward hiding it)."""
    from factory.worker_cost import usd_for

    assert usd_for("openrouter", "never/seen-this", input_tok=1_000, output_tok=1_000) > 0


def test_resume_respend_counted_as_real_spend(tmp_path: Path) -> None:
    """F5: a crash+resume re-spends a phase; BOTH the pre-crash burn and the re-run
    burn are committed as real spend — not just the reservation returned.

    We model one job that: reserves cap, burns pre-crash tokens (crash), then on
    resume reserves again and burns re-run tokens. The ledger's spent_today_usd must
    equal pre_crash_usd + rerun_usd (both real), within a cent.
    """
    from factory.worker_cost import usd_for, record_job_spend

    ledger = DailyBudgetLedger(db_path=_tmp_db(tmp_path))
    day = "2099-01-01"
    cap = 2.0

    pre_crash = usd_for("claude", "claude-sonnet", input_tok=50_000, output_tok=20_000)
    rerun = usd_for("claude", "claude-sonnet", input_tok=60_000, output_tok=25_000)

    # Pre-crash phase: reserve then commit the metered burn on reclaim (F5).
    assert ledger.try_reserve(cap, day=day)
    record_job_spend(ledger, job_id="j1", cap_usd=cap, actual_usd=pre_crash, day=day)
    # Resume: fresh reservation + commit the re-run burn.
    assert ledger.try_reserve(cap, day=day)
    record_job_spend(ledger, job_id="j1", cap_usd=cap, actual_usd=rerun, day=day)

    row = ledger._read_day(day)
    assert row is not None
    assert row["spent"] == pytest.approx(pre_crash + rerun, abs=0.01)


def test_reconciliation_against_synthetic_provider_within_tolerance(tmp_path: Path) -> None:
    """F8: reconcile the ledger against a SYNTHETIC provider figure computed OUTSIDE
    the ledger (from injected token counts × price table), within TOLERANCE.

    A synthetic night: claude + codex + openrouter workers with KNOWN tokens,
    including one crashed-and-resumed job (pre-crash + re-run both real).
    """
    from factory.worker_cost import usd_for, record_job_spend, TOLERANCE

    ledger = DailyBudgetLedger(db_path=_tmp_db(tmp_path))
    day = "2099-02-02"
    cap = 2.0

    # KNOWN token counts per (worker, model, in, out). The provider figure is the
    # sum of these priced OUTSIDE the ledger — the independent oracle.
    jobs = [
        ("claude", "claude-sonnet", 40_000, 15_000, False),
        ("codex", "gpt-5", 30_000, 12_000, False),
        ("openrouter", "zhipuai/glm-5", 20_000, 8_000, False),
        ("claude", "claude-sonnet", 50_000, 18_000, True),  # crashes + resumes
    ]

    provider_reported = 0.0
    for i, (w, m, itok, otok, crashes) in enumerate(jobs):
        phase_usd = usd_for(w, m, input_tok=itok, output_tok=otok)
        assert ledger.try_reserve(cap, day=day)
        record_job_spend(ledger, job_id=f"j{i}", cap_usd=cap, actual_usd=phase_usd, day=day)
        provider_reported += phase_usd
        if crashes:
            # Resume: re-run the SAME phase → provider bills it twice.
            assert ledger.try_reserve(cap, day=day)
            record_job_spend(ledger, job_id=f"j{i}", cap_usd=cap, actual_usd=phase_usd, day=day)
            provider_reported += phase_usd

    ledger_total = ledger._read_day(day)["spent"]
    assert abs(provider_reported - ledger_total) <= TOLERANCE
    # The crashed job's pre-crash tokens ARE in the ledger (not just reservation).
    assert ledger_total == pytest.approx(provider_reported, abs=0.01)


def test_reconciliation_catches_drift_beyond_tolerance(tmp_path: Path) -> None:
    """Anti-theater: if the ledger UNDER-counts a resume re-spend (the F5 bug), the
    reconciliation gap exceeds TOLERANCE and the check FAILS. This proves the test
    can actually catch drift, not just pass trivially.
    """
    from factory.worker_cost import usd_for, record_job_spend, TOLERANCE

    ledger = DailyBudgetLedger(db_path=_tmp_db(tmp_path))
    day = "2099-03-03"
    cap = 2.0
    # A large phase whose dropped re-spend clearly exceeds TOLERANCE — this is the
    # scenario the tolerance bound is meant to catch (an unbounded crash re-spend).
    phase_usd = usd_for("claude", "claude-opus", input_tok=120_000, output_tok=40_000)
    assert phase_usd > TOLERANCE, "test setup: a single dropped phase must exceed tol"

    # Provider billed the phase TWICE (crash + resume) but the buggy ledger only
    # committed it ONCE (returned the reservation on reclaim, ignored the burn).
    provider_reported = 2 * phase_usd
    assert ledger.try_reserve(cap, day=day)
    record_job_spend(ledger, job_id="jx", cap_usd=cap, actual_usd=phase_usd, day=day)
    ledger_total = ledger._read_day(day)["spent"]

    # The drift (one full phase) must exceed tolerance so the gate would fail.
    assert abs(provider_reported - ledger_total) > TOLERANCE


# ---------------------------------------------------------------------------
# REQ-03 / F6 — OpenRouter $2 sub-ceiling, two-CAS in one transaction
# ---------------------------------------------------------------------------


def test_openrouter_subceiling_blocks_third_dispatch(tmp_path: Path) -> None:
    """reserve_openrouter enforces the $2 sub-ceiling: with cap=$0.80, the 3rd
    dispatch (which would reach $2.40 > $2) returns WAIT_FOR_CAPACITY."""
    from factory.cost_stops import DailyBudgetLedger

    ledger = DailyBudgetLedger(db_path=_tmp_db(tmp_path))  # nightly=$5, sub=$2 default
    day = "2099-04-04"
    cap = 0.80
    assert ledger.reserve_openrouter(cap, day=day) is True   # 0.80
    assert ledger.reserve_openrouter(cap, day=day) is True   # 1.60
    assert ledger.reserve_openrouter(cap, day=day) is False  # would be 2.40 > $2


def test_openrouter_reserve_never_exceeds_nightly_ceiling(tmp_path: Path) -> None:
    """The two-CAS also honors the $5 nightly ceiling: if nightly is nearly full,
    an OR reserve that fits the $2 sub-ceiling but breaches $5 is rejected AND
    rolls back the sub-ceiling row (no orphaned sub reservation)."""
    from factory.cost_stops import DailyBudgetLedger

    ledger = DailyBudgetLedger(db_path=_tmp_db(tmp_path))
    day = "2099-05-05"
    # Fill nightly to $4.90 via first-party reservations (single-CAS nightly path).
    for _ in range(7):
        assert ledger.try_reserve(0.70, day=day)  # 7 * 0.70 = 4.90
    # An OR reserve of $0.50 fits the $2 sub-ceiling but 4.90+0.50=5.40 > $5 nightly.
    assert ledger.reserve_openrouter(0.50, day=day) is False
    # The sub-ceiling row must be untouched (rolled back) — spent+reserved on the
    # openrouter row is still 0.
    sub = ledger._read_provider(day, "openrouter")
    assert (sub["spent"] + sub["reserved"]) == pytest.approx(0.0, abs=1e-9)


def test_openrouter_two_cas_never_exceeds_either_ceiling_concurrent(tmp_path: Path) -> None:
    """F6 gate: fan out N concurrent OpenRouter reservations against nightly=$5,
    sub=$2. Assert committed OR reservations <= $2 AND all reservations <= $5, and
    every rejected reserve left BOTH rows consistent (no orphaned nightly reserve).
    """
    from factory.cost_stops import DailyBudgetLedger

    db = _tmp_db(tmp_path)
    day = "2099-06-06"
    cap = 0.30  # 2/0.30 = 6.66 → at most 6 OR reservations fit the $2 sub-ceiling
    n_threads = 24

    results: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker() -> None:
        # Each thread its own ledger instance (separate connection) → real race.
        lg = DailyBudgetLedger(db_path=db)
        barrier.wait()
        ok = lg.reserve_openrouter(cap, day=day)
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ledger = DailyBudgetLedger(db_path=db)
    sub = ledger._read_provider(day, "openrouter")
    nightly = ledger._read_provider(day, "__nightly__")
    or_reserved = sub["spent"] + sub["reserved"]
    total_reserved = nightly["spent"] + nightly["reserved"]

    n_ok = sum(results)
    assert or_reserved <= 2.0 + 1e-9, f"OR sub-ceiling breached: {or_reserved}"
    assert total_reserved <= 5.0 + 1e-9, f"nightly breached: {total_reserved}"
    # Each committed OR reserve also reserved against nightly (two-CAS coupling):
    # the nightly reserved total equals the OR reserved total (no first-party jobs).
    assert total_reserved == pytest.approx(or_reserved, abs=1e-9)
    # At most floor(2/0.30)=6 could succeed.
    assert n_ok <= 6
