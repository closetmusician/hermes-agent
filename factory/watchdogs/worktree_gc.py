# ABOUTME: Worktree GC (P5-d §5.5) — disk pre-flight gate (disk_ok() wired into
# ABOUTME: throttled_fn) and reaping of stale/terminal-state worktrees.  Never reaps
# ABOUTME: a live-pgid worktree; always preserves the forensics bundle (out_dir).
# ABOUTME: Design source: docs/plans/harness/fable/p5/P5-design.md §5.5
"""
Worktree GC (P5-d §5.5) — two responsibilities:
  1. disk_ok() → bool: free-disk gate wired into the scheduler's throttled_fn.
     Below the floor ⇒ disk_full()=True ⇒ no new spawns.
  2. reap(): remove merged/abandoned worktrees for terminal-state jobs older than
     the retention window; never touch a live pgid; preserve forensics bundles.

Wire into the scheduler:
    gc = WorktreeGC(store=store, repo_path=repo_path, out_dir=out_dir)
    throttled_fn = lambda: mz.throttled() or gc.disk_full()
    sched = Scheduler(..., throttled_fn=throttled_fn)
    # Each tick: gc.reap()
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from factory.job_store import JobStore

logger = logging.getLogger(__name__)

# Default disk floor: 2 GB free bytes.
DISK_FLOOR_BYTES = 2 * 1024 * 1024 * 1024

# Terminal states whose worktrees may be reaped.
_TERMINAL_STATES = frozenset({"DONE", "FAILED", "NEEDS_ATTENTION", "PAUSED_DRIFT"})

# Default retention window: worktrees older than 7 days may be reaped.
RETENTION_S = 7 * 24 * 3600


def _free_bytes_default(path: str) -> int:
    """
    Purpose: return free bytes on the filesystem containing the given path.
    Usage: free = _free_bytes_default("/data")
    Gotchas: returns a very large value (maxint) on any OS error so a broken sensor
    does not block all spawns (fail-open for disk reading — the floor is a safety
    margin, not a precision gate).
    """
    try:
        stat = shutil.disk_usage(path)
        return stat.free
    except OSError:
        return 2**62


def _is_pgid_alive(pgid: Optional[int]) -> bool:
    """
    Purpose: probe whether a process-group is still alive.
    Usage: if _is_pgid_alive(job["pgid"]): skip reap.
    Gotchas: a None pgid is treated as NOT alive (no process to protect).
    """
    if pgid is None:
        return False
    try:
        os.kill(-pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _git_worktree_remove(repo_path: str, worktree_path: str) -> bool:
    """
    Purpose: call `git worktree remove --force` to remove a stale worktree.
    Usage: ok = _git_worktree_remove("/path/to/repo", "/path/to/worktree")
    Gotchas: returns False on any error; the GC continues with the next worktree.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "worktree", "remove", "--force", worktree_path],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class WorktreeGC:
    """
    Purpose: disk pre-flight gate + stale-worktree reaper for the factory fleet.
    disk_full() → True blocks new spawns when free bytes < floor.
    reap() removes terminal-state worktrees older than the retention window.

    Usage:
        gc = WorktreeGC(store=store, repo_path="/path/to/repo", out_dir=out_dir)
        throttled_fn = lambda: mz.throttled() or gc.disk_full()
        sched = Scheduler(..., throttled_fn=throttled_fn)
        # Each tick:
        gc.reap()

    Gotchas:
      * free_bytes_fn is injectable (tests inject a lambda for sub-floor simulation).
      * reap NEVER touches a worktree whose pgid is alive (live-pgid guard).
      * Forensics bundles live in out_dir (separate from the worktree) — they
        survive worktree removal (forensics.py:110 comment: "bundle lives in out_dir").
    """

    def __init__(
        self,
        *,
        store: JobStore,
        repo_path: str,
        out_dir: Path,
        disk_floor_bytes: int = DISK_FLOOR_BYTES,
        retention_s: int = RETENTION_S,
        free_bytes_fn: Optional[Callable[[str], int]] = None,
        worktree_remove_fn: Optional[Callable[[str, str], bool]] = None,
    ):
        self._store = store
        self._repo_path = repo_path
        self._out_dir = Path(out_dir)
        self._disk_floor = disk_floor_bytes
        self._retention_s = retention_s
        self._free_bytes_fn = free_bytes_fn or _free_bytes_default
        self._worktree_remove_fn = worktree_remove_fn or _git_worktree_remove

        # Heartbeat for watchdog-supervisor health checks.
        self._last_run_ts: float = 0.0

    def disk_ok(self) -> bool:
        """
        Purpose: return True if free disk bytes are at or above the floor.
        Usage: if not gc.disk_ok(): block spawns.
        Gotchas: reads disk each call; not cached.
        """
        free = self._free_bytes_fn(self._repo_path)
        return free >= self._disk_floor

    def disk_full(self) -> bool:
        """
        Purpose: the throttled_fn callable — inverse of disk_ok().
        Usage: throttled_fn = lambda: mz.throttled() or gc.disk_full()
        Gotchas: pass gc.disk_full (not gc.disk_full()) — it's a bound method.
        """
        self._last_run_ts = time.time()
        return not self.disk_ok()

    def reap(self) -> List[Dict[str, Any]]:
        """
        Purpose: scan terminal-state jobs older than retention_s and remove their
        worktrees with git worktree remove --force.  Never touches a live pgid.
        Returns a list of reap-result dicts (one per worktree acted on).
        Usage: reaped = gc.reap()  # call each scheduler tick (or less often).
        Gotchas:
          * Forensics bundles in out_dir are NOT touched — they outlive the worktree.
          * A job with no worktree_path is silently skipped.
          * A failed git-worktree-remove is logged but does not block other reaps.
        """
        self._last_run_ts = time.time()
        now = time.time()
        results: List[Dict[str, Any]] = []

        for state in _TERMINAL_STATES:
            for job in self._store.list_jobs(state=state):
                worktree = job.get("worktree_path")
                if not worktree:
                    continue

                # Age check: updated_ts is in milliseconds.
                updated_ts_ms = job.get("updated_ts") or 0
                age_s = now - (updated_ts_ms / 1000.0)
                if age_s < self._retention_s:
                    continue

                # Live-pgid guard: never reap a worktree with a live process.
                if _is_pgid_alive(job.get("pgid")):
                    logger.warning(
                        "WorktreeGC: skipping job %s — pgid %s is alive",
                        job["id"], job.get("pgid"),
                    )
                    results.append({
                        "job_id": job["id"],
                        "worktree": worktree,
                        "action": "skipped_live_pgid",
                    })
                    continue

                # Check the worktree actually exists.
                if not Path(worktree).exists():
                    results.append({
                        "job_id": job["id"],
                        "worktree": worktree,
                        "action": "already_absent",
                    })
                    continue

                ok = self._worktree_remove_fn(self._repo_path, worktree)
                if ok:
                    logger.info(
                        "WorktreeGC: reaped worktree %r for job %s (state=%s, age=%.0fs)",
                        worktree, job["id"], state, age_s,
                    )
                    results.append({
                        "job_id": job["id"],
                        "worktree": worktree,
                        "action": "reaped",
                    })
                else:
                    logger.warning(
                        "WorktreeGC: failed to reap worktree %r for job %s",
                        worktree, job["id"],
                    )
                    results.append({
                        "job_id": job["id"],
                        "worktree": worktree,
                        "action": "reap_failed",
                    })

        return results
