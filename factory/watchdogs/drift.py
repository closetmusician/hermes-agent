# ABOUTME: Drift watchdog for the Fable factory — detects and PAUSES churning workers.
# ABOUTME: Trigger set: tokens-without-progress, scope creep (diff outside footprint),
# ABOUTME: stuck-loop (repeated transcript tail), guard/config edit (ring path touched).
# ABOUTME: Action on any trigger: transition RUNNING→PAUSED_DRIFT, capture forensics,
# ABOUTME: SIGTERM the process-group (kill happens AFTER capture — ordering is the contract).
"""
Drift watchdog (P5-d §5.1) — observe RUNNING jobs each scheduler tick and PAUSE
any that exhibit drift signals.  ENFORCE: the worker process-group is SIGTERMed and
the job transitions to PAUSED_DRIFT.  A test that only verifies a log line and no
state change MUST FAIL (anti-weakening).

Trigger set (any one ⇒ pause):
  tokens-without-progress  cost_so_far_usd climbs N ticks while updated_ts/diff unchanged
  scope creep              uncommitted diff touches files outside the job's declared footprint
  stuck loop               same transcript-tail hash repeats across N ticks
  guard/config edit        worktree diff touches a ring path → immediate pause

Design authoritative source: docs/plans/harness/fable/p5/P5-design.md §5.1
"""
from __future__ import annotations

import hashlib
import logging
import os
import signal
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from factory.forensics import capture_pre_kill
from factory.immutable_ring import is_ring_path
from factory.job_store import JobStore

logger = logging.getLogger(__name__)

# How many ticks of cost-climbing-without-progress before we pause.
COST_CLIMB_TICKS = 3

# How many ticks of repeated transcript-tail hash before we pause.
STUCK_LOOP_TICKS = 3


def _git_diff_files(worktree: str) -> List[str]:
    """
    Purpose: return the list of files touched by the worktree's uncommitted diff.
    Usage: files = _git_diff_files("/path/to/worktree")
    Gotchas: returns [] on any git error or if the path does not exist — never raises.
    """
    try:
        if not Path(worktree).exists():
            return []
        proc = subprocess.run(
            ["git", "-C", worktree, "diff", "--name-only"],
            capture_output=True, text=True, timeout=10,
        )
        return [f.strip() for f in proc.stdout.splitlines() if f.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def _read_transcript_tail_hash(worktree: str) -> Optional[str]:
    """
    Purpose: hash the last 4096 bytes of the worker transcript so the stuck-loop
    detector can identify a repeating pattern across ticks without storing the full text.
    Usage: h = _read_transcript_tail_hash(worktree)  # None if file absent
    Gotchas: returns None on any read error — a missing transcript does not trip the loop check.
    """
    try:
        p = Path(worktree) / ".factory" / "worker.out"
        if not p.exists():
            return None
        data = p.read_bytes()[-4096:]
        return hashlib.sha256(data).hexdigest()
    except OSError:
        return None


class DriftWatchdog:
    """
    Purpose: scan RUNNING jobs each tick; pause any worker exhibiting drift.
    Trigger set: cost-climb-without-progress, scope creep, stuck loop, ring/guard edit.
    Action: capture_pre_kill → SIGTERM pgid → transition RUNNING→PAUSED_DRIFT.

    Usage:
        wd = DriftWatchdog(store=store, out_dir=out_dir)
        alerts = wd.tick()   # call each scheduler tick

    Gotchas:
      * kill_fn is injectable (default: os.kill) — tests replace it with a recorder.
      * The state transition IS the enforcement; a test must assert job["state"] ==
        "PAUSED_DRIFT" (not merely a log line) — otherwise the anti-weakening gate fails.
      * capture_pre_kill runs BEFORE the kill signal (ordering per forensics.py:9-13).
    """

    def __init__(
        self,
        *,
        store: JobStore,
        out_dir: Path,
        kill_fn: Callable[[int, int], None] = os.kill,
        cost_climb_ticks: int = COST_CLIMB_TICKS,
        stuck_loop_ticks: int = STUCK_LOOP_TICKS,
    ):
        self._store = store
        self._out_dir = Path(out_dir)
        self._kill_fn = kill_fn
        self._cost_climb_ticks = cost_climb_ticks
        self._stuck_loop_ticks = stuck_loop_ticks

        # Per-job tracking state (keyed by job_id):
        #   _cost_history[jid] = list of (cost_usd, updated_ts) snapshots
        #   _tail_history[jid] = list of transcript-tail hashes
        self._cost_history: Dict[str, List[tuple]] = {}
        self._tail_history: Dict[str, List[Optional[str]]] = {}

        # Heartbeat for watchdog-supervisor health checks.
        self._last_run_ts: float = 0.0

    def tick(self) -> List[Dict[str, Any]]:
        """
        Purpose: one drift-scan pass.  Iterates over RUNNING jobs; pauses any that
        trip a drift trigger.  Returns a list of drift alert dicts.
        Usage: alerts = wd.tick()  # call from the scheduler tick loop.
        Gotchas: state transitions are permanent (PAUSED_DRIFT is forward-only to
        ADMITTED or NEEDS_ATTENTION — only owner action clears it).  The kill happens
        outside the store lock; a killed worker that tries to update state after the
        transition will hit a CAS mismatch (job no longer RUNNING) and fail harmlessly.
        """
        import time
        self._last_run_ts = time.time()

        running = self._store.list_jobs(state="RUNNING")
        alerts: List[Dict[str, Any]] = []

        for job in running:
            jid = job["id"]
            trigger = self._check_job(job)
            if trigger:
                alert = self._pause_job(job, trigger)
                alerts.append(alert)
                # Clean up tracking state for this job (it's now paused).
                self._cost_history.pop(jid, None)
                self._tail_history.pop(jid, None)

        # Prune tracking state for jobs no longer RUNNING.
        running_ids: Set[str] = {j["id"] for j in running}
        for jid in list(self._cost_history.keys()):
            if jid not in running_ids:
                self._cost_history.pop(jid, None)
                self._tail_history.pop(jid, None)

        return alerts

    def _check_job(self, job: Dict[str, Any]) -> Optional[str]:
        """
        Purpose: evaluate all drift triggers for one RUNNING job.
        Returns the trigger name (str) if a pause is warranted, or None.
        Usage: internal.
        Gotchas: ring/guard edits are checked FIRST (immediate, highest priority).
        """
        jid = job["id"]
        worktree = job.get("worktree_path") or ""

        # --- Trigger 4: guard/config edit (highest priority, immediate) ---
        if worktree:
            diff_files = _git_diff_files(worktree)
            for f in diff_files:
                if is_ring_path(f):
                    logger.warning(
                        "DriftWatchdog: job %s touches ring path %r — immediate pause",
                        jid, f,
                    )
                    return "guard_edit"

        # --- Trigger 2: scope creep ---
        if worktree:
            spec_footprint = _parse_footprint(job.get("spec") or "")
            if spec_footprint:
                diff_files = _git_diff_files(worktree)
                for f in diff_files:
                    if not _in_footprint(f, spec_footprint):
                        logger.warning(
                            "DriftWatchdog: job %s touches out-of-footprint file %r",
                            jid, f,
                        )
                        return "scope_creep"

        # --- Trigger 3: stuck loop ---
        tail_hash = _read_transcript_tail_hash(worktree) if worktree else None
        history = self._tail_history.setdefault(jid, [])
        history.append(tail_hash)
        if len(history) > self._stuck_loop_ticks:
            history.pop(0)
        if (
            tail_hash is not None
            and len(history) >= self._stuck_loop_ticks
            and all(h == tail_hash for h in history)
        ):
            logger.warning("DriftWatchdog: job %s stuck loop detected", jid)
            return "stuck_loop"

        # --- Trigger 1: tokens-without-progress ---
        cost = job.get("cost_so_far_usd") or 0.0
        updated_ts = job.get("updated_ts") or 0
        cost_hist = self._cost_history.setdefault(jid, [])
        cost_hist.append((cost, updated_ts))
        if len(cost_hist) > self._cost_climb_ticks:
            cost_hist.pop(0)
        if len(cost_hist) >= self._cost_climb_ticks:
            # Cost must be climbing (strictly increasing) AND updated_ts must be
            # unchanged (no phase progress) across the window.
            costs = [c for c, _ in cost_hist]
            ts_set = {ts for _, ts in cost_hist}
            cost_climbing = all(costs[i] < costs[i + 1] for i in range(len(costs) - 1))
            no_progress = len(ts_set) == 1  # same timestamp = no phase advance
            if cost_climbing and no_progress:
                logger.warning(
                    "DriftWatchdog: job %s tokens-without-progress (cost %.4f→%.4f)",
                    jid, costs[0], costs[-1],
                )
                return "tokens_without_progress"

        return None

    def _pause_job(self, job: Dict[str, Any], trigger: str) -> Dict[str, Any]:
        """
        Purpose: enforce the pause — capture forensics first, then kill, then transition.
        Order: capture_pre_kill → SIGTERM pgid → store.transition(RUNNING→PAUSED_DRIFT).
        Usage: internal; called when a trigger fires.
        Gotchas: kill is best-effort (process may already be dead); transition raises
        IllegalTransition only if the job was already moved (harmless — log and continue).
        """
        from factory.job_store import IllegalTransition

        jid = job["id"]
        pgid = job.get("pgid")
        worktree = job.get("worktree_path") or ""

        # 1. Capture forensics BEFORE kill (ordering is the contract).
        try:
            capture_pre_kill(
                jid,
                Path(worktree) if worktree else Path("/tmp"),
                self._out_dir,
                {"trigger": trigger, "kill_reason": "drift_watchdog", "job_id": jid},
            )
        except Exception as exc:
            logger.warning("DriftWatchdog: forensics capture failed for %s: %s", jid, exc)

        # 2. SIGTERM the process-group.
        if pgid:
            try:
                self._kill_fn(-pgid, signal.SIGTERM)
                logger.info("DriftWatchdog: SIGTERMed pgid %d (job %s, trigger=%s)", pgid, jid, trigger)
            except (ProcessLookupError, OSError):
                pass  # Already dead — still transition the state.

        # 3. Transition RUNNING → PAUSED_DRIFT.
        try:
            self._store.transition(
                jid, "RUNNING", "PAUSED_DRIFT",
                fail_reason=f"drift_watchdog:{trigger}",
            )
        except IllegalTransition:
            logger.warning(
                "DriftWatchdog: transition RUNNING→PAUSED_DRIFT failed for %s (already moved?)", jid
            )

        return {"job_id": jid, "trigger": trigger, "alert": "DRIFT"}


def _parse_footprint(spec: str) -> Optional[Set[str]]:
    """
    Purpose: extract the declared file footprint from a job spec, if present.
    A footprint is the set of paths the task is expected to touch; scope-creep
    detection compares the worktree diff against this set.
    Usage: footprint = _parse_footprint(spec); None means no footprint declared.
    Gotchas: only parses lines starting with "footprint:" or "files:" as a
    simple allowlist; most specs have no explicit footprint (returns None).
    """
    files: Set[str] = set()
    for line in spec.splitlines():
        stripped = line.strip()
        if stripped.startswith(("footprint:", "files:")):
            rest = stripped.split(":", 1)[1].strip()
            for entry in rest.split(","):
                f = entry.strip()
                if f:
                    files.add(f)
    return files if files else None


def _in_footprint(path: str, footprint: Set[str]) -> bool:
    """
    Purpose: check whether a changed file is within the job's declared footprint.
    Usage: if not _in_footprint(f, footprint): trigger scope_creep.
    Gotchas: footprint entries may be exact paths or directory prefixes ending in '/'.
    """
    for fp in footprint:
        if path == fp or path.startswith(fp.rstrip("/") + "/"):
            return True
    return False
