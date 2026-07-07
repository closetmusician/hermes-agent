# P6-a report — Mission-control cards + no-bypass crown (CODER)

**Task:** P6-a (CROWN, backlog mode, GOVERNANCE_EXEMPT). Mission-control cards for live
jobs where EVERY control action rides the broker held-action + out-of-band nonce — the
SAME surface as merges. A card is strictly weaker than a human tap and CANNOT mutate the
fleet without a broker nonce.

**Branch verified:** `factory` (`git rev-parse --abbrev-ref HEAD` → `factory`).

**Files (mine only):**
- `broker/executors/control_executor.py` (new, 122 lines) — broker-side effector via injected reconciler.
- `factory/mission_control.py` (new, 162 lines) — read-only reader + card producers.
- `broker/server.py` (+8 lines, `main()` comment only — additive, documentation, no behavior change; lines 452–459).
- `tests/broker/test_control_executor.py` (new) — 12 tests.
- `tests/factory/test_mission_control.py` (new) — 10 tests.

---

## RED-first evidence

Both new test modules were written FIRST and failed RED on missing modules:
```
tests/broker/test_control_executor.py → ModuleNotFoundError: broker.executors.control_executor
tests/factory/test_mission_control.py → ModuleNotFoundError: factory.mission_control
```
After implementation: **22 passed, 0 failed** (`scripts/run_tests.sh` per-file isolation).

---

## Requirement evidence

**REQ-01 — MissionControl holds a READ-ONLY reader; card methods PRODUCE broker held
actions, mutate nothing.**
- `factory/mission_control.py:79-92` `__init__` takes `(broker_client, job_store_reader)`;
  it stores NO `JobStore`/`Scheduler` write handle. `_is_writable_store` (`:50-57`) refuses
  construction if the reader exposes `transition`/`enqueue_job`/`reserve_slot`.
- `emit_control_card` (`:94-137`) is the ONLY mutating path — it calls
  `broker_client.enqueue_action(type="job_reject"|"job_rescope"|"queue_reprioritize", ...)`
  exactly once and returns the held `action_id`; it changes no job state.
- `job_approve` is deliberately absent (reuses the existing `type="merge"` card).
- Tests: `test_mission_control_holds_no_writable_store`, `test_reader_rejected_if_writable`,
  `test_emit_control_card_produces_one_held_action`, `test_emit_control_card_maps_verbs_to_types`.

**REQ-02 — control_executor (broker-side effector) via an INJECTED reconciler; a control
action executes ONLY via this path, ONLY with a valid broker-minted nonce.**
- `broker/executors/control_executor.py` — `build_control_executor(reconciler)` returns the
  callable the broker runs on approval. It imports NOTHING from factory (verified by AST test
  `test_control_executor_imports_nothing_from_factory`), mirroring `merge_gate`/`retro_diff_executor`.
  The reconciler is injected at broker construction (supervisor passes the three control types
  into `executors={...}`, exactly as `merge_gate` is injected at `server.py:91`).
- Behavioral over the real socketpair-driven `BrokerServer`:
  `test_approved_job_reject_effects_change_via_reconciler` (approved card mutates via reconciler),
  `test_control_card_cannot_mutate_job_without_nonce` (no-nonce AND forged-nonce approve raise
  `BrokerError`/`ApprovalRejected`, job untouched, reconciler never called; only a broker-minted
  `mint_approval_nonce` releases it). Real `HeldStore` + real `ApprovalAuthority` — no mocks on
  the nonce wall (only a real in-process state-machine reconciler stands in for JobStore).

**REQ-03 — No-bypass crown.**
- Structural: `test_mission_control_holds_no_writable_store` — introspects `vars(mc)`, asserts no
  held attribute has `.transition`/`.enqueue_job`/`.reserve_slot`. Capability removed by construction.
- Behavioral: `test_control_card_cannot_mutate_job_without_nonce` (modeled on
  `tests/broker/test_bypass_impossibility_gate.py`).
- Tail read-only: `test_tail_is_read_only_no_broker` — `tail()` reads `log_tail` via the reader,
  never calls the broker, never mutates. `list_live` likewise (`test_list_live_is_read_only_snapshot`).
- **P2-3 fix:** `test_approved_queued_reject_maps_to_failed` — a `job_reject` on a QUEUED job maps
  to **FAILED** (the allowed edge), NOT the illegal `QUEUED→NEEDS_ATTENTION`. The reconciler owns
  this mapping (the executor just calls `reconciler.reject(job_id)`); illegal transitions fail
  closed (`test_control_executor_rejects_illegal_transition` — reject on DONE raises, no change).
- Rescope single-scan boundary: the new_spec is `injection_scan.scan`+`fence`d on the FACTORY side
  in `mission_control.emit_control_card` (`:113-119`) before the card is produced (the broker-side
  executor imports nothing from factory and forwards verbatim). Tests: `test_rescope_reenqueues_scan_fenced`
  (injection fenced), `test_clean_rescope_spec_not_altered` (no false fencing),
  `test_control_executor_forwards_rescope_spec_verbatim`.

**REQ-04 — No-regression on broker edits.**
- My only tracked change is `broker/server.py` +8 lines (a comment in `main()`, no behavior change).
- Full `tests/broker/` before and after my change: **identical 17 failures**, all pre-existing
  git-sandbox (`git init` → "Operation not permitted" copying hook templates) in
  `test_merge_executor.py` / `test_retro_diff_executor.py`, plus one env `test_broker_ring_list_is_superset_of_factory`.
  Proven by stashing `server.py` and re-running: 17 failed both ways. **0 regressions from P6-a.**
- Standalone fail-closed proven: `test_unregistered_control_type_fails_closed` (a broker with no
  control executor rejects a control card with `UnknownActionType`, no mutation) and
  `test_broker_main_does_not_wire_control_types` (AST: `main()` registers no control type — the
  supervisor injects them with the factory-side reconciler).

**REQ-05 — RED-first; real objects; anti-weakening on BOTH no-bypass tests.**
- **(a) Writable store:** removed the `_is_writable_store` guard in `mission_control.__init__` →
  `test_reader_rejected_if_writable` FAILED ("DID NOT RAISE"). Restored → green.
- **(b) Nonce check:** rewrote `ApprovalAuthority._validate` to `return True` →
  `test_control_card_cannot_mutate_job_without_nonce` FAILED ("DID NOT RAISE BrokerError").
  Restored (`broker/approval.py` byte-identical, `git diff --quiet` clean) → green.
- Both weakenings backed up to `/tmp/claude/` before mutation; both files restored and re-verified green.

---

## No-bypass proof, both ways

1. **A card cannot self-mint / self-approve:** `MissionControl` has only `enqueue_action` egress;
   the nonce mint (`ApprovalAuthority.mint_nonce` / `server.mint_approval_nonce`) is NOT an RPC and
   never crosses the socket. `test_control_card_cannot_mutate_job_without_nonce` proves no-nonce and
   forged-nonce approves both raise and leave the job untouched.
2. **A card cannot mutate directly:** `MissionControl` holds a read-only reader; the reconciler that
   effects the change lives only in the broker process and is never referenced by the factory-side
   card layer. `test_mission_control_holds_no_writable_store` proves it structurally.

---

## Digest

- **REQ-01:** MissionControl read-only reader + `emit_control_card` produces exactly one held broker
  action; no writable store. `factory/mission_control.py:79-137`.
- **REQ-02:** `build_control_executor(reconciler)` broker-side effector, imports nothing from factory
  (AST-verified); executes only on nonce approval over the real socket. `broker/executors/control_executor.py`.
- **REQ-03:** structural + behavioral no-bypass; tail read-only; QUEUED reject → FAILED (P2-3).
- **REQ-04:** 0 regressions — broker suite 17 failures are pre-existing git-sandbox (identical with my
  change stashed). Standalone control card fails closed.
- **REQ-05:** RED shown; real HeldStore/ApprovalAuthority/socket; anti-weakening on BOTH no-bypass tests confirmed.
- **Test count:** 22 (12 broker + 10 factory), all green.
- **Files:** `broker/executors/control_executor.py` (new); `factory/mission_control.py` (new);
  `broker/server.py:452-459` (+8 comment-only); `tests/broker/test_control_executor.py` (new);
  `tests/factory/test_mission_control.py` (new).
- **Report:** `docs/plans/harness/fable/p6/P6-a-report.md`.
