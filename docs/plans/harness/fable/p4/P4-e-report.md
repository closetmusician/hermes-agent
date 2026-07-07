# P4-e Report — Cost-per-PR Trend Board

**Date:** 2026-07-07
**Task:** P4-e (P4-5 in plan) — cost-per-merged-PR trend board
**Status:** COMPLETE — 14/14 tests GREEN

---

## Files

| File | Role |
|---|---|
| `factory/cost_trend.py` | New (P4-e scope) — three public functions: `cost_per_merged_pr`, `trend`, `board` |
| `tests/factory/test_cost_trend.py` | New — 14 tests, RED before implementation, GREEN after |

No other files were edited.

---

## REQ evidence

### REQ-01 — cost_per_merged_pr(window)

**Done-when:** a window with known spend + merged count → the correct cost-per-PR.

- `test_single_merged_pr_returns_correct_cost`: one job $2.00 → returns 2.0
- `test_multiple_merged_prs_averages_correctly`: jobs ($1, $3, $2) → average $2.00
- `test_jobs_outside_window_excluded`: job outside window (cost $99) excluded; only in-window job ($1) counts

**Escalate-if (0 merged PRs):**
- `test_zero_merged_prs_returns_none_no_division_error`: returns `None`, no ZeroDivisionError

Implementation note: a single SQL `SELECT COALESCE(SUM(...),0), COUNT(*) … WHERE state='DONE' AND updated_ts BETWEEN …` handles both count and sum atomically. Returns `None` (not `0.0`) to distinguish "no data" from "zero cost".

---

### REQ-02 — trend across two windows

**Done-when:** window1 cost > window2 cost → `'down'` (improving); equal → `'flat'`.

- `test_trend_down_when_window_b_cheaper`: A=$3/PR, B=$1/PR → `direction='down'`
- `test_trend_up_when_window_b_more_expensive`: A=$1/PR, B=$3/PR → `direction='up'`
- `test_trend_flat_when_equal`: A=B=$2/PR → `direction='flat'`, `delta≈0`
- `test_result_contains_value_a_value_b_delta`: all four keys present; delta = B − A

Direction logic: `delta = value_b − value_a`. Negative → `'down'` (B cheaper). Positive → `'up'`. `|delta| < FLAT_THRESHOLD_USD ($0.10)` → `'flat'`. This is the ONE canonical comparison; inverting it would make 'down' mean "more expensive" — the inversion-guard test catches that.

---

### REQ-03 — board/summary with real numbers

**Done-when:** the board reflects real ledger + merged-job data.

- `test_board_contains_required_keys`: all six keys present (`value_a`, `value_b`, `direction`, `delta`, `window_a`, `window_b`)
- `test_board_values_reflect_real_job_store_data`: `board()` values match `cost_per_merged_pr()` queried directly — not hardcoded
- `test_board_direction_matches_trend_direction`: `board()` direction agrees with `trend()`
- `test_board_empty_windows_no_crash`: no DONE jobs → returns dict with `value_a=None`, `value_b=None`, no exception

Data source: `jobs.cost_so_far_usd` for `state='DONE'` rows within the window. This is the actual recorded spend (set by the supervisor via `worker_cost.record_job_spend` → `DailyBudgetLedger.commit_spend`). No placeholders.

---

### REQ-04 — RED-first; real sqlite fixtures; inversion test fails if inverted

**Done-when:** pytest green; RED shown before implementation.

- 14 tests all RED (ModuleNotFoundError) before `cost_trend.py` existed.
- 14 tests GREEN after implementation.
- `test_inversion_guard_direction_cannot_be_down_when_b_is_worse`: B ($5) > A ($1) → asserts `direction != 'down'` AND `direction == 'up'`. If the delta sign were flipped, this test fails.
- `test_zero_merge_window_propagates_to_trend`: one empty window → `direction='unknown'`; no crash.

All fixtures use real sqlite (pytest `tmp_path`) — no mocks, no in-memory stubs.

---

## Design decisions

**Cost source:** `jobs.cost_so_far_usd` for DONE jobs. The design's §4.4 "per-job cost log" (`job_id, phase, worker, model, input_tok, output_tok, usd, ts`) is the *ledger side*; the durable per-job total that persists through DONE is `cost_so_far_usd`. Using it avoids requiring a separate log table that `worker_cost.py` does not yet write to — this is the real data, not a placeholder.

**`updated_ts` as 'merged' timestamp:** the supervisor stamps `updated_ts` on every state transition. The `updated_ts` at `state='DONE'` is the merge timestamp. Window filtering uses this column.

**FLAT_THRESHOLD_USD = $0.10:** a one-dime noise floor. Meaningful at the $1–$5/PR operating scale; small enough not to suppress real improvements.

---

## Regression

Pre-existing failures in `test_worker_runner`, `test_supervisor`, `test_morning_packet`, `test_overnight_queue`, `test_voice_intake` are unrelated to P4-e (verified by running those same tests before introducing any files). No regressions introduced.
