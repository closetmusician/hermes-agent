# ABOUTME: RED-first tests for the ADDITIVE crash-resume states in job_store.py —
# ABOUTME: P4-a REQ-02 (Revision v2 §R1). RESUMABLE + WAITING_CAPACITY are added to
# ABOUTME: _ALLOWED with forward-only edges; every PRE-v2 edge stays intact
# ABOUTME: (one-way invariant preserved). _reclaim_dead_job routes a dead-pgid job
# ABOUTME: WITH a valid checkpoint → RESUMABLE, else → NEEDS_ATTENTION. Real sqlite.
"""
Tests for the additive crash-resume state machine (P4-a, design Revision v2 §R1).

Anti-weakening: test_dead_pgid_with_checkpoint_goes_resumable FAILS if the reclaim
routing is reverted to hardcoded NEEDS_ATTENTION — it is the falsifiable core of
"crashed worker resumes" being real.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from factory.job_store import IllegalTransition, JobStore, _ALLOWED


# The exact pre-v2 edge map (frozen snapshot for the additive-invariant guard).
_PRE_V2_EDGES = {
    "QUEUED": {"ADMITTED", "RUNNING", "FAILED"},
    "ADMITTED": {"RUNNING", "FAILED", "NEEDS_ATTENTION"},
    "RUNNING": {"TEST", "FAILED", "NEEDS_ATTENTION"},
    "TEST": {"REVIEW", "RUNNING", "NEEDS_ATTENTION"},
    "REVIEW": {"AWAITING_APPROVAL", "RUNNING", "NEEDS_ATTENTION"},
    "AWAITING_APPROVAL": {"MERGING", "NEEDS_ATTENTION"},
    "MERGING": {"DONE", "NEEDS_ATTENTION"},
    "DONE": set(),
    "FAILED": set(),
    "NEEDS_ATTENTION": set(),
}


@pytest.fixture()
def store(tmp_path):
    return JobStore(tmp_path / "jobs.db")


def _patch_dead_pgid(monkeypatch, dead_pgid):
    """Make os.kill in the job_store module report `dead_pgid` as gone (matches
    the existing test_integrity_check_parks_dead_pgid pattern — no real signal)."""
    import factory.job_store as js
    real_kill = os.kill

    def fake_kill(pid, sig, *a, **k):
        if pid == -dead_pgid and sig == 0:
            raise ProcessLookupError(f"[fake] pgid {dead_pgid} not found")
        return real_kill(pid, sig, *a, **k)

    monkeypatch.setattr(js.os, "kill", fake_kill)


def _spec(**ov):
    base = {
        "repo": "test-repo", "base_branch": "main", "spec": "do x",
        "kind": "quick", "worker": "claude", "model": "claude-haiku-4-5",
        "budget_usd": 0.5, "timeout_min": 10, "intake_source_hash": None,
    }
    base.update(ov)
    return base


def test_resumable_state_edges_additive():
    """REQ-02: every PRE-v2 edge is intact (one-way invariant preserved), and the
    two new states only flow FORWARD to ADMITTED / NEEDS_ATTENTION."""
    for state, targets in _PRE_V2_EDGES.items():
        assert targets.issubset(_ALLOWED[state]), (
            f"pre-v2 edge weakened for {state}: {targets - _ALLOWED[state]}"
        )
    # New states exist and are forward-only.
    assert _ALLOWED["RESUMABLE"] == frozenset({"ADMITTED", "NEEDS_ATTENTION"})
    assert _ALLOWED["WAITING_CAPACITY"] == frozenset({"ADMITTED", "NEEDS_ATTENTION"})
    # No backward edge from a terminal into a live state.
    assert _ALLOWED["NEEDS_ATTENTION"] == frozenset()


def test_running_can_reach_resumable():
    """REQ-02: the inbound edges to RESUMABLE were added to live states."""
    assert "RESUMABLE" in _ALLOWED["RUNNING"]
    assert "WAITING_CAPACITY" in _ALLOWED["RUNNING"]
    assert "RESUMABLE" in _ALLOWED["ADMITTED"]
    assert "RESUMABLE" in _ALLOWED["TEST"]
    assert "RESUMABLE" in _ALLOWED["REVIEW"]


def test_resumable_reenters_pipeline():
    """REQ-02: a RESUMABLE job re-admits (RESUMABLE→ADMITTED) or escalates
    (RESUMABLE→NEEDS_ATTENTION); no other exit."""
    store = None  # not needed; pure map check
    assert _ALLOWED["RESUMABLE"] == frozenset({"ADMITTED", "NEEDS_ATTENTION"})


def test_full_legacy_path_still_green(store):
    """REQ-05 0-regression: the P2 happy path still transitions cleanly."""
    jid = store.enqueue_job(_spec())
    for a, b in [
        ("QUEUED", "RUNNING"), ("RUNNING", "TEST"), ("TEST", "REVIEW"),
        ("REVIEW", "AWAITING_APPROVAL"), ("AWAITING_APPROVAL", "MERGING"),
        ("MERGING", "DONE"),
    ]:
        store.transition(jid, a, b)
    assert store.get(jid)["state"] == "DONE"


def test_dead_pgid_with_checkpoint_goes_resumable(store, monkeypatch):
    """REQ-02 (anti-weakening): a live job whose pgid is dead but a VALID checkpoint
    exists is reclaimed to RESUMABLE (NOT terminal NEEDS_ATTENTION)."""
    jid = store.enqueue_job(_spec())
    store.transition(jid, "QUEUED", "RUNNING")
    _patch_dead_pgid(monkeypatch, 2_000_111)
    with store._lock:
        store._conn.execute("UPDATE jobs SET pgid=? WHERE id=?", (2_000_111, jid))
        store._conn.commit()

    # A checkpoint reader that reports a valid, non-exhausted checkpoint.
    def ckpt_reader(job_row):
        return {"valid": True, "exhausted": False}

    store.check_integrity(ledger=None, checkpoint_reader=ckpt_reader)
    assert store.get(jid)["state"] == "RESUMABLE"


def test_dead_pgid_without_checkpoint_goes_needs_attention(store, monkeypatch):
    """REQ-02: no checkpoint → the EXISTING terminal behaviour is unchanged."""
    jid = store.enqueue_job(_spec())
    store.transition(jid, "QUEUED", "RUNNING")
    _patch_dead_pgid(monkeypatch, 2_000_112)
    with store._lock:
        store._conn.execute("UPDATE jobs SET pgid=? WHERE id=?", (2_000_112, jid))
        store._conn.commit()

    def ckpt_reader(job_row):
        return None  # no checkpoint

    store.check_integrity(ledger=None, checkpoint_reader=ckpt_reader)
    assert store.get(jid)["state"] == "NEEDS_ATTENTION"


def test_dead_pgid_exhausted_resumes_goes_needs_attention(store, monkeypatch):
    """REQ-02: a valid checkpoint but resume_count >= MAX_RESUMES → NEEDS_ATTENTION
    (the 4th crash parks terminal — bounded, no infinite relaunch)."""
    jid = store.enqueue_job(_spec())
    store.transition(jid, "QUEUED", "RUNNING")
    _patch_dead_pgid(monkeypatch, 2_000_113)
    with store._lock:
        store._conn.execute("UPDATE jobs SET pgid=? WHERE id=?", (2_000_113, jid))
        store._conn.commit()

    def ckpt_reader(job_row):
        return {"valid": True, "exhausted": True}

    store.check_integrity(ledger=None, checkpoint_reader=ckpt_reader)
    assert store.get(jid)["state"] == "NEEDS_ATTENTION"


def test_reclaim_default_reader_is_backcompat(store, monkeypatch):
    """REQ-05 0-regression: with NO checkpoint_reader passed (P2/P3 callers), the
    dead-pgid job parks NEEDS_ATTENTION exactly as before."""
    jid = store.enqueue_job(_spec())
    store.transition(jid, "QUEUED", "RUNNING")
    _patch_dead_pgid(monkeypatch, 2_000_114)
    with store._lock:
        store._conn.execute("UPDATE jobs SET pgid=? WHERE id=?", (2_000_114, jid))
        store._conn.commit()
    store.check_integrity(ledger=None)  # no reader — legacy signature
    assert store.get(jid)["state"] == "NEEDS_ATTENTION"
