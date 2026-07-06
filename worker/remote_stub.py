#!/usr/bin/env python3
# ABOUTME: RemoteWorker stub — identical Worker interface, NotImplementedError bodies (P1a-7).
# ABOUTME: Proves that the fleet supervisor can dispatch to local or remote workers
# ABOUTME: through the same code path without any local-only assumption in the caller.
# ABOUTME: Real remote-worker implementation ships post-P1a (P2/P3 fleet phase).
# ABOUTME: pgid is set to 0 to make the field present but explicitly inert for remote.
"""
RemoteWorker stub — implements Worker ABC with NotImplementedError bodies.

Design authoritative source: docs/plans/harness/fable/p1a/P1a-design.md §4.3

Purpose:
    A compile/dispatch proof: the fleet supervisor (P2/P3) dispatches to RemoteWorker
    through the IDENTICAL code path it uses for LocalSubprocessWorker, confirming that
    no local-only assumption (bare os.getpgid, local path) has leaked into the caller.

    "Design-now, implement-one."

Stub contract:
    - All four methods are present and raise NotImplementedError with a clear message.
    - The class is importable, passes issubclass(RemoteWorker, Worker), and can be
      passed to any function that type-checks against Worker.
    - pgid=0 in the (unused) handle to make the field present but inert.
"""

from worker.base import Worker, WorkerHandle, WorkerResult, WorkerSpec, WorkerStatus

_NOT_IMPLEMENTED_MSG = (
    "RemoteWorker is a P1a stub — remote-worker implementation ships in P2/P3. "
    "Use LocalSubprocessWorker for real execution."
)


class RemoteWorker(Worker):
    """
    Remote worker stub implementing the Worker ABC.

    All methods raise NotImplementedError with a clear message.  The stub exists
    so the supervisor's dispatch layer can be written against the Worker interface
    and tested for type-safety before the real remote implementation lands.

    Usage:
        w = RemoteWorker()
        try:
            handle = w.launch(spec)
        except NotImplementedError:
            ...  # expected until P2/P3 ships the real implementation
    """

    def launch(self, spec: WorkerSpec) -> WorkerHandle:
        """
        Stub launch — raises NotImplementedError.

        Purpose:  placeholder that satisfies the Worker ABC.
        Usage:    w.launch(spec)  → NotImplementedError in P1a.
        Gotchas:  real implementation will enqueue the job to a remote scheduler;
                  the handle will carry a job-id (not a local pid) and pgid=0.
        """
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def check(self, handle: WorkerHandle) -> WorkerStatus:
        """
        Stub check — raises NotImplementedError.

        Purpose:  placeholder; real implementation polls the remote scheduler API.
        Usage:    w.check(handle)  → NotImplementedError in P1a.
        Gotchas:  remote liveness will be based on scheduler status, not mtime.
        """
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def kill(self, handle: WorkerHandle) -> None:
        """
        Stub kill — raises NotImplementedError.

        Purpose:  placeholder; real implementation sends a cancel request.
        Usage:    w.kill(handle)  → NotImplementedError in P1a.
        Gotchas:  remote cancel semantics differ from local SIGTERM — idempotency
                  and grace windows are scheduler-dependent.
        """
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def collect_result(self, handle: WorkerHandle) -> WorkerResult:
        """
        Stub collect_result — raises NotImplementedError.

        Purpose:  placeholder; real implementation fetches the artifact from remote storage.
        Usage:    w.collect_result(handle)  → NotImplementedError in P1a.
        Gotchas:  remote result collection may be async (streaming) vs. file-read.
        """
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)
