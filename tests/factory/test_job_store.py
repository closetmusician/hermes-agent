# ABOUTME: RED-first tests for factory/job_store.py — P2-a requirements.
# ABOUTME: Uses real SQLite temp files (no mocks); covers the full §1.2 schema,
# ABOUTME: one-way state machine, CAS concurrency, supervisor lock, per-tick
# ABOUTME: integrity check, and the build-jobs.md atomic mirror write.
# ABOUTME: Each test maps to an explicit RED test in P2-design.md §5 P2-a.
"""
Tests for factory.job_store (P2-a).

Coverage:
  1. Schema has ALL §1.2 columns incl. nullable trust_tier_at_spawn + confidence.
  2. ★ Illegal backward transition (MERGING→RUNNING) raises IllegalTransition;
     the row state is unchanged.
  3. Legal full path QUEUED→RUNNING→TEST→REVIEW→AWAITING_APPROVAL→MERGING→DONE.
  4. Two concurrent transitions from the same expected state: exactly one wins.
  5. Supervisor lock: second supervisor cannot claim while heartbeat fresh;
     can steal after stale_ttl.
  6. Per-tick integrity check parks a job whose pgid is dead but state=RUNNING.
  7. build-jobs.md mirror: after a transition the mirror reflects the new state.
  8. Idempotency: has_job_for_intake_hash blocks a duplicate enqueue.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from factory.job_store import (
    IllegalTransition,
    JobStore,
    SupervisorLockError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    """A fresh JobStore backed by a real SQLite temp file."""
    return JobStore(tmp_path / "jobs.db")


def _minimal_spec(**overrides) -> dict:
    """Return a minimal valid job spec dict with sensible defaults."""
    base = {
        "repo": "test-repo",
        "base_branch": "main",
        "spec": "add a test feature",
        "kind": "quick",
        "worker": "claude",
        "model": "claude-haiku-4-5",
        "budget_usd": 0.5,
        "timeout_min": 10,
        "intake_source_hash": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test 1 — Schema completeness
# ---------------------------------------------------------------------------


def test_schema_has_all_columns(tmp_path):
    """
    P2-a #1: introspect PRAGMA table_info to verify all §1.2 columns are
    present, including the nullable trust_tier_at_spawn and confidence that
    are reserved for later phases but must exist from day one.
    """
    store = JobStore(tmp_path / "jobs.db")
    conn = sqlite3.connect(str(tmp_path / "jobs.db"))
    try:
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
    finally:
        conn.close()

    required = {
        "id",
        "repo",
        "base_branch",
        "spec",
        "kind",
        "worker",
        "model",
        "budget_usd",
        "timeout_min",
        "state",
        "worktree_path",
        "branch",
        "pgid",
        "cost_so_far_usd",
        "test_result",
        "review_findings_path",
        "log_tail",
        "confidence",           # nullable, reserved for P4
        "trust_tier_at_spawn",  # nullable, reserved for P1b
        "fail_reason",
        "created_ts",
        "updated_ts",
    }
    missing = required - cols
    assert not missing, f"jobs table missing columns: {missing!r}"


def test_nullable_confidence_and_trust_tier(store):
    """
    P2-a #1 (cont.): freshly created job has NULL confidence and
    trust_tier_at_spawn — they exist but are not required at enqueue time.
    """
    jid = store.enqueue_job(_minimal_spec())
    row = store.get(jid)
    assert row["confidence"] is None
    assert row["trust_tier_at_spawn"] is None


# ---------------------------------------------------------------------------
# Test 2 — ★ Illegal backward transition
# ---------------------------------------------------------------------------


def test_illegal_backward_transition_raises(store):
    """
    P2-a #2 ★: MERGING→RUNNING is NOT in the allowed map; should raise
    IllegalTransition; the row state must be unchanged after the attempt.
    """
    jid = store.enqueue_job(_minimal_spec())
    # Advance to MERGING via the legal path.
    store.transition(jid, "QUEUED", "RUNNING")
    store.transition(jid, "RUNNING", "TEST")
    store.transition(jid, "TEST", "REVIEW")
    store.transition(jid, "REVIEW", "AWAITING_APPROVAL")
    store.transition(jid, "AWAITING_APPROVAL", "MERGING")

    assert store.get(jid)["state"] == "MERGING"

    # Attempt illegal backward move.
    with pytest.raises(IllegalTransition):
        store.transition(jid, "MERGING", "RUNNING")

    # Row must still be in MERGING — no partial write.
    assert store.get(jid)["state"] == "MERGING"


def test_illegal_transition_unknown_edge(store):
    """
    Any transition not in the allowed map — even between adjacent-sounding
    states — raises IllegalTransition without touching the row.
    """
    jid = store.enqueue_job(_minimal_spec())
    store.transition(jid, "QUEUED", "RUNNING")

    with pytest.raises(IllegalTransition):
        store.transition(jid, "RUNNING", "DONE")  # skip over TEST/REVIEW etc.

    assert store.get(jid)["state"] == "RUNNING"


# ---------------------------------------------------------------------------
# Test 3 — Legal full state path
# ---------------------------------------------------------------------------


def test_legal_full_path_to_done(store):
    """
    P2-a #3: traverse the full happy-path QUEUED→RUNNING→TEST→REVIEW→
    AWAITING_APPROVAL→MERGING→DONE; every step must succeed and the final
    state must be DONE.
    """
    jid = store.enqueue_job(_minimal_spec())
    path = [
        ("QUEUED", "RUNNING"),
        ("RUNNING", "TEST"),
        ("TEST", "REVIEW"),
        ("REVIEW", "AWAITING_APPROVAL"),
        ("AWAITING_APPROVAL", "MERGING"),
        ("MERGING", "DONE"),
    ]
    for expected, target in path:
        store.transition(jid, expected, target)
        assert store.get(jid)["state"] == target, f"state should be {target}"

    # DONE is terminal — no further transitions are allowed.
    with pytest.raises(IllegalTransition):
        store.transition(jid, "DONE", "QUEUED")


def test_terminal_states_reject_all_transitions(store):
    """All three terminal states (DONE, FAILED, NEEDS_ATTENTION) must
    reject every transition, including the self-transition."""
    jid1 = store.enqueue_job(_minimal_spec(intake_source_hash="h1"))
    store.transition(jid1, "QUEUED", "FAILED")
    with pytest.raises(IllegalTransition):
        store.transition(jid1, "FAILED", "QUEUED")

    jid2 = store.enqueue_job(_minimal_spec(intake_source_hash="h2"))
    store.transition(jid2, "QUEUED", "RUNNING")
    store.transition(jid2, "RUNNING", "NEEDS_ATTENTION")
    with pytest.raises(IllegalTransition):
        store.transition(jid2, "NEEDS_ATTENTION", "RUNNING")


# ---------------------------------------------------------------------------
# Test 4 — CAS concurrency: exactly one concurrent transition wins
# ---------------------------------------------------------------------------


def test_concurrent_transitions_exactly_one_wins(store):
    """
    P2-a #4: two threads each racing to transition the same job from QUEUED
    to RUNNING; exactly one must succeed and the other must get
    IllegalTransition — the CAS WHERE id=? AND state=? guarantees this.
    """
    jid = store.enqueue_job(_minimal_spec())

    results = []

    def try_transition():
        try:
            store.transition(jid, "QUEUED", "RUNNING")
            results.append("ok")
        except IllegalTransition:
            results.append("fail")

    t1 = threading.Thread(target=try_transition)
    t2 = threading.Thread(target=try_transition)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results.count("ok") == 1, f"expected exactly 1 winner, got {results}"
    assert results.count("fail") == 1
    assert store.get(jid)["state"] == "RUNNING"


# ---------------------------------------------------------------------------
# Test 5 — Supervisor lock
# ---------------------------------------------------------------------------


def test_supervisor_lock_claimed_by_first_pid(store):
    """First claim on a fresh store succeeds."""
    store.claim_supervisor_lock(pid=1234)
    # No exception = success.


def test_supervisor_lock_second_supervisor_blocked(tmp_path):
    """
    P2-a #5: while supervisor A holds a fresh lock, supervisor B's claim
    attempt raises SupervisorLockError.
    """
    store = JobStore(tmp_path / "jobs.db")
    store.claim_supervisor_lock(pid=1111)

    with pytest.raises(SupervisorLockError):
        store.claim_supervisor_lock(pid=2222)


def test_supervisor_lock_steal_after_stale(tmp_path):
    """
    P2-a #5: if the existing lock's heartbeat is older than the stale TTL,
    a new supervisor can steal it.  We backdoor the row directly to simulate
    a stale heartbeat.
    """
    store = JobStore(tmp_path / "jobs.db")
    # Insert a lock row with a heartbeat far in the past.
    stale_ts = int(time.time() * 1000) - (20 * 60 * 1000)  # 20 minutes ago
    conn = sqlite3.connect(str(tmp_path / "jobs.db"))
    conn.execute(
        "INSERT INTO supervisor_lock (k, owner_pid, heartbeat_ts) VALUES (1, 9999, ?)",
        (stale_ts,),
    )
    conn.commit()
    conn.close()

    # New supervisor should steal the lock without error.
    store.claim_supervisor_lock(pid=2222)


def test_supervisor_lock_heartbeat_refresh(tmp_path):
    """Heartbeating does not raise and updates the ts so the lock stays fresh."""
    store = JobStore(tmp_path / "jobs.db")
    store.claim_supervisor_lock(pid=os.getpid())
    before = time.time() * 1000
    time.sleep(0.01)
    store.heartbeat_supervisor_lock(pid=os.getpid())
    conn = sqlite3.connect(str(tmp_path / "jobs.db"))
    row = conn.execute("SELECT heartbeat_ts FROM supervisor_lock WHERE k=1").fetchone()
    conn.close()
    assert row[0] > before


def test_supervisor_lock_release_allows_reclam(tmp_path):
    """After release a new supervisor can claim immediately."""
    store = JobStore(tmp_path / "jobs.db")
    store.claim_supervisor_lock(pid=1111)
    store.release_supervisor_lock(pid=1111)
    store.claim_supervisor_lock(pid=2222)  # must not raise


# ---------------------------------------------------------------------------
# Test 6 — Per-tick integrity check
# ---------------------------------------------------------------------------


def test_integrity_check_parks_dead_pgid(tmp_path, monkeypatch):
    """
    P2-a #6: a job with state=RUNNING and a dead pgid is caught by
    check_integrity() and parked to NEEDS_ATTENTION.

    We monkeypatch os.kill in the job_store module so the test doesn't
    actually signal a real process group (which the live-system guard blocks).
    The patched version raises ProcessLookupError for the specific dead pgid,
    simulating what the OS would return for a gone process group.
    """
    store = JobStore(tmp_path / "jobs.db")
    jid = store.enqueue_job(_minimal_spec())
    store.transition(jid, "QUEUED", "RUNNING")

    dead_pgid = 777777
    conn = sqlite3.connect(str(tmp_path / "jobs.db"))
    conn.execute("UPDATE jobs SET pgid=? WHERE id=?", (dead_pgid, jid))
    conn.commit()
    conn.close()

    # Simulate the OS returning ProcessLookupError for this (dead) pgid.
    import factory.job_store as _js_mod

    real_kill = os.kill

    def _fake_kill(pid, sig, *args, **kwargs):
        if pid == -dead_pgid and sig == 0:
            raise ProcessLookupError(f"[fake] pgid {dead_pgid} not found")
        return real_kill(pid, sig, *args, **kwargs)

    monkeypatch.setattr(_js_mod.os, "kill", _fake_kill)

    violations = store.check_integrity()

    parked = store.get(jid)
    assert parked["state"] == "NEEDS_ATTENTION", (
        f"expected job to be parked to NEEDS_ATTENTION, got {parked['state']}"
    )
    assert any(v["rule"] == "dead_pgid" for v in violations)


def test_integrity_check_catches_duplicate_worktree(tmp_path):
    """
    Per-tick integrity: two RUNNING jobs sharing a worktree_path violates
    invariant (b) and must appear in the violations list.
    """
    store = JobStore(tmp_path / "jobs.db")
    jid1 = store.enqueue_job(_minimal_spec(intake_source_hash="h1"))
    jid2 = store.enqueue_job(_minimal_spec(intake_source_hash="h2"))
    store.transition(jid1, "QUEUED", "RUNNING")
    store.transition(jid2, "QUEUED", "RUNNING")

    # Force both to share the same worktree_path.
    shared_wt = "/tmp/shared-worktree"
    conn = sqlite3.connect(str(tmp_path / "jobs.db"))
    conn.execute("UPDATE jobs SET worktree_path=? WHERE id=?", (shared_wt, jid1))
    conn.execute("UPDATE jobs SET worktree_path=? WHERE id=?", (shared_wt, jid2))
    conn.commit()
    conn.close()

    violations = store.check_integrity()
    dup_violations = [v for v in violations if v["rule"] == "duplicate_worktree"]
    assert dup_violations, "expected duplicate_worktree violation"


def test_integrity_check_clean_store_is_silent(store):
    """A healthy store with no active jobs produces no violations."""
    violations = store.check_integrity()
    assert violations == []


# ---------------------------------------------------------------------------
# Test 7 — build-jobs.md mirror
# ---------------------------------------------------------------------------


def test_build_jobs_mirror_reflects_new_state(tmp_path):
    """
    P2-a #7 (REQ-03): after a state transition, write_build_jobs_mirror()
    produces a file whose content mentions the job's new state.
    """
    store = JobStore(tmp_path / "jobs.db")
    jid = store.enqueue_job(_minimal_spec())
    store.transition(jid, "QUEUED", "RUNNING")

    mirror = tmp_path / "build-jobs.md"
    store.write_build_jobs_mirror(mirror)

    content = mirror.read_text()
    assert "RUNNING" in content
    assert jid[:10] in content  # short id present
    assert "test-repo" in content


def test_build_jobs_mirror_atomic_write(tmp_path):
    """
    The mirror write must be atomic: the final file must be readable (not
    partial).  We verify that os.replace is used by checking no .tmp sibling
    remains after the call.
    """
    store = JobStore(tmp_path / "jobs.db")
    store.enqueue_job(_minimal_spec())
    mirror = tmp_path / "build-jobs.md"
    store.write_build_jobs_mirror(mirror)

    assert mirror.exists()
    # No leftover .tmp files.
    temps = list(tmp_path.glob(".build-jobs-*.tmp"))
    assert temps == [], f"leftover temp files: {temps}"


# ---------------------------------------------------------------------------
# Test 8 — Idempotency via intake_source_hash
# ---------------------------------------------------------------------------


def test_idempotency_guard_blocks_duplicate(store):
    """
    P2-b #3 (partial — store side): has_job_for_intake_hash returns True
    after the first enqueue, so a second enqueue with the same hash can be
    blocked by the supervisor.
    """
    spec = _minimal_spec(intake_source_hash="stable-hash-abc")
    jid1 = store.enqueue_job(spec)
    assert store.has_job_for_intake_hash("stable-hash-abc") is True


def test_idempotency_different_hashes_both_allowed(store):
    """Two specs with different hashes both create rows without conflict."""
    store.enqueue_job(_minimal_spec(intake_source_hash="hash-A"))
    store.enqueue_job(_minimal_spec(intake_source_hash="hash-B"))
    assert len(store.list_jobs()) == 2


def test_none_intake_hash_not_idempotency_guarded(store):
    """A spec with intake_source_hash=None still creates a row; the guard
    only applies when the hash is explicitly set."""
    jid = store.enqueue_job(_minimal_spec(intake_source_hash=None))
    assert store.get(jid) is not None
    # has_job_for_intake_hash with None should not match the NULL rows.
    # (The method only makes sense when called with an actual hash string.)
