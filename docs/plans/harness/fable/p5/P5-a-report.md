# ABOUTME: P5-a build report — scorecard + routing view implementation.
# ABOUTME: Documents REQ-01..04 evidence, RED-first protocol, test coverage,
# ABOUTME: files produced, and anti-poisoning/residency-invariant verification.
# ABOUTME: Report only — zero code here.
# ABOUTME: Author: CODER subagent (sonnet), 2026-07-07.

# P5-a Build Report: Scorecard + Routing View

**Task:** P5-a (backlog mode).
**Branch:** `factory` (confirmed).
**Date:** 2026-07-07.

---

## Files produced

| File | Role |
|---|---|
| `factory/scorecard.py` | NEW — per-(model × task_type) sample store + leaderboard rollup |
| `factory/routing_view.py` | NEW — scorecard-driven ordering view for the model router |
| `factory/model_router.py` | ADDITIVE edit — `admissible_ladder`/`select_tier`/`next_rung` accept optional `scorecard=` + `task_type=`; when absent, original hand-written order is used (no behavior change for pre-P5 callers) |
| `tests/factory/test_scorecard_routing_view.py` | NEW — 11 tests covering SC-1..SC-6 + REQ-03 + helpers |

---

## REQ-01..04 evidence

### REQ-01 — scorecard.py: per-(model × task_type) table, SUPERVISOR-observed
- `scorecard_samples` schema co-located in jobs DB (pattern: `trust_ledger.py:37`).
- `record_sample_on_conn` / `record_sample` derive EVERY field from supervisor-written `job_row` columns: `model`, `worker`, `cost_so_far_usd` (supervisor writes), `derive_task_type` (keyword map, not worker free-text), `_resolve_outcome` (from `state`/`fail_reason`, not worker claim), `findings_count` (passed by the supervisor from the reviewer artifact).
- `rollup()` → `list[ScoreRow]` with `merged_clean_rate`, `mean_findings`, `mean_cost_usd`, `mean_wall_ms`. `leaderboard(task_type)` is `rollup(task_type=task_type)`.
- **SC-6 anti-poisoning test:** job_row carrying worker-emitted `findings=0` and `worker_outcome='merged_clean'` produces `mean_findings=2.0` (supervisor-parsed) not 0. **PASSES.**
- **SC-1 test:** empty DB → empty rollup; multiple jobs → one row per (model, task_type) with correct rates. **PASSES.**

### REQ-02 — routing_view.py: reorders catalog-confirmed set; cannot ADD a model
- `ranked_first_party(catalog, scorecard, task_type)` — intersects `_FIRST_PARTY_LADDER` with catalog (`cli.<worker>.present:true`), ranks by scorecard score (formula: `merged_clean_rate - α*cost_norm - β*findings_norm`).
- `ranked_openrouter(catalog, scorecard, task_type)` — intersects `_OPENROUTER_CANDIDATES` with probe-confirmed (`live_catalog AND ping_ok`) entries, ranks same way.
- Cold-start (n < `MIN_SAMPLES=5`): hot models (sufficient samples) are ranked first; cold models are appended in their original order (the hand-written default is the prior).
- **SC-4 test:** scorecard row for `acme/totally-made-up-9000` (absent from catalog) does NOT enter any ranked ladder. **PASSES.**
- **SC-3 test:** fewer than MIN_SAMPLES → ranked output equals default order, no crash. **PASSES.**

### REQ-03 — router reads view; residency pre-filter UNCHANGED
- `admissible_ladder` accepts `scorecard=` kwarg (additive). When provided, it calls `ranked_first_party`/`ranked_openrouter` for the ordering source. `_rung_admissible` (the residency pre-filter) runs AFTER ordering on the full candidate list — unchanged.
- **SC-5 test:** OR models seeded with perfect bugfix rate → `admissible_ladder` for a false-policy repo still returns ZERO openrouter rungs. **PASSES.**
- **SC-2 + REQ-03 test:** `select_tier` with scorecard returns `gpt-5` (scorecard-preferred) for `bugfix`; a false-policy repo never leaks OR. **PASSES.**
- **REQ-03 standalone test:** even if the view ranks OR first, `admissible_ladder(false_policy)` has zero OR rungs. **PASSES.**
- All 11 existing `test_model_router.py` + 4 `test_router_integration.py` tests pass without modification (additive-only change).

### REQ-04 — RED-first; anti-weakening noted
- Tests were written before implementation was finalized.
- **SC-4 (cannot-add-model):** removing the catalog intersection in `_rank_within_group` / `ranked_first_party` would make phantom models appear → test fails.
- **SC-6 (anti-poisoning):** reading `job_row.get("findings")` instead of the supervisor-passed `findings_count` would produce `0` instead of `2` → `mean_findings` check fails.
- **SC-5 (residency):** bypassing `_rung_admissible` for scorecard-path would admit OR rungs → assertion fails.
- **SC-2 (routing-changes-from-data):** not using scorecard data (always returning cold order) would put `claude-sonnet` first instead of `gpt-5` → assertion fails.

---

## Test count

11 tests in `tests/factory/test_scorecard_routing_view.py`. All GREEN. 0 pre-existing failures.
Combined suite: 26 tests (11 new + 11 model_router + 4 router_integration), 26 PASSED.

---

## Anti-weakening: how to break each gate

| Test | What you must delete to make it leak |
|---|---|
| SC-4 cannot-add-model | Remove catalog intersection from `_rank_within_group` callers |
| SC-6 anti-poisoning | Read `job_row["findings"]` in `record_sample_on_conn` instead of supervisor-passed `findings_count` |
| SC-5 residency | Remove `_rung_admissible` filter from `admissible_ladder` |
| SC-2 routing-changes | Return `_FIRST_PARTY_LADDER` regardless of scorecard data |
| SC-3 cold-start | Return empty tuple or crash when n < MIN_SAMPLES |
| REQ-03 | Pass scorecard-ordered OR rungs without residency filter |

---

## UNVERIFIED

- `_resolve_outcome` heuristic for `NEEDS_ATTENTION` states with nuanced `fail_reason` strings may
  not cover all edge cases in production — covers the main patterns (reject/revert/fix/manual).
  Production callsite should pass explicit `outcome=` where the supervisor knows the outcome directly.
- `parse_findings_count` line-counting fallback (non-JSON files) is approximate; a structured JSON
  format from the reviewer is strongly preferred.
