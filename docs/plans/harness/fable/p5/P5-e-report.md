# ABOUTME: P5-e task report — skill-improve + pattern bank implementation.
# ABOUTME: Documents REQ-01..04 evidence, test counts, RED shown, GREEN achieved,
# ABOUTME: files touched, and pre-existing regressions (none from this task).
# ABOUTME: Authoritative source: docs/plans/harmes/fable/p5/P5-design.md §4.3, §7.2.
# ABOUTME: Branch: factory.

# P5-e Report — Skill-Improve + Pattern Bank

**Task:** P5-e (backlog, GOVERNANCE_EXEMPT)
**Branch:** `factory`
**Files delivered:**
- `factory/skill_improve.py` (new)
- `factory/pattern_bank.py` (new)
- `tests/factory/test_skill_improve.py` (new — 17 tests)

---

## REQ-01 — Skill-improve weekly pass + ring gate + held action

**Evidence:**
- `factory/skill_improve.py::run_weekly_pass` reads scorecard_samples + failure_forensics via `_gather_history`, proposes a skill-file diff via injected `diff_fn`, calls `check_retro_diff` (the propose-gate, Wall 1) BEFORE `broker.enqueue_action`.
- `check_retro_diff` in `skill_improve.py` is the module-level name (importable from `factory.retro_ring_gate` after P5-b; shims to `immutable_ring.check_diff` when P5-b absent). Tests patch it directly by name.
- Every approved diff is enqueued as type `skill_diff`, carrying `diff_sha256 = sha256(diff_bytes)` (v2-C3 hash-pin). Never written to disk directly.
- Guard-adjacent skill flag (v2-C5): when the diff touches a `_GUARD_ADJACENT_SKILLS` member, the held-action summary includes "guard-adjacent" for extra owner scrutiny.

**Tests (SK-1, SK-2):**
- `test_weekly_pass_enqueues_held_action` — spy on `enqueue_action`; asserts called with `type=skill_diff`, payload carries `diff` + `diff_sha256`; hash verified.
- `test_weekly_pass_does_not_write_diff_to_disk` — asserts skill file mtime unchanged after pass.
- `test_ring_diff_rejected_no_enqueue[cost_stops|broker_server|trust_policy]` (3 parametrized) — gate raises `RingViolation`; `enqueue_action` never called; result `status=rejected`.
- `test_gate_called_before_enqueue` — call-order spy asserts gate precedes enqueue.
- `test_guard_adjacent_flag_on_card` — summary contains "guard" or "⚠" for guard-adjacent diffs.

---

## REQ-02 — Pattern bank: stored patterns + cross-repo query

**Evidence:**
- `factory/pattern_bank.py::PatternBank` stores `merged_clean` patterns in SQLite (schema: `patterns` table + `patterns_fts` FTS5 virtual table) co-located in the jobs DB.
- `query(task_type, limit, cross_repo)` fans out to `_try_cross_repo_search` (code-review-graph boundary) when `cross_repo=True`; degrades gracefully (same-repo-only) when the tool is unavailable offline.
- Module-level `add_pattern` / `query_patterns` aliases available for supervisor pre-spawn hooks.

**Tests (SK-3, SK-4):**
- `test_query_returns_stored_pattern` — stored bugfix pattern returned on query.
- `test_query_no_match_returns_empty` — [] on unmatched task_type, no crash.
- `test_query_module_level_alias` — module-level `query_patterns` works.
- `test_query_cross_repo_fallback_documented` — `_try_cross_repo_search` patched to raise `RuntimeError`; same-repo hits still returned (no crash).
- `test_add_pattern_appears_in_query` — add → query round-trip.
- `test_rejected_job_does_not_add_pattern` — `outcome='rejected'` → pattern NOT stored.
- `test_failed_job_does_not_add_pattern` — `outcome='failed'` → pattern NOT stored.
- `test_merged_clean_is_stored` — `outcome='merged_clean'` → pattern stored.

**Cross-repo note (design §4.3):** code-review-graph is available as an MCP plugin this session. The `_try_cross_repo_search` function imports `cross_repo_search_tool` at call time (late import), so offline code paths don't fail on import. Cold repos degrade to same-repo-only. UNVERIFIED: actual cross-repo MCP invocation was not tested with a real graph (plugin not built for this repo in this run).

---

## REQ-03 — Pattern bank is READ-ONLY advisory input

**Evidence:**
- `PatternHit` is a `NamedTuple` — plain data, no `.apply()`, `.patch()`, `.enqueue()` methods. Structural boundary (no code path from PatternHit → broker or disk).
- Documented in `PatternBank.query` docstring and `PatternHit` docstring.

**Tests:**
- `test_pattern_is_a_dataclass_not_callable` — asserts no `.apply/.patch/.enqueue` attributes on returned hit.
- `test_pattern_content_alone_cannot_call_broker` — malicious-looking diff content in summary is stored as plain `str`; verified not callable.

---

## REQ-04 — RED shown, GREEN achieved

**RED evidence (verified):** Before implementation, `python -m pytest tests/factory/test_skill_improve.py` reported:
```
ImportError while importing test module ... 'factory.skill_improve'
collected 0 items / 1 error
```
This is a genuine RED: the tests were written first (TDD), failed due to missing module, not due to incorrect test logic.

**GREEN:** After implementation: **17/17 passed** in 0.26s.

---

## Regression check

Full factory test suite (excluding pre-existing broken `test_watchdogs.py::TestDriftWatchdogChurning::test_forensics_captured_before_kill`): **542 passed, 0 failures**.

The watchdogs test was already failing before this task (it asserts `"kill:7777"` but the mock receives `"kill:-7777"` — a pre-existing off-by-one on pgid sign convention in the test, unrelated to P5-e). No new failures introduced.

---

## Summary

| Requirement | Status | Evidence |
|---|---|---|
| REQ-01: weekly pass → skill diff → ring gate → held action (never silent) | DONE | `run_weekly_pass`; gate called before enqueue; hash-pinned payload |
| REQ-01: ring/guard-path diff rejected | DONE | SK-2 (3 parametrized), gate-ordering test |
| REQ-01: guard-adjacent flag (v2-C5) | DONE | SK-2 guard flag test |
| REQ-02: stored pattern returned for matching task | DONE | SK-3 (4 tests) |
| REQ-02: cross-repo query degrades gracefully offline | DONE | SK-3 cross-repo fallback test |
| REQ-02: pattern populated on merged_clean only | DONE | SK-4 (4 tests) |
| REQ-03: pattern advisory-only (no apply/enqueue) | DONE | SK-3 advisory tests; structural NamedTuple boundary |
| REQ-04: RED shown, TDD | DONE | ImportError at RED; 17/17 GREEN |
