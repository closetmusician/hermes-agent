# P5-b CODER Report — Nightly Retro + Two-Wall Self-Modification Guard (CROWN)

**Task:** P5-b (backlog, GOVERNANCE_EXEMPT). **Branch:** `factory` (verified == verify).
**Mode:** RED-first inline TDD + anti-weakening witnesses. No commit/push.

## Summary

The crown P5 self-modification wall is built with **TWO independent fail-closed walls** over
the same hash-pinned bytes. A retro CANNOT edit the immutable ring (broker/, trust-policy,
cost_stops, never-graduates, immutable_ring.py) **or its own gate** — rejected at PROPOSE and,
even if that were bypassed, again at APPLY.

- **WALL 1 (propose)** — `factory/retro_ring_gate.check_retro_diff` delegates to the identical
  hardened `immutable_ring.check_diff`; a ring-touching or empty/unparseable proposal is dropped
  BEFORE `enqueue_action`, so the owner never sees it.
- **WALL 2 (apply)** — `broker/executors/retro_diff_executor` re-checks the ring via
  `broker.merge_gate.check_ring` over the EXACT hash-verified bytes AFTER the owner tap, BEFORE
  `git apply`. Closes review P0/V1 (the apply door was one-walled).

## Files (all NEW unless noted) + line ranges

| File | What | Lines |
|---|---|---|
| `factory/retro_ring_gate.py` | WALL 1 propose-gate; `RetroRingRejected`, `check_retro_diff` | 1-80 |
| `broker/executors/retro_diff_executor.py` | WALL 2 apply executor: hash-verify → check_ring → git apply | 1-154 |
| `factory/retro.py` | GATHER→PROPOSE→GATE→HOLD orchestration; `run_retro` | 1-160 |
| `factory/immutable_ring.py` (additive) | `+ "factory/retro_ring_gate.py"` to `RING_PATHS` | :60-62 |
| `broker/merge_gate.py` (additive) | `+ "factory/retro_ring_gate.py"` to `BROKER_RING_PATHS` | :46-48 |
| `broker/server.py` (additive) | register `"retro_diff": build_retro_diff_executor()` in `main()` executors dict; import | :440, :448-451 |
| `tests/factory/test_retro_ring_gate.py` | RT-3(a), RT-4, RT-5, RT-8, anti-weakening | NEW |
| `tests/factory/test_retro.py` | RT-1, RT-6, hash-pin payload | NEW |
| `tests/broker/test_retro_diff_executor.py` | RT-3(b), RT-7, fail-closed, anti-weakening | NEW |

The broker/ring edits are the SANCTIONED maintainer edits named in the design task row (P5-b).
The `retro_diff` executor imports NOTHING from `factory` (verified by AST) — the factory→broker
layering is preserved exactly as `merge_gate` does; the apply-door re-check reuses the broker's
own `BROKER_RING_PATHS` superset.

## Requirement evidence

- **REQ-01 (propose-gate, never silent, fail-closed):** `check_retro_diff` rejects a diff into
  every ring family at propose (RT-3(a), 7 parametrizations); `run_retro` produces exactly one
  `enqueue_action(type='retro_diff')` held action and never writes the diff to disk (RT-1); a
  ring proposal is dropped with `enqueue_action` never called (RT-6, ordering contract);
  empty/unparseable → rejected (RT-5). GREEN.
- **REQ-02 (apply-gate, the P0 fix, TOCTOU):** the executor re-checks the ring over exact bytes
  and rejects a ring diff fed straight in with a valid hash, applying NOTHING — worktree
  byte-identical, `git status --porcelain` empty (RT-3(b), + 4 ring families parametrized); a
  diff whose bytes ≠ `payload.diff_sha256` is rejected `diff_hash_mismatch` and applies nothing
  (RT-7). Order is hash-verify → check_ring → apply. GREEN.
- **REQ-03 (self-protecting gate + parity):** `factory/retro_ring_gate.py` ∈ `RING_PATHS` AND
  `BROKER_RING_PATHS`; a retro editing the gate is rejected (RT-8); AT-RING-2 superset
  (`test_broker_ring_list_is_superset_of_factory`) stays green. GREEN.
- **REQ-04 (no-regression):** full `tests/broker/` + `tests/factory/test_immutable_ring.py` =
  **137 passed, 1 skipped** (baseline 127+1 → +10 new broker tests; 0 regressions). The 1 skip
  is pre-existing. `broker/server.py` imports cleanly with the new executor registered.
- **REQ-05 (RED-first + anti-weakening both walls):** RED shown (3 modules absent → collection
  errors). Anti-weakening PROVEN by sabotage:
  - remove WALL 1 `check_diff` → all 7 RT-3(a) propose cases FAIL (leak).
  - remove WALL 2 `check_ring` → RT-3(b) + 4 ring families FAIL (leak, diff applies).
  - remove the hash recompute → RT-7 FAILS (leak, mismatched diff applies).
  All sabotage reverted; suite restored to green; no `SABOTAGE` residue in either file.

## Test counts

- New P5-b tests: **31** (test_retro_ring_gate 12, test_retro 5, test_retro_diff_executor 14).
- Combined crown-relevant suite (P5-b + auto_merge_gate + immutable_ring): **65 passed**.
- Full broker + immutable_ring regression: **137 passed, 1 skipped, 0 failed**.

## Design deviations / notes

- `factory/retro.py` GATHER/PROPOSE are INJECTED seams (`gather`, `propose` callables) so the
  retro is testable and does not hard-import the sibling P5-a (scorecard/routing_view) or P5-e
  (forensics_store) modules before they merge — it codes against the documented GATHER contract
  ({failure_class, samples, ...}). RT-2 (measurable failure-class reduction on a fixture) and the
  scorecard/forensics wiring belong to the P5-a/P5-e integration; the retro's crown-critical half
  (walls + held-action contract) is complete and verified here.
- `broker/server.py` `_rpc_approve` was NOT edited: the `ExecutorRegistry` dispatches by row
  type, so the owner-tap path runs the `retro_diff` executor automatically once registered — the
  one-line dict entry is the only server.py change (confirmed by design §v2-C1).
