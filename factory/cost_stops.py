# ABOUTME: The three cost stops for the Fable factory (P2 design §4.3).
# ABOUTME: Per-job budget cap, wall-clock timeout (SIGTERM to process group),
# ABOUTME: and atomic daily-ceiling CAS — so a runaway worker can never burn
# ABOUTME: past the owner's $5 nightly ceiling, even under P3 concurrency.
# ABOUTME: Grandchild-setsid residual is documented honestly, not hidden.
"""
Three cost stops — budget cap, wall-clock timeout, daily ceiling.

Design authoritative source: docs/plans/harness/fable/p2/P2-design.md §4.3
Red-team closures applied:
  P1-C1 (review):  process-group kill does NOT reach a doubly-detached setsid
                   grandchild.  kill_pgid_with_sweep() adds a best-effort psutil
                   process-tree walk after the killpg, and logs any survivor as
                   'orphan-survived'.  This is the deferred P2 mitigation now
                   specified.  Residual is documented, not silently ignored.
  P1-C3 (review):  daily ceiling reserve is a single atomic CAS
                   (BEGIN IMMEDIATE / UPDATE...WHERE spent+reserved+cap <= ceiling).
                   Two concurrent calls that would jointly breach the ceiling cannot
                   both succeed; only the first rowcount==1 call wins.

The three stops:
  1. check_budget(pgid, pid, cost_so_far_usd, budget_usd)
       → StopReason.BUDGET when cost_so_far_usd >= budget_usd;
         calls kill_pgid_with_sweep() and returns the reason.

  2. check_timeout(pgid, pid, elapsed_s, timeout_s)
       → StopReason.TIMEOUT when elapsed_s > timeout_s;
         SIGTERM to the process group via os.killpg, then psutil sweep.

  3. check_ceiling_breach(pgid, pid, spent_today_usd, day)
       → StopReason.CEILING when spent_today_usd > ceiling;
         kill_pgid_with_sweep().

  DailyBudgetLedger.try_reserve(job_cap_usd, day)
       → bool; atomic CAS — True = admitted and reserved, False = deferred.
  DailyBudgetLedger.commit_spend(job_cap_usd, actual_spend_usd, day)
       → folds actual spend in and releases the reservation atomically.
"""
from __future__ import annotations

import logging
import os
import signal
import sqlite3
import threading
import time
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Nightly spending ceiling (USD). This matches the owner parameter in the design.
_DEFAULT_CEILING_USD = 5.0

# Grace window between SIGTERM and SIGKILL escalation (seconds).
_KILL_GRACE_S = 5.0

# Schema for the daily_budget table (also declared in job_store.py so the
# supervisor DB already carries it; CREATE IF NOT EXISTS is idempotent).
_BUDGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_budget (
  day              TEXT PRIMARY KEY,   -- YYYY-MM-DD UTC
  spent_today_usd  REAL NOT NULL DEFAULT 0,
  reserved_usd     REAL NOT NULL DEFAULT 0
);
"""


class StopReason(Enum):
    """Why a cost stop fired."""

    BUDGET = auto()    # per-job dollar cap exceeded
    TIMEOUT = auto()   # wall-clock limit exceeded
    CEILING = auto()   # daily reserved-budget ceiling hit


# ---------------------------------------------------------------------------
# Process-group kill + psutil sweep  (closes P1-C1)
# ---------------------------------------------------------------------------


def kill_pgid_with_sweep(
    pgid: int,
    worker_pid: int,
    *,
    orphan_log: Optional[List[str]] = None,
    kill_grace_s: float = _KILL_GRACE_S,
) -> None:
    """
    Purpose: SIGTERM the entire process group, wait grace_s, SIGKILL survivors,
    then walk the process tree with psutil and terminate any setsid-escaped
    grandchildren that killpg missed.  Logs 'orphan-survived' for any process
    that survives both passes (a doubly-detached process that re-parented before
    the sweep) rather than silently ignoring it.

    Usage: kill_pgid_with_sweep(pgid=handle.pgid, worker_pid=handle.pid)
    Gotchas:
      DOCUMENTED RESIDUAL — a grandchild that calls os.setsid() AND fully
      re-parents (launchd/init) before this sweep runs may survive both the
      killpg and the psutil walk (it has left the process tree we can walk).
      Such orphans are recorded as 'orphan-survived' entries in orphan_log
      (or logged at WARNING level).  For the actual worker CLIs (claude -p,
      codex exec) this is low-probability because they do not self-daemonize.
    """
    if orphan_log is None:
        orphan_log = []

    # Step 0: snapshot the process tree BEFORE sending SIGTERM.
    # Must happen while worker_pid is still alive; setsid-escaped grandchildren
    # re-parent to launchd/init immediately on death of their original parent,
    # so after SIGTERM they would vanish from the tree we can walk.
    try:
        import psutil  # noqa: PLC0415

        try:
            worker_proc = psutil.Process(worker_pid)
            descendants = worker_proc.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            descendants = []
        _psutil_available = True
    except ImportError:
        descendants = []
        _psutil_available = False

    # Step 1: SIGTERM to the process GROUP
    try:
        os.killpg(pgid, signal.SIGTERM)
        logger.debug("kill_pgid_with_sweep: SIGTERM → pgid %d", pgid)
    except ProcessLookupError:
        logger.debug("kill_pgid_with_sweep: pgid %d already gone", pgid)
        return
    except PermissionError as exc:
        logger.warning("kill_pgid_with_sweep: SIGTERM pgid %d: %s", pgid, exc)

    # Step 2: grace window — wait for the direct child to reap
    deadline = time.monotonic() + kill_grace_s
    while time.monotonic() < deadline:
        try:
            os.kill(worker_pid, 0)
        except ProcessLookupError:
            break  # worker exited
        time.sleep(0.1)

    # Step 3: SIGKILL escalation
    # PermissionError on macOS: the entire group already exited after SIGTERM
    # so the pgid has no living members — EPERM is equivalent to "already gone".
    try:
        os.killpg(pgid, signal.SIGKILL)
        logger.debug("kill_pgid_with_sweep: SIGKILL → pgid %d (escalation)", pgid)
    except (ProcessLookupError, PermissionError):
        pass  # already gone

    # Step 4: psutil sweep of the pre-SIGTERM snapshot (catches setsid-escaped
    # grandchildren that killpg missed because they escaped the process group).
    if not _psutil_available:
        logger.warning(
            "kill_pgid_with_sweep: psutil unavailable; "
            "setsid-escaped grandchildren may survive (see design §4.3 residual)"
        )
        return

    for desc in descendants:
        try:
            desc.terminate()
            logger.debug(
                "kill_pgid_with_sweep: psutil SIGTERM → pid %d (setsid-escaped?)",
                desc.pid,
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Brief wait then SIGKILL any survivors
    time.sleep(0.2)
    for desc in descendants:
        try:
            if desc.is_running():
                desc.kill()
                logger.debug(
                    "kill_pgid_with_sweep: psutil SIGKILL → pid %d", desc.pid
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Final check — log any that survived both passes (doubly-detached orphans)
    time.sleep(0.1)
    for desc in descendants:
        try:
            if desc.is_running():
                msg = (
                    f"orphan-survived pid={desc.pid} "
                    f"ppid={desc.ppid() if hasattr(desc, 'ppid') else '?'} "
                    f"after kill_pgid_with_sweep for pgid={pgid}"
                )
                orphan_log.append(msg)
                logger.warning(msg)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


# ---------------------------------------------------------------------------
# Daily budget ledger — atomic CAS reserve / spend (closes P1-C3)
# ---------------------------------------------------------------------------


class DailyBudgetLedger:
    """
    Purpose: durable per-day spending ledger with an atomic CAS reserve step so
    parallel spawns (P3+) cannot race past the ceiling.  The table is in the
    same jobs DB as the rest of the factory store (idempotent CREATE IF NOT EXISTS).

    Usage:
        ledger = DailyBudgetLedger(db_path=Path("~/.hermes/factory/jobs.db"))
        admitted = ledger.try_reserve(job_cap_usd=0.30)
        if admitted:
            ... run the job ...
            ledger.commit_spend(job_cap_usd=0.30, actual_spend_usd=0.22)

    Gotchas:
        try_reserve uses BEGIN IMMEDIATE + an UPDATE ... WHERE clause so that
        two concurrent calls whose caps jointly exceed the ceiling produce
        exactly one rowcount==1 success (the SQLite write-lock serializes them).
        The ledger row is created lazily on the first try_reserve for the day.
    """

    def __init__(self, db_path: Path, ceiling_usd: float = _DEFAULT_CEILING_USD):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ceiling_usd = ceiling_usd
        self._lock = threading.Lock()
        # isolation_level=None → autocommit mode; we manage transactions explicitly
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(_BUDGET_SCHEMA)

    def _today_utc(self) -> str:
        """Return current UTC date as YYYY-MM-DD."""
        import datetime

        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    def _ensure_row(self, conn: sqlite3.Connection, day: str) -> None:
        """
        Purpose: lazily insert the daily_budget row for `day` if absent.
        Usage: called inside an already-open BEGIN IMMEDIATE transaction.
        Gotchas: INSERT OR IGNORE is safe; the row already existing is not an error.
        """
        conn.execute(
            "INSERT OR IGNORE INTO daily_budget (day, spent_today_usd, reserved_usd) "
            "VALUES (?, 0, 0)",
            (day,),
        )

    def try_reserve(
        self,
        job_cap_usd: float,
        day: Optional[str] = None,
    ) -> bool:
        """
        Purpose: atomically check whether adding job_cap_usd to today's
        (spent + reserved) would exceed the ceiling, and if not, increment
        reserved_usd by job_cap_usd in the SAME transaction.  Returns True if
        admitted, False if deferred.

        Usage: admitted = ledger.try_reserve(job_cap_usd=0.30)
        Gotchas:
            The CAS is: UPDATE daily_budget
                         SET reserved_usd = reserved_usd + :cap
                         WHERE day = :day
                           AND spent_today_usd + reserved_usd + :cap <= :ceiling
            rowcount==1 → admitted; rowcount==0 → deferred (ceiling would be breached).
            Two concurrent calls are serialized by SQLite's exclusive write lock on
            BEGIN IMMEDIATE — the second waits until the first commits.
        """
        if day is None:
            day = self._today_utc()

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_row(self._conn, day)
                cur = self._conn.execute(
                    """
                    UPDATE daily_budget
                       SET reserved_usd = reserved_usd + :cap
                     WHERE day = :day
                       AND spent_today_usd + reserved_usd + :cap <= :ceiling
                    """,
                    {"cap": job_cap_usd, "day": day, "ceiling": self._ceiling_usd},
                )
                admitted = cur.rowcount == 1
                self._conn.execute("COMMIT")
                return admitted
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def commit_spend(
        self,
        job_cap_usd: float,
        actual_spend_usd: float,
        day: Optional[str] = None,
    ) -> None:
        """
        Purpose: on job completion, fold actual_spend_usd into spent_today_usd
        and release the cap reservation in one atomic transaction.
        Usage: ledger.commit_spend(job_cap_usd=0.30, actual_spend_usd=0.22)
        Gotchas: if actual_spend > cap (metering can overshoot at tick granularity),
        the delta is still recorded; the ceiling becomes slightly softer post-hoc
        but the pre-launch CAS is the real guard.
        """
        if day is None:
            day = self._today_utc()

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_row(self._conn, day)
                self._conn.execute(
                    """
                    UPDATE daily_budget
                       SET spent_today_usd = spent_today_usd + :actual,
                           reserved_usd    = MAX(0, reserved_usd - :cap)
                     WHERE day = :day
                    """,
                    {"actual": actual_spend_usd, "cap": job_cap_usd, "day": day},
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def _seed_for_test(
        self, day: str, spent: float, reserved: float
    ) -> None:
        """
        Purpose: test-only helper — upsert a daily_budget row with known values
        so tests can control the ledger state without going through try_reserve.
        Usage: ledger._seed_for_test(day="2099-01-01", spent=4.80, reserved=0.0)
        Gotchas: do NOT call from production code; this bypasses the CAS guard.
        """
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO daily_budget (day, spent_today_usd, reserved_usd) "
                "VALUES (?, ?, ?)",
                (day, spent, reserved),
            )

    def _read_day(self, day: str) -> Optional[dict]:
        """
        Purpose: read the current ledger row for `day`; returns a dict with
        keys 'spent' and 'reserved', or None if the row doesn't exist.
        Usage: row = ledger._read_day("2099-03-01")
        Gotchas: used by tests and forensic logging; not performance-critical.
        """
        row = self._conn.execute(
            "SELECT spent_today_usd, reserved_usd FROM daily_budget WHERE day = ?",
            (day,),
        ).fetchone()
        if row is None:
            return None
        return {"spent": row[0], "reserved": row[1]}


# ---------------------------------------------------------------------------
# CostStopper — the three supervisor-tick checks
# ---------------------------------------------------------------------------


class CostStopper:
    """
    Purpose: encapsulates the three cost-stop checks that the supervisor tick
    calls for every RUNNING job.  Each check is independent of the others.
    Returning a StopReason means the stop FIRED and kill_pgid_with_sweep was
    called; returning None means the job is within limits.

    Usage:
        stopper = CostStopper(db_path=Path("~/.hermes/factory/jobs.db"))
        reason = stopper.check_budget(pgid, pid, cost_so_far_usd, budget_usd)
        reason = stopper.check_timeout(pgid, pid, elapsed_s, timeout_s)
        reason = stopper.check_ceiling_breach(pgid, pid, spent_today_usd)

    Gotchas:
        Each check calls kill_pgid_with_sweep on a breach; the caller (supervisor)
        is responsible for transitioning the job to FAILED(budget/timeout/ceiling).
        The DailyBudgetLedger is accessible as stopper.ledger for the pre-launch
        try_reserve step.
    """

    def __init__(
        self,
        db_path: Path,
        ceiling_usd: float = _DEFAULT_CEILING_USD,
        kill_grace_s: float = _KILL_GRACE_S,
    ):
        self._ceiling_usd = ceiling_usd
        self._kill_grace_s = kill_grace_s
        self.ledger = DailyBudgetLedger(db_path=db_path, ceiling_usd=ceiling_usd)

    def check_budget(
        self,
        *,
        pgid: int,
        pid: int,
        cost_so_far_usd: float,
        budget_usd: float,
        orphan_log: Optional[List[str]] = None,
    ) -> Optional[StopReason]:
        """
        Purpose: fire the per-job budget cap stop if cost_so_far_usd >= budget_usd.
        Sends SIGTERM to the process group (killpg) plus a psutil sweep.

        Usage: reason = stopper.check_budget(pgid=..., pid=..., cost_so_far_usd=1.05, budget_usd=1.0)
        Gotchas: returns None if still under budget; StopReason.BUDGET otherwise.
        The supervisor must then call store.transition(job_id, "RUNNING", "FAILED",
        fail_reason="budget") — this method does NOT touch the job store.
        """
        if cost_so_far_usd >= budget_usd:
            logger.warning(
                "cost stop BUDGET fired: pgid=%d cost=%.4f >= cap=%.4f",
                pgid,
                cost_so_far_usd,
                budget_usd,
            )
            kill_pgid_with_sweep(
                pgid=pgid,
                worker_pid=pid,
                orphan_log=orphan_log,
                kill_grace_s=self._kill_grace_s,
            )
            return StopReason.BUDGET
        return None

    def check_timeout(
        self,
        *,
        pgid: int,
        pid: int,
        elapsed_s: float,
        timeout_s: float,
        orphan_log: Optional[List[str]] = None,
    ) -> Optional[StopReason]:
        """
        Purpose: fire the wall-clock timeout stop if elapsed_s > timeout_s.
        Sends SIGTERM to the process GROUP (so all in-group children also die),
        then escalates to SIGKILL after the grace window, then psutil-sweeps
        any setsid-escaped grandchildren.

        Usage: reason = stopper.check_timeout(pgid=..., pid=..., elapsed_s=62, timeout_s=60)
        Gotchas:
          The SIGTERM reaches the whole process GROUP (a group child also dies)
          because we use os.killpg — this is what makes the timeout test assertable
          by checking that a child of the worker also died.
          Returns None if still within timeout; StopReason.TIMEOUT otherwise.
        """
        if elapsed_s > timeout_s:
            logger.warning(
                "cost stop TIMEOUT fired: pgid=%d elapsed=%.1f > timeout=%.1f",
                pgid,
                elapsed_s,
                timeout_s,
            )
            kill_pgid_with_sweep(
                pgid=pgid,
                worker_pid=pid,
                orphan_log=orphan_log,
                kill_grace_s=self._kill_grace_s,
            )
            return StopReason.TIMEOUT
        return None

    def check_ceiling_breach(
        self,
        *,
        pgid: int,
        pid: int,
        spent_today_usd: float,
        day: Optional[str] = None,
        orphan_log: Optional[List[str]] = None,
    ) -> Optional[StopReason]:
        """
        Purpose: fire the daily ceiling stop if spent_today_usd > ceiling.
        This is the mid-flight check (run per tick for every active job); the
        pre-launch admission check is DailyBudgetLedger.try_reserve().

        Usage: reason = stopper.check_ceiling_breach(pgid=..., pid=..., spent_today_usd=5.10)
        Gotchas: returns None if under ceiling; StopReason.CEILING otherwise.
        The CAS pre-launch guard (try_reserve) is the primary line of defence;
        check_ceiling_breach is the belt that catches mid-flight overruns.
        """
        if spent_today_usd > self._ceiling_usd:
            logger.warning(
                "cost stop CEILING fired: pgid=%d spent=%.4f > ceiling=%.4f",
                pgid,
                spent_today_usd,
                self._ceiling_usd,
            )
            kill_pgid_with_sweep(
                pgid=pgid,
                worker_pid=pid,
                orphan_log=orphan_log,
                kill_grace_s=self._kill_grace_s,
            )
            return StopReason.CEILING
        return None
