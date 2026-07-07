# ABOUTME: Finisher verification report for P4-b (model router + cost accounting).
# ABOUTME: REQ-by-REQ evidence: residency-every-rung + two-ceiling atomicity +
# ABOUTME: synthetic-provider reconciliation proofs, test counts, and all fixes made.

# P4-b Finisher Report

**Date:** 2026-07-07
**Branch:** `factory`
**Verifier:** FINISHER subagent (backlog mode)
**Status:** COMPLETE — all P4-b tests GREEN, 0 regressions

---

## REQ-01 — All P4-b tests green; no regressions in cost_stops/worker_runner

### Fix made

`factory/model_router.py` `_rung_admissible` (lines 138–144) was left in the **weakened
state** by the prior agent after the anti-theater demo was never restored. The dead code
was:

```python
if worker not in THIRD_PARTY_WORKERS:
    return True
return True  # WEAKENED          ← short-circuits; real check below never ran
try:
    assert_openrouter_allowed(repo, worker, policy)
    return True
except Exception:
    return False
```

The fix: removed `return True  # WEAKENED`, restoring the real predicate:

```python
if worker not in THIRD_PARTY_WORKERS:
    return True
try:
    assert_openrouter_allowed(repo, worker, policy)
    return True
except Exception:
    return False
```

### Test counts

| Suite | Tests | Result |
|---|---|---|
| `tests/factory/test_model_router.py` | 11 | ✓ all GREEN |
| `tests/factory/test_worker_cost.py` | 8 | ✓ all GREEN |
| `tests/factory/test_router_integration.py` | 4 | ✓ all GREEN |
| **P4-b total** | **23** | **✓ 23/23** |
| `tests/factory/test_cost_stops.py` | 7 | ✓ all GREEN (no regression) |
| `tests/factory/test_worker_runner.py` | 25 pass / 4 env-fail | 4 FAILURES are pre-existing `git init Operation not permitted` env-sandbox failures confirmed before this session — not regressions |

---

## REQ-02 — Residency-every-rung invariant

### Code evidence

**`factory/model_router.py` — the pre-filter is on EVERY rung (F1)**

`admissible_ladder` (`:175`) builds the full candidate ladder and filters:
```python
return [r for r in full if _rung_admissible(r.worker, repo, policy)]
```
`_rung_admissible` calls the SAME predicate as the runner wall (`assert_openrouter_allowed`)
— wall 1 and wall 2 use the same oracle so they cannot disagree.

`next_rung` (`:224`) **re-derives** the admissible ladder on every call:
```python
ladder = admissible_ladder(repo, policy, catalog)
```
It never caches the ladder from `select_tier` (F1 fix). A false repo's `next_rung` call
re-filters from `(repo, policy, catalog)` regardless of what `current` is — so even a
buggily injected OR rung in `current` cannot give back an OR rung from `next_rung`.

**Unknown/missing policy → fail-closed (F3)**

`resolve_policy` (`:247`) catches `FileNotFoundError`, `MergePolicyInvalid`, and `OSError`
and substitutes `default_policy(repo)` (allow_openrouter=False). A missing policy can
never leave OR rungs in the ladder.

**Runner guard backstop (`:368` — already in production)**

```python
assert_openrouter_allowed(run_spec.repo, run_spec.worker, run_spec.policy)
```
This fires on EVERY `build_worker_env` call — every launch. Even a buggy router that
emitted an OR rung for a false repo would hit wall 2 before any key is placed.

### Anti-weakening demo

**Pre-fix state** (weakened): `_rung_admissible` had `return True  # WEAKENED` that
short-circuited before the real check. Running the test suite in that state:

```
FAILED test_false_repo_ladder_has_no_openrouter_rung_at_any_depth
FAILED test_unknown_policy_ladder_fails_closed_no_openrouter
FAILED test_false_repo_429_exhaustion_waits_never_openrouter
FAILED test_next_rung_refilters_every_step
FAILED test_missing_policy_fails_closed_to_wait
```

5 crown-invariant tests FAILED — confirming the tests are non-theatrical and catch a real
weakening. The fix (removing `return True  # WEAKENED`) restored all 23 to GREEN.

**Exhaustion invariant** (`test_false_repo_429_exhaustion_waits_never_openrouter`): walks
the full 429 ladder for a false repo via `next_rung` — verifies every step is
`worker != "openrouter"` and the terminal result is `WAIT_FOR_CAPACITY`, never OR.

**F2 (stale checkpoint rung)**: `test_relaunch_recomputes_rung_from_live_policy_not_stale_checkpoint`
injects a true-era OR rung as `stale_rung`, then calls `select_tier` with the live false
policy — confirms the recomputed rung is not openrouter (first-party only).

---

## REQ-03 — Two-ceiling atomicity + synthetic-provider reconciliation

### Two-CAS atomicity (F6 fixed)

**Schema**: `cost_stops.py` `:77–85` uses composite PK `(day, provider)`:
```sql
CREATE TABLE IF NOT EXISTS daily_budget (
  day      TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '__nightly__',
  ...
  PRIMARY KEY (day, provider)
);
```
The review's finding F6 ("no provider column, cannot store sub-ceiling") is closed by the
composite-PK migration implemented in `DailyBudgetLedger._migrate_provider_column`.

**`reserve_openrouter` (`:390–435`) — two CASes in ONE `BEGIN IMMEDIATE`**:
```python
self._conn.execute("BEGIN IMMEDIATE")
ok_nightly = self._cas_reserve(self._conn, day, _NIGHTLY_PROVIDER, job_cap_usd)
ok_sub     = self._cas_reserve(self._conn, day, _OPENROUTER_PROVIDER, job_cap_usd)
if ok_nightly and ok_sub:
    self._conn.execute("COMMIT")
    return True
self._conn.execute("ROLLBACK")    # ← the compensating release; no orphaned nightly
return False
```
Both rows are reserved or rolled back atomically — no TOCTOU window between them. The
ROLLBACK IS the compensating release (review F6 fix).

**Test evidence** (`test_worker_cost.py`):
- `test_openrouter_subceiling_blocks_third_dispatch`: 3 reserves at $0.80 each → 3rd
  returns False (would reach $2.40 > $2 sub-ceiling).
- `test_openrouter_reserve_never_exceeds_nightly_ceiling`: nightly at $4.90 → OR reserve
  of $0.50 (fits sub-ceiling) rejected because $4.90+$0.50 > $5 nightly. Sub-ceiling row
  is untouched (rolled back) — `sub["spent"] + sub["reserved"] ≈ 0.0`.
- `test_openrouter_two_cas_never_exceeds_either_ceiling_concurrent`: 24 threads,
  each with its own ledger connection, race 24 concurrent `reserve_openrouter(0.30)` calls.
  Result: `or_reserved ≤ $2.00` AND `total_reserved ≤ $5.00` AND `n_ok ≤ 6` (at most
  floor(2/0.30)=6 can win).

### Synthetic-provider reconciliation (F8 fixed)

`test_reconciliation_against_synthetic_provider_within_tolerance`: a synthetic night of
4 workers (claude, codex, openrouter, plus one that crashes and resumes). The
**provider-reported figure is computed OUTSIDE the ledger** as the sum of
`usd_for(worker, model, itok, otok)` calls before any `record_job_spend` — an independent
oracle that the ledger cannot tautologically equal. The test then asserts:
```python
assert abs(provider_reported - ledger_total) <= TOLERANCE
```

`TOLERANCE` (`worker_cost.py :60–66`) = `max($0.25, 15% × per_job_cap × MAX_RESUMES)` =
`max(0.25, 0.15 × 2.0 × 3)` = `max(0.25, 0.90)` = **$0.90**.

`test_reconciliation_catches_drift_beyond_tolerance`: the anti-theater test. It simulates
the F5 bug (ledger only commits one phase, provider billed twice). The drift = one full
phase of `claude-opus` at 120k/40k tokens = well above $0.90 TOLERANCE — the assertion
`abs(provider_reported - ledger_total) > TOLERANCE` passes, proving the reconciliation
test is not self-referential and WOULD catch this class of drift.

---

## REQ-04 — Report written

This file is the report.

### Files touched in this session

| File | Change |
|---|---|
| `factory/model_router.py` | Restored `_rung_admissible` (removed `return True  # WEAKENED`) |
| `docs/plans/harness/fable/p4/P4-b-report.md` | This report (new) |

### Files verified (read-only)

- `factory/worker_cost.py` (144 ln) — TOLERANCE, usd_for, record_job_spend: correct.
- `factory/cost_stops.py` (699 ln) — composite-PK schema, `reserve_openrouter` two-CAS,
  `commit_spend` fan-out for OR spend: correct.
- `factory/worker_runner.py` — `openrouter` in `_PROVIDER_MODEL_KEY`, WALL-3 at `:378`,
  runner guard at `:368`: correct.
- `tests/factory/test_model_router.py`, `test_worker_cost.py`, `test_router_integration.py`
  — all 23 tests verified non-theatrical.

### Regressions

Zero. The 4 `test_worker_runner.py` env-failures (`git init Operation not permitted`) are
pre-existing sandbox constraint failures — confirmed pre-existing by the mandatory context
("~git-based tests fail 'Operation not permitted' = pre-existing env, not regressions").
