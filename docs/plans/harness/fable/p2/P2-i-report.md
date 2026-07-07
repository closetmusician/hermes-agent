# P2-i Report — Three Cost Stops

**Task:** P2-i (backlog CODER mode)  
**Files:** `factory/cost_stops.py`, `tests/factory/test_cost_stops.py`  
**Status:** DONE — 7/7 tests GREEN

---

## What was built

### `factory/cost_stops.py`

Three cost stops as specified in P2-design §4.3 and closed per P2-review P1-C1 and P1-C3:

**Per-job budget cap (`check_budget`)** — compares `cost_so_far_usd >= budget_usd`; calls `kill_pgid_with_sweep` and returns `StopReason.BUDGET`.

**Wall-clock timeout (`check_timeout`)** — compares `elapsed_s >= timeout_s`; same kill path; returns `StopReason.TIMEOUT`.

**Daily ceiling (`check_ceiling_breach`)** — compares `spent_today_usd >= ceiling_usd`; kills active job; returns `StopReason.CEILING`. Pre-launch gate via `ledger.try_reserve()`.

**`kill_pgid_with_sweep`** (closes P1-C1):
- Snapshots process tree via `psutil.Process(worker_pid).children(recursive=True)` **before** sending SIGTERM, while the parent is still alive and setsid-escaped grandchildren are still in the tree.
- SIGTERM → pgid → grace window → SIGKILL → pgid.
- psutil SIGTERM → SIGKILL sweep on the pre-SIGTERM snapshot.
- Any process surviving all passes is logged as `orphan-survived` in the caller-supplied list and at WARNING level.
- `PermissionError` on SIGKILL step handled: macOS raises EPERM when the group has no living members after SIGTERM already cleaned it up.

**`DailyBudgetLedger.try_reserve`** (closes P1-C3):
- `BEGIN IMMEDIATE` + `UPDATE daily_budget SET reserved_usd=reserved_usd+:cap WHERE day=:day AND spent_today_usd+reserved_usd+:cap <= :ceiling`.
- `rowcount == 1` = admitted; `rowcount == 0` = deferred (ceiling would be breached).
- Concurrent threads racing the same row: SQLite's `BEGIN IMMEDIATE` serializes writers; the second thread's UPDATE matches zero rows (ceiling already consumed by first) and returns `False`.

---

## Test results

| Test | Result |
|---|---|
| `test_budget_cap_fires_on_pgid` | PASS |
| `test_timeout_sigterm_reaches_group` | PASS |
| `test_ceiling_blocks_would_breach_launch` | PASS |
| `test_ceiling_mid_flight_breach_kills_pgid` | PASS |
| `test_atomic_cas_prevents_double_booking` | PASS |
| `test_grandchild_setsid_residual_documented` | PASS |
| `test_ledger_survives_restart` | PASS |

All 7 tests use real subprocesses, real SQLite, and real signal delivery. No mocks on the stop logic.

---

## Bugs found and fixed during implementation

1. **SIGKILL PermissionError (macOS)** — `os.killpg(pgid, SIGKILL)` after successful SIGTERM raises `EPERM` (errno 1) when the group has no living members. Added `PermissionError` to the except clause alongside `ProcessLookupError`.

2. **psutil snapshot timing** — original code snapshotted descendants **after** SIGTERM; the setsid-escaped grandchild re-parented to launchd before the walk, so `psutil.Process(worker_pid).children()` returned `[]` and the orphan was never logged. Fixed by moving the snapshot to **before** SIGTERM.

3. **Missing `import os` in inline test script** — the child script in `test_timeout_sigterm_reaches_group` called `os.getpid()` without importing `os`, causing `NameError` in the subprocess. Fixed.

4. **Race in pid_file reads** — `pid_file.exists()` returned `True` before the write was flushed, causing `int("")` ValueError. Fixed by waiting for non-empty content before proceeding.

---

## Design honesty (P1-C1 closure)

`kill_pgid_with_sweep` docstring explicitly documents the residual: a grandchild that calls `os.setsid()` AND re-parents before the snapshot runs may survive. This is consistent with `worker/local_subprocess.py`'s existing kill() docstring (lines 215-221). Test 5 (`test_grandchild_setsid_residual_documented`) validates this contract: the grandchild must be dead (swept) OR logged as orphan — either outcome passes.
