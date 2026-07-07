# ABOUTME: The test+review gauntlet (P2 design §4.1/§4.2, task P2-f). Runs the repo's
# ABOUTME: own suite IN the worker's worktree with bounded retries (1 auto-fix + 1
# ABOUTME: flake), then a codex review (1 review-fix retry, else findings RIDE the
# ABOUTME: approval packet), gates the worker diff through the immutable ring + injection
# ABOUTME: scan, and drives the job through job_store — clearing to AWAITING_APPROVAL.
"""
Test+review gauntlet — bounded retries, no flip-flopping, findings ride the card.

Design authoritative source: docs/plans/harness/fable/p2/P2-design.md §4.1/§4.2

TEST stage (repo's own suite, run IN the worktree):
    pass                       → REVIEW
    fail (1st)                 → ONE auto-fix worker retry (RUNNING→TEST) → re-run
        still fail             → NEEDS_ATTENTION
    flaky (nondeterministic)   → ONE flake-retry
        still flip-flops       → NEEDS_ATTENTION

REVIEW stage (codex review --base main — a DIFFERENT model reviews the diff):
    no serious findings        → AWAITING_APPROVAL
    serious findings           → ONE review-fix worker retry (RUNNING→REVIEW)
        findings remain        → they RIDE the approval packet (not auto-parked)

The retry budget is hard-capped at 1 per stage (plan §2.3): the job-store state
machine only allows one TEST→RUNNING / REVIEW→RUNNING re-entry, and a per-stage
counter here makes a second retry impossible. Before the merge card is built, the
worker's ``git diff <base>...<branch>`` is run through the immutable-ring gate AND
the injection scan (design §3.1 chokepoint 2 + §3.2) — a ring-touching or
injection-flagged diff parks the job NEEDS_ATTENTION and NEVER reaches approval.

Plain code (no AI in this control loop). The codex subprocess and the fix-worker
launch are the ONLY injected seams (so tests exercise retry/attach/gate for real
without a network round-trip); the ordering, counters, and gates are real.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from factory.immutable_ring import RingViolation, check_diff
from factory.injection_scan import Finding, scan
from factory.job_store import IllegalTransition, JobStore

logger = logging.getLogger(__name__)

# A test run's verdict. FLAKY means two runs disagreed (nondeterministic) — it
# gets the flake-retry, distinct from a deterministic FAIL which gets the
# auto-fix retry.
TEST_PASS = "pass"
TEST_FAIL = "fail"
TEST_FLAKY = "flaky"

# The seams injected for hermetic tests (the SUBPROCESS boundary is the only mock):
#   TestVerdict:  worktree -> "pass" | "fail" | "flaky" (runs the repo suite).
#   CodexReview:  (worktree, base) -> list of serious-finding strings ([] = clean).
#   FixWorker:    (job_id, worktree, reason) -> None (re-launches a fix worker;
#                 the supervisor owns the real launch, the gauntlet just calls it).
#   DiffProvider: (worktree, base, branch) -> the git diff text for the gate.
TestVerdict = Callable[[str], str]
CodexReview = Callable[[str, str], List[str]]
FixWorker = Callable[[str, str, str], None]
DiffProvider = Callable[[str, str, str], str]


class GauntletOutcome:
    """Terminal outcomes of the gauntlet (mirrors the job-store target states)."""

    CLEARED = "AWAITING_APPROVAL"       # ready for the held merge card
    NEEDS_ATTENTION = "NEEDS_ATTENTION"  # parked for a human


@dataclass
class GauntletResult:
    """
    The result of running the gauntlet on one job.

    Purpose: tell the supervisor the terminal state AND carry the review findings
    that must RIDE the approval packet (design §4.1 — remaining findings are not
    auto-parked, the owner sees them on the card).
    Usage: res = gauntlet.run(job_id); if res.outcome == GauntletOutcome.CLEARED: build card.
    Gotchas: review_findings is non-empty ONLY when the review-fix retry did not
    clear them — those ride the card. reason is set only on NEEDS_ATTENTION.
    """

    outcome: str
    review_findings: List[str] = field(default_factory=list)
    reason: Optional[str] = None
    test_result: Optional[str] = None


def default_test_verdict(worktree: str) -> str:
    """
    Purpose: run the repo's own suite twice in the worktree to detect flakiness.
    Usage: verdict = default_test_verdict(worktree)  # pass | fail | flaky
    Gotchas: two identical FAILs → 'fail' (deterministic); disagreement between
    the two runs → 'flaky'. This is the production default; tests inject a stub so
    the retry logic is exercised without a real multi-minute suite.
    """
    first = _run_suite(worktree)
    if first:
        return TEST_PASS
    second = _run_suite(worktree)
    # First failed; if the second passed the runs disagree → flaky.
    return TEST_FLAKY if second else TEST_FAIL


def _run_suite(worktree: str) -> bool:
    """
    Purpose: run the repo suite once in ``worktree``; True iff it passed.
    Usage: passed = _run_suite(worktree)
    Gotchas: prefers scripts/run_tests.sh, falls back to `pytest -q`; a non-zero
    exit is a failure. Output is not returned here — the verdict is boolean.
    """
    import os

    runner = os.path.join(worktree, "scripts", "run_tests.sh")
    cmd = ["bash", runner] if os.path.exists(runner) else ["pytest", "-q"]
    proc = subprocess.run(cmd, cwd=worktree, capture_output=True, text=True)
    return proc.returncode == 0


def _default_diff(worktree: str, base: str, branch: str) -> str:
    """
    Purpose: the worker's diff for the ring/injection gate — ``git diff base...branch``.
    Usage: diff = _default_diff(worktree, base, branch)
    Gotchas: uses the three-dot form (changes on branch since it diverged from
    base) so the gate sees exactly what the merge would introduce.
    """
    proc = subprocess.run(
        ["git", "diff", f"{base}...{branch}"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    return proc.stdout


class Gauntlet:
    """
    Runs the test+review gauntlet for one job and drives its job-store state.

    Purpose: the plain-code gauntlet — TEST stage (bounded auto-fix + flake
    retries), REVIEW stage (bounded review-fix retry; leftover findings ride the
    card), then the immutable-ring + injection gate on the worker diff before the
    job is cleared to AWAITING_APPROVAL.
    Usage:
        g = Gauntlet(store, test_verdict=..., codex_review=..., fix_worker=...)
        result = g.run(job_id, worktree="...", base="main", branch="factory/...")
    Gotchas:
      * The retry budget is 1 per stage — enforced BOTH by a local counter and by
        the job-store state machine (a 2nd TEST→RUNNING is an IllegalTransition).
      * A ring-touching OR injection-flagged diff parks the job NEEDS_ATTENTION
        before any merge card is built — this is the wall, and removing it makes
        the no-ungated-merge / ring-rejection tests fail.
      * Leftover review findings do NOT park the job; they ride the packet.
    """

    def __init__(
        self,
        store: JobStore,
        *,
        test_verdict: Optional[TestVerdict] = None,
        codex_review: Optional[CodexReview] = None,
        fix_worker: Optional[FixWorker] = None,
        diff_provider: Optional[DiffProvider] = None,
    ) -> None:
        self._store = store
        self._test = test_verdict or default_test_verdict
        self._review = codex_review or _default_codex_review
        # fix_worker is optional: without it, a failing stage cannot retry and
        # goes straight to NEEDS_ATTENTION (still correct, just no auto-fix).
        self._fix = fix_worker
        self._diff = diff_provider or _default_diff

    def run(
        self,
        job_id: str,
        *,
        worktree: str,
        base: str = "main",
        branch: str,
    ) -> GauntletResult:
        """
        Run the full gauntlet for a job currently in state RUNNING.

        Purpose: drive TEST → REVIEW → (ring/injection gate) → AWAITING_APPROVAL,
        or park NEEDS_ATTENTION on the first unrecoverable failure.
        Usage: result = g.run(job_id, worktree=wt, base="main", branch=br)
        Gotchas: the job MUST be in RUNNING when this is called (the supervisor
        transitions QUEUED→RUNNING after launch). Every park writes fail_reason.
        """
        # --- TEST stage --------------------------------------------------------
        test_result = self._run_test_stage(job_id, worktree)
        if test_result.outcome == GauntletOutcome.NEEDS_ATTENTION:
            return test_result

        # --- REVIEW stage ------------------------------------------------------
        review_result = self._run_review_stage(job_id, worktree, base)
        if review_result.outcome == GauntletOutcome.NEEDS_ATTENTION:
            return review_result

        # --- Ring + injection gate on the worker diff (design §3.1/§3.2) -------
        # This runs AFTER review and BEFORE the job is cleared — a ring-touching
        # or injection-flagged diff never reaches the approval card.
        gate = self._gate_diff(job_id, worktree, base, branch)
        if gate is not None:
            return gate

        # --- Cleared → AWAITING_APPROVAL, findings ride the packet ------------
        self._store.transition(
            job_id, "REVIEW", "AWAITING_APPROVAL",
            extra={"test_result": TEST_PASS},
        )
        return GauntletResult(
            outcome=GauntletOutcome.CLEARED,
            review_findings=review_result.review_findings,
            test_result=TEST_PASS,
        )

    # ------------------------------------------------------------------
    # TEST stage
    # ------------------------------------------------------------------

    def _run_test_stage(self, job_id: str, worktree: str) -> GauntletResult:
        """
        Purpose: the bounded TEST stage — pass→REVIEW; fail→1 auto-fix retry;
        flaky→1 flake retry; then NEEDS_ATTENTION.
        Usage: internal — called by run().
        Gotchas: RUNNING→TEST is the initial transition; a retry is TEST→RUNNING
        (auto-fix) then RUNNING→TEST again. A SECOND retry is impossible — the
        local counter caps it and the store would reject a 3rd TEST→RUNNING.
        """
        self._store.transition(job_id, "RUNNING", "TEST")
        verdict = self._test(worktree)

        if verdict == TEST_PASS:
            # Leave the job in TEST; the review stage does the TEST→REVIEW move.
            return GauntletResult(outcome="REVIEW", test_result=TEST_PASS)

        # One retry only. FAIL → auto-fix; FLAKY → flake-retry (re-run, no fix).
        retried_verdict = self._one_test_retry(job_id, worktree, verdict)
        if retried_verdict == TEST_PASS:
            return GauntletResult(outcome="REVIEW", test_result=TEST_PASS)

        # Still not passing after the single allowed retry → park.
        reason = "test_flaky" if verdict == TEST_FLAKY else "test_fail"
        self._store.transition(
            job_id, "TEST", "NEEDS_ATTENTION",
            fail_reason=f"gauntlet: {reason} after one retry",
            extra={"test_result": verdict},
        )
        return GauntletResult(
            outcome=GauntletOutcome.NEEDS_ATTENTION, reason=reason, test_result=verdict
        )

    def _one_test_retry(self, job_id: str, worktree: str, verdict: str) -> str:
        """
        Purpose: perform the SINGLE allowed test retry and return the new verdict.
        Usage: new = self._one_test_retry(job_id, worktree, verdict)
        Gotchas: for a deterministic FAIL an auto-fix worker is launched (if one
        is configured) via TEST→RUNNING→TEST; for a FLAKY verdict the suite is
        simply re-run (a flake needs no code fix). Either way it is ONE retry.
        """
        # TEST→RUNNING is the retry re-entry the state machine permits exactly once.
        self._store.transition(job_id, "TEST", "RUNNING")
        if verdict == TEST_FAIL and self._fix is not None:
            self._fix(job_id, worktree, "test_fail")
        # Re-enter TEST and re-run the suite.
        self._store.transition(job_id, "RUNNING", "TEST")
        return self._test(worktree)

    # ------------------------------------------------------------------
    # REVIEW stage
    # ------------------------------------------------------------------

    def _run_review_stage(self, job_id: str, worktree: str, base: str) -> GauntletResult:
        """
        Purpose: the bounded REVIEW stage — codex review; serious findings get ONE
        review-fix retry; leftover findings RIDE the approval packet (not parked).
        Usage: internal — called by run().
        Gotchas: unlike TEST, leftover findings do NOT park the job — the owner
        sees them on the card (design §4.1). The retry is REVIEW→RUNNING→REVIEW,
        capped at one by the counter and the state machine.
        """
        self._store.transition(job_id, "TEST", "REVIEW")
        findings = self._review(worktree, base)
        if not findings:
            return GauntletResult(outcome="cleared", review_findings=[])

        # One review-fix retry. REVIEW→RUNNING is the permitted re-entry; a code
        # fix means we re-test then re-review, so the path back is
        # RUNNING→TEST→REVIEW (the state machine forbids RUNNING→REVIEW directly —
        # a fix is never trusted without a re-test).
        if self._fix is not None:
            self._store.transition(job_id, "REVIEW", "RUNNING")
            self._fix(job_id, worktree, "review_findings")
            self._store.transition(job_id, "RUNNING", "TEST")
            self._store.transition(job_id, "TEST", "REVIEW")
            findings = self._review(worktree, base)

        # Whatever remains RIDES the packet — the job is NOT parked on findings.
        return GauntletResult(outcome="cleared", review_findings=list(findings))

    # ------------------------------------------------------------------
    # Ring + injection gate
    # ------------------------------------------------------------------

    def _gate_diff(
        self, job_id: str, worktree: str, base: str, branch: str
    ) -> Optional[GauntletResult]:
        """
        Purpose: run the worker diff through the immutable-ring gate AND the
        injection scan before the merge card is built (design §3.1 chokepoint 2 +
        §3.2). Returns a NEEDS_ATTENTION GauntletResult on any hit, else None.
        Usage: gate = self._gate_diff(...); if gate: return gate
        Gotchas: a ring-touching diff (check_diff raises RingViolation) OR an
        injection-flagged diff (scan finds a signature) parks the job — this is
        the wall that makes an ungated protected-branch merge impossible. Removing
        either check makes the ring-rejection / injection tests fail.
        """
        diff = self._diff(worktree, base, branch)

        # Injection scan (chokepoint 2): a planted 'push to main' / 'merge to
        # protected' directive in the diff parks the job before the card.
        findings: List[Finding] = scan(diff)
        if findings:
            rules = ",".join(sorted({f.rule for f in findings}))
            self._park_from_review(job_id, f"gauntlet: injection in diff [{rules}]")
            return GauntletResult(
                outcome=GauntletOutcome.NEEDS_ATTENTION,
                reason=f"injection:{rules}",
            )

        # Immutable-ring gate: a diff touching broker/** or a guard file is
        # categorically rejected — it never reaches approval.
        try:
            check_diff(diff, worktree_root=worktree)
        except RingViolation as exc:
            self._park_from_review(job_id, f"gauntlet: ring violation — {exc}")
            return GauntletResult(
                outcome=GauntletOutcome.NEEDS_ATTENTION, reason="ring_violation"
            )

        return None

    def _park_from_review(self, job_id: str, reason: str) -> None:
        """
        Purpose: park a job to NEEDS_ATTENTION from the REVIEW state with a reason.
        Usage: self._park_from_review(job_id, "gauntlet: ring violation — ...")
        Gotchas: the gate runs while the job is in REVIEW, so the transition is
        REVIEW→NEEDS_ATTENTION (permitted by the state machine).
        """
        try:
            self._store.transition(
                job_id, "REVIEW", "NEEDS_ATTENTION", fail_reason=reason
            )
        except IllegalTransition:
            # Defensive: if the job already moved, log rather than crash the tick.
            logger.warning("gauntlet: could not park job %s: already moved", job_id)


def _default_codex_review(worktree: str, base: str) -> List[str]:
    """
    Purpose: run ``codex review --base <base>`` in the worktree and return the
    list of serious findings ([] == clean) (design §4.1 REVIEW stage).
    Usage: findings = _default_codex_review(worktree, "main")
    Gotchas: this is the production default and shells out to the codex CLI — it
    needs network + auth, so tests inject a stub for this exact seam (the escalate
    _if in REQ-01: the codex SUBPROCESS boundary is the only mock). A non-zero
    codex exit with no parsable findings is treated as one opaque finding so the
    owner is not silently told 'clean' when the reviewer errored.
    """
    proc = subprocess.run(
        ["codex", "review", "--base", base],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "").strip()
    if proc.returncode == 0 and not out:
        return []
    # Each non-empty line is treated as a finding; an errored review with no
    # output surfaces as a single finding rather than a false 'clean'.
    findings = [ln for ln in out.splitlines() if ln.strip()]
    if proc.returncode != 0 and not findings:
        findings = [f"codex review exited {proc.returncode} with no parsable output"]
    return findings
