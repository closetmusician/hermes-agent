# ABOUTME: Adversarial review of P3-design.md (task-splitter + fleet scheduler + intake).
# ABOUTME: Read-only findings artifact. Crown risks: data-residency (work code → OpenRouter)
# ABOUTME: and scheduler bounding (concurrency cap 3, $5/night budget, depends_on ordering).
# ABOUTME: Every CONFIRMED/WRONG verdict is backed by a file:line the reviewer opened on branch `factory`.

# P3 Adversarial Review — Fleet Scheduler + Intake + Residency

**Verdict: SHIP-WITH-FIXES.** The reuse claims are honest and the cited seams are almost all
correct. But two design-level gaps in the crown-risk areas must be pinned into P3 (not deferred
to P4) or the design ships a false safety story: (1) the residency guard is specified as a
standalone unit-tested function that **nothing in the P3 launch path calls** — the actual
model-key injection seam (`worker_runner.build_worker_env`) has no residency check, so residency
is *documented* but not *enforced*; (2) the "scheduler is the sole writer" claim contradicts the
existing code — the per-node `Supervisor` instances the scheduler spawns each call
`store.transition()`/`enqueue_job()` themselves, so P3 has **N+1 concurrent writers** against a
store whose own docstring says "must only be used from supervisor.py."

Counts: **P0 = 2, P1 = 4, P2 = 2.**

Top-3 with exact fix:
1. **[P0] Residency guard is unwired** — pin `assert_openrouter_allowed(repo, policy)` as a
   mandatory call inside `worker_runner.build_worker_env` (and the splitter's worker launch),
   with a P3 RED test that a `worker="openrouter"` run for an `allow_openrouter=false` repo
   raises before `_worker.launch`. Do not defer the enforcement seam to P4.
2. **[P0] Sole-writer contract broken under fan-out** — the design must either (a) route ALL job
   state writes through the scheduler and strip `store.transition` out of `Supervisor`, or (b)
   explicitly document that N supervisor threads + scheduler share ONE `JobStore` object serialized
   by its `threading.Lock`, and add a RED test proving cross-thread write serialization + the
   busy_timeout claim under real concurrency. As written §4.7's "only the scheduler transitions
   job states" is false against `supervisor.py:338/476/477/521`.
3. **[P1] Splitter-derived per-node text is a second injection surface** — the scan runs on the
   *original* spec (splitter §3.1 step 1), but the AI then emits per-node `acceptance[]`/`title`
   text that becomes the enqueued job spec and is NOT re-scanned before a worker sees it. Fix:
   run `injection_scan.scan` on each node's rendered spec at enqueue time, or route node enqueue
   back through `supervisor.enqueue_from_intake` (which fences at `supervisor.py:215`).

---

## REQ-01 — Reuse claims verified at file:line (branch `factory`)

| Claim | Cited loc | Verdict | Evidence |
|---|---|---|---|
| JobStore sole-writer, WAL+FULL | `job_store.py:156` / `:177` | **CONFIRMED** | class at :156; `PRAGMA journal_mode=WAL` :177, `synchronous=FULL` :178; docstring :159-160 "SOLE writer … must only be used from supervisor.py". |
| Supervisor-lock steal-if-stale | `job_store.py:332` | **CONFIRMED** | `claim_supervisor_lock` :332; conditional-UPDATE steal `WHERE heartbeat_ts < stale_before` :362-366; TTL 15min :142. |
| `enqueue_job` = sole INSERT path | `job_store.py:186` | **CONFIRMED** | :186; docstring "ONLY INSERT path". |
| `depends_on` ABSENT / `job_deps` is NEW | §1, §4.2 | **CONFIRMED** | grep `depends_on\|job_deps` over `factory/` returns EMPTY — genuinely new schema, as claimed. |
| `busy_timeout` is a NEW pragma | §4.7 | **CONFIRMED** | grep `busy_timeout` over `factory/` EMPTY — not set today; P3 adds it. Honest. |
| `ready_jobs()` / `write_build_jobs_mirror` | §4.2 / `:527` | **PARTIAL** | mirror at `:527` CONFIRMED; `ready_jobs` does not exist (correctly presented as new). |
| Supervisor `run_until_approval` / `build_production_supervisor` no-mint | `supervisor.py:236` / `:578` | **CONFIRMED** | :236 and :578 exact; no-mint `authority=None` :616. |
| `DailyBudgetLedger.try_reserve` atomic CAS | `cost_stops.py:258` / BEGIN IMMEDIATE `:283` | **CONFIRMED** | :258; `BEGIN IMMEDIATE` :283; single-statement CAS `UPDATE … WHERE spent+reserved+cap <= ceiling` :286-294; rowcount gate :295. Genuinely atomic within one process. |
| `_DEFAULT_CEILING_USD = 5.0` | §1 (`cost_stops.py:54`) | **CONFIRMED** | :54. |
| `merge_policy.allow_openrouter` default False + `default_policy` | `merge_policy.py:78` / `:169` | **CONFIRMED** | field default False :78; `default_policy` openrouter-off :169-177. Note docstring :70-71 self-states "only the P4 router enforces it" — see REQ-02 P0. |
| `trust_policy.compute_tier` + Diligent hard gate | `trust_policy.py:210` / `:247` | **CONFIRMED** | pure `compute_tier` :210; HARD GATE 1 `if repo_class=="work" or _is_diligent_repo(repo): return 0` :247 BEFORE ledger read. `_is_diligent_repo` :166. |
| `derive_task_type` / `GRADUATABLE_TASK_TYPES` | `trust_ledger.py` | **CONFIRMED** | `GRADUATABLE_TASK_TYPES = {bugfix,feature,refactor,test,docs}` :61; `derive_task_type` :244 (explicit-label-wins, fails safe to hold). |
| `intake` spec-dict + parse fns | `intake.py:24/62/121/147` | **CONFIRMED** | spec-dict shape :24-35; `parse_telegram_command` :62; `scan_tasks_md` :121; `parse_tasks_md_line` :147. |
| `injection_scan.scan/fence/is_injection` | `injection_scan.py` | **CONFIRMED** | `scan` :118, `is_injection` :156, `fence` :166. |
| compute.md concurrency = min(18,3)=3; 11 tasks/night | §1 / `compute.md` | **CONFIRMED** | `Concurrency = min(18 logical cores, 3) = 3` compute.md:99; binding ceiling "11 Sonnet tasks/night" :108. |
| Residency guard exists | §2, §6.2 | **CONFIRMED-ABSENT (as designed)** | grep `residency\|assert_openrouter\|ResidencyViolation` EMPTY — the guard is new; §11 admits it. |

**REQ-01 conclusion:** no cited seam is WRONG. One is PARTIAL (`:527` right, `ready_jobs` new).
The reuse story is honest.

---

## REQ-02 — Data-residency attack (crown risk)

The design claims ONE chokepoint: `residency_guard.assert_openrouter_allowed`. I traced every path
a job's model call can take.

| Path | Residency enforced? | Severity | Finding |
|---|---|---|---|
| **Worker model-key injection** (`worker_runner.build_worker_env` :338) | **NO — bypassable** | **P0** | `build_worker_env` injects `run_spec.model_key` keyed by `run_spec.worker` via `_PROVIDER_MODEL_KEY` (:350-353) with **no residency check**. `assert_no_egress` (:357) only checks *unexpected* keys are absent — it does not check the *expected* key is residency-legal. The P3 guard is a standalone unit-tested function that this seam never calls. Today `_PROVIDER_MODEL_KEY` maps only `claude`/`codex` (:50-53), so a `worker="openrouter"` job accidentally gets NO key (fails to auth) — an *accidental* safety net that P4 removes the moment it adds the openrouter mapping. **The enforcement seam must be pinned in P3 at this line, not deferred to the P4 router.** |
| **The splitter itself** (task_splitter runs a worker) | **NOT SPECIFIED** | **P1** | §3.1 runs the splitter as a `WorkerRunner` worker for a *work* spec. The design never states the splitter's own model is residency-gated. A work Jira epic's text is sent to the splitter's model; if that model is ever an OpenRouter tier, work content leaks *before* any node exists. Fix: `assert_openrouter_allowed(repo, policy)` must gate the splitter's worker launch too (design §3.1 step 2). |
| **429 failover ladder** (P4) | **DEFERRED — flagged** | **P1** | §12 + §6.2 correctly defer the ladder to P4 but §11 flags that "if P4 is built without calling the guard, the gate is bypassed." Because the guard is unwired in P3 (row 1), P4 has no compile-time forcing function — it is a prose promise. Strengthen: ship the guard *called from the launch seam* in P3 so P4's failover physically cannot construct a work-repo OpenRouter env. |
| **Voice transcription** (§8 voice-note intake) | **NOT ADDRESSED** | **P1** | §8 routes a Telegram audio note → "the gateway's existing audio path" → transcribe. The transcription provider is unspecified; a *work* voice note transcribed by a third-party STT service is a residency leak the `allow_openrouter` flag never sees (it gates model dispatch, not transcription). At minimum the design must state that voice intake is sandbox/personal-only, or that transcription is local/first-party. |
| **Repo-onboarding** (§7 codebase-mapping + code-review-graph) | **NOT ADDRESSED** | **P1** | §7 runs `codebase-mapping` + `code-review-graph build_or_update_graph` over a repo that may be work/Diligent. If either sends code snippets to a hosted model (codebase-mapping spawns AI subagents), a work repo's source egresses during onboarding — again invisible to `allow_openrouter`. The design must assert onboarding uses only first-party/subscription models, or gate onboarding of Diligent repos behind the same hard tier-0 check. |
| **Jira poller → spec** (§6) | enforced via repo mapping | P2 | The residency decision hinges on `repo = <mapped from ticket>` (§6.1). If the ticket→repo mapping is wrong/missing, a work ticket could map to a non-work repo and inherit `allow_openrouter=true`. Low-likelihood given the sandbox/personal JQL filter, but the mapping is the trust root — the design should fail-closed (unmapped repo ⇒ tier-0 posture) rather than default. |

**REQ-02 conclusion — ESCALATE:** the design's own §11 concedes the guard is unenforced in P3.
Combined with four unaddressed egress paths (splitter model, transcription, onboarding, failover),
the "one chokepoint" story is aspirational. **No work/Diligent code reaches OpenRouter *today*
only because `_PROVIDER_MODEL_KEY` lacks an openrouter entry** — an accident, not a guard. Fix:
move `assert_openrouter_allowed` from "unit-tested standalone" to "called at
`worker_runner.build_worker_env` and the splitter launch," add the four missing egress statements,
and add a P3 RED test that fails if the guard is not on the launch path.

---

## REQ-03 — Scheduler attack (crown risk)

| Attack | Bounded? | Severity | Finding |
|---|---|---|---|
| **Concurrency > cap 3** via tick/spawn race | Bounded *within one tick* | **P1** | §4.3 computes `capacity = CAP - len(running)` then admits `ready[:capacity]`. Correct for a single serial tick (the 60s file-locked tick prevents overlap, §4.1). BUT `len(running)` counts states `{RUNNING,TEST,REVIEW,MERGING}` — a just-spawned job is only counted once the supervisor transitions it to RUNNING (`supervisor.py:338`). Between `spawn_background(sup.run_until_approval)` (§4.3 step 6) and that transition, the job is still QUEUED. If the stagger sleep or spawn ordering lets the next tick fire before the transition lands, `len(running)` undercounts and the cap is exceeded. Fix: the scheduler must mark the slot taken (a SPAWNING state or reserve-then-transition) *before* releasing the tick, not rely on the child's own transition. |
| **Crashed job never releases its slot** | Partially | **P1** | §4.3 step 1 calls `check_integrity()` which reconciles dead pgids to NEEDS_ATTENTION (`job_store.py:416` invariant (a)). Good — a crashed RUNNING job is reclaimed next tick. But the **budget reservation is not released on crash**: `try_reserve` increments `reserved_usd` (:289), and only `commit_spend` releases it (:327). A worker that crashes after `try_reserve` but before `commit_spend` leaks its reservation forever, permanently shrinking the nightly ceiling until the daily row rolls over. `check_integrity` does not reconcile the ledger. Fix: reconciling a dead job to NEEDS_ATTENTION must also `commit_spend(cap, actual=0)` to release the reservation. |
| **Daily budget overshoot** under fan-out | Bounded (atomic CAS) | — (P2 note) | `try_reserve` is a genuine single-statement CAS under `BEGIN IMMEDIATE` (:283-294); concurrent reservations serialize on SQLite's write lock. The scheduler consults it once per spawn (§4.5). This is correct. **Caveat:** it is atomic *within one process/connection*. The design assumes one scheduler process; if a second supervisor process ever opens its own `CostStopper` connection, `BEGIN IMMEDIATE` still serializes them at the SQLite file level (WAL) — so this holds *provided* busy_timeout is set (else the loser raises SQLITE_BUSY). The busy_timeout pragma (§4.7) is therefore load-bearing for the budget guarantee, not just robustness — the design under-states its criticality. |
| **`commit_spend` accounting drift** | Soft | **P2** | On `actual > cap`, `spent += actual` but `reserved -= cap` (:326-327) — spent can exceed the sum ever reserved, softening the post-hoc ceiling. §cost_stops docstring (:312-314) admits this; the pre-launch CAS remains the real guard, so a *single* overshoot can't runaway. Acceptable but should be noted in the design's risk list (it isn't). |
| **`depends_on` violated** (dependent spawned early) | Bounded by design | — | §4.2 `ready_jobs()` returns QUEUED jobs whose every `depends_on` is DONE; §4.3 step 5 only admits from `ready`. A dependent is structurally not *ready* until its dep is DONE. Sound — *if* `ready_jobs` is implemented as specified. Flag: this is new SQL not yet written; the P3-b RED test (§4.8 #2) must assert "T2 never RUNNING while T1 not DONE" against the real query, not a stub. |
| **Cycle admitted** → deadlock | Bounded | — | Cycle detection is in `task_graph.validate()` (Kahn, §3.2) — a cyclic graph parks the whole spec before any enqueue. Correct placement (fail before the scheduler sees it). |
| **Sole-writer / write race** | **BROKEN as written** | **P0** | §4.1/§4.7 claim "only the scheduler transitions job states; workers write only their own worktree files." **False against the code:** `Supervisor.run_until_approval` (spawned per node) calls `store.transition` at `supervisor.py:338, 476, 477, 521` and `enqueue_job` at :230. The scheduler spawns N of these as `spawn_background` (threads) sharing ONE `JobStore` (one sqlite conn + one `threading.Lock`, :172-173). So writers = scheduler + N supervisor threads. This is serialized by the in-process lock, but it directly contradicts the store's own contract (":159-160 must only be used from supervisor.py") and the design's stated invariant. It is likely *safe* (one process, one lock) but the design's justification is wrong, and if any supervisor is ever spawned as a separate **process** (the stated L4 remote-worker direction), the `threading.Lock` no longer serializes them and only WAL+busy_timeout stands between them. Fix: state the real concurrency model (N threads, one JobStore, one lock) explicitly, add a RED test for concurrent transition serialization, and forbid multi-process JobStore writers until a cross-process lock exists. |

**REQ-03 conclusion — ESCALATE:** concurrency is bounded within a tick but the spawn/transition
gap (P1) and the leaked-reservation-on-crash (P1) can erode both caps over a night; the
sole-writer claim is factually WRONG (P0). Budget CAS itself is correct and atomic.

---

## REQ-04 — Intake-integrity attack + gate-test verdicts

| Attack | Blocked? | Severity | Finding |
|---|---|---|---|
| **Injection via spec/Jira/voice reaches a worker** | Partially | **P1** | Each new source runs `scan()` on the *incoming body* (§6.1, §8) — good, mirrors the P2 chokepoint at `supervisor.py:215`. BUT the splitter then emits AI-generated per-node `title`/`acceptance[]` (§3.2) that become the enqueued job spec. That derived text is a **second, unscanned surface**: a spec that survives the input scan can still induce the AI to emit a node whose acceptance text contains an injection, and the scheduler enqueues nodes directly (sole writer, §4.3 step 6) — bypassing `enqueue_from_intake`'s fence at :215. Fix: scan each rendered node spec at enqueue, or route node enqueue through `enqueue_from_intake`. |
| **Jira write-back as an egress hole** | Blocked | — | §6.1 step 4 makes write-back a HELD/brokered action, never a direct poller write — correct egress discipline. |
| **Ambiguity grade gamed / self-cleared** | Blocked by design | — | The AI's self-reported score is advisory; the *authoritative* score is the PURE `ambiguity_rubric.grade()` (§3.1 step 4, §3.3). A malicious/hallucinated spec cannot lower its own grade because the rubric is deterministic over node structure. Sound — provided the rubric is genuinely pure (the §3.4 #4/#5 RED tests assert this; keep them). |
| **Ambiguous spec self-clears (skip parking)** | Blocked (twice) | — | Two independent rails: (a) `max_ambiguity >= 0.5 → PARK` (§5.1) is plain code over the pure rubric; (b) even a *clear* spec is HELD for an owner tap unless `compute_tier(repo) >= 1`, and `trust_policy.py:247` hard-returns 0 for work/Diligent. So a work spec can NEVER auto-clear. Verified against real code. Strong. |
| **`questions.md` parking skipped** | Blocked | P2 | Per-spec file `docs/factory/questions/<spec_hash>.md` (§5.2) — parallel parks don't clobber; §5.4 #5 tests it. No job enqueued until the gate clears. Fine. |
| **Malformed/cyclic graph → half-graph** | Blocked | — | `validate()` fail-closed → `ParkedSpec`, never a partial graph (§3.1 step 3, §3.2); §3.4 #2/#3 RED tests. |

### Gate-test real/theater verdict (from the design's own RED lists)

| Gate test | Real or theater? | Note |
|---|---|---|
| §4.8 #1 fan-out 2 independent | **REAL** | asserts 2 background spawns both RUNNING — observable, falsifiable. |
| §4.8 #2 serialize dependent | **REAL** *if* run against real `ready_jobs` SQL | must not stub the query; assert T2 never RUNNING while T1≠DONE. |
| §4.8 #3 concurrency ≤ cap | **REAL but INSUFFICIENT** | submits 6 nodes, asserts ≤3 running "across ticks." Does NOT cover the spawn/transition-gap race (REQ-03 P1) — the test uses serial ticks so it never exercises the undercount window. Add a test that fires a tick while a prior spawn's RUNNING transition is pending. |
| §4.8 #6 busy_timeout ≥5000 | **REAL** | direct pragma assertion, falsifiable. |
| §6.3 #3 work ticket never routes OpenRouter | **THEATER as scoped** | it tests `assert_openrouter_allowed` *raises* in isolation — but the guard is never on the launch path (REQ-02 P0), so a green test here does NOT prove a work job can't reach OpenRouter. The design even labels the through-the-router test "UNVERIFIED, P4-owned." A gate test that passes while the enforced path is unwired is the definition of theater. Fix: make the P3 test assert the guard is *called by* `build_worker_env`/splitter-launch, not merely that it raises when called. |
| §7.2 #1 fresh repo one-pass onboard | **REAL** | asserts committed profile + merge-policy row after one pass — observable. But see REQ-02 onboarding-egress P1: a "successful" onboard may have already egressed work code. |
| §5.4 #1 ambiguous spec parks | **REAL** | asserts questions.md written + zero jobs enqueued. |

**REQ-04 conclusion:** intake integrity is mostly sound (ambiguity self-clear and parking are
genuinely well-defended by the pure rubric + the tier-0 hard gate). Two gaps: the
splitter-derived-text injection surface (P1) and the residency gate test being theater as scoped
(P1, ties to the REQ-02 P0).

---

## Consolidated severities

**P0 (block until fixed in P3, not P4):**
1. Residency guard unwired — `assert_openrouter_allowed` not called at `worker_runner.build_worker_env:350` or the splitter launch. (REQ-02)
2. Sole-writer claim false — N supervisor threads + scheduler write the store; §4.7's invariant contradicts `supervisor.py:338/476/477/521`. (REQ-03)

**P1 (weakened gate / real risk):**
3. Splitter-derived per-node spec text is unscanned before enqueue. (REQ-04)
4. Spawn/transition gap can let a tick undercount `running` and exceed cap 3. (REQ-03)
5. Crashed job leaks its budget reservation (`try_reserve` never released), shrinking the nightly ceiling. (REQ-03)
6. Residency gate test (§6.3 #3) is theater as scoped — passes while the enforced path is unwired; + splitter/transcription/onboarding egress paths unaddressed. (REQ-02/04)

**P2 (log / cosmetic):**
7. `commit_spend` post-hoc overshoot on `actual>cap` unstated in the design risk list. (REQ-03)
8. Jira ticket→repo mapping should fail-closed to tier-0 on an unmapped repo. (REQ-02)

## What the design gets right (credit where due)
- The reuse map is honest — no cited seam is WRONG; `depends_on`/`busy_timeout`/`residency_guard`
  all correctly flagged as NEW/absent rather than pretended-present.
- `try_reserve` is a genuine atomic CAS; the budget *spend* bound is correct.
- Ambiguity self-clear is doubly defended (pure rubric + tier-0 hard gate at `trust_policy.py:247`);
  a work spec provably cannot auto-clear.
- Cycle detection sits before the scheduler (`task_graph.validate`), so a hallucinated cyclic DAG
  parks the spec instead of deadlocking the tick.
