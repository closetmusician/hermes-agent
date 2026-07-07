# ABOUTME: WALL 1 of the crown self-modification guard (P5 design §4.2, closes review
# ABOUTME: V3 + RT-1/3-propose). check_retro_diff() is a thin, fail-closed wrapper over
# ABOUTME: immutable_ring.check_diff: a retro-proposed diff touching ANY ring path is
# ABOUTME: REJECTED at propose time — before it can ever become an owner-approved held
# ABOUTME: action. This module is ITSELF a RING_PATHS member so a retro cannot edit it.
"""
Retro ring propose-gate — the wall against the retro weakening its own guardrails.

Design authoritative source: docs/plans/harness/fable/p5/P5-design.md §4.2 + v2-C2.

The crown risk P5 introduces: the nightly retro reads failures and proposes a git
diff editing skills / prompts / non-ring policy docs. A degenerate or
prompt-poisoned retro that proposes weakening the broker, a cost stop, the trust
policy, or the never-graduates list would be self-modification escaping its own
guardrails. This gate is WALL 1 of TWO: it routes every retro-produced diff
through the IDENTICAL hardened ``immutable_ring.check_diff`` the worker submission
path passes, BEFORE the diff can become a held action the owner ever sees.

Why reuse check_diff verbatim rather than re-implement: it already canonicalizes
paths (realpath, rename old+new, symlink target, ``..`` traversal) and fails
CLOSED on an un-parseable/empty entry. A retro that tries ``../../broker/x.py``, a
rename into ``broker/``, or a symlink onto ``trust-policy.md`` trips the identical
hardened checks — no second, drift-prone denylist.

WALL 2 (the apply door) lives in broker/executors/retro_diff_executor.py, which
re-checks the ring over the exact hash-pinned bytes after the owner tap, before
git-apply. Two independent fail-closed walls over the same bytes.

Self-protection: ``factory/retro_ring_gate.py`` is added to
``immutable_ring.RING_PATHS`` (and, for AT-RING-2 superset parity,
``broker.merge_gate.BROKER_RING_PATHS``), mirroring how ``immutable_ring.py``
protects its own source — so a retro cannot propose editing this wrapper either.
"""

from __future__ import annotations

from factory.immutable_ring import RingViolation, check_diff


class RetroRingRejected(Exception):
    """
    Raised when a retro-proposed diff touches (or resolves onto) a ring path,
    or when the proposed diff is empty/unparseable (fail-closed).

    Purpose: signal the retro orchestrator to DROP the proposal — produce no held
    action, log the rejection to forensics — before the owner ever sees it.
    Usage: check_retro_diff(diff, worktree_root=wt) raises this on any ring hit.
    Gotchas: this is fail-closed — an empty or un-parseable proposed diff also
    raises, so a malformed proposal is never treated as "no ring paths, allow".
    """


def check_retro_diff(diff: str, *, worktree_root: str) -> None:
    """
    Reject a retro-proposed diff that touches any ring path (the propose gate).

    Purpose: WALL 1 of the crown self-modification guard. Delegates to the
    identical ``immutable_ring.check_diff`` the worker path passes; on any
    ``RingViolation`` (a ring-path hit, a worktree escape, or an un-parseable
    entry — all fail-closed) it re-raises as ``RetroRingRejected`` so the retro
    orchestrator drops the proposal BEFORE ``enqueue_action``. An empty diff (no
    proposal at all) is also rejected — there is nothing to hold, and treating an
    empty diff as "clean, enqueue" would be a fail-open hole.

    Usage:
        check_retro_diff(proposed_diff, worktree_root=scratch_worktree)
        # raises RetroRingRejected → retro produces NO held action for this diff.

    Gotchas:
      * Order is the contract: the retro MUST call this BEFORE enqueue_action,
        never after — a ring proposal must never become a held action, even
        transiently (RT-6 asserts the ordering).
      * The whole diff is rejected if ANY hunk is ring-touching — no "strip the
        bad hunk and enqueue the rest" (a partial-apply is an evasion surface).
      * Removing the ``check_diff`` delegation makes the crown RT-3(a) test leak.
    """
    if not diff or not diff.strip():
        # Fail-closed: an empty proposal is not "clean" — there is nothing to hold.
        raise RetroRingRejected("empty proposed diff (fail-closed)")
    try:
        check_diff(diff, worktree_root=worktree_root)
    except RingViolation as exc:
        raise RetroRingRejected(str(exc)) from exc
