# ABOUTME: Per-(model × task_type) scorecard for the Fable factory (P5-a, REQ-01).
# ABOUTME: Records SUPERVISOR-observed samples from completed jobs — outcome, cost,
# ABOUTME: wall_time, and reviewer-parsed findings_count. Workers CANNOT influence
# ABOUTME: their own samples (anti-poisoning, v2-C4). Co-located in the jobs SQLite
# ABOUTME: DB (same pattern as trust_ledger.py) for co-transactional writes.
"""
Per-(model × task_type) scorecard: raw samples + leaderboard rollup.

Design source: docs/plans/harness/fable/p5/P5-design.md §REQ-01 (scorecard.py),
§3.2 (schema + anti-poisoning), v2-C4 (supervisor-observed fields only).

Anti-poisoning invariant (v2-C4, §3.2):
  EVERY column in scorecard_samples derives from a supervisor-observed fact:
    model/worker/cost_usd/wall_ms  — from job_store columns the supervisor writes
    task_type                      — from trust_ledger.derive_task_type (keyword map)
    outcome                        — from the gauntlet/merge result
    findings_count                 — parsed by the supervisor from the REVIEWER's
                                     artifact (review_findings_path), never a
                                     worker-supplied count.
  record_sample() enforces this: it accepts a job_row + an explicit findings_count
  (which the SUPERVISOR parses), and IGNORES any findings or outcome fields the
  worker may have emitted.  RED test SC-6 asserts a worker-emitted value cannot
  alter the recorded sample.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from factory.trust_ledger import VALID_OUTCOMES, derive_task_type

# ---------------------------------------------------------------------------
# Schema — co-located in the jobs DB (same pattern as trust_ledger._LEDGER_SCHEMA).
# ---------------------------------------------------------------------------

_SCORECARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS scorecard_samples (
  job_id        TEXT PRIMARY KEY,
  model         TEXT NOT NULL,
  worker        TEXT NOT NULL,
  task_type     TEXT NOT NULL,
  outcome       TEXT NOT NULL,
  findings_count INTEGER NOT NULL DEFAULT 0,
  cost_usd      REAL NOT NULL,
  wall_ms       INTEGER NOT NULL,
  sample_ts     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sc_model_type
  ON scorecard_samples(model, task_type, sample_ts);
"""

# Valid outcome vocabulary — mirrors trust_ledger.VALID_OUTCOMES extended with
# 'failed' for jobs that never reached a merge decision.
SCORECARD_OUTCOMES = frozenset(VALID_OUTCOMES | {"failed"})


@dataclass
class ScoreRow:
    """
    A leaderboard rollup row for one (model, task_type) pair.

    Purpose: the summary unit returned by rollup() and leaderboard(); aggregates
    over all recorded samples for that cell.
    Usage: rows = scorecard.leaderboard("bugfix"); rows[0].model is the best choice.
    Gotchas: n=0 means the cell has no samples; callers should check before using
    rate/mean fields, which are 0.0 when n=0.
    """

    model: str
    worker: str
    task_type: str
    n: int
    merged_clean_rate: float      # fraction with outcome == 'merged_clean'
    mean_findings: float          # mean findings_count across samples
    mean_cost_usd: float          # mean cost_usd across samples
    mean_wall_ms: float           # mean wall_ms across samples


class Scorecard:
    """
    Purpose: per-(model × task_type) sample store + leaderboard rollup, co-located
    in the jobs SQLite DB so a terminal job transition can write both tables in one
    atomic commit (no double-write hazard, matching trust_ledger.py:37).
    Usage:
        sc = Scorecard(db_path)
        sc.record_sample(conn, job_row, findings_count=n)   # co-transactional
        rows = sc.leaderboard("bugfix")
    Gotchas: record_sample_on_conn uses a CALLER-SUPPLIED connection and does NOT
    commit — call conn.commit() after to share the transaction with the jobs UPDATE.
    record_sample opens its own connection+lock for standalone writes.
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCORECARD_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write path — SUPERVISOR is the SOLE caller
    # ------------------------------------------------------------------

    def record_sample_on_conn(
        self,
        conn: sqlite3.Connection,
        job_row: Dict[str, Any],
        *,
        findings_count: int,
    ) -> None:
        """
        Insert one scorecard sample using an EXISTING connection (co-transactional).

        Purpose: co-transactional write mirroring trust_ledger.record_outcome_on_conn.
        Call from inside the supervisor's terminal-transition path so the scorecard
        row and the jobs-table state UPDATE share one atomic commit.
        Usage: sc.record_sample_on_conn(conn, job_row, findings_count=n)
               # then conn.commit()
        Gotchas: EVERY field is derived from supervisor-observed facts (anti-poisoning,
        v2-C4): model/worker/cost_usd/wall_ms from job_row (supervisor-written columns),
        task_type from derive_task_type (keyword map, not worker free-text), outcome from
        the merge result, findings_count from the REVIEWER artifact parsed by the caller.
        Any worker-emitted findings or outcome fields in job_row are IGNORED.
        """
        conn.execute(
            """
            INSERT OR REPLACE INTO scorecard_samples
              (job_id, model, worker, task_type, outcome, findings_count,
               cost_usd, wall_ms, sample_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_row["id"],
                job_row["model"],                          # supervisor-written
                job_row["worker"],                         # supervisor-written
                derive_task_type(job_row),                 # keyword map, not worker text
                _resolve_outcome(job_row),                 # from merge result / state
                int(findings_count),                       # reviewer artifact, caller-parsed
                float(job_row.get("cost_so_far_usd") or 0.0),  # supervisor-written
                _wall_ms(job_row),                         # supervisor timestamps
                int(time.time() * 1000),
            ),
        )

    def record_sample(
        self,
        job_row: Dict[str, Any],
        *,
        findings_count: int,
    ) -> None:
        """
        Insert one scorecard sample using the internal connection (standalone write).

        Purpose: convenience wrapper for when the caller does not share the supervisor's
        connection — opens the internal lock, executes, and commits atomically.
        Usage: scorecard.record_sample(job_row, findings_count=3)
        Gotchas: for co-transactional writes (supervisor terminal-transition) use
        record_sample_on_conn instead to share the commit boundary.
        """
        with self._lock:
            self.record_sample_on_conn(self._conn, job_row, findings_count=findings_count)
            self._conn.commit()

    # ------------------------------------------------------------------
    # Read path — open to all callers
    # ------------------------------------------------------------------

    def rollup(
        self,
        *,
        model: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> List[ScoreRow]:
        """
        Aggregate samples into per-(model × task_type) ScoreRows.

        Purpose: the leaderboard view — GROUP BY model,task_type with outcome+cost
        stats. Optional model/task_type filters narrow to a single row or column.
        Usage: all_rows = sc.rollup(); bug_rows = sc.rollup(task_type="bugfix")
        Gotchas: returns an empty list when the DB has no matching samples — callers
        must handle the empty case (cold start).
        """
        wheres: List[str] = []
        params: List[Any] = []
        if model is not None:
            wheres.append("model = ?")
            params.append(model)
        if task_type is not None:
            wheres.append("task_type = ?")
            params.append(task_type)

        where_clause = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = f"""
            SELECT
              model, worker, task_type,
              COUNT(*) AS n,
              AVG(CASE WHEN outcome = 'merged_clean' THEN 1.0 ELSE 0.0 END) AS merged_clean_rate,
              AVG(CAST(findings_count AS REAL)) AS mean_findings,
              AVG(cost_usd) AS mean_cost_usd,
              AVG(CAST(wall_ms AS REAL)) AS mean_wall_ms
            FROM scorecard_samples
            {where_clause}
            GROUP BY model, task_type
            ORDER BY merged_clean_rate DESC, mean_cost_usd ASC
        """
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            ScoreRow(
                model=r["model"],
                worker=r["worker"],
                task_type=r["task_type"],
                n=r["n"],
                merged_clean_rate=float(r["merged_clean_rate"] or 0.0),
                mean_findings=float(r["mean_findings"] or 0.0),
                mean_cost_usd=float(r["mean_cost_usd"] or 0.0),
                mean_wall_ms=float(r["mean_wall_ms"] or 0.0),
            )
            for r in rows
        ]

    def leaderboard(self, task_type: str) -> List[ScoreRow]:
        """
        Return per-model ScoreRows for a task_type, best-value first.

        Purpose: the input to routing_view — best (highest merged_clean_rate,
        tie-broken by lowest mean_cost_usd) first, so the routing view can rank
        models for a given task type without re-sorting.
        Usage: best = scorecard.leaderboard("bugfix")[0].model
        Gotchas: returns an empty list when no samples exist for that task_type —
        routing_view must fall back to the hand-written default ladder in that case.
        """
        return self.rollup(task_type=task_type)


# ---------------------------------------------------------------------------
# Internal helpers — supervisor-observed derivations
# ---------------------------------------------------------------------------


def _resolve_outcome(job_row: Dict[str, Any]) -> str:
    """
    Derive outcome from supervisor-controlled job_row fields (anti-poisoning).

    Purpose: map the job's state/fail_reason to a SCORECARD_OUTCOMES member using
    only fields the supervisor writes — never from worker-emitted text.
    Usage: outcome = _resolve_outcome(job_row)
    Gotchas: the 'outcome' key in job_row (if present) is treated as a hint only if
    it is in SCORECARD_OUTCOMES; otherwise the state-based derivation is used.
    A worker cannot inject an arbitrary outcome because derive_task_type and this
    function both key on supervisor-written columns, not free-text worker output.
    """
    # The supervisor may set an explicit 'outcome' column on the row.
    explicit = job_row.get("outcome")
    if explicit and explicit in SCORECARD_OUTCOMES:
        return explicit

    state = job_row.get("state", "")
    if state == "DONE":
        return "merged_clean"
    if state == "NEEDS_ATTENTION":
        fail = (job_row.get("fail_reason") or "").lower()
        if "reject" in fail:
            return "rejected"
        if "revert" in fail:
            return "reverted"
        if "fix" in fail or "manual" in fail:
            return "merged_with_fix"
        return "failed"
    return "failed"


def _wall_ms(job_row: Dict[str, Any]) -> int:
    """
    Compute wall-clock milliseconds from supervisor-written timestamps.

    Purpose: derive wall_ms purely from supervisor-written created_ts / updated_ts
    in the job_row — never from worker-reported timing.
    Usage: ms = _wall_ms(job_row)
    Gotchas: both timestamps are in milliseconds (same epoch as job_store._now_ms);
    returns 0 if either is absent or if updated < created (data anomaly).
    """
    created = int(job_row.get("created_ts") or 0)
    updated = int(job_row.get("updated_ts") or 0)
    diff = updated - created
    return max(diff, 0)


def parse_findings_count(findings_path: Optional[str]) -> int:
    """
    Parse the reviewer-artifact findings_count from a review_findings_path.

    Purpose: the SUPERVISOR (not the worker) calls this to extract findings_count
    from the codex review artifact and pass it to record_sample — so the count
    comes from the REVIEWER's output, not from the authoring worker.
    Usage: n = parse_findings_count(job_row.get("review_findings_path"))
    Gotchas: returns 0 if the path is absent, unreadable, or not JSON; never raises
    so it can be called safely on the terminal-transition crash path.
    """
    if not findings_path:
        return 0
    try:
        text = Path(findings_path).read_text(encoding="utf-8")
    except OSError:
        return 0
    # Try JSON {"findings": [...]} or {"count": N}.
    try:
        data = json.loads(text)
        if "count" in data:
            return int(data["count"])
        if "findings" in data and isinstance(data["findings"], list):
            return len(data["findings"])
    except (json.JSONDecodeError, (TypeError, ValueError, KeyError)):
        pass
    # Fallback: count lines starting with common finding prefixes.
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and (
            stripped.startswith("- ")
            or stripped.startswith("* ")
            or re.match(r"^\d+\.", stripped)
        ):
            count += 1
    return count
