# ABOUTME: Bounded self-heal policy (P4-a, design §3.3 + review F11). ONE self-heal
# ABOUTME: attempt per phase (N=1) from a forensic bundle before escalating to
# ABOUTME: NEEDS_ATTENTION — no endless flip-flopping. Plus the correctness
# ABOUTME: invariant: a resume/self-heal NEVER re-runs a cleared SIDE-EFFECTING
# ABOUTME: phase (merge/push), which would double-apply an irreversible effect.
"""
Self-heal retry policy + the no-re-run-side-effect guard (P4-a).

Two responsibilities, both plain code (no AI):
  1. Bound the self-heal retry to N=1 per phase (HealState / decide) — a single
     re-launch whose brief carries "the previous attempt failed with: <test/diff>";
     a second failure escalates rather than looping.
  2. Refuse to resume INTO a cleared side-effecting phase (guard_resume_phase). A
     merge/push is irreversible; re-running it on resume would double-apply it. This
     is the correctness invariant the flagship gate depends on.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List

# Phases that mutate state OUTSIDE the worktree (the shared repo, remote). Once
# cleared, they must NEVER be re-run on resume — the effect is already applied and
# is not idempotent. MERGING is the P4 side-effecting phase; the READY boundary is
# terminal. IMPLEMENT/TEST/REVIEW mutate only the isolated worktree and ARE safe to
# re-run from the last cleared brief.
_SIDE_EFFECTING_PHASES = frozenset({"MERGING"})

# At most this many self-heal attempts per phase before escalating (design's "one
# bounded self-heal"). N=1.
_MAX_HEAL_ATTEMPTS = 1


class UnsafeResume(Exception):
    """Raised when a resume would re-enter a cleared side-effecting phase."""


class Decision(Enum):
    """The self-heal decision for a phase failure."""

    ATTEMPT = "attempt"    # first failure — one bounded self-heal
    ESCALATE = "escalate"  # already attempted once — park NEEDS_ATTENTION


@dataclass
class HealState:
    """
    Per-phase self-heal bookkeeping.

    Purpose: enforce N=1 — record whether the single self-heal for this phase has
    been spent so a second failure escalates instead of looping.
    Usage: st = HealState(phase="IMPLEMENT"); if st.may_attempt(): heal();
           st.record_attempt().
    Gotchas: this is per-phase; a job that clears IMPLEMENT then fails TEST gets a
    fresh HealState for TEST (the N=1 budget is not shared across phases).
    """

    phase: str
    attempts: int = 0

    def may_attempt(self) -> bool:
        """True iff a self-heal attempt remains for this phase (N=1)."""
        return self.attempts < _MAX_HEAL_ATTEMPTS

    def record_attempt(self) -> None:
        """Spend one self-heal attempt for this phase."""
        self.attempts += 1


def decide(state: HealState, *, prior_attempt_failed: bool) -> Decision:
    """
    Purpose: decide whether to self-heal or escalate, given the per-phase state and
    whether the prior attempt failed. First failure with budget → ATTEMPT; a
    already-spent budget (or a failed prior attempt) → ESCALATE.
    Usage: d = decide(state, prior_attempt_failed=True).
    Gotchas: ``prior_attempt_failed`` short-circuits to ESCALATE — a self-heal that
    ALSO failed is never retried again (N=1, no flip-flop).
    """
    if prior_attempt_failed:
        return Decision.ESCALATE
    if state.may_attempt():
        return Decision.ATTEMPT
    return Decision.ESCALATE


def is_side_effecting(phase: str) -> bool:
    """True iff re-running ``phase`` would re-apply an irreversible side effect."""
    return phase in _SIDE_EFFECTING_PHASES


def guard_resume_phase(*, resume_into: str, gates_cleared: List[str]) -> None:
    """
    Purpose: the correctness gate — refuse to resume INTO a phase that both (a) is
    side-effecting and (b) has already cleared. Re-running a cleared merge would
    double-apply an irreversible effect.
    Usage: guard_resume_phase(resume_into="MERGING", gates_cleared=[...]) — raises
    UnsafeResume if the merge already cleared.
    Gotchas: a side-effecting phase that has NOT cleared is allowed (it genuinely
    needs to run); a non-side-effecting phase is always allowed. The guard only
    fires on the double-apply case.
    """
    if is_side_effecting(resume_into) and resume_into in gates_cleared:
        raise UnsafeResume(
            f"refusing to resume into cleared side-effecting phase {resume_into!r} "
            f"(gates_cleared={gates_cleared}) — would double-apply an irreversible effect"
        )
