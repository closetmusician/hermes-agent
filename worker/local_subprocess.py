#!/usr/bin/env python3
# ABOUTME: LocalSubprocessWorker — process-group-based subprocess launcher (P1a-6).
# ABOUTME: Uses os.setsid() in preexec_fn so the child is a session/process-group
# ABOUTME: leader; kill() sends SIGTERM to the entire group via os.killpg so all
# ABOUTME: grandchildren are terminated together.
# ABOUTME: Liveness is detected via output-file mtime (not just is_alive()) to catch
# ABOUTME: the hang case that delegate_tool's ThreadPoolExecutor cannot detect.
"""
LocalSubprocessWorker — real subprocess launcher with process-group kill semantics.

Design authoritative source: docs/plans/harness/fable/p1a/P1a-design.md §4.2
Review notes applied (P1a-review.md worker section):
  - SIGTERM-to-process-group via os.killpg / os.setsid (REQ-02)
  - SIGKILL escalation after grace window
  - Grandchild setsid-escape is a DOCUMENTED RESIDUAL (see kill() docstring)
  - mtime != progress documented in WorkerStatus (see base.py)

Key upgrade over delegate_tool (tools/delegate_tool.py:28,60):
  delegate_tool runs subagents in a ThreadPoolExecutor — you can't kill a runaway
  subprocess-of-a-thread cleanly.  This launcher is a direct Popen with setsid so
  every child inherits the pgid and a single killpg terminates the whole family.
"""

import logging
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

from worker.base import Worker, WorkerHandle, WorkerResult, WorkerSpec, WorkerStatus

logger = logging.getLogger(__name__)

# Default grace window between SIGTERM and escalating SIGKILL (seconds).
_DEFAULT_KILL_GRACE_S = 5.0

# Default staleness window for check() — output file mtime must have advanced
# within this window for the worker to be considered RUNNING (not STALLED).
_DEFAULT_STALE_WINDOW_S = 30.0


class LocalSubprocessWorker(Worker):
    """
    Subprocess-based worker with process-group kill semantics.

    Every launched child runs as a session/process-group leader (os.setsid in
    preexec_fn).  All grandchildren inherit the same pgid.  kill() targets the
    whole group — one killpg terminates the tree.

    Liveness (check) is based on output-file mtime to catch the "alive but frozen"
    hang state that thread-based launchers cannot detect.

    Known residual: a grandchild that itself calls os.setsid() escapes the process
    group and survives kill().  See kill() docstring and the documented-residual test.
    """

    def __init__(
        self,
        kill_grace_s: float = _DEFAULT_KILL_GRACE_S,
        stale_window_s: float = _DEFAULT_STALE_WINDOW_S,
    ) -> None:
        """
        Purpose:  configure kill-grace and staleness-window thresholds.
        Usage:    LocalSubprocessWorker(kill_grace_s=3.0, stale_window_s=60.0)
        Gotchas:  shorter stale_window_s means false STALLED on legitimately slow jobs;
                  increase for long-running batch workers.
        """
        self._kill_grace_s = kill_grace_s
        self._stale_window_s = stale_window_s

    # ------------------------------------------------------------------
    # Worker interface
    # ------------------------------------------------------------------

    def launch(self, spec: WorkerSpec) -> WorkerHandle:
        """
        Spawn the subprocess and return a WorkerHandle.

        Purpose:  open output_path for writing, Popen the cmd with os.setsid as
                  preexec_fn (makes child a session+process-group leader), capture
                  pid/pgid, return a WorkerHandle.
        Usage:    handle = w.launch(spec)
        Gotchas:  the output_path directory must exist; it is NOT auto-created so
                  the caller controls the worktree layout.
        """
        output_path = Path(spec.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Merge caller env over the current process env (don't inherit nothing)
        merged_env = {**os.environ, **spec.env}

        # Open output file — stdout and stderr both go here (mtime anchor)
        output_fh = open(output_path, "wb")  # noqa: WPS515

        try:
            proc = subprocess.Popen(
                spec.cmd,
                cwd=spec.cwd,
                env=merged_env,
                stdout=output_fh,
                stderr=output_fh,
                preexec_fn=os.setsid,  # child becomes session + process-group leader
                close_fds=True,
            )
        except Exception:
            output_fh.close()
            raise

        output_fh.close()  # parent doesn't need the fd open

        worker_id = str(uuid.uuid4())
        pgid = proc.pid  # setsid guarantees pgid == pid for the new session leader

        # Verify the pgid assumption; log if something unexpected happened
        try:
            actual_pgid = os.getpgid(proc.pid)
            if actual_pgid != proc.pid:
                logger.warning(
                    "worker %s: expected pgid=%d but got pgid=%d; "
                    "process-group kill may be incomplete",
                    worker_id,
                    proc.pid,
                    actual_pgid,
                )
                pgid = actual_pgid
        except ProcessLookupError:
            # Process already exited; pgid stays as pid
            pass

        handle = WorkerHandle(
            worker_id=worker_id,
            pid=proc.pid,
            pgid=pgid,
            output_path=str(output_path),
            worktree=spec.cwd,
        )

        # Attach the Popen object for later wait/poll — not part of the public interface
        handle._proc = proc  # type: ignore[attr-defined]
        handle._timeout_s = spec.timeout_s  # type: ignore[attr-defined]

        logger.debug(
            "worker %s launched: pid=%d pgid=%d cmd=%s",
            worker_id,
            proc.pid,
            pgid,
            spec.cmd,
        )
        return handle

    def check(self, handle: WorkerHandle) -> WorkerStatus:
        """
        Non-blocking liveness check: mtime freshness + process state.

        Purpose:  determine whether the worker is actively writing output (RUNNING),
                  alive but silent (STALLED), or has exited (DONE / DEAD).
        Usage:    status = w.check(handle)
        Gotchas:  RUNNING means "output mtime advanced recently," NOT "making progress."
                  A worker emitting a keep-alive spinner reads RUNNING forever even if
                  stuck.  mtime-freshness is a liveness heuristic, not a progress
                  guarantee.  A deeper signal (sequence-numbered heartbeat) would
                  populate WorkerHandle.last_progress_ts in a future subtype.
        """
        proc = getattr(handle, "_proc", None)

        # First check if the process has already exited
        returncode = None
        if proc is not None:
            returncode = proc.poll()

        if returncode is None:
            # Try os.kill(pid, 0) as a fallback for handles without _proc
            try:
                os.kill(handle.pid, 0)
                process_alive = True
            except ProcessLookupError:
                process_alive = False
                # If the process is gone and we somehow missed poll(), treat as dead
                returncode = -1
        else:
            process_alive = False

        if not process_alive:
            # Process has exited — determine DONE (rc=0) or DEAD (rc!=0)
            if returncode == 0:
                return WorkerStatus.DONE
            return WorkerStatus.DEAD

        # Process is alive — check output-file mtime for freshness
        output_path = Path(handle.output_path)
        if not output_path.exists():
            # File not created yet — very early in execution; treat as RUNNING
            return WorkerStatus.RUNNING

        mtime = output_path.stat().st_mtime
        age_s = time.time() - mtime

        if age_s <= self._stale_window_s:
            return WorkerStatus.RUNNING
        else:
            return WorkerStatus.STALLED

    def kill(self, handle: WorkerHandle) -> None:
        """
        Send SIGTERM to the entire process group; escalate to SIGKILL after grace.

        Purpose:  terminate the worker and all its children by targeting the pgid
                  with os.killpg (POSIX).  SIGTERM gives processes a chance to clean
                  up; SIGKILL is sent after kill_grace_s if any remain.
        Usage:    w.kill(handle)  — idempotent; safe after natural process exit.
        Gotchas:
          1. DOCUMENTED RESIDUAL — a grandchild that called os.setsid() has left the
             process group and is NOT killed by killpg.  Such orphans are reparented
             to launchd/init and survive.  Mitigation for P2: supervisor scans
             /proc/<ppid>/children (Linux) or uses psutil process tree walk after kill
             to detect and log orphans.  For P1a this residual is explicitly documented
             and does not block factory worker use-cases (claude -p / codex exec do not
             self-daemonize).
          2. If the process has already exited, killpg raises ProcessLookupError —
             caught and silently ignored (idempotent).
          3. On macOS (tested platform) os.killpg + SIGTERM signals the group
             synchronously; no additional sleep is needed before the SIGKILL check.
        """
        pid = handle.pid
        pgid = handle.pgid

        # --- SIGTERM to the process group ---
        try:
            os.killpg(pgid, signal.SIGTERM)
            logger.debug("kill: SIGTERM → pgid %d (pid %d)", pgid, pid)
        except ProcessLookupError:
            # Group already gone — nothing to do
            return
        except PermissionError as exc:
            logger.warning("kill: SIGTERM permission error for pgid %d: %s", pgid, exc)

        # --- Grace window ---
        proc = getattr(handle, "_proc", None)
        deadline = time.monotonic() + self._kill_grace_s
        while time.monotonic() < deadline:
            if proc is not None and proc.poll() is not None:
                break  # process reaped
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break  # direct child gone
            time.sleep(0.1)

        # --- SIGKILL escalation if still alive ---
        try:
            os.kill(pid, 0)
            # Still alive — escalate
            try:
                os.killpg(pgid, signal.SIGKILL)
                logger.debug("kill: SIGKILL → pgid %d (escalation)", pgid)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass  # already dead — no escalation needed

        # Reap the direct child to avoid zombie (non-blocking)
        if proc is not None:
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                logger.warning("kill: direct child pid %d did not reap within 2 s", pid)
            except ChildProcessError:
                pass

    def collect_result(self, handle: WorkerHandle) -> WorkerResult:
        """
        Wait (bounded) for process exit, then read output_path and return result.

        Purpose:  reap the process and return its output + exit code.
        Usage:    result = w.collect_result(handle)
        Gotchas:  bounded by handle._timeout_s; returns with timed_out=True and
                  returncode=None if the process has not exited within that window.
                  Caller must call kill() separately if it wants to stop the process.
        """
        proc = getattr(handle, "_proc", None)
        timeout_s = getattr(handle, "_timeout_s", _DEFAULT_KILL_GRACE_S)
        timed_out = False
        returncode: Optional[int] = None

        if proc is not None:
            try:
                proc.wait(timeout=timeout_s)
                returncode = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                returncode = None

        # Read output file (may not exist if the process crashed before writing anything)
        output_path = Path(handle.output_path)
        try:
            output = output_path.read_text(errors="replace")
        except FileNotFoundError:
            output = ""

        return WorkerResult(
            returncode=returncode,
            output=output,
            timed_out=timed_out,
        )
