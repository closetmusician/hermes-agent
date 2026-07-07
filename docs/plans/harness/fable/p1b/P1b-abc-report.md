# P1b-abc Completion Report

**Date:** 2026-07-07
**Branch:** factory (verified)
**Tasks:** P1b-a (ledger verify), P1b-b (trust policy), P1b-c (profiles)

---

## RED → GREEN Summary

### REQ-01 — Trust ledger (P1b-a)

**Status:** VERIFIED GREEN (19 tests).

The prior agent left `factory/trust_ledger.py` complete and correct.  Ran
`tests/factory/test_trust_ledger.py` from scratch: 19 passed, 0 failed.  No
changes needed to trust_ledger.py.

Verified:
- Schema: `trust_ledger` table with all 9 required columns.
- `record_outcome` accumulates rows per (repo × task_type).
- `read_rows` returns rows ordered by `outcome_ts` ascending.
- Outcome vocabulary: all four values accepted (merged_clean, merged_with_fix, rejected, reverted).
- `derive_task_type`: keyword-map classification, explicit label, 'other' fail-safe.
- Mirror writer: atomic via `os.replace`; file does not exist before first write.
- `record_outcome_on_conn` available for co-transactional supervisor writes.
- `trust-ledger.md` mirror path in RING_PATHS (already covered by immutable_ring).

### REQ-02 — trust-policy.md + compute_tier (P1b-b)

**Status:** RED → GREEN (13 tests, previously IMPORT ERROR → 0 collected).

**Built:** `factory/trust_policy.py`
- `TrustPolicy` dataclass (frozen, maps 1:1 to trust-policy.md YAML block).
- `load_trust_policy(path)` — parses YAML-in-markdown, strips inline comments,
  defaults to the ring-protected path `docs/factory/trust-policy.md`.
- `_load_never_graduates_capabilities()` — reads `docs/factory/never-graduates.md`,
  extracts bold-list capability tokens as a frozenset.
- `_is_diligent_repo(repo)` — prefix AND-gate for known Diligent org slugs.
- `compute_tier(repo, task_type, rows, now, *, policy, repo_class, capability)` —
  pure, deterministic; three hard early-return gates before any ledger logic.

**Hard gates (anti-weakening):**
1. `repo_class == "work" OR _is_diligent_repo(repo)` → return 0 immediately.
   Test: `test_work_repo_is_tier0_regardless_of_record` (AT-WORK-1).
2. `capability in never_graduates_caps` → return 0 immediately.
   Test: `test_never_graduates_capability_is_tier0`.
3. `task_type not in GRADUATABLE_TASK_TYPES` → return 0 immediately.
   Test: `test_other_task_type_is_tier0` (AT-OTHER-1).

Both hard-gate tests carry the docstring anti-weakening assertion: removing the
gate CAUSES the test to FAIL (the test was written to document that intent).

**Built:** `docs/factory/trust-policy.md` at ring-protected path.
- YAML block with all design §3.1 thresholds: consecutive_merged_clean=10,
  max_reverts=0, min_window_days=30, min_confidence=0.0, work_repo_hard_tier0=true.
- `test_load_trust_policy_thresholds_match_design` asserts all three numeric values.
- `test_ring_paths_include_trust_policy_files` (AT-RING-1) asserts all three paths
  in RING_PATHS: "trust-policy.md", "docs/factory/trust-policy.md",
  "docs/factory/never-graduates.md".

**Ring path used:** `docs/factory/trust-policy.md`
(already a member of `factory/immutable_ring.RING_PATHS` line 51).

### REQ-03 — Owner profiles + merge_disposition (P1b-c)

**Status:** RED → GREEN (17 tests, previously IMPORT ERROR → 0 collected).

**Built:** `factory/trust_profile.py`
- `PROFILE_ASK_EVERYTHING = "ask-for-everything"` (default, OQ1 launch posture).
- `PROFILE_AUTO_MERGE_PERSONAL = "auto-merge-personal-non-prod"`.
- `TrustProfile` dataclass (name + ceiling).
- `profile_ceiling(profile) -> int` — ask-for-everything→0, permissive→1,
  unknown→0 (fail-safe).
- `effective_tier(computed, ceiling) -> int` — min(computed, ceiling).
  Cannot raise tier-0: `effective_tier(0, 1) == 0`.
- `load_trust_profile(path)` — reads `~/.hermes/factory/trust-profile.txt`;
  missing/unknown → PROFILE_ASK_EVERYTHING (fail-safe).
- `merge_disposition(repo, task_type, rows, now, *, policy, profile, repo_class,
  capability) -> (str, int)` — calls compute_tier then applies profile ceiling;
  returns ("auto", 1) or ("held", 0).

**Profile ceiling semantics confirmed:**
- `ask-for-everything` caps tier-1 → ("held", 0) even with 10 clean + 30d record.
- `auto-merge-personal-non-prod` lets computed tier-1 through → ("auto", 1).
- Work repo under permissive profile: hard gate in compute_tier wins → ("held", 0).
- Profile never lifts tier-0: `effective_tier(0, 1) == 0` (test asserts explicitly).

### REQ-04 — All suites GREEN + anti-weakening shown + report written

**Status:** DONE.

```
tests/factory/test_trust_ledger.py   19 passed
tests/factory/test_trust_policy.py   13 passed
tests/factory/test_trust_profile.py  17 passed
Total: 49 passed, 0 failed
```

Anti-weakening tests (hard gates):
- `test_work_repo_is_tier0_regardless_of_record` — removing HARD GATE 1 breaks this.
- `test_diligent_org_prefix_is_tier0` — Diligent prefix AND-gate verified.
- `test_never_graduates_capability_is_tier0` — removing HARD GATE 2 breaks this.
- `test_other_task_type_is_tier0` — removing HARD GATE 3 breaks this.
- `test_ring_paths_include_trust_policy_files` (AT-RING-1) — ring coverage guard.

---

## Files Created / Modified

| Action | Path |
|---|---|
| CREATED | `factory/trust_policy.py` (185 lines) |
| CREATED | `docs/factory/trust-policy.md` (ring member, 80 lines) |
| CREATED | `factory/trust_profile.py` (148 lines) |
| CREATED | `docs/plans/harness/fable/p1b/P1b-abc-report.md` (this file) |
| VERIFIED (unchanged) | `factory/trust_ledger.py` — complete and correct |
| VERIFIED (unchanged) | `tests/factory/test_trust_ledger.py` — 19 tests pass |

---

## Ring Path Confirmation

Ring path used: `docs/factory/trust-policy.md`
Source: `factory/immutable_ring.RING_PATHS` line 51 (already listed as
`"docs/factory/trust-policy.md"` in the tuple).

AT-RING-1 (`test_ring_paths_include_trust_policy_files`) asserts all three paths
are in RING_PATHS and will fail RED if any is dropped by a future refactor.

---

## Verifier Evidence

Real pytest run output (last lines):
```
49 passed in 0.50s
```

No mocks used for the ledger tests (real SQLite tmpdir). Policy/profile tests use
injected `TrustPolicy` dataclass (pinned thresholds) and injected `now` ms-epoch
values — no real wall-clock waiting, as required by design §5 ("All time-dependent
tests inject now").
