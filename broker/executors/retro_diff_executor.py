# ABOUTME: WALL 2 of the crown self-modification guard (P5 design v2-C1, the P0 fix).
# ABOUTME: The broker executor that applies an owner-approved retro_diff held action.
# ABOUTME: BEFORE writing a single byte it (1) recomputes sha256 over the payload diff
# ABOUTME: and rejects on mismatch (TOCTOU close), (2) RE-CHECKS the ring via
# ABOUTME: merge_gate.check_ring over the EXACT bytes, fail-closed, then (3) git-applies.
"""
Retro-diff apply executor — the second, independent ring wall (design v2-C1).

Design authoritative source: docs/plans/harness/fable/p5/P5-design.md §4.1 step 5
+ Revision v2 (v2-C1 apply door, v2-C3 hash-pin).

Why this exists: a ``retro_diff`` held action, once the owner taps Approve on
Telegram, is executed via the broker's human-tap ``_rpc_approve`` path — which
resolves the executor by row type and runs it with NO server-side ring re-check
(unlike ``_rpc_auto_merge`` for ``type=="merge"`` cards). So the factory-side
propose-gate (``retro_ring_gate.check_retro_diff``) would be the SOLE wall unless
this executor re-checks. This executor IS that second wall: even if the
propose-gate were bypassed, mutated, or buggy, an owner-approved apply never
touches a ring path.

The executor performs, IN THIS ORDER, before it writes a single byte to disk:

    1. VERIFY BYTES   recompute sha256 over the exact payload diff bytes; a
                      mismatch with payload.diff_sha256 → rejected (no apply).
                      This closes the propose/apply TOCTOU (v2-C3): the gated
                      artifact and the applied artifact are provably identical.
    2. RING RE-CHECK  broker.merge_gate.check_ring(diff, worktree_root=<worktree>)
                      over the EXACT bytes about to be applied. RingViolation →
                      reject, apply NOTHING. Reuses the broker's OWN
                      BROKER_RING_PATHS superset — imports nothing from factory,
                      preserving the factory→broker layering exactly as
                      merge_gate does.
    3. FAIL-CLOSED    any exception, unreadable/empty diff, or path escape →
                      check_ring already raises → reject. Never apply on a check
                      that could not complete.
    4. APPLY          only now ``git apply`` the verified, ring-clean diff.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Any, Callable, Dict, List, Optional

from broker.merge_gate import RingViolation, check_ring

# The git runner: run a git subcommand in a cwd, return the CompletedProcess.
# Injectable for hermetic tests; production uses the real `git`. The apply step
# is the ONLY subprocess this executor runs.
GitRunner = Callable[[List[str], str], subprocess.CompletedProcess]


def _default_git_runner(args: List[str], cwd: str) -> subprocess.CompletedProcess:
    """
    Purpose: run one git subcommand in ``cwd`` and return the CompletedProcess.
    Usage: proc = _default_git_runner(["apply", "--", patch], worktree)
    Gotchas: never raises on a non-zero git exit — the caller inspects returncode
    so a failed ``git apply`` is reported as an error, not an exception.
    """
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def build_retro_diff_executor(
    *,
    git_runner: Optional[GitRunner] = None,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """
    Build the retro_diff apply executor (design v2-C1) — WALL 2 of the crown guard.

    Purpose: construct the callable the broker runs on owner approval of a held
    ``retro_diff`` (or ``skill_diff``) action. It hash-verifies, ring-re-checks,
    and only then git-applies the diff — refusing (apply NOTHING) on a hash
    mismatch, a ring hit, or any error.
    Usage: executor = build_retro_diff_executor(); executor(row).
    Gotchas:
      * The order is load-bearing: hash-verify → ring re-check → apply. The ring
        re-check runs over the EXACT bytes the apply will use, AFTER the hash
        confirms they match what was gated at propose. Removing the ``check_ring``
        call makes the apply-door crown test (RT-3(b)) leak; removing the hash
        recompute makes the TOCTOU test (RT-7) leak.
      * ``git_runner`` is injectable ONLY for hermetic tests; the git-apply is the
        sole subprocess. The hash + ring checks run in-process, no subprocess.
      * A malformed/non-JSON payload returns a clear error, never a partial apply.
    """
    _git = git_runner or _default_git_runner

    def _execute(row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply one owner-approved retro_diff under the two-check apply wall.

        Purpose: the crown apply door. Verifies the hash-pin, re-checks the ring,
        then git-applies — refusing on any failure with a reason, applying nothing.
        Usage: called by the broker's ApprovalAuthority after nonce validation.
        Gotchas: returns a result dict with ``status`` of 'applied' | 'rejected' |
        'error'; 'rejected' (ring / diff_hash_mismatch) is a NORMAL fail-closed
        outcome, NOT an exception. NEVER applies on a rejected path — the worktree
        is left byte-identical.
        """
        payload = row.get("payload") or "{}"
        try:
            spec = json.loads(payload) if isinstance(payload, str) else dict(payload)
        except (json.JSONDecodeError, TypeError):
            return {"status": "error", "error": "retro_diff payload is not valid JSON"}

        diff = spec.get("diff")
        pinned_sha = spec.get("diff_sha256")
        worktree = spec.get("worktree") or spec.get("worktree_path")

        if not diff or not worktree:
            return {"status": "error", "error": "retro_diff payload missing diff/worktree"}

        # 1. VERIFY BYTES — the applied artifact must equal the gated artifact
        #    (v2-C3 TOCTOU close). Reject on mismatch BEFORE any ring check or apply.
        actual_sha = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        if pinned_sha is None or actual_sha != pinned_sha:
            return {
                "status": "rejected",
                "reason": "diff_hash_mismatch",
                "detail": f"expected {pinned_sha!r}, got {actual_sha!r}",
            }

        # 2. RING RE-CHECK over the EXACT verified bytes (WALL 2). Fail-closed:
        #    a ring hit, a worktree escape, or an empty/unparseable diff raises.
        try:
            check_ring(diff, worktree_root=worktree)
        except RingViolation as exc:
            return {"status": "rejected", "reason": "ring", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 — never crash the broker; fail closed.
            return {"status": "rejected", "reason": "ring_check_error", "detail": str(exc)}

        # 3. APPLY — only now, over the verified, ring-clean bytes. Use --check-free
        #    apply with an explicit stdin patch (no shell). A non-zero rc → error,
        #    no partial apply is left behind (git apply is atomic per invocation).
        try:
            proc = subprocess.run(
                ["git", "apply", "--whitespace=nowarn"],
                cwd=str(worktree),
                input=diff,
                capture_output=True,
                text=True,
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"git apply failed: {exc}"}

        if proc.returncode != 0:
            return {
                "status": "error",
                "reason": "apply_failed",
                "detail": (proc.stderr or proc.stdout)[-1000:],
            }

        return {
            "status": "applied",
            "worktree": str(worktree),
            "target_files": spec.get("target_files", []),
        }

    return _execute
