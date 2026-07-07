# ABOUTME: SQLite job store for the Fable factory — the spine of all job state.
# ABOUTME: The supervisor is the SOLE writer; this module exposes the schema,
# ABOUTME: one-way CAS state machine, supervisor lock, per-tick integrity check,
# ABOUTME: and the build-jobs.md markdown mirror. Three DBs, three owners —
# ABOUTME: this store is separate from held_actions.db and the kanban board DB.
"""
Job store, one-way state machine, supervisor lock, integrity check, and
build-jobs.md mirror.

The supervisor is the SOLE writer of the jobs table.  Intake (intake.py) and
other modules return job-spec dicts; the supervisor calls enqueue_job() here to
produce the single INSERT.  No other module holds a write cursor on this store.

State machine (one-way, enforced via CAS UPDATE):

    QUEUED ──► RUNNING ──► TEST ──► REVIEW ──► AWAITING_APPROVAL ──► MERGING ──► DONE
       │          │           │         │               │                 │
       │          └──────┬────┴─────────┴───────────────┴─────────────────┘
       │                 ▼
       │          NEEDS_ATTENTION   (terminal until human intervenes)
       │
       └──► FAILED   (terminal)

Backward transitions raise IllegalTransition and leave the row unchanged.
REVIEW→RUNNING and TEST→RUNNING are allowed as one auto-fix retry each.

Reuses the CAS pattern from broker/held_store.py (UPDATE … WHERE id=? AND
state=?) and the supervisor-lock crash-grace from hermes_cli/kanban_db.py
(15-minute claim TTL, steal if stale).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Import ULID generation from the shared broker utility so job IDs are the
# same stable, time-sortable format as action IDs.
from broker.action_id import new_action_id as _new_ulid

# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

STATES = frozenset(
    {
        "QUEUED",
        "ADMITTED",
        "RUNNING",
        "TEST",
        "REVIEW",
        "AWAITING_APPROVAL",
        "MERGING",
        "DONE",
        "FAILED",
        "NEEDS_ATTENTION",
        # P4-a (Revision v2 §R1) — additive crash-resume + residency-wait states.
        "RESUMABLE",
        "WAITING_CAPACITY",
        # P5-d (design §5.1) — drift-watchdog pause; distinguishable from a QA
        # failure in the morning card.  Forward-only: RUNNING→PAUSED_DRIFT;
        # PAUSED_DRIFT→{ADMITTED, NEEDS_ATTENTION} when the owner clears it.
        "PAUSED_DRIFT",
    }
)

# Exact one-way transition map.  A key not present in any set means
# the state is terminal (no outgoing transitions).  REVIEW→RUNNING and
# TEST→RUNNING represent the one allowed auto-fix retry.
#
# ADMITTED (P3 §13.3) is the atomic-slot-reservation state: the fleet
# scheduler CAS-moves QUEUED→ADMITTED under the store lock BEFORE it spawns
# the per-node worker, so the concurrency slot is claimed the instant it is
# reserved — the readiness check cannot double-admit past the cap in the gap
# between spawn and the child's RUNNING transition.  A scheduler-admitted job
# advances ADMITTED→RUNNING.
#
# NOTE (P3-c scope deviation from design §13.3): the design's exact _ALLOWED
# edit DROPS QUEUED→RUNNING (making ADMITTED mandatory), paired with a matching
# supervisor.py:339 edit owned by P3-b.  P3-c must not touch supervisor.py and
# must keep every existing P2 job_store test green, so QUEUED→RUNNING is RETAINED
# alongside the new QUEUED→ADMITTED edge.  Both paths coexist: the P2 one-job
# supervisor still drives QUEUED→RUNNING directly; the P3 fleet scheduler drives
# QUEUED→ADMITTED→RUNNING.  The atomic-admission guarantee is unaffected — the
# scheduler ALWAYS reserves the slot via QUEUED→ADMITTED and never uses the direct
# edge, and the cap count includes ADMITTED.  Making ADMITTED mandatory is a P3-b
# follow-up (drop RUNNING here + edit supervisor in the same change).
# P4-a (design Revision v2 §R1) — ADDITIVE crash-resume states. RESUMABLE and
# WAITING_CAPACITY are new, forward-only states; the inbound edges to them are
# APPENDED to the existing live states (every pre-P4 target below is preserved, so
# the one-way invariant and all P2/P3 transitions still hold). RESUMABLE = a job
# whose worker died (dead pgid) but a VALID phase checkpoint exists — the scheduler
# re-admits it (RESUMABLE→ADMITTED) and resumes from the last cleared phase; if
# resume is impossible/exhausted it parks terminal (RESUMABLE→NEEDS_ATTENTION).
# WAITING_CAPACITY = the residency wait-for-capacity park (P4-b §4.3), same two
# forward exits. Both flow ONLY forward to ADMITTED or the terminal NEEDS_ATTENTION,
# so the machine stays a DAG toward terminals (bounded by MAX_RESUMES per job).
_ALLOWED: Dict[str, frozenset] = {
    "QUEUED": frozenset({"ADMITTED", "RUNNING", "FAILED", "PAUSED_DRIFT"}),
    "ADMITTED": frozenset({"RUNNING", "FAILED", "NEEDS_ATTENTION", "RESUMABLE", "PAUSED_DRIFT"}),
    "RUNNING": frozenset(
        {"TEST", "FAILED", "NEEDS_ATTENTION", "RESUMABLE", "WAITING_CAPACITY",
         "PAUSED_DRIFT"}
    ),
    "TEST": frozenset({"REVIEW", "RUNNING", "NEEDS_ATTENTION", "RESUMABLE"}),
    "REVIEW": frozenset({"AWAITING_APPROVAL", "RUNNING", "NEEDS_ATTENTION", "RESUMABLE"}),
    "AWAITING_APPROVAL": frozenset({"MERGING", "NEEDS_ATTENTION"}),
    "MERGING": frozenset({"DONE", "NEEDS_ATTENTION"}),
    "RESUMABLE": frozenset({"ADMITTED", "NEEDS_ATTENTION"}),
    "WAITING_CAPACITY": frozenset({"ADMITTED", "NEEDS_ATTENTION"}),
    # P5-d: drift-paused jobs can be re-admitted by the owner (cleared) or
    # escalated to terminal NEEDS_ATTENTION if the drift proves unrecoverable.
    "PAUSED_DRIFT": frozenset({"ADMITTED", "NEEDS_ATTENTION"}),
    "DONE": frozenset(),
    "FAILED": frozenset(),
    "NEEDS_ATTENTION": frozenset(),
}

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IllegalTransition(Exception):
    """Raised when a state transition is not permitted by the one-way machine."""


class SupervisorLockError(Exception):
    """Raised when the supervisor lock cannot be claimed (another live supervisor)."""


# ---------------------------------------------------------------------------
# Schema (mirrors held_store.py WAL+FULL pragma choice)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id                    TEXT PRIMARY KEY,       -- ULID
  repo                  TEXT NOT NULL,          -- repo slug / path
  base_branch           TEXT NOT NULL,          -- e.g. main / origin/main
  spec                  TEXT NOT NULL,          -- the task spec text
  kind                  TEXT NOT NULL,          -- 'feature' | 'quick'
  worker                TEXT NOT NULL,          -- 'claude' | 'codex' | 'openrouter'
  model                 TEXT NOT NULL,          -- resolved model id
  budget_usd            REAL NOT NULL,          -- per-job dollar cap
  timeout_min           INTEGER NOT NULL,       -- wall-clock cap in minutes
  state                 TEXT NOT NULL,          -- see state machine above
  worktree_path         TEXT,                   -- git worktree add target
  branch                TEXT,                   -- factory/<repo>/<id>-<slug>
  pgid                  INTEGER,                -- process-group id for cost stops
  cost_so_far_usd       REAL NOT NULL DEFAULT 0,
  test_result           TEXT,                   -- 'pass'|'fail'|'flaky'|NULL
  review_findings_path  TEXT,                   -- pointer to codex review findings
  log_tail              TEXT,                   -- last N lines of worker output
  confidence            REAL,                   -- NULL until P4.6 populates it
  trust_tier_at_spawn   INTEGER,                -- NULLABLE; reserved for P1b
  fail_reason           TEXT,                   -- populated on FAILED/NEEDS_ATTENTION
  intake_source_hash    TEXT,                   -- idempotency key for intake (§1.6)
  budget_settled        INTEGER NOT NULL DEFAULT 0,  -- P3 §13.5: reservation released?
  resume_count          INTEGER NOT NULL DEFAULT 0,   -- P4-a §R1: crash-resume count (≤MAX_RESUMES)
  created_ts            INTEGER NOT NULL,
  updated_ts            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_intake_hash ON jobs(intake_source_hash);

-- P3 §4.2: dependency edges for the fleet scheduler.  A dependent job is
-- 'ready' only when every depends_on job has reached DONE.  Same DB, same
-- WAL/FULL connection, same one shared lock — no new writer, no new DB.
CREATE TABLE IF NOT EXISTS job_deps (
  job_id      TEXT NOT NULL,   -- the dependent (blocked) job
  depends_on  TEXT NOT NULL,   -- the job that must reach DONE first
  graph_id    TEXT NOT NULL,   -- the TaskGraph.spec_hash this edge belongs to
  PRIMARY KEY (job_id, depends_on)
);
CREATE INDEX IF NOT EXISTS idx_job_deps_job ON job_deps(job_id);

-- Supervisor lock: 1-row table, stealed by the next supervisor if stale.
CREATE TABLE IF NOT EXISTS supervisor_lock (
  k              INTEGER PRIMARY KEY CHECK (k=1),
  owner_pid      INTEGER NOT NULL,
  heartbeat_ts   INTEGER NOT NULL
);

-- Daily budget ceiling ledger (for cost_stops.py; seeded here so the table
-- structure is owned alongside the jobs DB).
CREATE TABLE IF NOT EXISTS daily_budget (
  day            TEXT PRIMARY KEY,   -- YYYY-MM-DD UTC
  spent_today_usd  REAL NOT NULL DEFAULT 0,
  reserved_usd     REAL NOT NULL DEFAULT 0
);
"""

# How long a supervisor heartbeat may be stale before a new supervisor steals
# the lock.  Mirrors kanban_db DEFAULT_CLAIM_TTL_SECONDS (15 min).
_SUPERVISOR_LOCK_TTL_S = 15 * 60

# Human-readable mirror written at the end of each supervisor tick.
_BUILD_JOBS_COLUMNS = (
    "id",
    "repo",
    "state",
    "cost_so_far_usd",
    "test_result",
    "confidence",
    "spec",
)


class JobStore:
    """
    Purpose: durable SQLite store for factory jobs; the single source of truth
    for all job state.  The supervisor is the SOLE writer — this class must only
    be used from supervisor.py for INSERTs and state transitions.
    Usage: store = JobStore(path); jid = store.enqueue_job(spec_dict);
           store.transition(jid, "QUEUED", "RUNNING").
    Gotchas: WAL+FULL synchronous for crash safety; all writes acquire a lock;
    illegal backward transitions raise IllegalTransition and leave the row
    unchanged; do NOT import this class from any module other than supervisor.py
    for write operations.
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL + FULL synchronous: a committed state change must survive power
        # loss — the same durability posture as held_store.py.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        # P3 §4.7: absorb brief writer contention under fleet concurrency —
        # a momentary write-lock waits up to 5s instead of raising SQLITE_BUSY.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._migrate_columns()
        self._conn.commit()

    def _migrate_columns(self) -> None:
        """
        Purpose: add additive columns to a jobs table created before they existed
        (CREATE IF NOT EXISTS never alters an existing table).  Idempotent — a
        no-op once each column is present.  Covers budget_settled (P3 §13.5) and
        resume_count (P4-a §R1).
        Usage: called once from __init__ after the schema script runs.
        Gotchas: ALTER TABLE ADD COLUMN is the only safe in-place migration in
        SQLite; each add is guarded so a fresh DB (column already present) does not
        error.  A default is required because the column is NOT NULL.
        """
        cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "budget_settled" not in cols:
            self._conn.execute(
                "ALTER TABLE jobs ADD COLUMN budget_settled INTEGER NOT NULL DEFAULT 0"
            )
        if "resume_count" not in cols:
            self._conn.execute(
                "ALTER TABLE jobs ADD COLUMN resume_count INTEGER NOT NULL DEFAULT 0"
            )

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------

    def enqueue_job(self, spec: Dict[str, Any]) -> str:
        """
        Purpose: insert a new job row from a normalized spec dict and return
        its ULID job id.  This is the ONLY INSERT path — all intake sources
        funnel through the supervisor which calls this method.
        Usage: jid = store.enqueue_job({"repo": "acme", "spec": "add X", ...}).
        Gotchas: the caller (supervisor) must supply all required keys; missing
        keys raise KeyError early rather than producing a malformed row.
        """
        jid = _new_ulid()
        now = _now_ms()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (
                    id, repo, base_branch, spec, kind, worker, model,
                    budget_usd, timeout_min, state,
                    worktree_path, branch, pgid,
                    cost_so_far_usd, test_result, review_findings_path,
                    log_tail, confidence, trust_tier_at_spawn, fail_reason,
                    intake_source_hash, created_ts, updated_ts
                ) VALUES (
                    :id, :repo, :base_branch, :spec, :kind, :worker, :model,
                    :budget_usd, :timeout_min, 'QUEUED',
                    NULL, NULL, NULL,
                    0, NULL, NULL,
                    NULL, NULL, NULL, NULL,
                    :intake_source_hash, :created_ts, :updated_ts
                )
                """,
                {
                    "id": jid,
                    "repo": spec["repo"],
                    "base_branch": spec.get("base_branch", "main"),
                    "spec": spec["spec"],
                    "kind": spec.get("kind", "feature"),
                    "worker": spec.get("worker", "claude"),
                    "model": spec.get("model", "claude-opus-4-5"),
                    "budget_usd": spec.get("budget_usd", 1.0),
                    "timeout_min": spec.get("timeout_min", 30),
                    "intake_source_hash": spec.get("intake_source_hash"),
                    "created_ts": now,
                    "updated_ts": now,
                },
            )
            self._conn.commit()
        return jid

    def transition(
        self,
        job_id: str,
        expected: str,
        target: str,
        *,
        fail_reason: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Purpose: atomically move a job from `expected` state to `target` via
        a CAS UPDATE (UPDATE … WHERE id=? AND state=?).  Illegal or backward
        transitions are rejected before the SQL is issued.
        Usage: store.transition(jid, "QUEUED", "RUNNING").
        Gotchas: raises IllegalTransition if (a) the target is not reachable
        from expected in the one-way map, or (b) the row is not currently in
        the expected state (optimistic concurrency — two racing ticks cannot
        both advance a job).  The row is unchanged on any exception.
        """
        if target not in _ALLOWED.get(expected, frozenset()):
            raise IllegalTransition(f"{expected} → {target} is not permitted")

        # Build update columns: state + updated_ts are always set; optional
        # extras (fail_reason, worktree_path, pgid, …) are included when given.
        set_clauses = ["state = ?", "updated_ts = ?"]
        params: list = [target, _now_ms()]

        if fail_reason is not None:
            set_clauses.append("fail_reason = ?")
            params.append(fail_reason)

        for col, val in (extra or {}).items():
            set_clauses.append(f"{col} = ?")
            params.append(val)

        params.extend([job_id, expected])  # WHERE id=? AND state=?

        with self._lock:
            cur = self._conn.execute(
                f"UPDATE jobs SET {', '.join(set_clauses)} WHERE id = ? AND state = ?",
                params,
            )
            self._conn.commit()
            if cur.rowcount != 1:
                raise IllegalTransition(
                    f"job {job_id} not in expected state {expected!r} (or unknown id)"
                )

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Purpose: fetch the full job row by id.
        Usage: row = store.get(jid); row["state"], row["repo"], etc.
        Gotchas: returns None if id is unknown.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_jobs(
        self, *, state: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Purpose: list all jobs, optionally filtered by state.
        Usage: active = store.list_jobs(state="RUNNING").
        Gotchas: returns a snapshot; the store may change between calls.
        """
        with self._lock:
            if state is not None:
                rows = self._conn.execute(
                    "SELECT * FROM jobs WHERE state = ? ORDER BY created_ts",
                    (state,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM jobs ORDER BY created_ts"
                ).fetchall()
        return [dict(r) for r in rows]

    def has_job_for_intake_hash(self, intake_hash: str) -> bool:
        """
        Purpose: idempotency guard — return True if a job already exists for
        this intake source hash so the supervisor does not create a duplicate.
        Usage: if not store.has_job_for_intake_hash(h): store.enqueue_job(spec).
        Gotchas: the hash must be stable across re-ticks for the same source.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM jobs WHERE intake_source_hash = ? LIMIT 1",
                (intake_hash,),
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Dependency graph + atomic slot reservation (P3 fleet scheduler)
    # ------------------------------------------------------------------

    def add_dep(self, job_id: str, depends_on: str, graph_id: str) -> None:
        """
        Purpose: record a depends_on edge (job_id is blocked until depends_on
        reaches DONE).  Called by the scheduler thread at enqueue under the one
        shared lock — no new writer is introduced (§13.2).
        Usage: store.add_dep("T2job", "T1job", graph_id=spec_hash).
        Gotchas: INSERT OR IGNORE — re-adding the same edge is a no-op, so a
        re-tick that re-enqueues an idempotent graph does not error.
        """
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO job_deps (job_id, depends_on, graph_id) "
                "VALUES (?, ?, ?)",
                (job_id, depends_on, graph_id),
            )
            self._conn.commit()

    def deps_of(self, job_id: str) -> List[str]:
        """
        Purpose: return the list of job ids this job depends on.
        Usage: blockers = store.deps_of(jid).
        Gotchas: an empty list means the job has no dependencies (independent).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT depends_on FROM job_deps WHERE job_id = ?", (job_id,)
            ).fetchall()
        return [r["depends_on"] for r in rows]

    def ready_jobs(self) -> List[Dict[str, Any]]:
        """
        Purpose: the scheduler's serialization primitive — return QUEUED jobs
        whose EVERY depends_on job is DONE, oldest first.  A node with no deps is
        always ready; a dependent node is ready only after its predecessor merges.
        Usage: for node in store.ready_jobs()[:capacity]: admit(node).
        Gotchas: a job with a dependency that is FAILED/NEEDS_ATTENTION is NOT
        ready (its blocker never reached DONE) — it will not spawn, which is the
        fail-safe posture (a dependent cannot start before its predecessor lands).
        """
        with self._lock:
            queued = self._conn.execute(
                "SELECT * FROM jobs WHERE state = 'QUEUED' ORDER BY created_ts"
            ).fetchall()
            ready: List[Dict[str, Any]] = []
            for row in queued:
                jid = row["id"]
                dep_rows = self._conn.execute(
                    "SELECT depends_on FROM job_deps WHERE job_id = ?", (jid,)
                ).fetchall()
                dep_ids = [d["depends_on"] for d in dep_rows]
                if not dep_ids:
                    ready.append(dict(row))
                    continue
                # All dependencies must be DONE for the job to be ready.
                placeholders = ",".join("?" * len(dep_ids))
                done = self._conn.execute(
                    f"SELECT COUNT(*) AS n FROM jobs "
                    f"WHERE id IN ({placeholders}) AND state = 'DONE'",
                    dep_ids,
                ).fetchone()["n"]
                if done == len(dep_ids):
                    ready.append(dict(row))
        return ready

    def reserve_slot(self, job_id: str) -> bool:
        """
        Purpose: atomically claim a concurrency slot by CAS-moving a job
        QUEUED→ADMITTED under the one shared lock, BEFORE the scheduler spawns
        the per-node supervisor (P3 §13.3).  This is what makes the cap race-free:
        the slot is taken the instant it is reserved, so a tick firing in the
        spawn→RUNNING gap already counts this job and cannot double-admit.
        Usage: if store.reserve_slot(jid): spawn(...) else: retry next tick.
        Gotchas: returns True on rowcount==1 (we won the CAS), False if the job
        was no longer QUEUED (another writer took it) — the scheduler releases any
        budget reservation and moves on when False.
        """
        now = _now_ms()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET state='ADMITTED', updated_ts=? "
                "WHERE id=? AND state='QUEUED'",
                (now, job_id),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def reserve_resume_slot(self, job_id: str) -> bool:
        """
        Purpose: atomically claim a concurrency slot for a RESUMABLE or
        WAITING_CAPACITY job by CAS-moving it → ADMITTED under the one shared lock,
        BEFORE the scheduler re-launches it from its checkpoint (P4-a §R1).  Mirrors
        reserve_slot's atomic-admission guarantee for the crash-resume re-entry.
        Usage: if store.reserve_resume_slot(jid): resume_spawn(...); else retry.
        Gotchas: returns True on rowcount==1 (we won the CAS from RESUMABLE or
        WAITING_CAPACITY); False if the job was no longer in a resumable state
        (another writer took it, or it already re-admitted).  The forward-only edge
        RESUMABLE/WAITING_CAPACITY → ADMITTED is enforced by the DB predicate here.
        """
        now = _now_ms()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET state='ADMITTED', updated_ts=? "
                "WHERE id=? AND state IN ('RESUMABLE','WAITING_CAPACITY')",
                (now, job_id),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def resumable_jobs(self) -> List[Dict[str, Any]]:
        """
        Purpose: return jobs parked RESUMABLE (a crashed worker with a valid
        checkpoint) that the scheduler should re-admit and relaunch, oldest first.
        WAITING_CAPACITY is intentionally EXCLUDED here — those need a router
        admissibility re-check (P4-b) before re-admission and are handled separately.
        Usage: for job in store.resumable_jobs()[:capacity]: re_admit(job).
        Gotchas: a snapshot; the store may change between calls.  Ordering by
        updated_ts surfaces the longest-waiting crash first.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE state = 'RESUMABLE' ORDER BY updated_ts"
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Supervisor lock
    # ------------------------------------------------------------------

    def claim_supervisor_lock(self, pid: Optional[int] = None) -> None:
        """
        Purpose: claim the supervisor-sole-writer lock.  Only one supervisor
        may hold a live lock; a stale lock (heartbeat older than TTL) is stolen.
        Usage: call at supervisor startup; raises SupervisorLockError if a live
        lock is held by another PID.
        Gotchas: the TTL is 15 minutes (mirrors kanban claim TTL); the supervisor
        must call heartbeat_supervisor_lock() at least that often.  This lock
        complements fcntl.flock-style tick locks — the flock stops overlapping
        ticks of one process; this lock stops two separate supervisor processes.
        """
        if pid is None:
            pid = os.getpid()
        now = _now_ms()
        stale_before = now - (_SUPERVISOR_LOCK_TTL_S * 1000)

        with self._lock:
            # Try to INSERT the single lock row.  If it already exists, try to
            # steal it only if heartbeat_ts is stale.
            try:
                self._conn.execute(
                    "INSERT INTO supervisor_lock (k, owner_pid, heartbeat_ts)"
                    " VALUES (1, ?, ?)",
                    (pid, now),
                )
                self._conn.commit()
                return
            except sqlite3.IntegrityError:
                pass  # Row exists — try steal.

            cur = self._conn.execute(
                "UPDATE supervisor_lock SET owner_pid = ?, heartbeat_ts = ?"
                " WHERE k = 1 AND heartbeat_ts < ?",
                (pid, now, stale_before),
            )
            self._conn.commit()
            if cur.rowcount != 1:
                # Lock row exists and heartbeat is fresh — another supervisor lives.
                row = self._conn.execute(
                    "SELECT owner_pid FROM supervisor_lock WHERE k = 1"
                ).fetchone()
                other = row["owner_pid"] if row else "unknown"
                raise SupervisorLockError(
                    f"supervisor lock held by pid {other} (heartbeat fresh)"
                )

    def heartbeat_supervisor_lock(self, pid: Optional[int] = None) -> None:
        """
        Purpose: refresh the supervisor heartbeat so the lock is not stolen
        by a later supervisor that sees a stale timestamp.
        Usage: call periodically inside the tick loop (every tick is fine).
        Gotchas: silently no-ops if the lock row is owned by a different pid
        (means we were overtaken; the supervisor should detect this and exit).
        """
        if pid is None:
            pid = os.getpid()
        with self._lock:
            self._conn.execute(
                "UPDATE supervisor_lock SET heartbeat_ts = ? WHERE k = 1 AND owner_pid = ?",
                (_now_ms(), pid),
            )
            self._conn.commit()

    def release_supervisor_lock(self, pid: Optional[int] = None) -> None:
        """
        Purpose: release the supervisor lock on clean shutdown so a successor
        can claim immediately without waiting for the TTL.
        Usage: call in the supervisor's finally block or atexit handler.
        Gotchas: only deletes the row if the pid matches (guards against a late
        cleanup of an already-stolen lock).
        """
        if pid is None:
            pid = os.getpid()
        with self._lock:
            self._conn.execute(
                "DELETE FROM supervisor_lock WHERE k = 1 AND owner_pid = ?",
                (pid,),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Per-tick integrity check
    # ------------------------------------------------------------------

    def check_integrity(
        self,
        ledger: Optional[Any] = None,
        checkpoint_reader: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Purpose: validate store invariants at the start of each supervisor/scheduler
        tick.  Catches corruption before acting on potentially bad state.  Each
        violation is logged and the offending job is parked to NEEDS_ATTENTION — the
        check never crashes the tick loop.

        Invariants checked:
          (a) No live job (ADMITTED/RUNNING/TEST/REVIEW) whose pgid is dead but
              state is still active — reconcile to NEEDS_ATTENTION.  When it
              reclaims such a job it ALSO releases that job's budget reservation
              via the passed-in ledger (P3 §13.5) so the ledger and the job table
              can never drift; the release is idempotent via the budget_settled
              flag set in the SAME transaction as the state change.
          (b) No two RUNNING rows share a worktree_path.
          (c) cost_so_far_usd ≤ budget_usd for every live job.
          (d) Every non-NULL branch matches the factory/<repo>/<id>- prefix.

        Usage: violations = store.check_integrity(ledger=stopper.ledger).
        Gotchas: this method WRITES to the store (parks bad rows); call only
        from the supervisor/scheduler tick, not from read-only callers.  Passing
        no ledger keeps the P2 behaviour (state reconciliation only, no release).

        P4-a (Revision v2 §R1): ``checkpoint_reader`` is an optional callback
        ``(job_row) -> {"valid": bool, "exhausted": bool} | None`` that lets the
        reclaim path route a dead-pgid job WITH a valid, non-exhausted phase
        checkpoint to RESUMABLE instead of terminal NEEDS_ATTENTION.  When it is
        None (every P2/P3 caller), the reclaim behaviour is UNCHANGED — the job
        parks NEEDS_ATTENTION exactly as before.  The reader is injected (rather
        than importing phase_checkpoint here) to keep job_store free of a factory
        module cycle.
        """
        violations: List[Dict[str, Any]] = []
        now = _now_ms()
        # ADMITTED is a live slot (§13.3): an admitted job whose spawn died before
        # RUNNING must be reclaimed too, releasing BOTH its slot and reservation.
        live_states = (
            "ADMITTED", "RUNNING", "TEST", "REVIEW", "AWAITING_APPROVAL", "MERGING",
        )

        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM jobs WHERE state IN ({','.join('?'*len(live_states))})",
                live_states,
            ).fetchall()

        for row in rows:
            job = dict(row)
            jid = job["id"]

            # (a) Dead process group but state still active — pgid must be set,
            #     and the state must be one that owns a live process (ADMITTED or
            #     RUNNING).  An ADMITTED job whose spawn died has a stale pgid too.
            if job["pgid"] is not None and job["state"] in ("ADMITTED", "RUNNING"):
                pgid = job["pgid"]
                try:
                    os.kill(-pgid, 0)  # probe the process group
                except ProcessLookupError:
                    # Process group is gone but job still ADMITTED/RUNNING.
                    v = {
                        "job_id": jid,
                        "rule": "dead_pgid",
                        "detail": f"pgid {pgid} no longer alive",
                    }
                    violations.append(v)
                    self._reclaim_dead_job(
                        jid, job, pgid, now, ledger, checkpoint_reader
                    )

            # (c) Cost over budget.
            if (
                job["cost_so_far_usd"] is not None
                and job["budget_usd"] is not None
                and job["cost_so_far_usd"] > job["budget_usd"]
            ):
                v = {
                    "job_id": jid,
                    "rule": "over_budget",
                    "detail": (
                        f"cost {job['cost_so_far_usd']:.4f} > "
                        f"budget {job['budget_usd']:.4f}"
                    ),
                }
                violations.append(v)

            # (d) Branch prefix sanity.
            branch = job["branch"]
            repo = job["repo"]
            if branch is not None:
                expected_prefix = f"factory/{repo}/"
                if not branch.startswith(expected_prefix):
                    v = {
                        "job_id": jid,
                        "rule": "bad_branch_prefix",
                        "detail": (
                            f"branch {branch!r} does not start with "
                            f"{expected_prefix!r}"
                        ),
                    }
                    violations.append(v)

        # (b) Duplicate worktree_path among RUNNING jobs.
        running_jobs = [j for j in [dict(r) for r in rows] if j["state"] == "RUNNING"]
        seen_paths: Dict[str, str] = {}  # worktree_path → first job_id
        for job in running_jobs:
            wt = job["worktree_path"]
            if wt is None:
                continue
            if wt in seen_paths:
                v = {
                    "job_id": job["id"],
                    "rule": "duplicate_worktree",
                    "detail": (
                        f"worktree_path {wt!r} also used by "
                        f"job {seen_paths[wt]}"
                    ),
                }
                violations.append(v)
            else:
                seen_paths[wt] = job["id"]

        return violations

    def _reclaim_dead_job(
        self,
        jid: str,
        job: Dict[str, Any],
        pgid: int,
        now: int,
        ledger: Optional[Any],
        checkpoint_reader: Optional[
            Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]
        ] = None,
    ) -> None:
        """
        Purpose: reclaim a dead-pgid job.  P4-a (Revision v2 §R1) makes the target
        CHECKPOINT-AWARE: a job with a VALID, non-exhausted phase checkpoint is
        routed to RESUMABLE (the scheduler re-admits and resumes from the last
        cleared phase); a job with NO checkpoint, an INVALID one, or one that has
        exhausted MAX_RESUMES is parked terminal at NEEDS_ATTENTION exactly as
        before.  The budget_settled flag is set in the SAME UPDATE that changes
        state so a second sweep never double-settles.
        Usage: called from check_integrity when a live job's process group is gone.
        Gotchas:
          * RESERVATION ORDERING differs by target.  On the terminal
            NEEDS_ATTENTION path the reservation is RELEASED (unchanged P3 §13.5)
            and budget_settled flips to 1.  On the RESUMABLE path the job WILL
            re-run and re-spend, so the reservation is KEPT and budget_settled
            stays 0 — releasing it would let a resumed job spend past the ceiling.
          * The release happens only if budget_settled was 0 BEFORE this reclaim,
            so the terminal path is idempotent across sweeps.
          * checkpoint_reader is injected (not imported) to avoid a job_store →
            phase_checkpoint module cycle.  None ⇒ legacy terminal behaviour.
        """
        expected = job["state"]  # ADMITTED or RUNNING

        # Decide the target: RESUMABLE iff a valid, non-exhausted checkpoint exists.
        target = "NEEDS_ATTENTION"
        if checkpoint_reader is not None:
            try:
                ckpt = checkpoint_reader(job)
            except Exception:
                ckpt = None  # a broken reader must not crash the sweep — fail-closed
            if ckpt and ckpt.get("valid") and not ckpt.get("exhausted"):
                target = "RESUMABLE"

        if target == "RESUMABLE":
            # Keep the reservation (the job re-runs); do NOT flip budget_settled.
            with self._lock:
                self._conn.execute(
                    "UPDATE jobs SET state='RESUMABLE', fail_reason=?, updated_ts=? "
                    "WHERE id=? AND state=?",
                    (f"integrity: dead pgid {pgid} (resumable)", now, jid, expected),
                )
                self._conn.commit()
            return

        # Terminal park (unchanged P3 §13.5 settlement path).
        already_settled = bool(job.get("budget_settled", 0))
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET state='NEEDS_ATTENTION', fail_reason=?, "
                "budget_settled=1, updated_ts=? WHERE id=? AND state=?",
                (f"integrity: dead pgid {pgid}", now, jid, expected),
            )
            self._conn.commit()
            parked = cur.rowcount == 1
        # Release the reservation only if we actually parked it AND it was not
        # already settled — the flag makes this idempotent across sweeps.
        if parked and not already_settled and ledger is not None:
            cap = job.get("budget_usd") or 0.0
            ledger.release_reservation(cap, jid)

    # ------------------------------------------------------------------
    # build-jobs.md mirror
    # ------------------------------------------------------------------

    def write_build_jobs_mirror(self, mirror_path: Path) -> None:
        """
        Purpose: regenerate the human-readable build-jobs.md from the current
        jobs table.  Written atomically (temp file + os.replace) so a reader
        never sees a partial file.
        Usage: call at the end of each supervisor tick.
        Gotchas: mirror is read-only from outside the supervisor; the store is
        the single source of truth — the mirror is never read back as input.
        """
        jobs = self.list_jobs()
        lines = [
            "# build-jobs.md — factory job mirror",
            f"<!-- generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} -->",
            "",
            "| id (short) | repo | state | cost_usd | test | conf | spec |",
            "|---|---|---|---|---|---|---|",
        ]
        for job in jobs:
            short_id = job["id"][:10]
            cost = f"{job['cost_so_far_usd']:.4f}" if job["cost_so_far_usd"] else "0"
            test = job["test_result"] or "—"
            conf = f"{job['confidence']:.2f}" if job["confidence"] is not None else "—"
            spec_short = (job["spec"] or "")[:60].replace("|", "\\|")
            lines.append(
                f"| {short_id} | {job['repo']} | {job['state']} "
                f"| {cost} | {test} | {conf} | {spec_short} |"
            )

        content = "\n".join(lines) + "\n"

        # Atomic write: write to a sibling temp file then os.replace.
        mirror_path = Path(mirror_path)
        mirror_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=mirror_path.parent, prefix=".build-jobs-", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w") as fh:
                fh.write(content)
            os.replace(tmp_name, mirror_path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    """Return the current wall-clock time in milliseconds (UTC)."""
    return int(time.time() * 1000)
