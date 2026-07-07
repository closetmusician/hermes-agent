# ABOUTME: Regression sentinel for the Fable factory — re-tests main immediately
# ABOUTME: after a merge and on a break: opens a revert held action (broker_client),
# ABOUTME: pauses all branched tasks, and writes a 'reverted' trust-ledger outcome.
# ABOUTME: ENFORCEMENT, not advisory: a test that only logs MUST FAIL (anti-weakening).
# ABOUTME: Design source: docs/plans/harness/fable/p5/P5-design.md §5.2
"""
Regression sentinel (P5-d §5.2) — post-merge regression detection and enforcement.

Invoked immediately after a merge (in the broker's post-merge / supervisor MERGING→DONE
callback), NOT on a slow poll.  On a break it:
  (a) enqueues a revert held action via broker_client → owner-approved (never auto-push);
  (b) pauses every QUEUED/ADMITTED job whose base_branch == the suspect main sha;
  (c) writes a 'reverted' trust-ledger outcome for the merged job.

A test must assert (a) revert action enqueued AND (b) branched tasks paused AND
(c) ledger row written — any downgrade to log-only MUST make the test fail.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from factory.job_store import JobStore
from factory.trust_ledger import TrustLedger

logger = logging.getLogger(__name__)

# States that represent "branched from main and now at risk" when main breaks.
_SUSPECT_STATES = ("QUEUED", "ADMITTED", "RUNNING", "TEST", "REVIEW")


def _run_test_suite(
    repo_path: str,
    test_cmd: Optional[List[str]] = None,
    timeout_s: int = 300,
) -> bool:
    """
    Purpose: run the repo's test suite at HEAD and return True if it passes.
    Usage: ok = _run_test_suite("/path/to/repo")
    Gotchas: returns False on any subprocess error, timeout, or non-zero exit code.
    The test_cmd is injectable for unit tests (default: ["python", "-m", "pytest"]).
    """
    cmd = test_cmd or ["python", "-m", "pytest", "--tb=short", "-q"]
    try:
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return False


class RegressionSentinel:
    """
    Purpose: post-merge regression detector.  Called immediately after each merge.
    Re-tests main; on a break → revert held action + pause branched tasks + trust feedback.

    Usage:
        sentinel = RegressionSentinel(
            store=store, ledger=ledger, broker_client=client, repo_path="/path"
        )
        # Call this in the post-merge hook (MERGING→DONE transition):
        result = sentinel.on_merge(
            merge_sha="abc123", merged_job_id="job-ulid", repo="my-repo"
        )

    Gotchas:
      * broker_client is injectable (tests use a mock that records calls).
      * test_fn is injectable for unit tests (avoids spawning real pytest).
      * The revert held action is type='merge' with spec='revert merge <sha>' —
        owner-approved, never auto-push (matches never-graduates.md policy).
      * Branched tasks are paused to PAUSED_DRIFT (the design's "hold until revert lands").
    """

    def __init__(
        self,
        *,
        store: JobStore,
        ledger: TrustLedger,
        broker_client: Any,
        repo_path: str,
        test_fn: Optional[Callable[[], bool]] = None,
    ):
        self._store = store
        self._ledger = ledger
        self._broker_client = broker_client
        self._repo_path = repo_path
        self._test_fn = test_fn  # injectable for tests

        # Heartbeat for watchdog-supervisor health checks.
        self._last_run_ts: float = 0.0

    def on_merge(
        self,
        *,
        merge_sha: str,
        merged_job_id: str,
        repo: str,
    ) -> Dict[str, Any]:
        """
        Purpose: the post-merge hook — re-test main and enforce if broken.
        Returns a dict with keys: passed (bool), action_taken (str),
        paused_jobs (list[str]), action_id (str|None).
        Usage: result = sentinel.on_merge(merge_sha=sha, merged_job_id=jid, repo="r")
        Gotchas: if the test runner raises the suite counts as failed (fail-closed).
        """
        self._last_run_ts = time.time()

        # Re-test main (use injectable test_fn in tests; real suite in production).
        if self._test_fn is not None:
            passed = self._test_fn()
        else:
            passed = _run_test_suite(self._repo_path)

        if passed:
            logger.info(
                "RegressionSentinel: post-merge tests PASSED for sha %s (job %s)",
                merge_sha, merged_job_id,
            )
            return {"passed": True, "action_taken": "none", "paused_jobs": [], "action_id": None}

        # Tests failed → enforce all three actions.
        logger.error(
            "RegressionSentinel: post-merge REGRESSION detected (sha %s, job %s)",
            merge_sha, merged_job_id,
        )

        # (a) Open a revert held action via broker_client.
        action_id = self._open_revert_action(merge_sha, merged_job_id, repo)

        # (b) Pause branched tasks (QUEUED/ADMITTED/RUNNING jobs on suspect main sha).
        paused = self._pause_branched_tasks(merge_sha)

        # (c) Write 'reverted' trust-ledger outcome.
        self._record_reverted_outcome(merged_job_id, repo)

        return {
            "passed": False,
            "action_taken": "revert_opened",
            "paused_jobs": paused,
            "action_id": action_id,
        }

    def _open_revert_action(
        self, merge_sha: str, merged_job_id: str, repo: str
    ) -> Optional[str]:
        """
        Purpose: enqueue a revert-merge held action via broker_client.
        The owner must tap Approve — this is never an auto-push (never-graduates policy).
        Usage: internal.
        Gotchas: returns the action_id string, or None if broker is unavailable (log only).
        """
        payload = json.dumps({
            "type": "revert_merge",
            "merge_sha": merge_sha,
            "merged_job_id": merged_job_id,
            "repo": repo,
            "reason": "post_merge_regression",
        })
        try:
            result = self._broker_client.enqueue_action(
                type="merge",
                summary=f"REVERT merge {merge_sha[:12]} — post-merge regression detected",
                payload=payload,
                origin="regression_sentinel",
            )
            action_id = result.get("action_id") if result else None
            logger.info(
                "RegressionSentinel: revert held-action enqueued (action_id=%s)", action_id
            )
            return action_id
        except Exception as exc:
            logger.error(
                "RegressionSentinel: failed to enqueue revert action: %s", exc
            )
            return None

    def _pause_branched_tasks(self, suspect_sha: str) -> List[str]:
        """
        Purpose: pause every QUEUED/ADMITTED/RUNNING job whose base_branch matches
        the suspect main sha, bounding the blast window.
        Usage: internal; returns list of paused job_ids.
        Gotchas: uses PAUSED_DRIFT as the park state (distinguishable in morning card).
        A job that already left RUNNING will not be caught here; that is acceptable
        (it ran on clean code or is terminal).
        """
        from factory.job_store import IllegalTransition

        paused: List[str] = []
        for state in _SUSPECT_STATES:
            for job in self._store.list_jobs(state=state):
                base = job.get("base_branch") or ""
                # Match on the SHA or a branch name that resolves to it.
                if suspect_sha in base or base == "main" or base == "origin/main":
                    try:
                        self._store.transition(
                            job["id"], state, "PAUSED_DRIFT",
                            fail_reason=f"regression_sentinel:suspect_base:{suspect_sha[:12]}",
                        )
                        paused.append(job["id"])
                        logger.info(
                            "RegressionSentinel: paused job %s (was %s, base=%r)",
                            job["id"], state, base,
                        )
                    except IllegalTransition:
                        pass  # Job moved between our query and the transition — harmless.
        return paused

    def _record_reverted_outcome(self, merged_job_id: str, repo: str) -> None:
        """
        Purpose: write a 'reverted' trust-ledger outcome for the merged job so
        compute_tier's streak logic auto-revokes the tier (trust_ledger.py:22-23).
        Usage: internal.
        Gotchas: uses record_outcome (its own connection), not record_outcome_on_conn,
        because we're not inside a supervisor co-transactional path here.
        """
        try:
            self._ledger.record_outcome(
                merged_job_id,
                repo=repo,
                task_type="feature",  # best-effort; derive_task_type not available here
                outcome="reverted",
                repo_class="personal",
                outcome_ts=int(time.time() * 1000),
                detail="post_merge_regression",
            )
            logger.info(
                "RegressionSentinel: recorded 'reverted' outcome for job %s",
                merged_job_id,
            )
        except Exception as exc:
            logger.error(
                "RegressionSentinel: failed to record reverted outcome: %s", exc
            )
