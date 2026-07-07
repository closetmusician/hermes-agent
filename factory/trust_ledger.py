# ABOUTME: Trust ledger for the Fable factory (P1b-a, REQ-01). Stores per-(repo ×
# ABOUTME: task_type) job outcomes in the same SQLite DB as the jobs table so the
# ABOUTME: supervisor can write both in one atomic commit (no double-write hazard).
# ABOUTME: Also provides derive_task_type (keyword-map classifier) and the trust-
# ABOUTME: ledger.md human mirror writer (atomic temp-file + os.replace).
"""
Trust ledger — per-(repo × task_type) outcome table, co-located in the jobs DB.

Design authoritative source: docs/plans/harness/fable/p1b/P1b-design.md §2

The supervisor is the SOLE WRITER.  record_outcome must be called from within the
supervisor's terminal-transition path (inside the same connection + lock) so the
ledger row and the jobs-table state UPDATE land in a single atomic commit.

Reader access (read_rows) is open: any module may read the ledger; only the
supervisor writes it.

Outcome vocabulary (§2.1):
  merged_clean    — merged with no human edit / auto-fix; counts toward graduation.
  merged_with_fix — merged but a human edited the diff or a retry fired; breaks streak.
  rejected        — owner rejected; auto-revokes tier (breaks streak).
  reverted        — post-merge regression; auto-revokes tier.  No live producer until
                    P5.4; column + logic are ready now.
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Schema extension — added to the same DB as the jobs table.
# ---------------------------------------------------------------------------

_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS trust_ledger (
  job_id        TEXT PRIMARY KEY,
  repo          TEXT NOT NULL,
  task_type     TEXT NOT NULL,
  outcome       TEXT NOT NULL,
  repo_class    TEXT NOT NULL,
  tier_at_spawn INTEGER,
  confidence    REAL,
  outcome_ts    INTEGER NOT NULL,
  detail        TEXT
);
CREATE INDEX IF NOT EXISTS idx_tl_repo_type
  ON trust_ledger(repo, task_type, outcome_ts);
"""

# Valid outcome values (§2.1).
VALID_OUTCOMES = frozenset({"merged_clean", "merged_with_fix", "rejected", "reverted"})

# Task types that are eligible for graduation (§3.2 GRADUATABLE_TASK_TYPES).
# 'other' is intentionally excluded — misclassification fails safe toward holding.
GRADUATABLE_TASK_TYPES = frozenset({"bugfix", "feature", "refactor", "test", "docs"})


class TrustLedger:
    """
    Purpose: append-only SQLite store for per-(repo × task_type) job outcomes.
    All write operations (record_outcome) MUST be called from the supervisor's
    terminal-transition path to ensure co-transactional atomicity with the jobs
    table UPDATE.  Readers may call read_rows freely.
    Usage: ledger = TrustLedger(path / "jobs.db"); ledger.record_outcome(...).
    Gotchas: mirror_path is optional; write_ledger_mirror is a no-op when absent.
    The record_outcome call acquires the internal lock — callers sharing the same
    connection (supervisor co-transactional path) must use record_outcome_on_conn
    instead to avoid a deadlock on the shared lock.
    """

    def __init__(self, db_path: Path, *, mirror_path: Optional[Path] = None):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._mirror_path = Path(mirror_path) if mirror_path is not None else None
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_LEDGER_SCHEMA)
        self._conn.commit()

    def record_outcome(
        self,
        job_id: str,
        *,
        repo: str,
        task_type: str,
        outcome: str,
        repo_class: str,
        outcome_ts: int,
        tier_at_spawn: Optional[int] = None,
        confidence: Optional[float] = None,
        detail: Optional[str] = None,
    ) -> None:
        """
        Purpose: insert one ledger row for a completed job.  Must be called at the
        exact terminal transition moment so the ledger and jobs-table state share
        a commit boundary.  Primary key is job_id — exactly one row per job.
        Usage: ledger.record_outcome("job-ulid", repo="r", task_type="feature",
                 outcome="merged_clean", repo_class="personal", outcome_ts=ts)
        Gotchas: does NOT validate outcome against VALID_OUTCOMES at the Python
        level (the caller is the supervisor — trusted); SQLite PRIMARY KEY uniqueness
        will raise IntegrityError on a duplicate job_id (each job has one terminal
        outcome).
        """
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO trust_ledger
                  (job_id, repo, task_type, outcome, repo_class,
                   tier_at_spawn, confidence, outcome_ts, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, repo, task_type, outcome, repo_class,
                 tier_at_spawn, confidence, outcome_ts, detail),
            )
            self._conn.commit()

    def record_outcome_on_conn(
        self,
        conn: sqlite3.Connection,
        job_id: str,
        *,
        repo: str,
        task_type: str,
        outcome: str,
        repo_class: str,
        outcome_ts: int,
        tier_at_spawn: Optional[int] = None,
        confidence: Optional[float] = None,
        detail: Optional[str] = None,
    ) -> None:
        """
        Purpose: insert a ledger row using an EXISTING connection (for co-transactional
        writes from the supervisor, which already holds the jobs DB connection).  The
        caller is responsible for commit — this function only executes the INSERT.
        Usage: ledger.record_outcome_on_conn(conn, job_id, ...)  # then conn.commit()
        Gotchas: do NOT call record_outcome (which opens a separate connection) when
        using the supervisor's shared connection — use this variant instead.
        """
        conn.execute(
            """
            INSERT INTO trust_ledger
              (job_id, repo, task_type, outcome, repo_class,
               tier_at_spawn, confidence, outcome_ts, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, repo, task_type, outcome, repo_class,
             tier_at_spawn, confidence, outcome_ts, detail),
        )

    def read_rows(self, repo: str, task_type: str) -> List[Dict[str, Any]]:
        """
        Purpose: return all ledger rows for the given (repo, task_type) pair, ordered
        by outcome_ts ascending (oldest first, as required by the trailing-streak
        calculation in compute_tier).
        Usage: rows = ledger.read_rows("my-repo", "feature")
        Gotchas: returns an empty list (not None) when no rows exist for the key.
        Reads are not write-exclusive — safe to call from any module.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM trust_ledger
                WHERE repo = ? AND task_type = ?
                ORDER BY outcome_ts ASC
                """,
                (repo, task_type),
            ).fetchall()
        return [dict(r) for r in rows]

    def write_ledger_mirror(self) -> None:
        """
        Purpose: regenerate the trust-ledger.md human mirror from the current
        trust_ledger table.  Written atomically (temp file + os.replace) so a
        reader never sees a partial file.  No-op when no mirror_path was supplied.
        Usage: call at the end of each supervisor tick, alongside write_build_jobs_mirror.
        Gotchas: mirror is read-only from outside the supervisor; the ledger is the
        single source of truth — the mirror is never read back as input.
        """
        if self._mirror_path is None:
            return

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM trust_ledger ORDER BY repo, task_type, outcome_ts"
            ).fetchall()

        rows_list = [dict(r) for r in rows]

        lines = [
            "# trust-ledger.md — factory trust ledger mirror",
            f"<!-- generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} -->",
            "",
            "| job_id (short) | repo | task_type | outcome | repo_class | outcome_ts |",
            "|---|---|---|---|---|---|",
        ]
        for row in rows_list:
            short_id = row["job_id"][:10]
            lines.append(
                f"| {short_id} | {row['repo']} | {row['task_type']} "
                f"| {row['outcome']} | {row['repo_class']} | {row['outcome_ts']} |"
            )

        content = "\n".join(lines) + "\n"

        mirror_path = self._mirror_path
        mirror_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=mirror_path.parent, prefix=".trust-ledger-", suffix=".tmp"
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
# Task-type derivation (pure function, no I/O, §2.2)
# ---------------------------------------------------------------------------

# Keyword map: pattern → task_type.  First match wins; checked in order.
_KEYWORD_MAP = [
    (re.compile(r"\b(?:fix|bug|bugfix|regression|repair)\b", re.IGNORECASE), "bugfix"),
    (re.compile(r"\b(?:refactor|rename|extract|cleanup|clean.up)\b", re.IGNORECASE), "refactor"),
    (re.compile(r"\b(?:test|tests|testing|coverage|spec|specs)\b", re.IGNORECASE), "test"),
    (re.compile(r"\b(?:docs?|readme|comment|comments|documentation)\b", re.IGNORECASE), "docs"),
]


def derive_task_type(job_row: Dict[str, Any]) -> str:
    """
    Purpose: classify a job into a task-type string used as the trust-ledger key.
    Runs in the SUPERVISOR at job-creation time over the INTAKE SPEC (trusted) —
    the classification is frozen at that point (never re-derived from worker output).
    Usage: task_type = derive_task_type(job_spec_dict)  # → 'bugfix'|'feature'|…
    Gotchas: 'other' is the fail-safe default for empty/unrecognizable specs; 'other'
    is not in GRADUATABLE_TASK_TYPES, so a misclassification fails safe toward holding.
    explicit task_type label in the dict takes priority over keyword scan.
    kind='quick' (with no keyword match) biases toward 'bugfix' over 'feature'.
    """
    # 1. Explicit label in the job dict wins (P3 task-splitter emits one per node).
    explicit = job_row.get("task_type")
    if explicit and isinstance(explicit, str):
        return explicit.strip().lower() or "other"

    spec = (job_row.get("spec") or "").strip()
    if not spec:
        return "other"

    # 2. Keyword scan over spec text (fixed map, no AI).
    for pattern, label in _KEYWORD_MAP:
        if pattern.search(spec):
            return label

    # 3. kind='quick' biases toward bugfix (small patches).
    if job_row.get("kind") == "quick":
        return "bugfix"

    # 4. Default.
    return "feature"
