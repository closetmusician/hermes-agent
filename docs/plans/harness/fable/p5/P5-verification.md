# P5 Verification — Learning Loops & Self-Supervision

**Verifier:** independent (GOVERNANCE_EXEMPT — re-ran work, fixed nothing).
**Date:** 2026-07-07 · **Repo:** /Users/yklin/Code/hermes · **Branch:** `factory`.
**Method:** RE-RAN every suite and recorded my own counts. Neutered each wall/enforcer
in place and observed the guard test flip RED, then restored (git-tracked files → clean).
**Overall P5 gate verdict: PASS-WITH-STAGED.**

## Env-failure separation (SANDBOX)
Under the harness sandbox, `git init` inside the pytest tmpdir fails
`fatal: cannot copy '.../git-core/templates/hooks/commit-msg.sample' … Operation not
permitted` — a sandbox denial on copying macOS git template hooks into the test worktree,
NOT a code defect. Proof: `git init` succeeds in `/tmp/claude` (sandbox-writable) but
fails in the repo's `pytest-of-yklin/...` tmpdir. Workaround for git-based suites:
`GIT_TEMPLATE_DIR=<empty dir>` (skips the blocked template copy) run via `venv/bin/python
-m pytest` directly. With that, all git-worktree tests pass. Non-workaround runs show 9
false failures in `test_retro_diff_executor` — all env, zero code.

## Per-suite counts (MY re-run, GIT_TEMPLATE_DIR workaround where git is used)

| Suite | Result |
|---|---|
| `tests/factory/test_scorecard_routing_view.py` | 11 passed |
| `tests/factory/test_retro_ring_gate.py` (WALL 1) | 17 passed |
| `tests/factory/test_retro.py` | 4 passed |
| `tests/broker/test_retro_diff_executor.py` (WALL 2) | 10 passed |
| `tests/factory/test_watchdogs.py` | 23 passed |
| `tests/factory/test_forensics_store.py` | 14 passed |
| `tests/factory/test_skill_improve.py` | 17 passed |
| `tests/broker/test_auto_merge_gate.py` (no-regression) | 21 passed |
| `tests/broker/test_server_client.py` | 7 passed, 1 skipped |
| **Consolidated P5 run** | **124 passed, 1 skipped, 0 failed** |
| Full `tests/factory/` (cross-task interaction check) | **565 passed, 0 failed** |

## Watchdogs-test investigation (REQ-01, the flagged item)
P5-e reported "1 pre-existing broken watchdogs test"
(`TestDriftWatchdogChurning::test_forensics_captured_before_kill`, `kill:7777` vs
`kill:-7777`). **It does NOT reproduce — the test PASSES**, both in isolation and inside
the full 565-test factory run. The assertion was made robust
(`assert any(s.startswith("kill:") …)` at line 189, `os.kill(-pgid, …)` sign-agnostic);
the load-bearing assertion — capture BEFORE kill ordering — is intact. `test_watchdogs`
passes **23/23 cleanly on its own AND 565/565 in full suite**; P5-c's `PAUSED_DRIFT`
`job_store` addition introduces **no cross-task breakage**. VERIFIED, no escalation.

## Claim table

| REQ | Claim | Verdict | Evidence |
|---|---|---|---|
| REQ-01 | All P5 suites pass; watchdogs flag was stale | **VERIFIED** | counts above; flagged test passes in isolation + full suite |
| REQ-02 | WALL 1 (propose) rejects ring diffs | **VERIFIED** | 17/17; neuter → 8 propose tests RED |
| REQ-02 | WALL 2 (apply) INDEPENDENTLY rejects ring diffs, zero writes | **VERIFIED** | 10/10; neuter → 5 apply tests RED (`assert 'applied' != 'applied'` = leak) |
| REQ-02 | Gate self-protected in RING_PATHS + BROKER_RING_PATHS | **VERIFIED** | immutable_ring.py:61, merge_gate.py:47 |
| REQ-03 | Retro never silent — held owner action, not disk write | **VERIFIED** | test_retro RT-1: `diff_writes == []`, one `enqueue_action`, disposition `held` |
| REQ-03 | diff_sha256 hash-pin rejects propose/apply mismatch (TOCTOU) | **VERIFIED** | executor `test_hash_mismatch_rejected_applies_nothing`; payload pins sha (test_retro) |
| REQ-03 | Executor imports NOTHING from factory (layering) | **VERIFIED** | AST walk → zero factory imports; only `broker.merge_gate` |
| REQ-04 | Drift PAUSES churning worker (state→PAUSED_DRIFT, not log) | **VERIFIED** | `test_churning_worker_paused` asserts `state=="PAUSED_DRIFT"`; log-only neuter → RED |
| REQ-04 | Regression sentinel OPENS revert + pauses branched tasks | **VERIFIED** | `test_regression_opens_revert_…`: `action_taken=="revert_opened"`, branched jobs `PAUSED_DRIFT` |
| REQ-04 | Routing view REORDERS, CANNOT ADD a model (residency) | **VERIFIED** | SC-4 phantom model never enters ladder; SC-5 residency survives reorder |
| REQ-04 | Scorecard is supervisor-observed (worker field ignored) | **VERIFIED** | SC-6: worker-emitted `findings`/`worker_outcome` produce identical sample |

## CROWN two-wall anti-theater (REQ-02) — the P5 crown risk

Both walls proven independently load-bearing by neuter-and-observe, each fully reverted:

- **WALL 1 neuter** — made `factory/retro_ring_gate.check_retro_diff` a no-op
  (`return None`). Result: **8 propose-gate tests flipped RED**
  (`test_retro_diff_touching_ring_rejected_at_propose` × all ring families +
  `…editing_the_gate_itself_rejected`) — a ring diff leaks through propose. Restored via
  `git checkout`; 17/17 green; tree clean.
- **WALL 2 neuter** — replaced the executor's `check_ring(diff, …)` call with `pass`.
  Result: **5 apply-door tests flipped RED**, with `assert 'applied' != 'applied'` proving
  the ring diff was actually **`git apply`-ed** (not merely mis-flagged). Restored via
  `git checkout`; 10/10 green; tree clean.

Neuter one wall and the OTHER still holds — the two walls are independent, over the same
bytes, both fail-closed. Gate protects itself (in both ring-path lists). **CROWN: VERIFIED.**

## REQ-04 enforcement anti-theater
Downgraded drift's `store.transition(RUNNING→PAUSED_DRIFT)` to a log-only warning.
`test_churning_worker_paused` flipped RED at `job["state"] == "PAUSED_DRIFT"` (drift.py:135)
— the log line appeared but no state change. The state transition IS the enforcement, not
a log. Restored; 23/23 green; tree clean. **VERIFIED.**

## REQ-05 — Staged vs Verified (honest split)

| Item | Status | Note |
|---|---|---|
| Two walls + held-action + hash-pin (crown) | **VERIFIED** | re-run + both anti-theater demos above |
| Drift-pause / sentinel-revert enforcement | **VERIFIED** | state transitions asserted + anti-theater |
| Scorecard integrity (no add-model, supervisor-observed) | **VERIFIED** | SC-4/5/6 green |
| Cost-per-merged-PR **2-week trend** (`factory/cost_trend.py`) | **STAGED** | function computes trend over caller-supplied windows; not yet wired to live routing decisions — mechanism present, real 2-week signal not exercised |
| Retro **measurably reduces a failure class** (RT-2) | **STAGED / UNVERIFIED** | reduction shown on a fixture only; needs real overnight runs — belongs to P5-a/P5-e integration, not proven here |
| Live skill files updated by skill_improve | **VERIFIED (contract)** | `skill_improve` writes NOTHING directly — asserts skill mtime unchanged after pass; edits only via held action (same never-silent contract as retro) |
| code-review-graph **cross-repo transfer** (pattern_bank) | **STAGED / UNVERIFIED** | `_try_cross_repo_search` late-imports the MCP tool + degrades to same-repo offline (SK-3/graceful-fallback tested); a REAL cross-repo graph query was NOT run (plugin graph not built for this repo this session) |

## BLOCKING
**None.** No non-env failure exists. Both crown walls un-bypassable and self-protecting;
watchdogs enforce via state, not logs; scorecard non-poisonable and cannot add a model.

## Verdict
**PASS-WITH-STAGED.** Crown self-modification defense (the P5 risk) is real, two-walled,
independently load-bearing, and self-protecting. Watchdog enforcement and scorecard
integrity are real. Staged (honest, non-blocking): 2-week cost trend wiring, RT-2
real-run failure-class reduction, and live code-review-graph cross-repo transfer — all
mechanism-complete but not exercised against real longitudinal/cross-repo data this run.

_Git tree confirmed clean after all neuters reverted (only pre-existing untracked
`hermes-evolution/`, `.claude/worktrees/` remain — present at session start)._
