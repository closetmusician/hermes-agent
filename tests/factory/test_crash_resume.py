# ABOUTME: The REAL crash-resume gate test — P4-a REQ-03/05 (design Revision v2 §R1).
# ABOUTME: Launches a hermetic short-lived subprocess in its own process group,
# ABOUTME: kill -9's the whole group mid-phase, then drives ONE scheduler tick and
# ABOUTME: asserts the dead-pgid job with a valid checkpoint goes RESUMABLE (NOT
# ABOUTME: parked terminal) and the NEXT tick re-admits + resumes from the checkpoint.
"""
The flagship crash-resume proof (P4-a, design Revision v2 §R1 "the REAL test").

This is NOT a supervised rewind — it drives a genuine dead pgid (kill -9 on a real
process group) and asserts the scheduler re-admits the job from its last CLEARED
phase via the brief, bounded by MAX_RESUMES. Anti-weakening: if the reclaim routing
is reverted to hardcoded NEEDS_ATTENTION, the RESUMABLE assertion fails.

Real sqlite, real process kill, hermetic subprocess (cleaned up in a finally).
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from factory import phase_checkpoint
from factory.cost_stops import DailyBudgetLedger
from factory.job_store import JobStore
from factory.scheduler import Scheduler


@pytest.fixture()
def store(tmp_path):
    return JobStore(tmp_path / "jobs.db")


@pytest.fixture()
def ledger(tmp_path):
    return DailyBudgetLedger(tmp_path / "jobs.db", ceiling_usd=1000.0)


def _spec(repo, **ov):
    base = {
        "repo": repo, "base_branch": "main", "spec": "add x",
        "kind": "feature", "worker": "claude", "model": "claude-sonnet-4-5",
        "budget_usd": 2.0, "timeout_min": 10, "intake_source_hash": None,
    }
    base.update(ov)
    return base


def _spawn_real_process_group() -> subprocess.Popen:
    """A short-lived child in its OWN process group (os.setsid) that sleeps so its
    pgid is alive until we kill it. Real process, hermetic, killed in the test."""
    return subprocess.Popen(
        ["sleep", "30"],
        preexec_fn=os.setsid,  # new session → child pid == pgid
    )


@pytest.mark.live_system_guard_bypass  # genuine dead-pgid crash needs real signals
def test_crashed_worker_goes_resumable_then_relaunches_from_checkpoint(
    store, ledger, tmp_path
):
    repo = str(tmp_path / "acme")
    wt = tmp_path / "wt-j"
    (wt / ".factory").mkdir(parents=True)

    # 1. A job in a worktree that cleared SPEC (checkpoint at IMPLEMENT).
    jid = store.enqueue_job(_spec(repo))
    store.transition(jid, "QUEUED", "RUNNING",
                     extra={"worktree_path": str(wt), "branch": f"factory/{Path(repo).name}/{jid}"})
    phase_checkpoint.clear_phase(
        wt, job_id=jid, repo=repo, cleared_phase="SPEC",
        artifacts={"spec": ".factory/spec.json"},
        brief_path=str(wt / ".factory" / "handoff-brief.md"),
        rung={"worker": "claude", "model": "claude-sonnet-4-5", "tier": 0},
        branch=f"factory/{Path(repo).name}/{jid}", budget_usd=2.0,
    )
    (wt / ".factory" / "handoff-brief.md").write_text("SPEC cleared; resume IMPLEMENT")

    proc = _spawn_real_process_group()
    try:
        pgid = os.getpgid(proc.pid)
        with store._lock:
            store._conn.execute("UPDATE jobs SET pgid=? WHERE id=?", (pgid, jid))
            store._conn.commit()

        # 2. CRASH — kill -9 the whole process group. Dead before the tick runs.
        os.killpg(pgid, signal.SIGKILL)
        proc.wait(timeout=5)
        # Give the OS a beat to reap.
        for _ in range(50):
            try:
                os.kill(-pgid, 0)
                time.sleep(0.05)
            except ProcessLookupError:
                break

        # A checkpoint reader wired to the worktree the scheduler uses.
        def ckpt_reader(job_row):
            ck = phase_checkpoint.read(Path(job_row["worktree_path"]))
            if ck is None:
                return None
            return {"valid": True, "exhausted": phase_checkpoint.resumes_exhausted(ck)}

        resumed = {}

        def resume_spawn(job_row):
            # The scheduler's resume seam: build the run spec from the checkpoint.
            ck = phase_checkpoint.read(Path(job_row["worktree_path"]))
            resumed["phase"] = phase_checkpoint.resume_phase(ck)
            resumed["brief"] = Path(ck.brief_path).read_text()
            phase_checkpoint.bump_resume_count(Path(job_row["worktree_path"]))

        sched = Scheduler(
            store=store, ledger=ledger, spawn_fn=resume_spawn,
            stagger_fn=lambda: None, checkpoint_reader=ckpt_reader,
        )

        # 3. Tick 1: check_integrity routes the dead-pgid job → RESUMABLE (not terminal).
        sched.tick()
        assert store.get(jid)["state"] == "RESUMABLE", (
            "a crashed worker WITH a valid checkpoint must go RESUMABLE, not terminal"
        )

        # 4. Tick 2: the RESUMABLE job is re-admitted and resumed from the checkpoint.
        sched.tick()
        assert store.get(jid)["state"] == "ADMITTED"
        assert resumed["phase"] == "IMPLEMENT", "resume must start at the last CLEARED boundary, not SPEC"
        assert "resume IMPLEMENT" in resumed["brief"]
    finally:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait(timeout=5)


def test_resume_bounded_by_max_resumes(store, ledger, tmp_path, monkeypatch):
    """REQ-02: after MAX_RESUMES crashes the job parks NEEDS_ATTENTION (bounded).
    Uses a fake dead pgid (monkeypatched os.kill in the job_store module) — no real
    signal, matching the existing test_integrity_check_parks_dead_pgid pattern."""
    import factory.job_store as js

    repo = str(tmp_path / "acme")
    wt = tmp_path / "wt-j2"
    (wt / ".factory").mkdir(parents=True)
    jid = store.enqueue_job(_spec(repo))
    store.transition(jid, "QUEUED", "RUNNING", extra={"worktree_path": str(wt)})
    phase_checkpoint.clear_phase(
        wt, job_id=jid, repo=repo, cleared_phase="SPEC", artifacts={},
        brief_path="b.md", rung={"worker": "claude", "model": "m", "tier": 0},
        branch=f"factory/acme/{jid}", budget_usd=2.0,
    )
    # Exhaust resume budget.
    for _ in range(phase_checkpoint.MAX_RESUMES):
        phase_checkpoint.bump_resume_count(wt)

    dead_pgid = 2_000_222
    real_kill = os.kill

    def fake_kill(pid, sig, *a, **k):
        if pid == -dead_pgid and sig == 0:
            raise ProcessLookupError(f"[fake] pgid {dead_pgid} not found")
        return real_kill(pid, sig, *a, **k)

    monkeypatch.setattr(js.os, "kill", fake_kill)
    with store._lock:
        store._conn.execute("UPDATE jobs SET pgid=? WHERE id=?", (dead_pgid, jid))
        store._conn.commit()

    def ckpt_reader(job_row):
        ck = phase_checkpoint.read(Path(job_row["worktree_path"]))
        return {"valid": ck is not None, "exhausted": phase_checkpoint.resumes_exhausted(ck)}

    store.check_integrity(ledger=None, checkpoint_reader=ckpt_reader)
    assert store.get(jid)["state"] == "NEEDS_ATTENTION"
