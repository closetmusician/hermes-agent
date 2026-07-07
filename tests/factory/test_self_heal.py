# ABOUTME: RED-first tests for factory/self_heal.py — P4-a REQ-04.
# ABOUTME: One bounded self-heal attempt (N=1) from a forensic bundle before
# ABOUTME: escalating to NEEDS_ATTENTION; and the correctness invariant — a
# ABOUTME: resume/self-heal NEVER re-runs a cleared SIDE-EFFECTING phase (merge).
# ABOUTME: Plain code, no AI in the loop. No mocks.
"""
Tests for factory.self_heal (P4-a, design §3.3 + review F11 "no re-run merge").
"""
from __future__ import annotations

import pytest

from factory import self_heal


def test_self_heal_is_bounded_n1():
    """REQ-04: at most ONE self-heal attempt per phase. A HealState that already
    attempted once must not attempt again — it escalates."""
    state = self_heal.HealState(phase="IMPLEMENT")
    assert state.may_attempt() is True
    state.record_attempt()
    assert state.may_attempt() is False  # N=1 — no second attempt


def test_self_heal_escalates_after_one_failure():
    """REQ-04: a self-heal that also fails → the decision is ESCALATE (the caller
    parks NEEDS_ATTENTION with the bundle), never a second retry."""
    state = self_heal.HealState(phase="IMPLEMENT")
    decision = self_heal.decide(state, prior_attempt_failed=True)
    assert decision == self_heal.Decision.ESCALATE


def test_self_heal_attempts_first_failure_once():
    state = self_heal.HealState(phase="IMPLEMENT")
    decision = self_heal.decide(state, prior_attempt_failed=False)
    assert decision == self_heal.Decision.ATTEMPT


def test_resume_never_reruns_cleared_merge_phase():
    """REQ-04 correctness invariant: a resume that would re-enter a cleared
    side-effecting phase (MERGING) is REFUSED — it advances past it. Re-running a
    merge would double-apply an irreversible side effect."""
    # Merge already cleared; the only remaining phase is terminal.
    assert self_heal.is_side_effecting("MERGING") is True
    assert self_heal.is_side_effecting("IMPLEMENT") is False
    # guard_resume_phase raises if asked to resume INTO a cleared side-effecting phase.
    with pytest.raises(self_heal.UnsafeResume):
        self_heal.guard_resume_phase(
            resume_into="MERGING", gates_cleared=["SPEC", "IMPLEMENT", "TEST", "REVIEW", "MERGING"]
        )


def test_resume_into_non_side_effecting_phase_allowed():
    # Resuming into IMPLEMENT (not cleared, not side-effecting) is fine.
    self_heal.guard_resume_phase(resume_into="IMPLEMENT", gates_cleared=["SPEC"])
