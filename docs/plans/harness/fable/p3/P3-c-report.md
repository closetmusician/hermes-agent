# P3-c — FLEET SCHEDULER — CODER report

**Task:** P3-c (backlog, GOVERNANCE_EXEMPT). Objective: the fleet scheduler — plain
code (no AI), fan-out/serialize on `depends_on`, cap=3 via atomic admission, budget
throttle + settlement, node-scan-at-enqueue, N-threads-one-lock writer model.
**Branch:** `factory` (verified). **Date:** 2026-07-07.

## Files (all changes)
| File | Kind | Ranges |
|---|---|---|
| `factory/scheduler.py` | NEW (289 lines) | whole file — `Scheduler.tick/enqueue_graph/run_forever`, `CONCURRENCY_CAP=3`, `STAGGER_MIN/MAX_S=30/60` |
| `factory/job_store.py` | ADDITIVE | ADMITTED state + edges (`_ALLOWED` ~66-96); `busy_timeout` + `_migrate_budget_settled` (~197-235); `job_deps` table + `budget_settled` col (schema ~117,128-140); `add_dep`/`deps_of`/`ready_jobs`/`reserve_slot` (386-471); `check_integrity(ledger=...)` + `_reclaim_dead_job` (560-711) |
| `factory/cost_stops.py` | ADDITIVE | `busy_timeout` (~239); `release_reservation` (339-360) |
| `tests/factory/test_scheduler.py` | NEW (462 lines, 17 tests) | whole file |

## RED → GREEN
- **RED shown:** `test_scheduler.py` first run → `ModuleNotFoundError: No module named
  'factory.scheduler'` (collection error, 0 passed) before `scheduler.py` existed.
- **GREEN:** all 17 scheduler tests pass. Combined `test_scheduler.py +
  test_job_store.py + test_cost_stops.py` → **44 passed, 0 failed**.

## Requirement evidence (REQ-01..06)
- **REQ-01 (depends_on fan-out/serialize):** `ready_jobs()` = QUEUED & all deps DONE
  (job_store.py:415). `test_fans_out_two_independent_jobs` (one tick admits BOTH edge-free
  nodes), `test_serializes_dependent_job` (T1→T2: T2 stays QUEUED until T1 DONE, then
  admitted), `test_dependent_never_runs_before_dep_done` (T2 blocked while T1 merely
  RUNNING). Cyclic graphs are rejected upstream by `task_graph.validate` (Kahn) before the
  scheduler ever sees them.
- **REQ-02 (cap=3 atomic admission):** new `ADMITTED` state; `reserve_slot` CAS
  QUEUED→ADMITTED under the store lock BEFORE spawn (job_store.py:450); capacity counts
  `{ADMITTED,RUNNING,TEST,REVIEW,AWAITING_APPROVAL,MERGING}`. `test_concurrency_never_exceeds_cap`
  (6 nodes → exactly 3 live), `test_cap_not_exceeded_across_spawn_transition_gap` (fires a
  tick INSIDE the spawn→RUNNING window; ADMITTED slots counted → no over-admit).
- **REQ-03 (budget throttle + settlement):** `try_reserve` before admission;
  `test_budget_throttle_defers_spawn` (near-ceiling → node stays QUEUED, not spawned),
  `test_admitted_job_reserves_budget` (reserved_usd += cap), `test_crashed_job_releases_budget_reservation`
  (dead-pgid → `check_integrity(ledger)` parks NEEDS_ATTENTION AND restores reserved_usd; a
  second sweep does NOT double-release via `budget_settled`). `release_reservation` in
  cost_stops.py:339. WAL + `busy_timeout=5000` on both factory DBs (`test_busy_timeout_set_on_all_dbs`).
- **REQ-04 (node-scan at enqueue):** `enqueue_graph` renders `title + acceptance[]` and runs
  `injection_scan.scan`+`fence` on each node BEFORE `enqueue_job` (scheduler.py:131).
  `test_rendered_node_spec_scanned_at_enqueue` (an AI-emitted "push to main" acceptance line
  is fenced — enqueued spec carries the marker), `test_clean_node_not_fenced` (no-op on benign).
- **REQ-05 (N-threads-one-lock, no AI):** `test_concurrent_transition_no_lost_update` — 8
  real threads transition distinct jobs against ONE shared JobStore, plus a racing
  illegal-backward: all 8 legal forwards commit, the backward raises `IllegalTransition`,
  final states are exactly the legal set (no lost update). `test_no_ai_in_scheduler_module`
  greps the source for `anthropic/openai/claude -p/WorkerRunner/model_tools` → CLEAN.
- **REQ-06 (RED-first, real deps, anti-weakening):** real `JobStore`/`DailyBudgetLedger`
  (real sqlite temp files); only the per-node worker spawn is stubbed (`spawn_fn` records).
  Anti-weakening demonstrated live: forcing `capacity=999` → the 3 cap tests FAIL; forcing
  `ready_jobs` to ignore deps → the 2 serialize tests FAIL (T2 became ADMITTED). Both probes
  reverted; suite re-verified green.

## 0-regression
- `test_job_store.py` + `test_cost_stops.py`: **27 passed, 0 failed** (canonical runner).
- Full non-broken factory suite (all files except the 2 below): **314 passed, 0 failed**.
- `test_supervisor.py` (9) + `test_worker_runner.py` (4) fail ONLY under the parallel
  runner and fail IDENTICALLY on a pristine checkout (stash-verified) — pre-existing
  sandbox/git-env failures, NOT introduced by P3-c. They PASS in isolation.

## Deviation from design §13.3 (documented, deliberate)
The design's exact `_ALLOWED` edit DROPS `QUEUED→RUNNING` (making ADMITTED mandatory),
paired with a `supervisor.py:339` edit owned by P3-b. P3-c is scoped to NOT touch
supervisor.py and to keep every existing P2 job_store test green. Dropping the edge broke
8 existing tests + the P2 supervisor path. Resolution: **RETAIN `QUEUED→RUNNING` alongside
the new `QUEUED→ADMITTED` edge** (both coexist in `_ALLOWED`, documented inline). The
scheduler ALWAYS reserves via QUEUED→ADMITTED and never uses the direct edge, and the cap
count includes ADMITTED — so the atomic-admission guarantee is fully intact. Making ADMITTED
mandatory (drop RUNNING + edit supervisor in one change) is a P3-b follow-up.

## Notes
- `spawn_fn(job_row)` is the single injected seam; wiring it to `build_production_supervisor`
  (which currently re-enqueues its own job via `run_until_approval`) is P3-b/P3-d integration
  — out of P3-c scope. The scheduler owns the control loop + atomic admission primitive.
- `check_integrity(ledger=None)` keeps the P2 signature working (ledger optional, additive).
