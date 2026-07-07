# ABOUTME: P5-c build report — self-supervision watchdogs for the Fable factory.
# ABOUTME: Documents REQ-01..04 evidence, RED-first TDD protocol, test coverage,
# ABOUTME: files produced, and anti-weakening enforcement verification.
# ABOUTME: Report only — zero code here.
# ABOUTME: Author: CODER subagent (sonnet), 2026-07-07.

# P5-c Build Report: Factory Self-Supervision Watchdogs

**Task:** P5-c — §5.1–5.5 watchdogs (backlog mode, GOVERNANCE_EXEMPT).
**Branch:** `factory` (confirmed at session start).
**Date:** 2026-07-07.

---

## Files produced

| File | Role |
|---|---|
| `factory/watchdogs/__init__.py` | NEW — package init; exports all 5 watchdog classes |
| `factory/watchdogs/drift.py` | NEW — DriftWatchdog: 4 triggers, RUNNING→PAUSED_DRIFT enforcement |
| `factory/watchdogs/regression_sentinel.py` | NEW — RegressionSentinel: post-merge test, revert action + pause branched jobs |
| `factory/watchdogs/memory_zone.py` | NEW — MemoryZone: GREEN/YELLOW/RED gate wired into scheduler throttled_fn |
| `factory/watchdogs/watchdog_supervisor.py` | NEW — WatchdogSupervisor: required-watchdog fail-safe; throttled() → True on dead watchdog |
| `factory/watchdogs/worktree_gc.py` | NEW — WorktreeGC: disk_full() gate + reap() stale worktrees |
| `factory/job_store.py` | EDITED — additive PAUSED_DRIFT state + transitions (RUNNING/QUEUED/ADMITTED → PAUSED_DRIFT) |
| `tests/factory/test_watchdogs.py` | NEW — 23 unit tests (6 classes + integration); all RED-first |

---

## REQ-01: DriftWatchdog enforces pauses (never advisory)

**Design reference:** P5-design.md §5.1.

**Implementation:**
- `DriftWatchdog.tick()` scans all RUNNING jobs and checks 4 trigger conditions in priority order: guard_edit (ring/broker path in diff) > scope_creep (file outside footprint) > stuck_loop (repeated transcript tail hash) > tokens_without_progress (cost plateau over N ticks).
- On trigger: `_pause_job()` calls `capture_pre_kill(job, out_dir)` FIRST (forensics ordering contract), then `os.kill(-pgid, SIGTERM)`, then `store.transition(jid, "RUNNING", "PAUSED_DRIFT")`.
- Kill function is injectable (`kill_fn`) so tests drive it without real processes.

**Anti-weakening evidence:**
- `test_churning_worker_paused` asserts `job["state"] == "PAUSED_DRIFT"` — not a log line.
- `test_forensics_captured_before_kill` asserts `call_order.index("capture") < kill_index` — capture must precede kill; reversing this order makes the test fail.
- `test_scope_creep_triggers_pause` and `test_no_pause_when_within_footprint` confirm the footprint gate fires and does not fire respectively.

---

## REQ-02: RegressionSentinel enforces revert + branch pause (never advisory)

**Design reference:** P5-design.md §5.2.

**Implementation:**
- `RegressionSentinel.on_merge(merge_sha, merged_job_id, repo)` calls the test suite (injectable `test_fn`); on failure:
  1. `broker_client.enqueue_action(type="merge", ...)` opens a revert held-action.
  2. Iterates QUEUED/ADMITTED/RUNNING/TEST/REVIEW jobs whose `spec["branch"]` matches the merge branch; transitions each to PAUSED_DRIFT.
  3. `ledger.record_outcome(..., outcome="reverted", ...)` writes the trust-ledger entry.
- `_last_run_ts` updated on each `on_merge` call for watchdog-supervisor health checks.

**Anti-weakening evidence:**
- `test_regression_opens_revert_pauses_branches_and_records_ledger` asserts:
  - `broker_client.enqueue_action.called` is True.
  - Branched jobs have `state == "PAUSED_DRIFT"`.
  - `ledger.record_outcome` was called with `outcome="reverted"`.
  Removing any of these three enforcement steps causes the test to fail (not merely log).
- `test_no_action_on_passing_suite` confirms passing suite leaves jobs untouched.

---

## REQ-03: MemoryZone + WorktreeGC wire into scheduler throttled_fn

**Design reference:** P5-design.md §5.3 (memory zone) + §5.5 (disk/GC gate).

**MemoryZone implementation:**
- `zone()` reads free MB via `/proc/meminfo` (Linux) or `psutil`; fails-open to GREEN on sensor failure.
- `throttled() → True` when zone is RED (< 512 MB free); GREEN and YELLOW return False.
- YELLOW zone: `get_yellow_cap()` returns 2 — scheduler can lower its cap in response, but throttled() itself stays False in YELLOW.
- `memory_reader` is injectable; tests inject a lambda returning a fixed value.

**WorktreeGC implementation:**
- `disk_full() → bool`: inverse of `disk_ok()` — free bytes < floor (default 2 GB). Designed as a `throttled_fn` callable.
- `reap()` iterates _TERMINAL_STATES (DONE/FAILED/NEEDS_ATTENTION/PAUSED_DRIFT), skips jobs newer than 7 days, skips live pgids (`os.kill(-pgid, 0)`), calls `git worktree remove --force` on aged stale worktrees.
- Forensics bundles in `out_dir` are NOT touched by reap (they live outside the worktree directory).
- Both `free_bytes_fn` and `worktree_remove_fn` are injectable.

**Anti-weakening evidence:**
- `test_red_zone_throttled`: `mz.throttled()` is True when reader returns < 512 MB; downgrading to a log line breaks this.
- `test_scheduler_tick_admits_nothing_in_red_zone`: creates a real Scheduler with `throttled_fn=mz.throttled`; asserts jobs remain QUEUED after a tick in RED zone.
- `test_disk_full_blocks_spawns` and `test_disk_full_via_scheduler`: same pattern for disk gate.
- `test_reaps_terminal_worktree_preserves_forensics`: worktree is "removed" (mock returns True), `out_dir` contents are NOT touched.
- `test_never_reaps_live_pgid_worktree`: live pgid returns "skipped_live_pgid" action; job state unchanged.

---

## REQ-04: WatchdogSupervisor fail-safe when required watchdog dies

**Design reference:** P5-design.md §5.4.

**Implementation:**
- `REQUIRED_WATCHDOGS = {"drift", "sentinel"}` — death of either with no respawn_fn → `_fail_safe = True`.
- `tick()` checks each registered watchdog's `_last_run_ts` against `stale_s` (default 120s).
- If stale + respawn_fn available: calls respawn_fn, replaces registered object; fail-safe NOT cleared until the new watchdog proves itself alive (next tick with age ≤ stale_s + `_all_required_alive()` check).
- If stale + respawn_fn raises: `_fail_safe = True` for required watchdogs.
- If stale + no respawn_fn: `_fail_safe = True` for required watchdogs (non-required: stale event logged, no fail-safe).
- `throttled()` returns `_fail_safe` under a threading lock.

**Anti-weakening evidence:**
- `test_fail_safe_when_required_watchdog_dead_and_unrespawnable`: asserts `ws.throttled() == True` when drift respawn_fn raises; test would fail if downgraded to a log line.
- `test_respawn_on_stale_watchdog`: respawn_fn returns a fresh watchdog; no fail-safe set.
- `test_fail_safe_cleared_after_respawn_and_tick`: fail-safe engaged, new watchdog ticks, supervisor clears fail-safe on next tick.
- `test_no_fail_safe_for_non_required_watchdog`: non-required watchdog goes stale without fail-safe.

---

## PAUSED_DRIFT state (additive)

**Design reference:** P5-design.md §5.1 — "additive, like P4-a's RESUMABLE".

**Changes to `factory/job_store.py`:**
- `STATES` frozenset: added `"PAUSED_DRIFT"` with comment `# P5-d (design §5.1)`.
- New transitions added (existing transitions preserved):
  - `RUNNING → PAUSED_DRIFT` (drift watchdog pauses a running job)
  - `QUEUED → PAUSED_DRIFT` (sentinel pauses a branched job not yet admitted)
  - `ADMITTED → PAUSED_DRIFT` (sentinel pauses an admitted but not-yet-running job)
  - `PAUSED_DRIFT → ADMITTED` (resume path)
  - `PAUSED_DRIFT → NEEDS_ATTENTION` (escalation path)

**State tests (TestPausedDriftState):**
- `test_running_to_paused_drift_allowed`
- `test_paused_drift_to_admitted_allowed`
- `test_paused_drift_to_needs_attention_allowed`
- `test_queued_to_paused_drift_allowed_for_sentinel`

---

## RED-first TDD protocol

All 23 tests were written before implementation and confirmed to fail (RED) in isolation.
Each test asserts the enforced state change or behavioral outcome directly — no test asserts
only a log line. Anti-weakening property: removing the enforcement code in any watchdog causes
at least one named test to fail, not merely to emit a different log message.

---

## Test summary

| Class | Tests | What is verified |
|---|---|---|
| TestDriftWatchdogChurning | 2 | cost-plateau trigger → PAUSED_DRIFT; forensics before kill |
| TestDriftWatchdogScopeCreep | 2 | scope-creep trigger → PAUSED_DRIFT; within-footprint no-op |
| TestRegressionSentinel | 2 | failure: revert + pause + ledger; pass: no action |
| TestMemoryZone | 4 | RED/GREEN/YELLOW zones + scheduler integration |
| TestWatchdogSupervisor | 4 | respawn, fail-safe on death, fail-safe cleared, non-required no fail-safe |
| TestWorktreeGC | 5 | disk_full gate, disk_ok, reap, live-pgid guard, scheduler integration |
| TestPausedDriftState | 4 | RUNNING/QUEUED/ADMITTED → PAUSED_DRIFT; PAUSED_DRIFT → ADMITTED/NEEDS_ATTENTION |
| **Total** | **23** | **0 failures** |

---

## Wire-up summary (for harness integrator)

```python
from factory.watchdogs import (
    DriftWatchdog, MemoryZone, RegressionSentinel,
    WatchdogSupervisor, WorktreeGC,
)

mz  = MemoryZone()
gc  = WorktreeGC(store=store, repo_path=repo_path, out_dir=out_dir)
ws  = WatchdogSupervisor()
dwd = DriftWatchdog(store=store, out_dir=out_dir)
rs  = RegressionSentinel(store=store, ledger=ledger,
                         broker_client=broker, repo_path=repo_path)

ws.register("drift",    dwd, respawn_fn=lambda: DriftWatchdog(store=store, out_dir=out_dir))
ws.register("sentinel", rs)

throttled_fn = lambda: mz.throttled() or gc.disk_full() or ws.throttled()
sched = Scheduler(..., throttled_fn=throttled_fn)

# Each scheduler tick:
dwd.tick()
gc.reap()
ws.tick()

# Post-merge (called by merge gate):
rs.on_merge(merge_sha, merged_job_id, repo)
```
