# P0-8 Report — Jobs-First Health Command

**Task:** P0-8 — Build the jobs-first health command
**Date:** 2026-07-06
**Branch:** factory
**Status:** COMPLETE

---

## Files Produced

| File | Purpose |
|---|---|
| `scripts/factory_health.py` | Standalone health command; no upstream edits |
| `tests/test_factory_health.py` | 19 unit tests; RED first, then GREEN |

---

## TDD Evidence — RED Run (before implementation)

Run command:
```
/Users/yklin/Code/hermes/venv/bin/python -m pytest tests/test_factory_health.py -v
```

Output (abbreviated — all 19 FAILED on ModuleNotFoundError: No module named 'factory_health'):

```
tests/test_factory_health.py::TestProviderReachability::test_anthropic_reachable_returns_ok_with_latency FAILED [  5%]
tests/test_factory_health.py::TestProviderReachability::test_openrouter_reachable_returns_ok FAILED [ 10%]
tests/test_factory_health.py::TestProviderReachability::test_provider_network_failure_attributed_to_network FAILED [ 15%]
tests/test_factory_health.py::TestProviderReachability::test_provider_internal_bug_attributed_to_hermes FAILED [ 21%]
tests/test_factory_health.py::TestComputeState::test_caffeinate_present_when_process_running FAILED [ 26%]
tests/test_factory_health.py::TestComputeState::test_caffeinate_absent_when_no_process FAILED [ 31%]
tests/test_factory_health.py::TestComputeState::test_disk_free_returns_numeric_gb FAILED [ 36%]
tests/test_factory_health.py::TestComputeState::test_load_avg_returns_tuple FAILED [ 42%]
tests/test_factory_health.py::TestRunningWorkers::test_no_workers_returns_not_provisioned_message FAILED [ 47%]
tests/test_factory_health.py::TestRunningWorkers::test_worker_processes_counted_when_present FAILED [ 52%]
tests/test_factory_health.py::TestSchedulerTick::test_never_when_tick_file_absent FAILED [ 57%]
tests/test_factory_health.py::TestSchedulerTick::test_reads_timestamp_from_tick_file FAILED [ 63%]
tests/test_factory_health.py::TestSchedulerTick::test_stale_tick_flagged_in_seconds_ago FAILED [ 68%]
tests/test_factory_health.py::TestFailureAttribution::test_socket_failure_is_network FAILED [ 73%]
tests/test_factory_health.py::TestFailureAttribution::test_internal_exception_is_hermes FAILED [ 78%]
tests/test_factory_health.py::TestFailureAttribution::test_timeout_is_network FAILED [ 84%]
tests/test_factory_health.py::TestExitCodes::test_all_healthy_sections_return_exit_0 FAILED [ 89%]
tests/test_factory_health.py::TestExitCodes::test_provider_down_returns_exit_1 FAILED [ 94%]
tests/test_factory_health.py::TestExitCodes::test_internal_error_cause_hermes_returns_exit_2 FAILED [100%]

FAILED tests/test_factory_health.py::TestProviderReachability::... - ModuleNotFoundError: No module named 'factory_health'
19 failed in 0.12s
```

---

## GREEN Run (after implementation)

```
============================= test session starts ==============================
platform darwin -- Python 3.11.15, pytest-9.0.2, pluggy-1.6.0
collected 19 items

tests/test_factory_health.py::TestProviderReachability::test_anthropic_reachable_returns_ok_with_latency PASSED [  5%]
tests/test_factory_health.py::TestProviderReachability::test_openrouter_reachable_returns_ok PASSED [ 10%]
tests/test_factory_health.py::TestProviderReachability::test_provider_network_failure_attributed_to_network PASSED [ 15%]
tests/test_factory_health.py::TestProviderReachability::test_provider_internal_bug_attributed_to_hermes PASSED [ 21%]
tests/test_factory_health.py::TestComputeState::test_caffeinate_present_when_process_running PASSED [ 26%]
tests/test_factory_health.py::TestComputeState::test_caffeinate_absent_when_no_process PASSED [ 31%]
tests/test_factory_health.py::TestComputeState::test_disk_free_returns_numeric_gb PASSED [ 36%]
tests/test_factory_health.py::TestComputeState::test_load_avg_returns_tuple PASSED [ 42%]
tests/test_factory_health.py::TestRunningWorkers::test_no_workers_returns_not_provisioned_message PASSED [ 47%]
tests/test_factory_health.py::TestRunningWorkers::test_worker_processes_counted_when_present PASSED [ 52%]
tests/test_factory_health.py::TestSchedulerTick::test_never_when_tick_file_absent PASSED [ 57%]
tests/test_factory_health.py::TestSchedulerTick::test_reads_timestamp_from_tick_file PASSED [ 63%]
tests/test_factory_health.py::TestSchedulerTick::test_stale_tick_flagged_in_seconds_ago PASSED [ 68%]
tests/test_factory_health.py::TestFailureAttribution::test_socket_failure_is_network PASSED [ 73%]
tests/test_factory_health.py::TestFailureAttribution::test_internal_exception_is_hermes PASSED [ 78%]
tests/test_factory_health.py::TestFailureAttribution::test_timeout_is_network PASSED [ 84%]
tests/test_factory_health.py::TestExitCodes::test_all_healthy_sections_return_exit_0 PASSED [ 89%]
tests/test_factory_health.py::TestExitCodes::test_provider_down_returns_exit_1 PASSED [ 94%]
tests/test_factory_health.py::TestExitCodes::test_internal_error_cause_hermes_returns_exit_2 PASSED [100%]

============================== 19 passed in 0.25s ==============================
```

---

## Live Health Output

Run command:
```
python scripts/factory_health.py; echo "EXIT: $?"
```

Output:
```
=== Factory Host Health  2026-07-06T00:36:47Z ===

[1] Provider Reachability
  ANTHROPIC: REACHABLE (121.7ms)
  OPENROUTER: REACHABLE (1513.6ms)

[2] Compute State
  caffeinate: YES (awake)
  disk free:  1652.7 GiB
  load avg:   4.50, 4.28, 4.75 (1m/5m/15m)

[3] Running Workers
  no workers, factory not provisioned (fleet scheduler ships in P2/P3)

[4] Last Scheduler Tick
  last tick: never (not yet provisioned — expected in P0)

--- Status: ALL HEALTHY (exit 0) ---
EXIT: 0
```

Live verdict: **ALL HEALTHY, exit 0.**

---

## REQ Evidence

| REQ | Evidence |
|---|---|
| REQ-01: 4 sections | All 4 printed in live run above; all 4 tested in GREEN run |
| REQ-02: NETWORK vs HERMES attribution | `test_socket_failure_is_network`, `test_internal_exception_is_hermes`, `test_timeout_is_network` all pass; live run shows real state |
| REQ-03: TDD RED-first | RED run shown above (19 FAILED before implementation) |
| REQ-04: Exit codes 0/1/2 | `TestExitCodes` covers all three; live run exits 0 (all-healthy) |
