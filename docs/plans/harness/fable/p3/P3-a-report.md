# P3-a Completion Report

**Task:** P3-a-finish — task_splitter + ambiguity_rubric + task_graph tests GREEN  
**Mode:** backlog (GOVERNANCE_EXEMPT, no acceptance-test pipeline)  
**Branch:** factory  
**Date:** 2026-07-07

---

## RED → GREEN summary

### Prior state (on entry)
- `factory/ambiguity_rubric.py` (173 ln) — complete and correct; no edits needed.
- `factory/task_graph.py` (281 ln) — complete and correct; no edits needed.
- `tests/factory/test_task_splitter.py` (490 ln) — 21 tests, all RED (import error: `ModuleNotFoundError: No module named 'factory.task_splitter'`).
- MISSING: `factory/task_splitter.py`, `tests/factory/test_ambiguity_rubric.py`, `tests/factory/test_task_graph.py`.

### Work done
1. Created `factory/task_splitter.py` — the public `split()` entry point, `_call_ai_worker()` (sole AI boundary, patched in tests), and `_build_prompt()`.  The implementation: (a) injection-scans spec_text and fences with `[FENCED SPEC …]` if dirty; (b) calls `_call_ai_worker`; (c) parses JSON and validates via `task_graph.from_json()` (fail-closed → `ParkedSpec` on any error); (d) overwrites each node's ambiguity with `ambiguity_rubric.grade()` (anti-gaming); (e) sets `graph.max_ambiguity`.
2. One fix required: the injection test asserted `"FENCED" in prompt` but `injection_scan.fence()` wraps with `[EXTERNAL CONTENT …]`.  Fixed by wrapping the fenced spec with an explicit `[FENCED SPEC …]` header in `task_splitter._build_prompt`.
3. Created `tests/factory/test_ambiguity_rubric.py` (25 tests) — weight constants, undefined-AC, vague-dep (5 hedge patterns), missing-field, clamping, determinism, anti-gaming property (design §3.3).
4. Created `tests/factory/test_task_graph.py` (31 tests) — dataclass fields, validate() happy paths (single node, linear chain, diamond DAG, empty ACs pass), validate() failure paths (cycle, 3-node cycle, dangling edge, invalid task_type, duplicate id, bad node types), from_json() happy paths (minimal, optional defaults, multi-node chain), from_json() failure paths (non-dict, nodes-not-list, missing id/title, cyclic, dangling, invalid task_type), spec_hash().

---

## Real pytest output

```
▶ running per-file parallel test suite via run_tests_parallel.py
  (TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0; clean env)
Discovered 3 test files (~77 tests) under [
  'tests/factory/test_task_splitter.py',
  'tests/factory/test_ambiguity_rubric.py',
  'tests/factory/test_task_graph.py'
]; running with -j 36

[ 32.5% |    25/~77 | ✓25 | ✗ 0] ✓ tests/factory/test_ambiguity_rubric.py (25✓, 0.8s)
[ 59.7% |    46/~77 | ✓46 | ✗ 0] ✓ tests/factory/test_task_splitter.py (21✓, 0.8s)
[100.0% |    77/~77 | ✓77 | ✗ 0] ✓ tests/factory/test_task_graph.py (31✓, 0.8s)

=== Summary: 3 files, 77 tests passed, 0 failed (100% complete) in 0.8s (36 workers) ===
```

---

## REQ evidence

| REQ | Evidence | Status |
|-----|----------|--------|
| REQ-01 (verify rubric + graph; write their tests; fix only if broken) | `ambiguity_rubric.py` and `task_graph.py` verified complete — zero edits. `test_ambiguity_rubric.py` (25 tests) + `test_task_graph.py` (31 tests) GREEN. | DONE |
| REQ-02 (build task_splitter.py satisfying test_task_splitter.py) | `factory/task_splitter.py` created. All 21 tests in `test_task_splitter.py` GREEN. Anti-gaming: splitter overwrites AI self-score with `grade(node)` (test `test_splitter_attaches_rubric_scores_not_ai_self_scores`). Injection-scanned: spec fenced before AI prompt (test `test_splitter_scans_injection_before_ai`). | DONE |
| REQ-03 (all 3 suites GREEN; anti-gaming test passing; injection-scan test passing) | 77/77 tests GREEN. Anti-gaming: `TestDeterminismAndAntiGaming::test_ai_self_score_high_does_not_affect_rubric` + `TestTaskSplitter::test_splitter_attaches_rubric_scores_not_ai_self_scores`. Injection scan: `TestTaskSplitter::test_splitter_scans_injection_before_ai`. | DONE |

---

## Files created / modified

| File | Action | Lines |
|------|--------|-------|
| `factory/task_splitter.py` | Created | ~155 |
| `tests/factory/test_ambiguity_rubric.py` | Created | ~238 |
| `tests/factory/test_task_graph.py` | Created | ~277 |
| `docs/plans/harness/fable/p3/P3-a-report.md` | Created | this file |
| `factory/ambiguity_rubric.py` | Verified only — no edits | — |
| `factory/task_graph.py` | Verified only — no edits | — |
