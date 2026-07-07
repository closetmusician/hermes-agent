# P5-d (P5-e Forensics) — Implementation Report

**Task:** P5-d (backlog) — Structured failure forensics store  
**Design ref:** P5-design.md §6 (REQ-04) + task row P5-e  
**Branch:** `factory`  
**Date:** 2026-07-07

---

## Files Changed

| File | Type | Description |
|---|---|---|
| `factory/forensics_store.py` | NEW | `failure_forensics` SQLite table, `classify()`, `record_failure()`, `query_failed_for_scorecard()`, `query_recurring_failures()` |
| `factory/forensics.py` | ADDITIVE | 5-line ABOUTME update + `record_structured()` post-capture hook (deferred import, never-raises wrapper) |
| `tests/factory/test_forensics_store.py` | NEW | 14 tests (FF-1..4 + schema + REQ-04 supervisor-observed property) |

---

## REQ Evidence

### REQ-01 — failure_forensics table + record_failure

- `apply_schema()` creates `failure_forensics` with all 8 schema fields: `job_id`, `stage`, `error_class`, `cost_burned_usd`, `retry_worthy`, `bundle_path`, `detail`, `captured_ts`.
- `classify(job_row, bundle_path=)` is pure/deterministic, derives `stage` from `job_row["state"]`, `error_class` from `fail_reason` (supervisor-written), `retry_worthy` from the fixed rule table.
- `record_failure(conn, rec)` writes the structured row co-transactionally (caller commits).
- **Test FF-1** (`test_ff1_record_failure_all_fields_present`): all schema fields present and correct after one call.

### REQ-02 — Feeds scorecard + retro; retry_worthy computed correctly

- `query_failed_for_scorecard(conn)` returns all failure rows as dicts — the scorecard reads `error_class` from each for its `outcome='failed'` samples.
- `query_recurring_failures(conn, since_ts=)` returns `(error_class, count)` sorted by count descending — the retro GATHER step reads this to find the recurring failure class.
- **Tests FF-2a..f**: `cost_stop→retry_worthy=0`, `flaky test→retry_worthy=1`, `ring_violation→retry_worthy=0 + escalate=True`, `timeout→retry_worthy=1`, `injection→retry_worthy=0`, `review_reject→retry_worthy=0`.
- **Tests FF-3a,b**: scorecard query returns correct records; empty DB returns `[]`.
- **Tests FF-4a,b**: retro GATHER groups correctly by error_class; `since_ts` window respected.

### REQ-03 — No regression on existing capture paths

- `capture_pre_kill` and `capture_post_mortem` in `forensics.py` are UNTOUCHED — the ABOUTME was extended (additive) and `record_structured()` was appended as a new function.
- **Test `test_req03_existing_forensics_not_broken`**: imports both modules and verifies raw bundle capture still works.
- Pre-existing `tests/factory/test_forensics.py` (4 tests): all 4 still GREEN.
- Total: **18/18 tests passed** (4 legacy + 14 new).

### REQ-04 — RED-first; real SQLite; supervisor-observed property

- RED shown: `ModuleNotFoundError: No module named 'factory.forensics_store'` before implementation.
- All tests use real SQLite in `pytest tmp_path` — no mocks, no fixture DB.
- **Test `test_req04_supervisor_observed_not_worker_self_report`**: a job row with a worker-injected `worker_class` field does not alter the classified `error_class`; `classify()` reads only `fail_reason` and `test_result` (supervisor-written fields).

---

## Design Compliance Notes

- `record_structured()` in `forensics.py` uses a deferred import to avoid circular dependency (forensics_store imports nothing from forensics), wrapped in bare `except Exception: pass` to preserve the never-raises contract of the post-mortem path.
- `classify()` ignores any extra keys in `job_row` — a worker cannot inject its own classification by adding fields.
- `INSERT OR REPLACE` in `record_failure()` is idempotent for crash-resume re-recording.
- Error class vocabulary matches design §6: `timeout`, `cost_stop`, `test_fail`, `review_reject`, `crash`, `ring_violation`, `drift`, `injection`.

---

## Test Count Summary

| Suite | Tests | Result |
|---|---|---|
| `test_forensics_store.py` (new) | 14 | GREEN |
| `test_forensics.py` (existing, REQ-03) | 4 | GREEN (no regression) |
| **Total** | **18** | **18/18 GREEN** |
