# P1a-WORKER Coder Report — Worker Contract (P1a-6/7)

**Date:** 2026-07-06  
**Branch:** factory  
**Tasks:** P1a-6 (Worker ABC + LocalSubprocessWorker) · P1a-7 (RemoteWorker stub)  
**Test result:** 22/22 GREEN (was 0/22 RED at start)

---

## RED → GREEN evidence

Tests were run **RED-first** before any implementation file existed:

```
BEFORE (RED):
  tests/worker/test_worker.py: 1 error (ModuleNotFoundError: No module named 'worker')

AFTER (GREEN):
  tests/worker/test_worker.py: 22 passed in 7.6s
```

---

## REQ-01 — Worker ABC / interface / launch / collect_result

**Evidence:** `TestWorkerInterface` (5 tests) + `TestLocalSubprocessLaunchAndCollect` (4 tests)

- `test_worker_abc_has_four_methods` — Worker ABC has launch/check/kill/collect_result.
- `test_launch_creates_process_group` — child pgid == child pid (setsid applied).
- `test_collect_result_returns_zero_rc_on_clean_exit` — rc=0 on clean subprocess exit.
- `test_collect_result_captures_stdout_in_output_file` — stdout in output_path.
- `test_collect_result_bounded_does_not_hang` — returns in <10 s on a 30-s sleeper.

**Implementation:** `worker/base.py` (Worker ABC, WorkerSpec, WorkerHandle, WorkerStatus,
WorkerResult), `worker/local_subprocess.py` (LocalSubprocessWorker).

---

## REQ-02 — SIGTERM-to-process-group kills ALL descendants

**Evidence:** `TestProcessGroupKill` (3 tests)

- `test_kill_terminates_direct_child` — after kill(), direct child pid is gone (ProcessLookupError confirmed).
- `test_kill_terminates_grandchildren_via_pgid` — parent spawns two extra children, all three pids gone after `w.kill()`.  The test writes pids to a file, kills, then asserts every pid raises ProcessLookupError.
- `test_kill_setsid_escaping_grandchild_documented_residual` — **DOCUMENTED RESIDUAL** (marked `@pytest.mark.live_system_guard_bypass`): a grandchild that calls `os.setsid()` escapes the process group and survives `killpg`.  The test confirms the escape, cleans up the orphan, and passes in both outcomes (alive = residual documented; dead = fine too).

**Mechanism:** `subprocess.Popen(preexec_fn=os.setsid)` makes the child a session+process-group leader.  `kill()` calls `os.killpg(pgid, SIGTERM)` → grace window → `os.killpg(pgid, SIGKILL)` escalation.

**Residual (P2):** grandchildren that self-daemonize via `setsid()` escape the group.  P1a factory use-cases (`claude -p`, `codex exec`) do not self-daemonize, so this is acceptable.  Mitigation slot: supervisor ppid-tree scan after kill; full genealogy kill is a P2 item.

---

## REQ-03 — Liveness via output-mtime (RUNNING / STALLED / DONE)

**Evidence:** `TestMtimeLiveness` (4 tests)

- `test_fresh_mtime_reports_running` — process writing every 0.1 s → status RUNNING.
- `test_stale_mtime_alive_reports_stalled` — process writes once then sleeps 30 s; after stale_window_s=1.0 → status STALLED.
- `test_exited_process_reports_done` — rc=0 exit → status DONE.
- `test_nonzero_exit_reports_dead` — rc=1 exit → status DEAD.

**Known limitation (documented in WorkerStatus docstring + check() docstring):**  
mtime detects "process is writing output," not "making real progress."  A keep-alive spinner advances mtime and reads RUNNING indefinitely.  The staleness window is a liveness HEURISTIC.  A deeper progress signal (structured heartbeat with sequence numbers) would populate `WorkerHandle.last_progress_ts` in a future worker subtype.

---

## REQ-04 — RemoteWorker stub: same-interface polymorphic dispatch

**Evidence:** `TestRemoteWorkerStub` (4 tests)

- `test_remote_worker_is_worker_subclass` — `issubclass(RemoteWorker, Worker)`.
- `test_remote_worker_has_all_methods` — all four methods present.
- `test_remote_worker_raises_not_implemented` — `launch()` raises `NotImplementedError`.
- `test_polymorphic_dispatch_same_code_path` — a `_supervisor_dispatch(worker, spec)` function dispatches identically to `LocalSubprocessWorker` (returns `"launched:<id>"`) and `RemoteWorker` (returns `"stub:not_implemented"`) through the same Python call path.  No local-only assumption in the caller.

---

## REQ-05 — TDD, real subprocesses, no leaks

- RED-first confirmed (collection error before implementation).
- All process-spawning tests use real `subprocess.Popen` — no mocking of the process logic.
- Every test that spawns a process has a `try/finally w.kill(handle)` cleanup block.
- `test_kill_setsid_escaping_grandchild_documented_residual` cleans up the escaped orphan in `finally` (requires `@pytest.mark.live_system_guard_bypass` because the orphan's pid is outside the test subtree after `setsid()`).
- `pytest tests/worker/` exits clean with no leaked processes.

---

## Files created

| File | Role |
|---|---|
| `worker/__init__.py` | Package marker + version (0.1.0) |
| `worker/base.py` | Worker ABC + WorkerSpec / WorkerHandle / WorkerStatus / WorkerResult |
| `worker/local_subprocess.py` | LocalSubprocessWorker — setsid + killpg + mtime liveness |
| `worker/remote_stub.py` | RemoteWorker stub — NotImplementedError bodies, same interface |
| `tests/worker/__init__.py` | Test package marker |
| `tests/worker/test_worker.py` | 22 tests covering REQ-01..05 |

Files NOT touched: `broker/`, `gateway/`, `tools/delegate_tool.py`, any existing file.

---

## Process-group kill proof output (from test run)

```
tests/worker/test_worker.py::TestProcessGroupKill::test_kill_terminates_grandchildren_via_pgid PASSED
tests/worker/test_worker.py::TestProcessGroupKill::test_kill_terminates_direct_child PASSED
tests/worker/test_worker.py::TestProcessGroupKill::test_kill_setsid_escaping_grandchild_documented_residual PASSED
```

All three PIDs (parent + 2 extra children) confirmed gone via `ProcessLookupError` after `w.kill()`.
Setsid-escaping orphan confirmed, cleaned up, residual documented.

---

## Summary

22 tests, 5 source files, 0 broker/gateway files touched.  
RED → GREEN confirmed. Process-group kill proof verified on real subprocesses.  
mtime != progress limitation and setsid-escape residual both explicitly documented.
