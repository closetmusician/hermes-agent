# P4-a Report — Phase-checkpoint + self-healing crash-resume + forensics

**Task:** P4-a (backlog mode). **Branch:** `factory` (verified). **Date:** 2026-07-07.
**Authoritative design:** `docs/plans/harness/fable/p4/P4-design.md` §3 + Revision v2 §R1/§R4.
**Verdict:** GREEN. 26 new tests pass; 0 regressions on the 37 existing job_store/scheduler
tests. Anti-weakening proven (8 tests fail when the RESUMABLE routing is reverted).

---

## What shipped (files + line ranges)

**New modules (mine):**
- `factory/phase_checkpoint.py` (~270 lines) — REQ-01/03. The job-level phase checkpoint,
  disjoint from `tools/checkpoint_manager.py` (reused as-is, NOT extended). `PhaseCheckpoint`
  dataclass; `clear_phase` (atomic temp+fsync+os.replace); `read` (fail-closed validation);
  `resume_phase` (last cleared boundary); `bump_resume_count` / `resumes_exhausted` (MAX_RESUMES=3).
- `factory/forensics.py` (~155 lines) — REQ-04 + F10. `capture_pre_kill` (supervised: bundle
  BEFORE the kill) and `capture_post_mortem` (crash / dead pgid: best-effort, never raises).
  Both write `{transcript-tail, failing-test, phase.diff, meta.json}`.
- `factory/self_heal.py` (~110 lines) — REQ-04. `HealState`/`decide` (N=1 bounded);
  `is_side_effecting` + `guard_resume_phase` (raises `UnsafeResume` on re-running a cleared merge).

**Additive edits (kept minimal, existing edges preserved):**
- `factory/job_store.py`:
  - `STATES` frozenset + `_ALLOWED` map (job_store.py:49-96 region): added `RESUMABLE` +
    `WAITING_CAPACITY` states and their inbound edges to ADMITTED/RUNNING/TEST/REVIEW. Every
    pre-P4 edge is byte-for-byte preserved (asserted by `test_resumable_state_edges_additive`).
  - schema `resume_count` column + `_migrate_columns` (renamed from `_migrate_budget_settled`,
    additive ALTER for both budget_settled and resume_count — idempotent).
  - `check_integrity(..., checkpoint_reader=None)` + `_reclaim_dead_job(..., checkpoint_reader)`:
    a dead-pgid job WITH a valid, non-exhausted checkpoint → RESUMABLE (reservation KEPT); else
    → NEEDS_ATTENTION (reservation released, unchanged P3 §13.5). Reader injected to avoid an
    import cycle.
  - `reserve_resume_slot` (RESUMABLE/WAITING_CAPACITY → ADMITTED CAS) + `resumable_jobs`.
- `factory/scheduler.py`:
  - `Scheduler.__init__(..., checkpoint_reader=None)`; `tick` passes it to `check_integrity`
    and adds a RESUME-first re-admission branch (step 5a) that re-admits RESUMABLE jobs
    snapshotted BEFORE the sweep, under the SAME atomic slot (`reserve_resume_slot`) and the
    `CONCURRENCY_CAP=3`. Snapshot-before-sweep makes crash→resume two distinct ticks (§R1's
    "run one tick … run the next tick"): tick 1 parks RESUMABLE, tick 2 re-admits.

I did NOT touch `cost_stops.py`, `worker_runner.py`, or `model_router` (P4-b scope).

---

## Requirement evidence (REQ-01..05)

- **REQ-01 (atomic checkpoint; corrupt rejected):** `test_phase_checkpoint.py` — round-trip
  (`test_clear_phase_writes_atomic_record`), atomic no-partial (`..._is_atomic_no_partial`),
  truncated JSON rejected (`test_corrupt_checkpoint_rejected`), missing field rejected,
  absent = fresh. **7/7 pass.**
- **REQ-02 (additive states + reclaim routing + MAX_RESUMES):** `test_job_store_resumable.py` —
  pre-v2 edges intact + new forward-only edges (`test_resumable_state_edges_additive`);
  dead-pgid+checkpoint → RESUMABLE; dead-pgid+no-checkpoint → NEEDS_ATTENTION; exhausted →
  NEEDS_ATTENTION; default-reader back-compat; legacy full path green. **8/8 pass.**
- **REQ-03 (scheduler re-admits from last cleared phase; REAL kill-resume):**
  `test_crash_resume.py::test_crashed_worker_goes_resumable_then_relaunches_from_checkpoint` —
  spawns a real subprocess in its own pgid, `kill -9`s the group, tick 1 → RESUMABLE, tick 2 →
  ADMITTED with resume phase == IMPLEMENT (last cleared boundary, NOT SPEC) and the brief
  injected. Marked `@pytest.mark.live_system_guard_bypass` (genuine signal delivery required).
  `resume_phase` returns the boundary after the last cleared gate (`test_resume_phase_is_last_cleared_boundary`).
- **REQ-04 (forensics before kill; no-re-run-merge; N=1):** `test_forensics.py` — pre-kill
  ordering spy proves bundle-before-kill; post-mortem on dead pgid + reaped worktree never
  raises. `test_self_heal.py` — N=1 (`test_self_heal_is_bounded_n1`), escalate-after-one,
  `guard_resume_phase` raises `UnsafeResume` on a cleared MERGING. **9/9 pass.**
- **REQ-05 (RED-first; real sqlite; real kill; anti-weakening; 0 regression):** see below.

---

## RED → GREEN

**RED (before implementation):** the 5 new test files were written first and run via
`scripts/run_tests.sh`. Result: import errors for the 3 new modules (not yet created) and 7
job_store_resumable failures (RESUMABLE state absent) — a genuine RED (exit != 0). Captured
during development.

**GREEN (after implementation):**
```
=== Summary: 5 files, 26 tests passed, 0 failed (100% complete) ===
  test_phase_checkpoint.py (7), test_forensics.py (4), test_self_heal.py (5),
  test_job_store_resumable.py (8), test_crash_resume.py (2)
```

## Anti-weakening proof (REQ-05)

Temporarily reverting the reclaim routing (`_reclaim_dead_job`: force the RESUMABLE branch to
`if False`) and re-running:
```
=== Summary: 2 files, 2 tests passed, 8 failed ===
```
The 8 failures are exactly the RESUMABLE-routing tests (`test_dead_pgid_with_checkpoint_goes_resumable`,
the real crash-resume test, etc.). Restored immediately and re-verified GREEN. The
crash-resume and RESUMABLE-routing tests FAIL if the fix is reverted — the fix is load-bearing,
not decorative.

## 0-regression proof (REQ-05)

```
tests/factory/test_job_store.py     20 passed
tests/factory/test_scheduler.py     17 passed
```
Full P4-a + these two files: **63 passed, 0 failed.** Adjacent suites (cost_stops, intake,
task_graph, two_stage, gauntlet) also 77/77 green.

## Pre-existing failures (NOT caused by this task — UNVERIFIED as anything but env/other-scope)

- `test_supervisor.py`, `test_worker_runner.py` (git-based cases): fail with
  `git clone/init … Operation not permitted` on `.git/hooks/*.sample` — the documented sandbox
  env issue (brief: "~git-based tests fail 'Operation not permitted' = pre-existing env"). Not
  code-related; these are not files I touched.
- `test_worker_cost.py`, `test_router_integration.py`: **P4-b** files (owned by the parallel
  P4-b agent), currently RED because `model_router.py`/`worker_cost.py`/cost_stops two-CAS are
  in-progress. Out of my scope; my modules do not import them and adjacent job_store/scheduler
  tests confirm no cross-contamination.

## Notes / correctness invariants honored

- The one-way invariant survives: RESUMABLE/WAITING_CAPACITY flow ONLY forward to ADMITTED or
  the terminal NEEDS_ATTENTION; the RUNNING→RESUMABLE→ADMITTED loop is bounded by MAX_RESUMES=3.
- A cleared side-effecting phase (MERGING) is NEVER re-run on resume (`self_heal.guard_resume_phase`).
- On RESUMABLE the budget reservation is KEPT (the job re-runs); on the terminal path it is
  released exactly as P3 §13.5. The pre-crash-token `commit_spend` fold (§R3/F5) is a P4-b
  concern (worker_cost) and is intentionally not implemented here.
- checkpoint_reader is dependency-injected into job_store to avoid a factory module cycle.
