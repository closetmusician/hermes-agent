# ABOUTME: The job-level PHASE checkpoint — a new disjoint layer above the
# ABOUTME: within-worker tools/checkpoint_manager.py (which is REUSED as-is, not
# ABOUTME: extended). Writes a resumable {job, phase, gates_cleared, artifacts,
# ABOUTME: brief_path, rung, resume_count} record ATOMICALLY per cleared phase so a
# ABOUTME: worker killed mid-night re-launches from the last CLEARED boundary.
"""
Phase-checkpoint layer (P4-a, design §3 + Revision v2 §R1).

A worker that clears a phase gate persists a compact record to
``<worktree>/.factory/checkpoint.json`` via atomic temp-file + os.replace, so a
crash never loses the cleared-phase state and never leaves a half-written file. On
resume the scheduler re-launches from the phase AFTER the last cleared gate (the
last cleared *boundary*), never mid-phase — the brief is the portable resume unit.

This module is plain code (no AI). It is disjoint from tools/checkpoint_manager.py:
that tool undoes turns WITHIN a worker by commit hash; this one models the factory
job's phase lifecycle. They never share files.

Fail-closed: a truncated / corrupt / field-missing checkpoint is REJECTED (read()
returns None) so resume treats the job as fresh rather than entering a bad state.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# The ordered gauntlet-driven phase lifecycle (design §3.1). SPEC exists only for
# feature-class jobs; a quick job starts at IMPLEMENT (matches two_stage.plan_stages).
# READY is the terminal boundary — nothing resumes "into" it.
PHASE_ORDER: List[str] = ["SPEC", "IMPLEMENT", "TEST", "REVIEW", "READY"]

# A job may crash+resume at most this many times before it is parked terminal
# (NEEDS_ATTENTION). Bounds the RUNNING→RESUMABLE→ADMITTED→RUNNING loop so the
# state graph cannot cycle unboundedly (design §R1) and bounds cost re-spend (§R3).
MAX_RESUMES: int = 3

# The checkpoint schema version — a future stale-schema checkpoint can be rejected
# on a version mismatch (design §R5 defers the full checksum; the field is here now).
CHECKPOINT_VERSION: int = 1

_CHECKPOINT_NAME = "checkpoint.json"
_REQUIRED_FIELDS = (
    "version",
    "job_id",
    "repo",
    "phase",
    "gates_cleared",
    "brief_path",
    "rung",
    "resume_count",
)


@dataclass
class PhaseCheckpoint:
    """
    One job's on-disk phase-resume record.

    Purpose: the portable unit a resume relaunches from — the last cleared phase
    boundary plus the compact brief path (never a raw transcript, which does not
    map across providers).
    Usage: ck = read(worktree); resume at resume_phase(ck) with ck.brief_path.
    Gotchas: ``phase`` is the phase to run NEXT (the one after the last cleared
    gate); ``gates_cleared`` is the ordered history of cleared boundaries. A record
    read from disk is only ever returned by read() once validated — an invalid one
    yields None, so a caller never holds a partial PhaseCheckpoint.
    """

    job_id: str
    repo: str
    phase: str
    gates_cleared: List[str]
    brief_path: str
    rung: Dict[str, Any]
    resume_count: int = 0
    artifacts: Dict[str, str] = field(default_factory=dict)
    branch: Optional[str] = None
    budget_usd: Optional[float] = None
    version: int = CHECKPOINT_VERSION


def _checkpoint_path(worktree: Path) -> Path:
    """The canonical checkpoint path under a worktree's .factory dir."""
    return Path(worktree) / ".factory" / _CHECKPOINT_NAME


def _next_phase(cleared_phase: str) -> str:
    """
    Purpose: given the phase whose gate just cleared, return the phase to run next.
    Usage: internal — clear_phase records this as the record's ``phase``.
    Gotchas: the last real phase (REVIEW) clears to READY (terminal boundary);
    an unknown phase name falls back to itself so a bad caller never crashes here.
    """
    try:
        idx = PHASE_ORDER.index(cleared_phase)
    except ValueError:
        return cleared_phase
    return PHASE_ORDER[min(idx + 1, len(PHASE_ORDER) - 1)]


def clear_phase(
    worktree: Path,
    *,
    job_id: str,
    repo: str,
    cleared_phase: str,
    artifacts: Dict[str, str],
    brief_path: str,
    rung: Dict[str, Any],
    branch: Optional[str] = None,
    budget_usd: Optional[float] = None,
) -> PhaseCheckpoint:
    """
    Purpose: record that ``cleared_phase``'s gate passed — append it to
    gates_cleared, set ``phase`` to the next phase, and write the record ATOMICALLY
    (temp file + os.replace) so a crash mid-write never corrupts the checkpoint.
    Usage: called by the supervisor the instant a phase gate passes, ordered BEFORE
    the JobStore.transition to the next state (write-checkpoint-first discipline).
    Gotchas: resume_count is PRESERVED across clear_phase calls (a resumed job that
    clears a new phase keeps its accumulated resume count). The prior checkpoint
    file stays intact until os.replace swaps it — there is never a partial file.
    """
    worktree = Path(worktree)
    existing = read(worktree)
    gates = list(existing.gates_cleared) if existing else []
    if cleared_phase not in gates:
        gates.append(cleared_phase)
    resume_count = existing.resume_count if existing else 0

    ck = PhaseCheckpoint(
        job_id=job_id,
        repo=repo,
        phase=_next_phase(cleared_phase),
        gates_cleared=gates,
        brief_path=str(brief_path),
        rung=dict(rung),
        resume_count=resume_count,
        artifacts=dict(artifacts or {}),
        branch=branch,
        budget_usd=budget_usd,
        version=CHECKPOINT_VERSION,
    )
    _write_atomic(worktree, ck)
    return ck


def _write_atomic(worktree: Path, ck: PhaseCheckpoint) -> None:
    """
    Purpose: serialize the checkpoint to a sibling temp file, fsync, then
    os.replace onto checkpoint.json — the standard atomic-write discipline used by
    write_build_jobs_mirror. A reader never sees a half-written file.
    Usage: internal to clear_phase / bump_resume_count.
    Gotchas: the temp file is created in the SAME dir as the target so os.replace is
    atomic (a cross-device rename is not); on any error the temp is unlinked.
    """
    target = _checkpoint_path(worktree)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": ck.version,
        "job_id": ck.job_id,
        "repo": ck.repo,
        "phase": ck.phase,
        "gates_cleared": ck.gates_cleared,
        "artifacts": ck.artifacts,
        "brief_path": ck.brief_path,
        "branch": ck.branch,
        "rung": ck.rung,
        "budget_usd": ck.budget_usd,
        "resume_count": ck.resume_count,
    }
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=".checkpoint-", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w") as fh:
            json.dump(payload, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read(worktree: Path) -> Optional[PhaseCheckpoint]:
    """
    Purpose: read + VALIDATE the checkpoint for a worktree. A truncated / corrupt /
    field-missing / wrong-version file is rejected (returns None) — fail-closed, so
    resume treats such a job as fresh rather than entering a bad state.
    Usage: ck = read(worktree); if ck is None: re-enqueue at start.
    Gotchas: returns None (not a raise) for BOTH "absent" and "corrupt" — the caller
    cannot distinguish, which is intentional: both mean "no resumable state".
    """
    path = _checkpoint_path(worktree)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    if any(k not in raw for k in _REQUIRED_FIELDS):
        return None
    if raw.get("version") != CHECKPOINT_VERSION:
        return None
    if not isinstance(raw.get("gates_cleared"), list):
        return None
    return PhaseCheckpoint(
        job_id=raw["job_id"],
        repo=raw["repo"],
        phase=raw["phase"],
        gates_cleared=list(raw["gates_cleared"]),
        brief_path=raw["brief_path"],
        rung=raw["rung"],
        resume_count=int(raw.get("resume_count", 0)),
        artifacts=dict(raw.get("artifacts") or {}),
        branch=raw.get("branch"),
        budget_usd=raw.get("budget_usd"),
        version=int(raw["version"]),
    )


def is_valid(worktree: Path) -> bool:
    """True iff a well-formed, resumable checkpoint exists for this worktree."""
    return read(worktree) is not None


def resume_phase(ck: PhaseCheckpoint) -> str:
    """
    Purpose: the phase a resume starts at — the phase AFTER the last cleared gate,
    i.e. the last cleared BOUNDARY, never an arbitrary mid-phase turn.
    Usage: start = resume_phase(read(worktree)).
    Gotchas: this is exactly ck.phase (clear_phase already advanced it past the last
    cleared gate); the function makes the intent explicit at the call site.
    """
    if not ck.gates_cleared:
        return PHASE_ORDER[0]
    return _next_phase(ck.gates_cleared[-1])


def bump_resume_count(worktree: Path) -> int:
    """
    Purpose: increment the persisted resume_count atomically (re-uses the atomic
    write) and return the new value. Called each time a crashed job is relaunched.
    Usage: n = bump_resume_count(worktree); if n >= MAX_RESUMES: escalate.
    Gotchas: raises if there is no valid checkpoint — the caller must only bump a
    job that actually has one (the reclaim path checks is_valid first).
    """
    ck = read(worktree)
    if ck is None:
        raise ValueError(f"no valid checkpoint to bump under {worktree}")
    ck.resume_count += 1
    _write_atomic(Path(worktree), ck)
    return ck.resume_count


def resumes_exhausted(ck: Optional[PhaseCheckpoint]) -> bool:
    """
    Purpose: True if the job has already used its MAX_RESUMES budget — the reclaim
    path then routes NEEDS_ATTENTION instead of RESUMABLE (bounded relaunch).
    Usage: if resumes_exhausted(read(worktree)): target = NEEDS_ATTENTION.
    Gotchas: a None checkpoint is treated as exhausted (no resumable state ⇒ do not
    resume), which is the fail-closed posture.
    """
    if ck is None:
        return True
    return ck.resume_count >= MAX_RESUMES
