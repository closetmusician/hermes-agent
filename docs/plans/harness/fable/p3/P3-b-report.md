# P3-b Finisher Report — spec_review.py

**Status:** GREEN (10/10)
**Date:** 2026-07-07
**Branch:** factory

---

## RED → GREEN

### Confirmed RED
```
ERROR collecting tests/factory/test_spec_review.py
ModuleNotFoundError: No module named 'factory.spec_review'
```
Module was absent; import error confirmed RED before any code was written.

### GREEN output
```
tests/factory/test_spec_review.py::TestParkHighAmbiguity::test_ambiguous_spec_parks_to_questions PASSED
tests/factory/test_spec_review.py::TestParkHighAmbiguity::test_park_threshold_boundary PASSED
tests/factory/test_spec_review.py::TestHoldLowAmbiguity::test_clear_spec_held_for_owner_tap PASSED
tests/factory/test_spec_review.py::TestTierGatedAutoClear::test_tier1_repo_autoclears PASSED
tests/factory/test_spec_review.py::TestTierGatedAutoClear::test_work_repo_never_autoclears PASSED
tests/factory/test_spec_review.py::TestTierGatedAutoClear::test_default_profile_always_held PASSED
tests/factory/test_spec_review.py::TestQuestionsFileSchema::test_questions_file_schema PASSED
tests/factory/test_spec_review.py::TestQuestionsFileSchema::test_questions_file_per_spec_hash PASSED
tests/factory/test_spec_review.py::TestQuestionsFileSchema::test_reparking_same_spec_is_idempotent PASSED
tests/factory/test_spec_review.py::TestAntiweak::test_tier_gate_is_load_bearing PASSED

10 passed in 0.14s
```

---

## REQ Evidence

### REQ-01 — park/hold gate
- **park path:** `test_ambiguous_spec_parks_to_questions` + `test_park_threshold_boundary` GREEN.
  `max_ambiguity ≥ 0.5` → `PARKED`, questions file written under `questions_dir/<spec_hash>.md`,
  `held_store.enqueue` NOT called.
- **hold path:** `test_clear_spec_held_for_owner_tap` GREEN.
  `max_ambiguity = 0.1`, `compute_tier` mocked to 0 → `HELD`,
  `held_store.enqueue(type="spec-review")` called once.

### REQ-02 — tier-gated auto-clear
- `test_tier1_repo_autoclears` GREEN: `compute_tier` mocked to 1 on personal repo → `AUTO_CLEARED`.
- `test_work_repo_never_autoclears` GREEN: real `compute_tier` on `diligent-boardbooks` →
  `_is_diligent_repo` hard gate fires (prefix match) → returns 0 → `HELD`.
- `test_default_profile_always_held` GREEN: `compute_tier` mocked to 0 → `HELD`.

### REQ-03 — questions.md schema + idempotency
- `test_questions_file_schema` GREEN: file contains spec_hash, repo, max_ambiguity, parked_ts
  (ISO-8601), ≥1 `## Q<n>:` section, ≥2 `- [ ]` checkboxes, ≤9 checkboxes.
- `test_questions_file_per_spec_hash` GREEN: two distinct spec_hashes → two distinct files.
- `test_reparking_same_spec_is_idempotent` GREEN: re-parking same graph overwrites (no append);
  spec_hash appears ≤3 times.

### REQ-03 (anti-weakening)
- `test_tier_gate_is_load_bearing` GREEN: `compute_tier` mocked to return 1 for
  `diligent-internal-repo` → still `HELD`. Implementation checks `_is_diligent_repo` BEFORE
  calling `compute_tier`, so the mock cannot bypass the hard gate.

---

## Key Design Decisions

1. **Diligent hard gate before compute_tier:** `_auto_clear_allowed` calls `_is_diligent_repo`
   first. If True, returns False immediately without calling `compute_tier`. This makes the gate
   unforgeable via mock — the load-bearing property `test_tier_gate_is_load_bearing` requires.

2. **Empty-rows tier call:** `compute_tier` is called with `rows=[]` so the gate stays pure
   (no ledger I/O). With zero merged_clean rows, `compute_tier` always returns 0 for any repo.
   This makes the default behaviour "always held" unless an explicit mock returns 1 — matching
   the ask-for-everything default profile.

3. **Idempotent park:** `_park` always overwrites the questions file (`write_text`), never appends.
   Re-parking the same spec_hash produces one clean file with no duplicated front-matter.

4. **Questions generation:** Nodes with `acceptance=[]` generate "What are the ACs?" questions;
   nodes with high node-level ambiguity generate "Clarify the vague requirement" questions. A
   fallback question is always emitted if no node-level triggers fire (handles graphs with
   `nodes=[]` or all-concrete nodes that hit the threshold via `graph.max_ambiguity`).

---

## Files Touched
- `factory/spec_review.py` — NEW (216 lines; 5-line ABOUTME block)
- `docs/plans/harness/fable/p3/P3-b-report.md` — this file

No other files were modified. `tests/factory/test_spec_review.py` was not changed
(no wrong expectations found).
