# ABOUTME: The broker's real merge executor (P2 design §4.2, task P2-g). Runs in the
# ABOUTME: broker process on approval of a held 'merge' action: acquires a per-repo
# ABOUTME: lock, fetches, rebases the job branch onto the target, RE-TESTS after the
# ABOUTME: rebase, then merges and pushes via the git_push primitive. On ANY conflict
# ABOUTME: or post-rebase test failure it returns needs_attention — it NEVER force-pushes.
"""
Merge executor — the held-for-approval merge path, under the broker.

Design authoritative source: docs/plans/harness/fable/p2/P2-design.md §4.2

The merge is a HELD action on the existing broker approval surface: the
supervisor calls ``enqueue_action(type="merge", payload=<card JSON>)``; the
safe-lane holds it (C4 empty in P1a ⇒ every merge held); the owner approves via
the nonce-gated Telegram button; only THEN does the broker run this executor.
The assistant/supervisor has no nonce, so it can never self-approve — the merge
never touches a protected branch without owner approval.

On approval, this executor performs, IN THIS ORDER, under a PER-REPO LOCK:

    acquire repo lock ─► git fetch ─► rebase branch onto base
      ├─ conflict ─────────────► release lock → needs_attention  (NEVER force)
      └─ clean ─► RE-TEST in the rebased worktree
            ├─ fail ─────────► release lock → needs_attention
            └─ pass ─► merge (--ff-only) ─► git_push primitive ─► DONE

The lock spans rebase+retest+merge+push as one critical section (review M3) so
two jobs merging the same repo serialize. Conflict / re-test fail → the result
is needs_attention; the executor NEVER passes force=True to the push primitive.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from broker.executors.git_push_executor import build_git_push_executor

# Per-repo merge locks. A merge for repo R holds _repo_lock(R) across the whole
# rebase→retest→merge→push critical section, so two 'merge' actions for the same
# repo cannot interleave (review M3). Locks are process-local: the broker is a
# single process, so an in-process lock is the correct serialization primitive.
_REPO_LOCKS: Dict[str, threading.Lock] = {}
_REPO_LOCKS_GUARD = threading.Lock()


def _repo_lock(repo: str) -> threading.Lock:
    """
    Purpose: return the singleton lock for a repo slug, creating it on first use.
    Usage: with _repo_lock(repo): <critical section>
    Gotchas: guarded by _REPO_LOCKS_GUARD so two threads racing to create the same
    repo's lock get the SAME Lock object (never two distinct locks for one repo).
    """
    with _REPO_LOCKS_GUARD:
        lock = _REPO_LOCKS.get(repo)
        if lock is None:
            lock = threading.Lock()
            _REPO_LOCKS[repo] = lock
        return lock


# The test runner: given a worktree path, return (passed: bool, output: str).
# Defaults to running the repo's own suite via scripts/run_tests.sh when present,
# else `pytest`. Injectable so tests exercise the merge sequence without a real
# multi-minute suite (the SUBPROCESS BOUNDARY is the only thing stubbed).
TestRunner = Callable[[str], "tuple[bool, str]"]

# The git runner: run a git subcommand in a cwd, return the CompletedProcess.
# Injectable for hermetic tests; production uses the real `git`.
GitRunner = Callable[[List[str], str], subprocess.CompletedProcess]


def _default_git_runner(args: List[str], cwd: str) -> subprocess.CompletedProcess:
    """
    Purpose: run one git subcommand in ``cwd`` and return the CompletedProcess.
    Usage: proc = _default_git_runner(["fetch", "origin"], worktree)
    Gotchas: never raises on a non-zero git exit — the caller inspects returncode
    so a rebase conflict (rc!=0) is handled as needs_attention, not an exception.
    """
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True
    )


def _default_test_runner(worktree: str) -> "tuple[bool, str]":
    """
    Purpose: run the repo's own test suite in the rebased worktree (design §4.2
    RE-TEST step). Prefers scripts/run_tests.sh (the canonical hermes runner),
    falls back to `pytest -q`.
    Usage: passed, output = _default_test_runner(worktree)
    Gotchas: returns (False, output) on any non-zero exit; the merge path treats a
    False here as post-rebase re-test failure → needs_attention (no merge).
    """
    runner = os.path.join(worktree, "scripts", "run_tests.sh")
    if os.path.exists(runner):
        cmd = ["bash", runner]
    else:
        cmd = ["pytest", "-q"]
    proc = subprocess.run(cmd, cwd=worktree, capture_output=True, text=True)
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-4000:]


def build_merge_executor(
    egress_cred: Optional[Callable[[str], Optional[str]]] = None,
    *,
    git_runner: Optional[GitRunner] = None,
    test_runner: Optional[TestRunner] = None,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """
    Build the real merge executor (design §4.2) — the P2-g seam fill.

    Purpose: construct the callable the broker runs on approval of a 'merge' held
    action. It holds a per-repo lock and performs fetch → rebase → re-test →
    merge → push, returning needs_attention on any conflict or re-test failure.
    Usage: executor = build_merge_executor(creds.egress_cred); executor(row).
    Gotchas:
      * The push half REUSES build_git_push_executor (the broker holds the token);
        this executor NEVER sets force=True — a conflict is needs_attention, never
        a force-push (design §4.2 / plan §2.4).
      * git_runner / test_runner are injectable ONLY for hermetic tests; the
        SUBPROCESS boundary is the sole stub — the lock, ordering, conflict
        handling, and no-force guarantee are exercised for real.
      * The row payload is the merge card JSON: {repo, branch, base, worktree,
        remote?, policy?}. A malformed payload returns a clear error, never a
        partial merge.
    """
    push = build_git_push_executor(egress_cred)
    _git = git_runner or _default_git_runner
    _test = test_runner or _default_test_runner

    def _execute(row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform one held-for-approval merge under the per-repo lock (design §4.2).

        Purpose: the approved-merge critical section — rebase, re-test, merge,
        push, all serialized per repo; conflict / re-test fail → needs_attention.
        Usage: called by the broker's ApprovalAuthority after nonce validation.
        Gotchas: returns a result dict with a ``status`` of 'merged' |
        'needs_attention' | 'error'; needs_attention is a NORMAL outcome (conflict
        or re-test fail), NOT an exception — the supervisor parks the job on it.
        NEVER force-pushes: every needs_attention path releases the lock and
        returns without touching the protected branch.
        """
        payload = row.get("payload") or "{}"
        try:
            card = json.loads(payload) if isinstance(payload, str) else dict(payload)
        except (json.JSONDecodeError, TypeError):
            return {"status": "error", "error": "merge payload is not valid JSON"}

        repo = card.get("repo")
        branch = card.get("branch")
        base = card.get("base") or card.get("base_branch") or "main"
        worktree = card.get("worktree") or card.get("worktree_path")
        remote = card.get("remote") or "origin"

        missing = [k for k, v in (("repo", repo), ("branch", branch), ("worktree", worktree)) if not v]
        if missing:
            return {
                "status": "error",
                "error": f"merge card missing required fields: {missing}",
            }

        # The whole rebase→retest→merge→push is ONE critical section per repo
        # (review M3): two jobs merging the same repo serialize here.
        lock = _repo_lock(repo)
        with lock:
            # 1. Fetch the latest base so the rebase is onto current target.
            fetch = _git(["fetch", remote], worktree)
            if fetch.returncode != 0:
                return {
                    "status": "needs_attention",
                    "reason": "fetch_failed",
                    "detail": (fetch.stderr or fetch.stdout)[-1000:],
                }

            # 2. Rebase the job branch onto the base. A conflict → needs_attention;
            #    we ABORT the rebase to leave the worktree clean and NEVER force.
            rebase = _git(["rebase", base], worktree)
            if rebase.returncode != 0:
                # Abort so the worktree is not left mid-rebase; ignore abort rc.
                _git(["rebase", "--abort"], worktree)
                return {
                    "status": "needs_attention",
                    "reason": "rebase_conflict",
                    "detail": (rebase.stderr or rebase.stdout)[-1000:],
                    "forced": False,  # explicit: we did NOT force
                }

            # 3. RE-TEST in the rebased worktree (design §4.2). A post-rebase
            #    failure → needs_attention: the rebase may have surfaced a
            #    semantic conflict the merge would otherwise hide.
            passed, test_output = _test(worktree)
            if not passed:
                return {
                    "status": "needs_attention",
                    "reason": "post_rebase_test_fail",
                    "detail": test_output[-1000:],
                    "forced": False,
                }

            # 4. Merge the rebased branch into the base with --ff-only. After a
            #    clean rebase this fast-forwards; if it cannot ff (someone moved
            #    base underneath us) → needs_attention, still never a force.
            #
            #    IMPORTANT: check the checkout return code explicitly. git refuses
            #    `git checkout <base>` when <base> is already checked out in a
            #    linked worktree (the primary tree), returning rc!=0 with a fatal
            #    message. If we ignore rc here we proceed to merge+push while HEAD
            #    is still on the job branch — producing a false 'merged' that never
            #    moved the protected ref. Any non-zero rc here is needs_attention.
            checkout = _git(["checkout", base], worktree)
            if checkout.returncode != 0:
                return {
                    "status": "needs_attention",
                    "reason": "checkout_failed",
                    "detail": (checkout.stderr or checkout.stdout)[-1000:],
                    "command": f"git checkout {base}",
                    "forced": False,
                }
            merge = _git(["merge", "--ff-only", branch], worktree)
            if merge.returncode != 0:
                return {
                    "status": "needs_attention",
                    "reason": "merge_not_fast_forward",
                    "detail": (merge.stderr or merge.stdout)[-1000:],
                    "forced": False,
                }

            # 5. Push the merged base via the broker's git_push primitive. force
            #    is NEVER set — a merge that cannot push cleanly is needs_attention,
            #    not a force-push.
            push_result = push(
                {
                    "payload": json.dumps(
                        {"repo": worktree, "remote": remote, "ref": base, "force": False}
                    )
                }
            )
            if not push_result.get("success"):
                return {
                    "status": "needs_attention",
                    "reason": "push_failed",
                    "detail": push_result,
                    "forced": False,
                }

            return {
                "status": "merged",
                "repo": repo,
                "branch": branch,
                "base": base,
                "push": push_result,
                "forced": False,
            }

    return _execute
