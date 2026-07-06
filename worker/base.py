#!/usr/bin/env python3
# ABOUTME: Worker ABC and data types for the factory worker launcher (P1a-6).
# ABOUTME: Defines the four-method interface (launch/check/kill/collect_result)
# ABOUTME: that both LocalSubprocessWorker and RemoteWorker implement.
# ABOUTME: WorkerSpec carries the job description; WorkerHandle carries the live
# ABOUTME: reference (pid/pgid/output_path) returned after a successful launch.
"""
Worker abstract base class and supporting data types.

Design authoritative source: docs/plans/harness/fable/p1a/P1a-design.md §4.1

The four-method interface is the sole contract between the fleet supervisor and
any worker implementation.  No implementation detail (pid, pgid, local path) may
appear in the supervisor's dispatch code — those live in WorkerHandle, which the
supervisor passes back to check/kill/collect_result opaquely.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class WorkerStatus(Enum):
    """
    Lifecycle state returned by Worker.check().

    RUNNING  — output-file mtime advanced within the staleness window AND process alive.
    STALLED  — process alive but output-file mtime frozen beyond the staleness window.
               NOTE: mtime detects "writing output," not "making real progress."  A
               worker emitting keep-alive lines reads RUNNING forever; a deeper progress
               signal would go in a future subtype (see WorkerHandle.last_progress_ts).
    DONE     — process exited with rc == 0.
    DEAD     — process exited with rc != 0, or crashed without producing output.
    """

    RUNNING = "running"
    STALLED = "stalled"
    DONE = "done"
    DEAD = "dead"


@dataclass
class WorkerSpec:
    """
    Job description passed to Worker.launch().

    All fields are required; env overrides (not replaces) the subprocess env.
    output_path is the file to which stdout+stderr are redirected — it is also
    the mtime anchor for liveness detection in check().

    Usage:
        spec = WorkerSpec(cmd=["python", "worker.py"], cwd="/tmp/job1",
                          output_path="/tmp/job1/out.txt", env={}, timeout_s=120.0)
    """

    cmd: list  # command + args to execute
    cwd: str  # working directory (worktree path)
    output_path: str  # stdout/stderr sink; mtime anchor for liveness
    env: dict  # extra env vars to overlay on the subprocess environment
    timeout_s: float  # collect_result / kill-grace upper bound (seconds)


@dataclass
class WorkerHandle:
    """
    Live reference returned by Worker.launch(); passed back to check/kill/collect_result.

    pid   — direct child PID.
    pgid  — process group ID (== pid when os.setsid was applied in preexec_fn).
    worktree — working directory, same as WorkerSpec.cwd; kept here for supervisor
               bookkeeping without re-reading the spec.
    last_progress_ts — placeholder for a future structured-heartbeat progress signal.
                       Currently unused; left as a hook so a richer subtype can populate
                       it without changing the interface.

    Gotcha: pgid is only meaningful for local processes.  RemoteWorker sets pgid=0
    to make the field present but explicitly inert.
    """

    worker_id: str  # opaque, unique per launch (UUID4 recommended)
    pid: int  # direct child pid (0 for remote stub)
    pgid: int  # process group id; == pid for setsid-launched local workers
    output_path: str  # path to the output file (mtime anchor)
    worktree: str  # working directory (for supervisor bookkeeping)
    last_progress_ts: Optional[float] = field(default=None)  # hook for future heartbeat


@dataclass
class WorkerResult:
    """
    Outcome returned by Worker.collect_result().

    returncode == 0 means clean exit; non-zero is an error.
    output is the contents of output_path (may be empty if the file was never written).
    timed_out is True when collect_result returned before the process exited naturally
    (i.e., the timeout_s bound was hit).
    """

    returncode: Optional[int]  # None if the process is still running at collect time
    output: str  # contents of output_path (stdout + stderr)
    timed_out: bool = False  # True when collect hit the timeout_s bound


class Worker(ABC):
    """
    Abstract base class for factory worker launchers.

    All four methods must be implemented by every subtype.  The fleet supervisor
    dispatches LOCAL and REMOTE workers through this single interface — no bare
    os.getpgid() or local-path assumptions may appear in supervisor code.

    Usage:
        w = LocalSubprocessWorker()  # or RemoteWorker()
        handle = w.launch(spec)
        status = w.check(handle)
        result = w.collect_result(handle)
        w.kill(handle)  # idempotent after process exit
    """

    @abstractmethod
    def launch(self, spec: "WorkerSpec") -> "WorkerHandle":
        """
        Start the job described by spec; return a live handle.

        Purpose:  spawn the subprocess (or enqueue the remote job) and return
                  a WorkerHandle carrying pid/pgid/output_path for subsequent calls.
        Usage:    handle = w.launch(spec)
        Gotchas:  for local workers, output_path is created/truncated here; the
                  file must exist before check() reads its mtime.
        """
        ...

    @abstractmethod
    def check(self, handle: "WorkerHandle") -> WorkerStatus:
        """
        Return the current liveness state of the worker.

        Purpose:  non-blocking snapshot of process state + output-file mtime.
        Usage:    status = w.check(handle); if status == WorkerStatus.STALLED: ...
        Gotchas:  for local workers, STALLED is a heuristic (mtime freshness), not
                  a guaranteed progress signal.  See WorkerStatus docstring.
        """
        ...

    @abstractmethod
    def kill(self, handle: "WorkerHandle") -> None:
        """
        Terminate the worker and all its descendants.

        Purpose:  send SIGTERM to the entire process group (local) or cancel the
                  remote job; escalate to SIGKILL after a grace window.
        Usage:    w.kill(handle)  — idempotent; safe to call after process exit.
        Gotchas:  a grandchild that called os.setsid() escapes the process group
                  and is NOT killed by killpg.  This is a documented residual —
                  see P1a-design.md §4.2 and test_kill_setsid_escaping_grandchild.
        """
        ...

    @abstractmethod
    def collect_result(self, handle: "WorkerHandle") -> "WorkerResult":
        """
        Read the output artifact and return code; never blocks past timeout_s.

        Purpose:  wait for the process to exit (bounded), then read output_path
                  and return a WorkerResult.
        Usage:    result = w.collect_result(handle)
        Gotchas:  if the process has not exited within timeout_s the method returns
                  with timed_out=True and returncode=None; the caller must call kill()
                  separately if it wants to stop the process.
        """
        ...
