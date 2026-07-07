# ABOUTME: RED-first tests for factory/phase_checkpoint.py — P4-a REQ-01/03.
# ABOUTME: A resumable {job, phase, gates_cleared, artifacts, brief_path, rung,
# ABOUTME: resume_count} record written ATOMICALLY per cleared phase, round-trips,
# ABOUTME: and a corrupt/partial checkpoint is REJECTED (fail-closed). Resume
# ABOUTME: relaunches from the last CLEARED boundary, never mid-phase. No mocks.
"""
Tests for factory.phase_checkpoint (P4-a, design §3 + Revision v2 §R1).

Coverage:
  * clear_phase writes an atomic record; a partial/corrupt file is rejected.
  * read() returns None for an absent checkpoint (fresh job).
  * resume_phase() = the phase AFTER the last cleared gate (last cleared boundary),
    never mid-phase.
  * resume_count increments and MAX_RESUMES is enforced.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.phase_checkpoint import (
    MAX_RESUMES,
    PhaseCheckpoint,
    clear_phase,
    is_valid,
    read,
    resume_phase,
)


def _wt(tmp_path) -> Path:
    """A worktree root with the .factory dir the checkpoint lives under."""
    root = tmp_path / "j-1a2b-add-x"
    (root / ".factory").mkdir(parents=True)
    return root


RUNG = {"worker": "claude", "model": "claude-sonnet-4-5", "tier": 0}


def test_clear_phase_writes_atomic_record(tmp_path):
    """REQ-01: clear SPEC → checkpoint.json exists with gates_cleared=['SPEC'],
    phase='IMPLEMENT' (the phase after the cleared boundary)."""
    wt = _wt(tmp_path)
    clear_phase(
        wt,
        job_id="j-1a2b",
        repo="/repo/acme",
        cleared_phase="SPEC",
        artifacts={"spec": ".factory/spec.json"},
        brief_path=".factory/handoff-brief.md",
        rung=RUNG,
        branch="factory/acme/j-1a2b-add-x",
        budget_usd=2.0,
    )
    ck = read(wt)
    assert ck is not None
    assert ck.gates_cleared == ["SPEC"]
    assert ck.phase == "IMPLEMENT"  # the phase AFTER the last cleared boundary
    assert ck.job_id == "j-1a2b"
    assert ck.resume_count == 0


def test_clear_phase_is_atomic_no_partial(tmp_path):
    """REQ-01: a temp file is used; there is never a half-written checkpoint.json
    left behind (only the temp gets os.replace'd)."""
    wt = _wt(tmp_path)
    clear_phase(wt, job_id="j", repo="/r", cleared_phase="SPEC",
                artifacts={}, brief_path="b.md", rung=RUNG,
                branch="factory/r/j", budget_usd=1.0)
    # No leftover temp files, exactly one checkpoint.json.
    factory_dir = wt / ".factory"
    files = sorted(p.name for p in factory_dir.iterdir())
    assert "checkpoint.json" in files
    assert not any(f.startswith(".checkpoint-") for f in files)


def test_corrupt_checkpoint_rejected(tmp_path):
    """REQ-01 fail-closed: a truncated/corrupt checkpoint.json is NOT resumed into
    a bad state — read() returns None (treated as fresh) and is_valid() is False."""
    wt = _wt(tmp_path)
    (wt / ".factory" / "checkpoint.json").write_text('{"job_id": "j", "pha')  # truncated
    assert read(wt) is None
    assert is_valid(wt) is False


def test_missing_required_field_rejected(tmp_path):
    """REQ-01: a syntactically-valid JSON missing a required field is rejected."""
    wt = _wt(tmp_path)
    (wt / ".factory" / "checkpoint.json").write_text(json.dumps({"job_id": "j"}))
    assert read(wt) is None


def test_absent_checkpoint_is_fresh(tmp_path):
    """REQ-03: a job that cleared no phase → read() is None → resume treats fresh."""
    wt = _wt(tmp_path)
    assert read(wt) is None
    assert is_valid(wt) is False


def test_resume_phase_is_last_cleared_boundary(tmp_path):
    """REQ-03: resume starts at the phase AFTER the last cleared gate, never
    mid-phase. Clear SPEC then IMPLEMENT → resume at TEST."""
    wt = _wt(tmp_path)
    clear_phase(wt, job_id="j", repo="/r", cleared_phase="SPEC",
                artifacts={}, brief_path="b.md", rung=RUNG,
                branch="factory/r/j", budget_usd=1.0)
    clear_phase(wt, job_id="j", repo="/r", cleared_phase="IMPLEMENT",
                artifacts={}, brief_path="b.md", rung=RUNG,
                branch="factory/r/j", budget_usd=1.0)
    ck = read(wt)
    assert ck.gates_cleared == ["SPEC", "IMPLEMENT"]
    assert resume_phase(ck) == "TEST"


def test_resume_count_increments_and_bounded(tmp_path):
    """REQ-02: bump_resume_count increments; after MAX_RESUMES the guard reports
    exhausted so the reclaim routes NEEDS_ATTENTION not RESUMABLE."""
    from factory.phase_checkpoint import bump_resume_count, resumes_exhausted

    wt = _wt(tmp_path)
    clear_phase(wt, job_id="j", repo="/r", cleared_phase="SPEC",
                artifacts={}, brief_path="b.md", rung=RUNG,
                branch="factory/r/j", budget_usd=1.0)
    assert read(wt).resume_count == 0
    for i in range(MAX_RESUMES):
        assert resumes_exhausted(read(wt)) is False
        bump_resume_count(wt)
    assert read(wt).resume_count == MAX_RESUMES
    assert resumes_exhausted(read(wt)) is True
