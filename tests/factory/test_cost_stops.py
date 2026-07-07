# ABOUTME: RED-first test suite for factory/cost_stops.py (P2-i).
# ABOUTME: Tests the three cost stops: per-job budget cap, wall-clock timeout,
# ABOUTME: and atomic daily-ceiling CAS. Uses real subprocesses and real SQLite.
# ABOUTME: No mocks on the stop logic itself — each stop must DEMONSTRABLY FIRE.
# ABOUTME: Grandchild-residual test documents honesty, not false completeness.
"""
Tests for factory/cost_stops.py — three cost stops (P2 design §4.3).

RED → GREEN protocol: this file was committed before cost_stops.py existed;
the tests drove the implementation (not the reverse).

Test coverage:
  1. Budget cap: simulated spend >= budget_usd → kill(handle) on pgid; FAILED(budget).
  2. Wall-clock timeout: sleep-forever worker + 1 s timeout → SIGTERM to group +
     in-group child also dies; FAILED(timeout).
  3. Daily ceiling CAS: would-breach launch → blocked (rowcount 0); mid-flight
     breach kills active pgid.
  4. Atomic CAS under concurrency: two concurrent reserve attempts jointly
     exceeding the ceiling → exactly ONE succeeds; ledger never over-books.
  5. Grandchild residual (honesty): setsid-escaped grandchild → in-group child
     dies via killpg; psutil sweep terminates OR orphan-survived logged.
  6. Ledger survives restart: daily_budget row persists after DB close/reopen.
"""
from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _tmp_db(tmp_path: Path) -> Path:
    """Return a path for a fresh test DB in tmp_path."""
    return tmp_path / "jobs.db"


def _open_db(db_path: Path) -> sqlite3.Connection:
    """Open a fresh sqlite3 conn with WAL+FULL, create daily_budget table."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_budget (
          day              TEXT PRIMARY KEY,
          spent_today_usd  REAL NOT NULL DEFAULT 0,
          reserved_usd     REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Test 1 — per-job budget cap fires against the pgid
# ---------------------------------------------------------------------------


@pytest.mark.live_system_guard_bypass
def test_budget_cap_fires_on_pgid(tmp_path: Path) -> None:
    """
    REQ-01/REQ-04: simulating a job whose cost_so_far_usd >= budget_usd causes
    kill_pgid() (SIGTERM→SIGKILL) to fire against the process group, and the
    returned reason is 'budget'.
    """
    from factory.cost_stops import CostStopper, StopReason

    # Launch a real sleep subprocess; capture its pgid.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        preexec_fn=os.setsid,
    )
    pgid = os.getpgid(proc.pid)

    try:
        stopper = CostStopper(db_path=_tmp_db(tmp_path))
        reason = stopper.check_budget(
            pgid=pgid,
            pid=proc.pid,
            cost_so_far_usd=1.01,
            budget_usd=1.00,
        )
        assert reason == StopReason.BUDGET, f"expected BUDGET, got {reason}"

        # The process should be gone after the kill
        proc.wait(timeout=5)
        with pytest.raises(ProcessLookupError):
            os.kill(proc.pid, 0)
    finally:
        # Ensure cleanup even if test fails mid-way.
        # PermissionError: process already dead (os.killpg on a gone group raises this).
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Test 2 — wall-clock timeout SIGTERMs the GROUP (child also dies)
# ---------------------------------------------------------------------------


@pytest.mark.live_system_guard_bypass
def test_timeout_sigterm_reaches_group(tmp_path: Path) -> None:
    """
    REQ-02/REQ-04: a wall-clock timeout sends SIGTERM to the process GROUP.
    The test proves the group was targeted (not just the parent) by also
    spawning a child inside the group and asserting it dies.

    The parent spawns a child that writes its PID to a file then sleeps.
    After kill, both parent and child must be dead.
    """
    from factory.cost_stops import CostStopper, StopReason

    pid_file = tmp_path / "child.pid"

    # Parent: becomes PG leader (setsid), forks a child that sleeps too.
    parent_script = f"""
import os, subprocess, sys, time
# Spawn a child inside the same process group (no setsid in child).
child = subprocess.Popen([sys.executable, '-c',
    'import os, time; open("{pid_file}", "w").write(str(os.getpid())); time.sleep(60)'])
time.sleep(60)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", parent_script],
        preexec_fn=os.setsid,
    )
    pgid = os.getpgid(proc.pid)

    # Wait for child to write its PID (max 5 s); also wait for non-empty content
    # to avoid a race where the file exists but the write isn't flushed yet.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if pid_file.exists() and pid_file.read_text().strip():
            break
        time.sleep(0.05)
    content = pid_file.read_text().strip() if pid_file.exists() else ""
    assert content, "child did not write its PID within 5 s"
    child_pid = int(content)

    try:
        stopper = CostStopper(db_path=_tmp_db(tmp_path))
        # Simulate elapsed time > timeout
        reason = stopper.check_timeout(
            pgid=pgid,
            pid=proc.pid,
            elapsed_s=62,
            timeout_s=60,
        )
        assert reason == StopReason.TIMEOUT, f"expected TIMEOUT, got {reason}"

        # Both parent AND child must be dead (SIGTERM reached the group)
        proc.wait(timeout=5)
        # Give child a moment to die
        deadline = time.monotonic() + 5
        while True:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            if time.monotonic() > deadline:
                pytest.fail(f"child pid {child_pid} survived SIGTERM-to-group")
            time.sleep(0.1)
    finally:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Test 3 — daily ceiling blocks a would-breach launch (CAS rowcount 0)
# ---------------------------------------------------------------------------


def test_ceiling_blocks_would_breach_launch(tmp_path: Path) -> None:
    """
    REQ-03: a new job whose cost would push spent+reserved over the $5 ceiling
    is refused (reserve returns False, rowcount == 0). The ledger is unchanged.
    """
    from factory.cost_stops import DailyBudgetLedger

    db_path = _tmp_db(tmp_path)
    ledger = DailyBudgetLedger(db_path=db_path, ceiling_usd=5.0)

    # Seed: $4.80 spent today
    ledger._seed_for_test(day="2099-01-01", spent=4.80, reserved=0.0)

    # A $0.30 job would push total to $5.10 — must be blocked
    admitted = ledger.try_reserve(job_cap_usd=0.30, day="2099-01-01")
    assert not admitted, "expected reservation to be blocked (would breach ceiling)"

    # A $0.15 job fits under the ceiling — must be admitted
    admitted_small = ledger.try_reserve(job_cap_usd=0.15, day="2099-01-01")
    assert admitted_small, "expected $0.15 reservation to be admitted"


# ---------------------------------------------------------------------------
# Test 3b — mid-flight breach kills active pgid
# ---------------------------------------------------------------------------


@pytest.mark.live_system_guard_bypass
def test_ceiling_mid_flight_breach_kills_pgid(tmp_path: Path) -> None:
    """
    REQ-03: when spent_today_usd surpasses the ceiling mid-flight (after
    check_ceiling_breach is called), kill_pgid is called on the active pgid.
    """
    from factory.cost_stops import CostStopper, StopReason

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        preexec_fn=os.setsid,
    )
    pgid = os.getpgid(proc.pid)

    db_path = _tmp_db(tmp_path)
    stopper = CostStopper(db_path=db_path, ceiling_usd=5.0)

    # Seed the ledger: $4.99 spent + $0.50 reserved = $5.49 (already over ceiling)
    stopper.ledger._seed_for_test(day="2099-01-01", spent=4.99, reserved=0.50)

    try:
        reason = stopper.check_ceiling_breach(
            pgid=pgid,
            pid=proc.pid,
            spent_today_usd=5.10,
            day="2099-01-01",
        )
        assert reason == StopReason.CEILING, f"expected CEILING, got {reason}"
        proc.wait(timeout=5)
        with pytest.raises(ProcessLookupError):
            os.kill(proc.pid, 0)
    finally:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Test 4 — atomic CAS: two concurrent reservations that jointly exceed ceiling
# ---------------------------------------------------------------------------


def test_atomic_cas_prevents_double_booking(tmp_path: Path) -> None:
    """
    REQ-03/REQ-04: two concurrent try_reserve() calls with caps that jointly
    exceed the ceiling (e.g. ceiling=$5, spent=$4.60, each job cap=$0.30 so
    two would total $5.20) — exactly ONE must succeed; the other is blocked.
    This proves P3 concurrency-safety (CAS rowcount guard).
    """
    from factory.cost_stops import DailyBudgetLedger

    db_path = _tmp_db(tmp_path)
    ledger = DailyBudgetLedger(db_path=db_path, ceiling_usd=5.0)

    # Seed: $4.60 spent; each job cap $0.30 → two jobs would total $5.20 (breach).
    # But only $0.40 of headroom remains so only ONE $0.30 job can fit.
    ledger._seed_for_test(day="2099-02-01", spent=4.60, reserved=0.0)

    results: list[bool] = []
    barrier = threading.Barrier(2)

    def _attempt() -> None:
        barrier.wait()  # both threads start simultaneously
        result = ledger.try_reserve(job_cap_usd=0.30, day="2099-02-01")
        results.append(result)

    t1 = threading.Thread(target=_attempt)
    t2 = threading.Thread(target=_attempt)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert len(results) == 2, "both threads must have completed"
    # Exactly one must have succeeded — no over-booking
    successes = sum(1 for r in results if r)
    assert successes == 1, (
        f"expected exactly 1 reservation to succeed, got {successes}: {results}"
    )

    # Verify the ledger shows exactly $0.30 reserved (not $0.60)
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT reserved_usd FROM daily_budget WHERE day='2099-02-01'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert abs(row[0] - 0.30) < 1e-9, f"ledger reserved_usd should be 0.30, got {row[0]}"


# ---------------------------------------------------------------------------
# Test 5 — grandchild setsid residual: psutil sweep fires; residual documented
# ---------------------------------------------------------------------------


@pytest.mark.live_system_guard_bypass
def test_grandchild_setsid_residual_documented(tmp_path: Path) -> None:
    """
    REQ-02 (P1-C1 honesty): a worker forks a child that calls os.setsid() and
    sleeps (escaping the process group).

    After kill_pgid + psutil sweep:
      - The in-group child (no setsid) must be dead.
      - The setsid-escaped grandchild must be dead (swept by psutil) OR an
        'orphan-survived' log line must have been written.

    This test documents the residual rather than claiming perfect completeness.
    """
    from factory.cost_stops import kill_pgid_with_sweep

    pid_file_in_group = tmp_path / "ingroup.pid"
    pid_file_setsid = tmp_path / "setsid.pid"

    # Parent: PG leader (setsid at launch). Spawns:
    #   - child_a: stays in group, sleeps
    #   - child_b: calls setsid() itself (escapes), sleeps
    parent_script = f"""
import os, subprocess, sys, time
child_a = subprocess.Popen(
    [sys.executable, '-c',
     'import os, time; open("{pid_file_in_group}", "w").write(str(os.getpid())); time.sleep(60)'],
)
child_b = subprocess.Popen(
    [sys.executable, '-c',
     'import os, time; os.setsid(); open("{pid_file_setsid}", "w").write(str(os.getpid())); time.sleep(60)'],
)
time.sleep(60)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", parent_script],
        preexec_fn=os.setsid,
    )
    pgid = os.getpgid(proc.pid)

    # Wait for both children to write their PIDs (non-empty) — avoids race where
    # file exists but write hasn't flushed yet.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        ig_ok = pid_file_in_group.exists() and pid_file_in_group.read_text().strip()
        ss_ok = pid_file_setsid.exists() and pid_file_setsid.read_text().strip()
        if ig_ok and ss_ok:
            break
        time.sleep(0.05)

    ig_content = pid_file_in_group.read_text().strip() if pid_file_in_group.exists() else ""
    ss_content = pid_file_setsid.read_text().strip() if pid_file_setsid.exists() else ""
    assert ig_content, "in-group child did not write its PID within 5 s"
    assert ss_content, "setsid child did not write its PID within 5 s"

    ingroup_pid = int(ig_content)
    setsid_pid = int(ss_content)

    orphan_log: list[str] = []

    try:
        kill_pgid_with_sweep(
            pgid=pgid,
            worker_pid=proc.pid,
            orphan_log=orphan_log,
        )

        # Wait for proc to die
        proc.wait(timeout=5)

        # In-group child must be dead
        deadline = time.monotonic() + 3
        while True:
            try:
                os.kill(ingroup_pid, 0)
            except ProcessLookupError:
                break
            if time.monotonic() > deadline:
                pytest.fail(f"in-group child {ingroup_pid} survived killpg")
            time.sleep(0.05)

        # setsid-escaped grandchild: must be dead (swept) OR orphan logged
        time.sleep(0.3)  # brief pause for sweep to complete
        setsid_alive = True
        try:
            os.kill(setsid_pid, 0)
        except ProcessLookupError:
            setsid_alive = False

        if setsid_alive:
            # Escaped — must have been logged
            assert any("orphan-survived" in entry for entry in orphan_log), (
                f"setsid-escaped grandchild {setsid_pid} survived AND no "
                f"'orphan-survived' was logged. orphan_log={orphan_log}"
            )
            # Clean up the escaped orphan
            try:
                os.kill(setsid_pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        # else: psutil sweep successfully terminated the escaped grandchild
    finally:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            os.kill(setsid_pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, RuntimeError):
            # RuntimeError: conftest guard fires when setsid_pid was recycled
            # and the new owner is outside the test subtree.
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Test 6 — ledger survives restart (persisted WAL+FULL)
# ---------------------------------------------------------------------------


def test_ledger_survives_restart(tmp_path: Path) -> None:
    """
    REQ-03: the daily_budget row must persist after the DB connection is closed
    and reopened — simulating a crash/restart scenario.
    """
    from factory.cost_stops import DailyBudgetLedger

    db_path = _tmp_db(tmp_path)

    ledger1 = DailyBudgetLedger(db_path=db_path, ceiling_usd=5.0)
    ledger1._seed_for_test(day="2099-03-01", spent=2.50, reserved=0.75)
    admitted = ledger1.try_reserve(job_cap_usd=0.20, day="2099-03-01")
    assert admitted
    del ledger1  # "restart"

    # Re-open the DB fresh
    ledger2 = DailyBudgetLedger(db_path=db_path, ceiling_usd=5.0)
    row = ledger2._read_day("2099-03-01")
    assert row is not None, "row disappeared after restart"
    assert abs(row["spent"] - 2.50) < 1e-9, f"spent_today_usd changed: {row['spent']}"
    assert abs(row["reserved"] - 0.95) < 1e-9, (
        f"reserved_usd changed: {row['reserved']}"
    )
