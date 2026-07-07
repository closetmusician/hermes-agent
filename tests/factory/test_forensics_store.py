# ABOUTME: RED-first tests for factory/forensics_store.py — P5-e REQ-01..04.
# ABOUTME: Verifies: structured failure_forensics table, classify() accuracy,
# ABOUTME: retry_worthy judgment, scorecard/retro query feeds, and the
# ABOUTME: supervisor-observed-only property (no worker self-report).
"""
Tests for factory.forensics_store (P5-e, design §6 + REQ-01..04 / FF-1..4).

All tests use real SQLite in pytest tmp dirs — no mocks, no fixture DB.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

import factory.forensics_store as ffs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db(tmp_path: Path) -> sqlite3.Connection:
    """Open a real SQLite DB in tmp_path and apply the forensics_store schema."""
    conn = sqlite3.connect(str(tmp_path / "jobs.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    ffs.apply_schema(conn)
    conn.commit()
    return conn


def _job_row(
    job_id: str = "j-001",
    state: str = "FAILED",
    fail_reason: Optional[str] = None,
    cost_so_far_usd: float = 0.42,
    test_result: Optional[str] = None,
) -> Dict[str, Any]:
    """Minimal job row dict mirroring job_store schema."""
    return {
        "id": job_id,
        "state": state,
        "fail_reason": fail_reason,
        "cost_so_far_usd": cost_so_far_usd,
        "test_result": test_result,
        "model": "claude-opus-4-5",
        "worker": "claude",
        "repo": "acme",
        "kind": "feature",
    }


def _bundle(tmp_path: Path, job_id: str = "j-001") -> Optional[Path]:
    """Create a minimal forensics bundle dir and return its path."""
    d = tmp_path / "bundles" / job_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text('{"kill_reason": "timeout"}')
    return d


# ---------------------------------------------------------------------------
# FF-1: a failed job produces a failure_forensics row with all fields
# (stage, error_class, cost_burned, retry_worthy, bundle_path)
# ---------------------------------------------------------------------------


def test_ff1_record_failure_all_fields_present(tmp_path):
    """FF-1: record_failure writes a row with all schema fields populated."""
    conn = _db(tmp_path)
    job_row = _job_row(fail_reason="timeout: wall-clock exceeded 30min")
    bundle_path = _bundle(tmp_path)

    rec = ffs.classify(job_row, bundle_path=bundle_path)
    ffs.record_failure(conn, rec)
    conn.commit()

    rows = conn.execute("SELECT * FROM failure_forensics").fetchall()
    assert len(rows) == 1
    r = dict(rows[0])
    assert r["job_id"] == "j-001"
    assert r["stage"] == "FAILED"
    assert r["error_class"] is not None and r["error_class"] != ""
    assert isinstance(r["cost_burned_usd"], float)
    assert r["cost_burned_usd"] == pytest.approx(0.42)
    assert r["retry_worthy"] in (0, 1)
    assert r["bundle_path"] is not None
    assert r["captured_ts"] > 0


# ---------------------------------------------------------------------------
# FF-2: classify maps specific failure types to correct retry_worthy values
# (cost_stop → not worthy; flaky test → worthy; ring_violation → not worthy)
# ---------------------------------------------------------------------------


def test_ff2_cost_stop_not_retry_worthy(tmp_path):
    """FF-2a: a cost_stop failure → retry_worthy=0."""
    job_row = _job_row(fail_reason="cost_stop: daily budget exceeded")
    rec = ffs.classify(job_row)
    assert rec.error_class == "cost_stop"
    assert rec.retry_worthy == 0


def test_ff2_flaky_test_is_retry_worthy(tmp_path):
    """FF-2b: a flaky (flip-flopping) test failure → retry_worthy=1."""
    job_row = _job_row(fail_reason="test_fail: flaky", test_result="flaky")
    rec = ffs.classify(job_row)
    assert rec.error_class == "test_fail"
    assert rec.retry_worthy == 1


def test_ff2_ring_violation_not_retry_worthy_and_escalates(tmp_path):
    """FF-2c: a ring_violation → retry_worthy=0; escalate flag set."""
    job_row = _job_row(fail_reason="ring_violation: tried to edit broker/")
    rec = ffs.classify(job_row)
    assert rec.error_class == "ring_violation"
    assert rec.retry_worthy == 0
    assert rec.escalate is True


def test_ff2_timeout_is_retry_worthy(tmp_path):
    """FF-2d: a plain timeout → retry_worthy=1 (transient, retry-worthy)."""
    job_row = _job_row(fail_reason="timeout: wall-clock exceeded")
    rec = ffs.classify(job_row)
    assert rec.error_class == "timeout"
    assert rec.retry_worthy == 1


def test_ff2_injection_not_retry_worthy(tmp_path):
    """FF-2e: an injection failure → retry_worthy=0 (security signal)."""
    job_row = _job_row(fail_reason="injection: prompt injection detected")
    rec = ffs.classify(job_row)
    assert rec.error_class == "injection"
    assert rec.retry_worthy == 0


def test_ff2_review_reject_not_retry_worthy(tmp_path):
    """FF-2f: a review rejection → retry_worthy=0 (quality gate, not transient)."""
    job_row = _job_row(fail_reason="review_reject: findings exceeded threshold")
    rec = ffs.classify(job_row)
    assert rec.error_class == "review_reject"
    assert rec.retry_worthy == 0


# ---------------------------------------------------------------------------
# FF-3: the scorecard query returns failed records grouped by model×task_type
# (the feed from forensics → scorecard for failed samples)
# ---------------------------------------------------------------------------


def test_ff3_scorecard_feed_returns_failed_records(tmp_path):
    """FF-3: query_failed_for_scorecard returns rows the scorecard can consume."""
    conn = _db(tmp_path)

    # Record two different failure types for two jobs
    for job_id, fail_reason, cost in [
        ("j-100", "timeout: wall-clock exceeded", 0.10),
        ("j-101", "test_fail: assertion error", 0.20),
    ]:
        row = _job_row(job_id=job_id, fail_reason=fail_reason, cost_so_far_usd=cost)
        rec = ffs.classify(row)
        ffs.record_failure(conn, rec)
    conn.commit()

    results = ffs.query_failed_for_scorecard(conn)
    assert len(results) == 2
    job_ids = {r["job_id"] for r in results}
    assert "j-100" in job_ids
    assert "j-101" in job_ids
    # Each row has error_class
    for r in results:
        assert "error_class" in r
        assert r["error_class"] is not None


def test_ff3_scorecard_feed_empty_on_no_failures(tmp_path):
    """FF-3: empty DB → empty scorecard feed (no crash)."""
    conn = _db(tmp_path)
    results = ffs.query_failed_for_scorecard(conn)
    assert results == []


# ---------------------------------------------------------------------------
# FF-4: the retro GATHER query groups failures by error_class
# (feeds the retro's recurring failure class detection)
# ---------------------------------------------------------------------------


def test_ff4_retro_gather_groups_by_error_class(tmp_path):
    """FF-4: query_recurring_failures returns counts grouped by error_class."""
    conn = _db(tmp_path)

    # Insert 3 timeouts and 1 test_fail
    for i in range(3):
        row = _job_row(job_id=f"j-t{i}", fail_reason="timeout: exceeded 30min")
        ffs.record_failure(conn, ffs.classify(row))
    row = _job_row(job_id="j-tf", fail_reason="test_fail: assertion error")
    ffs.record_failure(conn, ffs.classify(row))
    conn.commit()

    classes = ffs.query_recurring_failures(conn)
    # Returns list of (error_class, count) sorted by count desc
    class_map = {c: n for c, n in classes}
    assert class_map.get("timeout", 0) == 3
    assert class_map.get("test_fail", 0) == 1
    # timeout (3) should rank above test_fail (1)
    assert classes[0][0] == "timeout"


def test_ff4_retro_gather_within_window(tmp_path):
    """FF-4: query_recurring_failures respects a since_ts cutoff."""
    conn = _db(tmp_path)

    old_ts = int(time.time()) - 10_000
    new_ts = int(time.time())

    # Insert an old record directly (bypassing classify to control captured_ts)
    conn.execute(
        """INSERT INTO failure_forensics
           (job_id, stage, error_class, cost_burned_usd, retry_worthy, bundle_path,
            detail, captured_ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("j-old", "FAILED", "timeout", 0.05, 1, None, None, old_ts),
    )
    conn.execute(
        """INSERT INTO failure_forensics
           (job_id, stage, error_class, cost_burned_usd, retry_worthy, bundle_path,
            detail, captured_ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("j-new", "FAILED", "timeout", 0.05, 1, None, None, new_ts),
    )
    conn.commit()

    # Query with a cutoff that excludes the old record
    cutoff = old_ts + 5_000
    classes = ffs.query_recurring_failures(conn, since_ts=cutoff)
    class_map = {c: n for c, n in classes}
    assert class_map.get("timeout", 0) == 1  # only the new one


# ---------------------------------------------------------------------------
# REQ-04: supervisor-observed only — forensics come from trusted supervisor
# observation, not worker self-report
# ---------------------------------------------------------------------------


def test_req04_supervisor_observed_not_worker_self_report(tmp_path):
    """REQ-04: classify() derives error_class from the job_row fields the
    SUPERVISOR writes (fail_reason, test_result, state), NOT from a worker-
    supplied annotation field.  A worker cannot set a free-text 'error_class'
    field and have it reflected in the forensic record."""
    conn = _db(tmp_path)

    # Simulate a job row that a worker might try to manipulate by injecting
    # an error_class through a free-text field that workers can write.
    # The supervisor's fail_reason says 'ring_violation'; if a worker-supplied
    # field were trusted, it could set a benign class to avoid escalation.
    job_row = _job_row(
        job_id="j-manipulated",
        fail_reason="ring_violation: tried to edit broker/server.py",
    )
    # Add a synthetic 'worker_class' field — classify() must ignore it.
    job_row["worker_class"] = "test_fail"  # worker tries to downgrade severity

    rec = ffs.classify(job_row)
    # Must classify from fail_reason (ring_violation), not from worker_class
    assert rec.error_class == "ring_violation"
    assert rec.retry_worthy == 0
    assert rec.escalate is True


# ---------------------------------------------------------------------------
# REQ-03: additive — existing forensics.capture_pre_kill / capture_post_mortem
# still work unchanged (no regression)
# ---------------------------------------------------------------------------


def test_req03_existing_forensics_not_broken(tmp_path):
    """REQ-03: the raw bundle capture still works after forensics_store is imported."""
    from factory import forensics

    wt = tmp_path / "wt"
    (wt / ".factory").mkdir(parents=True)
    (wt / ".factory" / "worker.out").write_text("crash output\n")

    bundle = forensics.capture_pre_kill(
        job_id="j-legacy",
        worktree=wt,
        out_dir=tmp_path / "fb",
        meta={"model": "claude", "phase": "TEST", "kill_reason": "stall"},
        failing_test="test_foo",
    )
    assert bundle.exists()
    assert (bundle / "meta.json").exists()
    assert (bundle / "failing-test.txt").read_text().strip() == "test_foo"


# ---------------------------------------------------------------------------
# Schema idempotency — apply_schema twice is a no-op (CREATE IF NOT EXISTS)
# ---------------------------------------------------------------------------


def test_schema_idempotent(tmp_path):
    """apply_schema can be called on a DB that already has the table."""
    conn = _db(tmp_path)
    # Second apply should not raise
    ffs.apply_schema(conn)
    conn.commit()
    # Table still has the right columns
    cols = {r[1] for r in conn.execute("PRAGMA table_info(failure_forensics)")}
    expected = {
        "job_id", "stage", "error_class", "cost_burned_usd",
        "retry_worthy", "bundle_path", "detail", "captured_ts",
    }
    assert expected.issubset(cols)
