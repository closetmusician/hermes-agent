# ABOUTME: Structured failure forensics store (P5-e, design §6 + REQ-04).
# ABOUTME: Additive layer on top of forensics.py's raw bundle capture — writes
# ABOUTME: a SQLite failure_forensics table in the jobs DB with per-job structured
# ABOUTME: records (stage, error_class, cost_burned, retry_worthy, bundle_path).
# ABOUTME: Feeds the scorecard (failed-sample query) and the retro GATHER step.
"""
Structured failure forensics — a SQLite store in the jobs DB that classifies
every failed job into a ForensicRecord and exposes two query seams:

  * query_failed_for_scorecard(conn) — the list of failed samples the scorecard
    reads (one row per job, carrying error_class for the failed-outcome column).

  * query_recurring_failures(conn, since_ts) — grouped (error_class, count) the
    retro GATHER step reads to find the recurring failure class it must address.

Design authoritative source: docs/plans/harness/fable/p5/P5-design.md §6 (REQ-04)

The SUPERVISOR is the sole writer.  record_failure() is meant to be called inside
the same terminal-transition path that writes the job's FAILED/NEEDS_ATTENTION
row — exactly like trust_ledger.record_outcome_on_conn (trust_ledger.py:126).

No AI in the loop: classify() is pure, deterministic, plain code.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS failure_forensics (
  job_id          TEXT PRIMARY KEY,
  stage           TEXT NOT NULL,      -- QUEUED|ADMITTED|RUNNING|TEST|REVIEW|MERGING (where it died)
  error_class     TEXT NOT NULL,      -- timeout|cost_stop|test_fail|review_reject|crash|ring_violation|drift|injection
  cost_burned_usd REAL NOT NULL,      -- jobs.cost_so_far_usd at failure (supervisor-observed)
  retry_worthy    INTEGER NOT NULL,   -- 0/1: transient → worthy; injection/ring/cost_stop → not
  bundle_path     TEXT,               -- pointer to the forensics.capture_* bundle dir
  detail          TEXT,               -- optional free-text excerpt for human review
  captured_ts     INTEGER NOT NULL    -- unix epoch seconds
);
CREATE INDEX IF NOT EXISTS idx_ff_class ON failure_forensics(error_class, captured_ts);
"""

# ---------------------------------------------------------------------------
# Error-class classification rules.
#
# error_class is a structured, closed vocabulary derived ONLY from supervisor-
# observed fields (fail_reason, test_result, state).  A worker cannot inject its
# own class — classify() ignores any field a worker might have written.
#
# retry_worthy judgment:
#   Transient (worthy): timeout, crash (OOM / dead pgid), test_fail/flaky (flip-flop)
#   Deterministic or security (not worthy): cost_stop, ring_violation, injection,
#     review_reject, drift (repeated pattern — needs investigation not retry)
# ---------------------------------------------------------------------------

# (pattern_substring_in_fail_reason, error_class, retry_worthy, escalate)
# Checked in order; first match wins.
_RULES: List[Tuple[str, str, int, bool]] = [
    ("ring_violation", "ring_violation", 0, True),   # security — escalate, never retry
    ("injection",      "injection",      0, True),   # security — escalate, never retry
    ("cost_stop",      "cost_stop",      0, False),  # deterministic limit — no point retrying
    ("review_reject",  "review_reject",  0, False),  # quality gate failure — fix needed
    ("drift",          "drift",          0, False),  # repeated churn pattern — investigate
    ("timeout",        "timeout",        1, False),  # transient — retry-worthy
    ("crash",          "crash",          1, False),  # transient OOM/dead-pgid — retry
    ("test_fail",      "test_fail",      0, False),  # default: deterministic failure
]

# Flaky test result overrides test_fail → retry_worthy=1 (flip-flop is transient).
_FLAKY_RESULT = "flaky"


@dataclass
class ForensicRecord:
    """
    Purpose: structured post-mortem for one failed job.  All fields derive from
    supervisor-observed data (fail_reason, test_result, state from the jobs table).
    Usage: rec = classify(job_row, bundle_path); record_failure(conn, rec).
    Gotchas: escalate is a Python-side flag (not stored in the DB column);
    the supervisor/morning-packet reads it to surface ring/injection events.
    """

    job_id: str
    stage: str
    error_class: str
    cost_burned_usd: float
    retry_worthy: int       # 0 or 1
    bundle_path: Optional[str]
    detail: Optional[str]
    captured_ts: int
    escalate: bool = field(default=False, compare=False)  # runtime flag, not persisted


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_schema(conn) -> None:
    """
    Purpose: create the failure_forensics table and index in the given SQLite
    connection (CREATE IF NOT EXISTS — idempotent).  Call once from __init__
    of any class that owns the jobs DB, just as TrustLedger does.
    Usage: apply_schema(conn); conn.commit()
    Gotchas: does NOT commit — caller must commit after, to batch with any other
    schema extensions in the same DB initialisation pass.
    """
    conn.executescript(_SCHEMA)


def classify(
    job_row: Dict[str, Any],
    *,
    bundle_path: Optional[Path] = None,
) -> ForensicRecord:
    """
    Purpose: derive a ForensicRecord from the supervisor-observed job_row.
    Pure function — no I/O, no AI.  error_class is classified from
    job_row['fail_reason'] (written by the supervisor) and
    job_row['test_result'] (also supervisor-written).  Any worker-supplied
    field is explicitly ignored — forensics come from trusted supervisor
    observation, never worker self-report.
    Usage: rec = classify(job_row, bundle_path=bundle)
    Gotchas: a missing/None fail_reason falls through to error_class='crash'
    (unknown terminal — conservative, supervisor-safe).
    """
    job_id: str = job_row["id"]
    stage: str = job_row.get("state", "FAILED")
    cost: float = float(job_row.get("cost_so_far_usd", 0.0))

    # Classify from fail_reason only — supervisor writes this field.
    fail_reason: str = (job_row.get("fail_reason") or "").lower()
    test_result: str = (job_row.get("test_result") or "").lower()

    error_class = "crash"  # default: unknown terminal
    retry_worthy = 1       # default: transient (crash/unknown → worth retrying)
    escalate = False

    for pattern, cls, worthy, esc in _RULES:
        if pattern in fail_reason:
            error_class = cls
            retry_worthy = worthy
            escalate = esc
            break

    # Flaky override: test_fail that flip-flopped is transient → retry-worthy.
    if error_class == "test_fail" and test_result == _FLAKY_RESULT:
        retry_worthy = 1

    bundle_str: Optional[str] = str(bundle_path) if bundle_path is not None else None
    detail: Optional[str] = fail_reason if fail_reason else None

    return ForensicRecord(
        job_id=job_id,
        stage=stage,
        error_class=error_class,
        cost_burned_usd=cost,
        retry_worthy=retry_worthy,
        bundle_path=bundle_str,
        detail=detail,
        captured_ts=int(time.time()),
        escalate=escalate,
    )


def record_failure(conn, rec: ForensicRecord) -> None:
    """
    Purpose: write (or replace) a ForensicRecord into failure_forensics.  Meant
    to be called inside the supervisor's terminal-transition path so the forensic
    row and the jobs-state UPDATE land in the same atomic commit.
    Usage: record_failure(conn, classify(job_row, bundle_path=bundle)); conn.commit()
    Gotchas: uses INSERT OR REPLACE so idempotent retries (e.g. crash-resume re-
    records the same job) do not raise UNIQUE violation.
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO failure_forensics
          (job_id, stage, error_class, cost_burned_usd, retry_worthy,
           bundle_path, detail, captured_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rec.job_id,
            rec.stage,
            rec.error_class,
            rec.cost_burned_usd,
            rec.retry_worthy,
            rec.bundle_path,
            rec.detail,
            rec.captured_ts,
        ),
    )


def query_failed_for_scorecard(conn) -> List[Dict[str, Any]]:
    """
    Purpose: return all failure_forensics rows as plain dicts — the list of
    failed samples the scorecard reads to record 'failed' outcomes with their
    error_class.  Returns every record; the scorecard applies its own window/
    model filters on top.
    Usage: samples = query_failed_for_scorecard(conn)
    Gotchas: returns an empty list on an empty table (no crash).
    """
    rows = conn.execute(
        "SELECT * FROM failure_forensics ORDER BY captured_ts"
    ).fetchall()
    return [dict(r) for r in rows]


def query_recurring_failures(
    conn,
    *,
    since_ts: Optional[int] = None,
) -> List[Tuple[str, int]]:
    """
    Purpose: GROUP BY error_class → count for the retro GATHER step.  Returns
    a list of (error_class, count) tuples sorted by count descending so the
    retro can immediately surface the most frequent failure class to address.
    Usage: classes = query_recurring_failures(conn, since_ts=week_ago_ts)
    Gotchas: since_ts is optional (None = all time).  Returns [] on empty table.
    """
    if since_ts is not None:
        rows = conn.execute(
            """
            SELECT error_class, COUNT(*) AS n
            FROM failure_forensics
            WHERE captured_ts >= ?
            GROUP BY error_class
            ORDER BY n DESC
            """,
            (since_ts,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT error_class, COUNT(*) AS n
            FROM failure_forensics
            GROUP BY error_class
            ORDER BY n DESC
            """
        ).fetchall()
    return [(r[0], r[1]) for r in rows]
