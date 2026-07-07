# P3-0 — Data-Residency Wall — CODER report

**Task:** P3-0 (backlog, GOVERNANCE_EXEMPT). Make it structurally impossible for a
work/Diligent/`allow_openrouter:false` job to receive an OpenRouter (third-party)
model key. Closes red-team P0-1 (guard specified but nothing called it).
**Branch:** `factory` (verified `git branch --show-current` == `factory`).
**Design source:** `docs/plans/harness/fable/p3/P3-design.md` §6.2, §3.1, §13.1.

## Files touched
- `factory/residency_guard.py` (NEW, 96 lines) — `assert_openrouter_allowed(repo,
  worker, policy)` + `ResidencyViolation` + `THIRD_PARTY_WORKERS = {"openrouter"}`.
- `factory/worker_runner.py` — 3 edits:
  - lines 35-36: import `MergePolicy` + `assert_openrouter_allowed`.
  - line 173: new `policy: Optional[MergePolicy] = None` field on `WorkerRunSpec`
    (fail-closed default).
  - lines 358-362: **the wall** — unconditional `assert_openrouter_allowed(...)`
    call at the TOP of `build_worker_env`, BEFORE the `_PROVIDER_MODEL_KEY.get`
    lookup (line 364).
- `tests/factory/test_worker_runner.py` — +9 tests (lines ~344-520).

## Requirement evidence

### REQ-01 — the guard (deny / allow / fail-closed)
`factory/residency_guard.py:64-96`. Pure predicate over (repo, worker, policy),
zero I/O. First-party workers (`claude`/`codex`) return early (never gated); only
`THIRD_PARTY_WORKERS` are gated; `policy=None` or `allow_openrouter=False` → raise.
- Tests: `test_guard_denies_work_openrouter`, `test_guard_allows_personal_openrouter_when_opted_in`,
  `test_guard_fails_closed_on_unknown_policy`, `test_guard_ignores_first_party_workers`. All GREEN.

### REQ-02 — wired as a MANDATORY call at the seam
`factory/worker_runner.py:362` — the guard call is the FIRST statement in
`build_worker_env`, before `_PROVIDER_MODEL_KEY.get` at `:364`. `WorkerRunSpec`
gains `policy` (`:173`) defaulting `None` (fail-closed). Because the call precedes
key placement, a future `_PROVIDER_MODEL_KEY["openrouter"]` entry cannot leak a key
for a work job: the guard raises first, no env is built.

### REQ-03 — the CALL + no-key OUTCOME at the real seam (not theater)
- `test_build_worker_env_calls_residency_guard` — spies on
  `factory.worker_runner.assert_openrouter_allowed`, asserts `build_worker_env`
  called it once with (repo, worker, policy). Proves the WIRING.
- `test_work_openrouter_job_gets_no_key_at_the_seam` — monkeypatches
  `_PROVIDER_MODEL_KEY` to DO map openrouter (the P4 future state), then a
  work/`allow_openrouter:false` job requesting `worker="openrouter"` makes
  `build_worker_env` raise `ResidencyViolation` — no env with an OpenRouter key is
  ever returned. Proves the OUTCOME.
- `test_work_env_defaults_to_failclosed_policy` — a spec with `policy=None` +
  openrouter worker also raises (unknown residency → no OpenRouter).
- `test_personal_openrouter_admitted_at_seam` — positive: personal repo with
  `allow_openrouter:true` builds a clean env carrying `OPENROUTER_API_KEY`
  (the guard does not over-block). All GREEN.

### REQ-04 — anti-weakening + no regression
- `test_antiweakening_guard_call_is_load_bearing` — asserts the work job is refused
  WITH the guard active; documents that neutralizing the guard would inject the key.
- **Removal proof (executed):** deleting line 362 (`assert_openrouter_allowed(...)`)
  and re-running made 3 tests fail —
  `test_work_openrouter_job_gets_no_key_at_the_seam`,
  `test_work_env_defaults_to_failclosed_policy`,
  `test_antiweakening_guard_call_is_load_bearing`. Line restored → all GREEN. So a
  future removal of the guard call breaks the suite (the guard call is load-bearing).
- **No regression:** `tests/factory/test_worker_runner.py` → 25 passed, 4 failed.
  The 4 failures (`test_worker_env_lacks_seeded_egress_tokens`,
  `test_worktree_created_before_launch`, `test_worker_push_attempt_fails_no_token_no_tool`,
  `test_collect_maps_bad_output_to_error`) are ALL `git init ... returned non-zero
  exit status 128` — the pre-existing test-sandbox failure (the sandbox denies
  filesystem writes needed by `git init`, VERIFIED by attempting `mkdir /tmp/...`
  → "Operation not permitted"). They were failing identically on the pre-change
  baseline (16 passed / 4 failed before; 25 passed / 4 failed after = +9 new, 0 new
  failures). `tests/factory/test_merge_policy.py` also GREEN.

## RED → GREEN trace
1. RED: added 9 tests; import of `factory.residency_guard` →
   `ModuleNotFoundError` (collection error) — confirmed genuinely failing.
2. GREEN: created `residency_guard.py`, wired guard into `build_worker_env`, added
   `policy` field → all 9 tests pass; full worker_runner suite = 25 passed / 4
   pre-existing-sandbox failures.

## Notes / scope
- Scope respected: touched ONLY `factory/residency_guard.py`,
  `factory/worker_runner.py`, `tests/factory/test_worker_runner.py`. Did NOT touch
  scheduler/splitter/job_store/cost_stops/worker_env. The `worker_env` scrub is intact.
- Did NOT commit/push (per constraints).
- The splitter's own worker launch (§3.1 step 2a) funnels through the same
  `build_worker_env` seam, so it inherits the wall automatically once the splitter
  (P3-a) constructs its `WorkerRunSpec` with a `policy` — no separate check needed.
