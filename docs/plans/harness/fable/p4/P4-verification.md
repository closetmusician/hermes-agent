# P4 Verification — Independent Re-Run (VERIFIER, GOVERNANCE_EXEMPT)

**Verifier role:** produced none of P4; re-ran every suite; fixed nothing; wrote only this doc + two fully-reverted anti-theater neuters.
**Repo:** /Users/yklin/Code/hermes, branch `factory`. **Date:** 2026-07-07.
**Overall P4 gate verdict: PASS-WITH-STAGED.** No non-env test failure. No weakened guard on disk. Crown residency, two-ceiling cost, and crash-resume all verified real (not theater). The 3-consecutive-night flagship live run is STAGED (cannot execute unattended live under this sandbox); its component mechanisms are all synthetically verified.

---

## REQ-01 — Re-run all P4 suites + separate env failures

Run via the canonical `scripts/run_tests.sh` (hermetic `env -i`, per-file subprocess). **13 P4 suites: 117 tests, 117 passed, 0 failed.**

| Suite | Tests | Result |
|---|---|---|
| test_phase_checkpoint | 7 | PASS |
| test_job_store_resumable | 8 | PASS |
| test_crash_resume | 2 | PASS |
| test_forensics | 4 | PASS |
| test_self_heal | 5 | PASS |
| test_model_router | 11 | PASS |
| test_worker_cost | 8 | PASS |
| test_router_integration | 4 | PASS |
| test_overnight_queue | 5 | PASS |
| test_morning_packet | 8 | PASS |
| test_confidence | 35 | PASS |
| test_cost_trend | 14 | PASS |
| test_voice_intake | 6 | PASS |
| **Total** | **117** | **117 PASS / 0 FAIL** |

**Env-failure separation (proven, not assumed).** The 13 P4 suites above touch no git, so the ~14 sandbox failures do not appear in them at all. I confirmed the sandbox failure class directly: `git init -b main <dir>` fails with `fatal: cannot copy '.../hooks/commit-msg.sample' ... Operation not permitted` — a sandbox filesystem denial, not a logic error. The adjacent git-based suites reproduce exactly this: `test_worker_runner.py` (4 fails, all `git init ... returned non-zero exit status 128`) and `test_job_store.py`. These are the pre-existing env failures the prompt described; none is a P4 regression.

**Verdict: VERIFIED.** Non-env failures: **NONE**.

---

## REQ-02 — Lingering-weakening sweep (a guard was left neutered before)

Grepped every P4 module + related source (`worker_runner`, `residency_guard`, `cost_stops`, `job_store`, `scheduler`) for `WEAKENED / always True / short-circuit / disabled / bypass / neuter / return True #`. **Every hit is a legitimate anti-bypass comment, docstring, or invariant label — no neutered guard.**

- `factory/model_router.py::_rung_admissible` (line 129) — **INTACT.** Returns `True` only for non-third-party workers; for `openrouter` it calls `assert_openrouter_allowed(...)` and returns `False` on any exception (fail-closed). It does **NOT** return `True` unconditionally.
- Residency **pre-filter** `admissible_ladder` (line 147) — **INTACT.** Filters via `_rung_admissible`; a false/None-policy repo yields zero openrouter rungs.
- `factory/residency_guard.py::assert_openrouter_allowed` — **INTACT & fail-closed** (None policy → deny).
- `worker_runner.py` wall 2 (line 368, unconditional `assert_openrouter_allowed`) and wall 3 (line 372–381, explicit `_PROVIDER_MODEL_KEY→None` for a disallowed OR key) — **both present and load-bearing.**
- `cost_stops.py:520` "bypasses the CAS guard" — a `_seed_for_test` test-only helper; **no production caller** (grep found only its own definition/docstring).

**Uncommitted-file note (surfaced, not authored by me):** the working tree has one modified source-adjacent file, `tests/factory/test_worker_runner.py` (+29/−17). It is a **STRENGTHENING**, not a weakening: it rewrites `test_antiweakening_guard_call_is_load_bearing` to assert BOTH wall 2 AND wall 3 (a false-policy repo gets no OR key even with the guard neutered; a true-policy repo does). It matches the real source at `worker_runner.py:372–381`. Its 6 wall/residency tests pass (`6 passed, 23 deselected`); its only failures are the git-sandbox class. No `factory/` **source** file is dirty (`git diff --name-only factory/` = 0).

**Verdict: VERIFIED** — model_router pre-filter confirmed intact; no guard weakened on disk.

---

## REQ-03 — CROWN residency (run + anti-theater + restore)

`test_model_router.py` (11 tests) run green. Observed, at every rung / depth:
- `test_false_repo_ladder_has_no_openrouter_rung_at_any_depth` — false-policy ladder = `{claude, codex}` only.
- `test_unknown_policy_ladder_fails_closed_no_openrouter` — policy=None → no OR rung.
- `test_false_repo_429_exhaustion_waits_never_openrouter` — walks the full ladder via `next_rung`; terminal = `WAIT_FOR_CAPACITY` sentinel, never an OR rung.
- `test_next_rung_refilters_every_step` (F1) — an injected stale OR "current" rung on a false repo → re-filtered → WAIT.
- `test_resolve_policy_missing_file_fails_closed` / `test_unparseable_policy_fails_closed` — missing/malformed policy → `default_policy` (allow_openrouter=False) → WAIT on exhaustion.

**Anti-theater:** neutered `_rung_admissible` to `return True` (backup in `$TMPDIR`; source git-clean so `git checkout` restores). Result: **5 residency tests FAILED**, including the crown `test_false_repo_ladder_has_no_openrouter_rung_at_any_depth` (`'openrouter' in {'claude','codex','openrouter'}`). **Restored** via `git checkout -- factory/model_router.py`; re-ran → **11/11 pass**; grep confirms no neuter marker remains.

**Verdict: VERIFIED** — a false-policy job cannot reach OpenRouter at any depth; the tests are load-bearing.

---

## REQ-04 — Cost + crash-resume (run + synthetic reconciliation + anti-theater)

`test_worker_cost.py` (8) + `test_cost_stops.py` (7) + `test_crash_resume.py` (2) = **17/17 pass.**

**Two-ceiling, atomic, concurrent** — `test_openrouter_two_cas_never_exceeds_either_ceiling_concurrent`: 24 threads, each its own `DailyBudgetLedger` connection, race a real SQLite DB; asserts committed OR reservations ≤ $2 AND all reservations ≤ $5, with the two CASes coupled in one `BEGIN IMMEDIATE` transaction (F6 fix — `daily_budget` now composite `(day, provider)` with `__nightly__` + `openrouter` rows; the review's "un-storable sub-ceiling" is resolved). Neither $5 nor $2 is exceedable.

**Crash-resume (real dead pgid)** — `test_crashed_worker_goes_resumable_then_relaunches_from_checkpoint`: spawns a real `os.setsid` process group, `kill -9`s the whole group, waits for OS reap, then drives the scheduler. Tick 1 → job state `RESUMABLE` (F11 fix — not parked terminal at NEEDS_ATTENTION). Tick 2 → `ADMITTED`, resumes at phase `IMPLEMENT` (last cleared = SPEC), **not** re-running SPEC and **not** re-running a merge; brief carries "resume IMPLEMENT". `test_resume_bounded_by_max_resumes` → parks NEEDS_ATTENTION after `MAX_RESUMES` (bounded).

**Synthetic reconciliation (not ledger-vs-itself)** — `test_reconciliation_against_synthetic_provider_within_tolerance`: `provider_reported` is summed from KNOWN token counts × the price table **outside** the ledger (the independent oracle, incl. one crash+resume double-billed phase), then `|provider − ledger| ≤ TOLERANCE`. F8 fix confirmed. `test_reconciliation_catches_drift_beyond_tolerance` proves the check actually catches an under-counted resume re-spend (drift > tolerance ⇒ fail).

**Anti-theater:** neutered the sub-ceiling CAS (`ok_sub = True`) in `cost_stops.reserve_openrouter` (backup in `$TMPDIR`; source git-clean). Result: **2 tests FAILED** (`test_openrouter_subceiling_blocks_third_dispatch`, `test_openrouter_two_cas_never_exceeds_either_ceiling_concurrent`). **Restored** via `git checkout`; re-ran → **8/8 pass**; no neuter marker remains.

**Verdict: VERIFIED** — no ceiling breachable; resume neither re-runs a merge nor is theater; reconciliation uses a synthetic provider figure.

---

## REQ-05 — Morning surfaces + confidence

`test_morning_packet.py` (8) + `test_confidence.py` (35) run green.

- **Batch-approve nonce** — `test_batch_approve_still_uses_broker_nonce`: `batch_approve` calls `held_store.transition(aid,'held','approved')` **once per action ID** (2 IDs → exactly 2 transition calls, no extras); source `morning_packet.batch_approve` (line 168) loops per-action with no direct-merge shortcut. `test_irreversible_card_never_batch_auto`: irreversible cards excluded from batch. **No bulk bypass.**
- **Confidence reproducibility** — `test_recompute_from_stored_inputs_reproduces_exact_score`: recomputing from stored inputs yields a bit-identical score; `test_recompute_requires_same_weights` pins weights as part of the reproducer; `test_high_quality_vs_low_quality_differ_by_more_than_0_3`: two differing-risk jobs differ by >0.3 (material).
- **PR card 6 fields** — `factory/pr_card.py` `PRCard` carries all required decision fields as non-default dataclass members: summary, reasoning, alternatives, reversibility, diff_stat, confidence, risk (+ state); long payloads never truncated.

**Verdict: VERIFIED** — batch-approve does not bypass the nonce; confidence reproducible and materially differentiated.

---

## REQ-06 — Staged vs verified (honest split)

| Gate element | Status | Basis |
|---|---|---|
| 3-consecutive-night flagship (≥5 tasks intake→PR-ready, zero human before 7am, ×3 nights) | **STAGED** | A live unattended overnight run needs a real broker + real provider spend + real worktrees; cannot run under this read-only sandbox (git blocked). Its mechanisms (queue, scheduler, packet) are unit-verified below. |
| Live 429 failover to a lower tier | **SYNTHETIC-VERIFIED** | `test_router_integration` + `test_model_router`: 429 re-launch recomputes the rung from live policy via the real `WorkerRunner.build_worker_env` seam; step-down + WAIT sentinel proven. No live provider 429 forced. |
| Crashed-worker resume from checkpoint | **REAL-VERIFIED** | `test_crash_resume`: genuine `os.setsid` process group, real `kill -9`, real SQLite, real scheduler ticks → RESUMABLE → resume at IMPLEMENT. |
| Two-ceiling cost bound ($5 / $2) | **REAL-VERIFIED (concurrent)** | 24-thread race on a real DB with independent connections; atomic two-CAS. |
| Cost matches provider within tolerance | **SYNTHETIC-VERIFIED** | Reconciled against a provider figure computed outside the ledger; drift-catching test present. Not reconciled against a real invoice. |
| Voice-note → spec'd task | **SYNTHETIC-VERIFIED** | `test_voice_intake` (6) exercises the transcribe→splitter→card path with injected transcription; no live audio/STT round-trip run here. |
| Risk score from code-review-graph on PR cards | **PLACEHOLDER-HONEST** | `pr_card` documents `risk.source='placeholder'` when code-review-graph is unavailable; the field exists, the live-graph source is not exercised in these tests. |

---

## Claim table

| # | Requirement | Verdict |
|---|---|---|
| REQ-01 | Re-run all 13 suites; separate env failures | VERIFIED (117/117; env class proven via direct git-init) |
| REQ-02 | Lingering-weakening sweep; pre-filter intact | VERIFIED (no neuter; `_rung_admissible` intact) |
| REQ-03 | Crown residency every-rung + anti-theater | VERIFIED (5 fail on neuter, restored, 11/11) |
| REQ-04 | Two-ceiling + crash-resume + synthetic reconciliation + anti-theater | VERIFIED (2 fail on neuter, restored, 17/17) |
| REQ-05 | Batch-approve nonce + confidence reproducibility + 6 fields | VERIFIED |
| REQ-06 | Staged-vs-verified honesty | VERIFIED (table above) |

## BLOCKING list
**NONE.** No non-env test failure; no weakened guard on disk; both anti-theater neuters fully reverted; git tree clean for all `factory/` source.

## Git tree at end
`git status --short` shows only: ` M tests/factory/test_worker_runner.py` (pre-existing, not authored by verifier — a wall-3 test strengthening) and `?? hermes-evolution` (untracked, unrelated to P4). No `factory/` source dirty. Both `model_router.py` and `cost_stops.py` restored to committed state.
