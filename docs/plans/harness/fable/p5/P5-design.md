# ABOUTME: P5 architecture — learning loops + self-supervision for the Fable factory.
# ABOUTME: Scorecard→routing-view, nightly retro gated by the immutable ring (the crown
# ABOUTME: self-modification wall), weekly skill-improvement + pattern bank, drift/
# ABOUTME: regression/memory-zone/watchdog watchdogs, and structured failure forensics.
# ABOUTME: Design doc ONLY — zero code, file-disjoint task breakdown + RED tests within.

# P5 Design — Learning Loops + Self-Supervision

**Phase:** P5 (backlog mode, GOVERNANCE_EXEMPT — architecture design; adversarial review + verifier follow).
**Branch:** `factory` (confirmed == verify; `git branch --show-current` → `factory`).
**Plan source (sole context):** `docs/plans/diagnosis-2026-07-03/hermes-fable-plan.md` §P5 (deliverables 5.1–5.5, tasks P5-1..5, binding exit gate).
**Author role:** ARCHITECT. No code, no commits.

> **The crown risk, stated once.** P5 is the phase where the factory can propose edits to
> *its own* code. The retro (5.3) reads failures and emits diffs. A degenerate or
> prompt-poisoned retro that proposes weakening the broker, the trust policy, a cost stop,
> or the never-graduates list is *self-modification escaping its own guardrails*. The single
> load-bearing design decision below is: **every retro-produced diff is passed through the
> exact same `factory/immutable_ring.check_diff` gate that guards worker diffs, BEFORE it
> can become an owner-approved held action — fail-closed, bypass-tested.** A retro diff
> touching a ring path is rejected by plain code and never reaches the owner's attention.

---

## 1. What already exists (REUSE — verified interfaces)

Every interface below was read from source on branch `factory`. Line numbers are real.

| Module | Interface P5 reuses | Confirmed |
|---|---|---|
| `factory/immutable_ring.py` | `check_diff(diff, *, worktree_root)` raises `RingViolation` on any ring path; `RING_PATHS` (broker/, broker_client.py, trust-policy.md, docs/factory/trust-policy.md, docs/factory/never-graduates.md, factory/immutable_ring.py, injection_scan.py, cost_stops.py, worker_env.py); `is_ring_path`, `fs_scope_excludes()` | `immutable_ring.py:45-59` (RING_PATHS), `:184` (check_diff), `:100` (is_ring_path), `:259` (fs_scope_excludes) |
| `factory/model_router.py` | `select_tier`, `next_rung`, `admissible_ladder(repo, policy, catalog)`, `RungSpec(worker, model, tier)`, `_FIRST_PARTY_LADDER`, `_OPENROUTER_CANDIDATES` (the two hardcoded orderings the scorecard replaces), `resolve_policy` | `model_router.py:50-63` (the two orderings), `:147` (admissible_ladder), `:177` (select_tier), `:200` (next_rung) |
| `factory/job_store.py` | `JobStore`, `_conn` (SQLite), `STATES`, `get`, `list_jobs`, `transition`; jobs schema has `kind`, `worker`, `model`, `cost_so_far_usd`, `test_result`, `review_findings_path`, `confidence`, `fail_reason`, `updated_ts` | `job_store.py:206` (JobStore), `:132-159` (schema), `:49` (STATES), `:360/:372` (get/list_jobs) |
| `factory/trust_ledger.py` | `TrustLedger.record_outcome_on_conn`, `read_rows(repo, task_type)`, `derive_task_type(job_row)`, `VALID_OUTCOMES` (incl. `reverted` — "No live producer until P5.4; column + logic are ready now"), `GRADUATABLE_TASK_TYPES` | `trust_ledger.py:57` (VALID_OUTCOMES), `:126` (record_outcome_on_conn), `:159` (read_rows), `:244` (derive_task_type), `:22-23` (reverted awaits P5.4) |
| `factory/trust_policy.py` | `compute_tier(repo, task_type, rows, now, *, policy, repo_class, capability)` — PURE, three hard gates; `load_trust_policy`, `_load_never_graduates_capabilities` | `trust_policy.py:210` (compute_tier), `:84` (load) |
| `factory/forensics.py` | `capture_pre_kill`, `capture_post_mortem` — bundle = {transcript-tail, failing-test, phase.diff, meta.json}, pure I/O, never-raises on the post-mortem path | `forensics.py:86` (pre_kill), `:110` (post_mortem) |
| `factory/worker_cost.py` | `usd_for`, `usd_from_usage`, `record_job_spend(ledger, ...)`, `TOLERANCE` | `worker_cost.py:69/:87/:115` |
| `factory/cost_trend.py` | `cost_per_merged_pr(store, ...)`, `trend(store, window_a, window_b)`, `board(...)` — reads DONE jobs' `cost_so_far_usd` by `updated_ts` window | `cost_trend.py:52/:104/:168` |
| `factory/scheduler.py` | `Scheduler(store, ledger, spawn_fn, concurrency_cap, throttled_fn, checkpoint_reader, ...)`, `tick()`, `run_forever(cadence_s)`, `_count_live()`; the `throttled_fn` seam admits nothing new when True | `scheduler.py:81` (class), `:106` (init), `:195` (tick), `:234-237` (throttle seam) |
| `broker_client.py` (repo root, NOT factory/) | `BrokerClient.enqueue_action` → `{action_id, disposition}` ('held' = queued for owner tap); `approve(action_id, *, nonce)`, `reject`, `auto_merge`; holds NO credentials, exposes NO send/push verb | `broker_client.py:28` (class), `:106` (disposition 'held'), `:124` (approve+nonce) |
| `docs/factory/never-graduates.md` | Already a RING_PATHS member; enumerates the six categories a worker (and now a retro) can never edit into graduation | verified: file present, lists broker/guard/trust-policy/self-approve |
| `~/.claude/scripts/synthesize-lessons.py` + `~/.claude/skills/retro/SKILL.md` | Retro discipline to reuse: distill raw run history → curated high-signal entries; NEVER cite raw journal; owner-in-the-loop | verified present |
| code-review-graph MCP (`cross_repo_search_tool`, `semantic_search_nodes_tool`) | Cross-repo pattern transfer for the pattern bank (5.2) | plugin present this session |

**Two facts that shape the whole design:**

1. **The ring already lists `trust-policy.md` and `never-graduates.md`** (`immutable_ring.py:49-59`).
   So the retro's ring gate is not new policy — it is *routing retro output through an existing
   wall*. The design work is making that routing structural and fail-closed, not inventing a
   new denylist. (UNVERIFIED-as-of-P5: `cost_stops.py` and `broker/` are ring members too, so a
   retro diff touching cost-stop code or broker source is rejected identically.)

2. **The router's data-driven-ness is a *within-tier ordering* problem, not a rewrite.**
   `admissible_ladder` already filters by residency and probe-confirmation; what is
   *hand-written* today is the ORDER of `_FIRST_PARTY_LADDER` and `_OPENROUTER_CANDIDATES`
   (`model_router.py:50-63`) and the fact that task_type is "currently informational"
   (`:190-192`). The scorecard makes that ordering + task_type routing a **view over data**,
   without touching the residency wall (which stays the crown P4 guard).

---

## 2. Component architecture (5 components, file-disjoint)

```
   completed jobs (job_store)         failed jobs (forensics bundles)
          │                                    │
          ▼                                    ▼
   ┌──────────────────┐               ┌──────────────────────┐
   │ P5-a SCORECARD   │◄──────────────│ P5-e FORENSICS       │
   │ scorecard.py     │  feeds        │ forensics.py (extend)│
   │ (model×tasktype  │               │ + forensics_store.py │
   │  outcome rollup) │               └──────────────────────┘
   └────────┬─────────┘
            │ regenerate-from-data
            ▼
   ┌──────────────────────────┐        ┌──────────────────────────────┐
   │ routing_view.py          │        │ P5-b RETRO (CROWN)           │
   │ → capabilities-derived   │        │ retro.py + retro_ring_gate.py│
   │   ladder order + per-     │        │ reads forensics+scorecard →  │
   │   task-type table         │        │ proposes diff → RING GATE →  │
   │ (router reads this)       │        │ held action (owner tap)      │
   └──────────────────────────┘        └───────────────┬──────────────┘
                                          check_diff FAILS CLOSED
   ┌──────────────────────────┐          before broker_client.enqueue_action
   │ P5-c SKILL-IMPROVE +      │                        │
   │ PATTERN BANK              │◄───────────────────────┘ (weekly pass shares
   │ skill_improve.py         │    ring gate + held-action path)
   │ pattern_bank.py          │
   └──────────────────────────┘
   ┌───────────────────────────────────────────────────────────────────┐
   │ P5-d WATCHDOGS (observe the running scheduler fleet, plain code)   │
   │ watchdogs/drift.py · regression_sentinel.py · memory_zone.py ·     │
   │ watchdog_supervisor.py · worktree_gc.py                            │
   │   drift → pause worker + DRIFT alert                               │
   │   sentinel → re-test main post-merge → revert PR + pause branched  │
   │   memory-zone → throttled_fn feeds scheduler (GREEN/YELLOW/RED)    │
   └───────────────────────────────────────────────────────────────────┘
```

---

## 3. REQ-01 — Scorecard + routing as a view (P5-a) [deliverable 5.1]

### 3.1 What it is
A per-`(model × task_type)` rollup fed by *completed* jobs (any terminal state carrying an
outcome), and a **routing view** the router reads instead of the two hand-written orderings.

### 3.2 Files + schema
New module `factory/scorecard.py`; new module `factory/routing_view.py`. New SQLite tables
**co-located in the existing jobs DB** (same pattern trust_ledger used — `trust_ledger.py:37`
"added to the same DB as the jobs table" — so a job's terminal transition can write the
scorecard row in the same commit; no new DB, no double-write hazard).

```sql
-- factory/scorecard.py owns this schema (executescript at __init__, like TrustLedger).
CREATE TABLE IF NOT EXISTS scorecard_samples (
  job_id        TEXT PRIMARY KEY,        -- one row per completed job (FK-in-spirit to jobs.id)
  model         TEXT NOT NULL,           -- jobs.model (resolved id)
  worker        TEXT NOT NULL,           -- 'claude'|'codex'|'openrouter'
  task_type     TEXT NOT NULL,           -- trust_ledger.derive_task_type(job_row) — REUSE, not a new classifier
  outcome       TEXT NOT NULL,           -- merged_clean|merged_with_fix|rejected|reverted|failed
  findings_count INTEGER NOT NULL DEFAULT 0,  -- parsed count from review_findings_path
  cost_usd      REAL NOT NULL,           -- jobs.cost_so_far_usd at terminal
  wall_ms       INTEGER NOT NULL,        -- updated_ts - created_ts
  sample_ts     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sc_model_type ON scorecard_samples(model, task_type, sample_ts);
```

The **scorecard proper** is a derived view (a query, not a table) — a leaderboard rollup:
`GROUP BY model, task_type` → `n, merged_clean_rate, mean_findings, mean_cost_usd, mean_wall_ms`.
`scorecard.py` exposes:
- `record_sample(conn, job_row, *, findings_count)` — co-transactional insert (mirrors
  `trust_ledger.record_outcome_on_conn`, `trust_ledger.py:126`; the SUPERVISOR is sole writer).
- `rollup(model=None, task_type=None) -> list[ScoreRow]` — the leaderboard view.
- `leaderboard(task_type) -> list[ScoreRow]` — ordered best-value-first for a task type.

### 3.3 The router-reads-scorecard design (the "view over it" requirement)
`routing_view.py` regenerates the router's *orderings* from scorecard data, without touching
the residency wall:

- **`ranked_first_party(catalog) -> tuple[(worker,model),...]`** — replaces the hand-written
  `_FIRST_PARTY_LADDER` order (`model_router.py:50`). Ranks confirmed first-party models by a
  scorecard score (merged_clean_rate, penalized by mean_cost and mean_findings). Falls back to
  the current hardcoded order when a model has `n < MIN_SAMPLES` (cold start — the hand-written
  default IS the prior).
- **`ranked_openrouter(catalog) -> tuple[model,...]`** — same, replaces `_OPENROUTER_CANDIDATES`
  order (`:58`).
- **`task_type_table() -> dict[task_type, tuple[(worker,model),...]]`** — the per-task-type
  routing table the plan calls "learned over time." `bugfix` may rank a cheaper model first
  than `feature` does. This is where `select_tier`'s currently-informational `task_type` arg
  (`model_router.py:190`) becomes load-bearing.

**Seam into the router (minimal, additive):** `admissible_ladder` (`:147`) is refactored to
compose its full ladder from `routing_view.ranked_first_party(catalog)` +
`routing_view.ranked_openrouter(catalog)` **instead of** the module-level tuples — the residency
filter (`_rung_admissible`) and the WAIT_FOR_CAPACITY sentinel are UNCHANGED. So the crown P4
invariant (no OR rung for a false/unknown-policy repo) survives verbatim; only *order within the
admissible set* becomes data-driven. `select_tier` consults `task_type_table()` first, then falls
back to the generic ladder.

> **Anti-regression, load-bearing:** `routing_view` must NEVER add a model absent from
> `catalog` (a scorecard row for a since-removed model cannot resurrect a phantom — F12 from
> P4). The view *orders* the catalog-confirmed set; it never *extends* it. RED test SC-4 asserts
> this.

### 3.4 Regenerate-from-data flow
The router never mutates `capabilities.json`. `routing_view` reads scorecard + catalog live per
call (like `admissible_ladder` re-derives per call — `model_router.py:161`). A human-readable
mirror `docs/factory/model-routing.md` is *regenerated* (atomic temp+os.replace, the
`trust_ledger.write_ledger_mirror` pattern) each retro run for owner visibility — it is a
**generated artifact, not a hand-edited input**. `model-routing.md` is NOT a ring path, so it can
be regenerated freely; the router reads scorecard+catalog, not the markdown.

### REQ-01 done-when
Scorecard schema ✔ (§3.2); router-reads-scorecard via `routing_view` composed into
`admissible_ladder` ✔ (§3.3); regenerate-from-data (live read + generated mirror) ✔ (§3.4).

---

## 4. REQ-02 — Nightly retro (CROWN) + weekly skill-improvement + pattern bank [5.2/5.3]

### 4.1 The retro flow (P5-b)
New modules `factory/retro.py` (orchestration) + `factory/retro_ring_gate.py` (the wall).
The retro is a **held-action producer**, never a silent editor — it runs as a scheduled job,
reads inputs, produces a *proposed diff*, and the diff's only path to disk is an owner tap.

```
nightly retro tick (scheduler-fired, plain code drives an AI worker for the DIFF text only):
  1. GATHER   read failure bundles (P5-e forensics) + review-findings + scorecard rollup
             for the last window. No AI yet — plain queries.
  2. PROPOSE  an AI retro worker (sonnet) emits ONE unified diff proposing edits to
             skill files / prompt templates / NON-ring policy docs, + a rationale.
             Output is text, written to a scratch worktree — NOT applied.
  3. RING GATE (retro_ring_gate.check_retro_diff) — THE WALL. §4.2 below. FAIL-CLOSED.
  4. HOLD     surviving diff → broker_client.enqueue_action(type='retro_diff',
             summary=..., payload={diff, rationale, target_files}) → disposition 'held'.
             Owner taps Approve on Telegram → broker applies the diff via a
             human-reviewed apply path. Reject → discarded, logged.
  5. NEVER    there is no code path from PROPOSE to disk that skips steps 3+4.
```

### 4.2 The ring gate on retro output (the crown self-modification guard)
`retro_ring_gate.check_retro_diff(diff, *, worktree_root)` is a **thin, fail-closed wrapper that
calls `immutable_ring.check_diff` verbatim** (`immutable_ring.py:184`) and, on `RingViolation`,
*refuses to enqueue the held action at all* — the owner never sees a ring-touching proposal.

Why reuse `check_diff` rather than re-implement: it already canonicalizes paths (realpath, rename
old+new, symlink target, `..` traversal) and fails closed on an un-parseable entry
(`immutable_ring.py:19-30`). A retro that tries `../../broker/x.py`, a rename into `broker/`, or a
symlink onto `trust-policy.md` trips the identical hardened checks the worker path already passes.

**Fail-closed specifics (design intent for the RED tests):**
- The gate runs on the FULL proposed diff *before* `enqueue_action`. Order is the contract:
  gate → enqueue, never enqueue → gate. (Mirrors forensics' "capture BEFORE kill" ordering
  discipline, `forensics.py:9-13`.)
- Any `RingViolation` → the retro job records the rejected proposal to forensics/log and produces
  NO held action for that diff. It does not "strip the bad hunk and enqueue the rest" — a diff
  that *contains* a ring hunk is rejected whole (a partial-apply is an evasion surface).
- An un-parseable / empty diff → rejected (inherited fail-closed from `check_diff`).
- The retro worker runs with `fs_scope_excludes()` (`immutable_ring.py:259`) applied to its
  scratch worktree — belt (can't even write a ring file) *and* the check_diff wall — the same
  two-layer defense P2 uses.

> **The bypass test that must exist (crown):** a retro proposal whose diff edits
> `factory/cost_stops.py` (or `broker/`, `trust-policy.md`, `never-graduates.md`) is REJECTED by
> `check_retro_diff` and produces zero held actions — even when the AI rationale claims the edit
> "improves" the cost stop. Removing the `check_diff` call makes this test leak (RED). This is
> `RT-3` in §8.

**Trust-graduation proposals ride this same path.** A retro proposing to graduate a
(repo × task_type) does so as a held action referencing `trust_policy.compute_tier` inputs — it
CANNOT edit `trust-policy.md` or `never-graduates.md` (ring), so a graduation is always a
data-driven tier bump the owner approves, never a policy-doc rewrite. `compute_tier`'s three hard
gates (`trust_policy.py:247-262`) remain the arbiter.

### 4.3 Weekly skill-improvement pass + pattern bank (P5-c)
New modules `factory/skill_improve.py` + `factory/pattern_bank.py`.

- **skill_improve.py** — weekly scheduled pass. Reads recent job histories (scorecard + forensics)
  for a recurring failure signature, and produces a **skill-file diff** (add/update a skill). It
  reuses the retro's ring gate + held-action path (§4.2) — a skill edit is *also* an owner-approved
  diff, never silent. Discipline borrowed from `~/.claude/scripts/synthesize-lessons.py`: distill
  high-signal patterns, don't dump raw history.
- **pattern_bank.py** — a searchable store (SQLite FTS in the jobs DB, or reuse hermes full-text
  session search) of **distilled merged solutions**: `{task_signature, repo, approach_summary,
  diff_pointer, embedding?}`. `query(task_spec) -> list[PatternHit]` is called **before a new
  worker starts** (a supervisor pre-spawn hook injects the top hit into the worker brief).
  Cross-repo transfer: `pattern_bank.query` also fans out to code-review-graph
  `cross_repo_search_tool` for a matching prior solution in another repo. Populated on every
  `merged_clean` outcome (co-transactional with the scorecard sample).

### REQ-02 done-when
Retro flow ✔ (§4.1); ring-gate-on-retro-diffs, bypass-tested ✔ (§4.2 + RT-3); pattern bank ✔
(§4.3); skill-improvement ✔ (§4.3).

---

## 5. REQ-03 — Self-supervision watchdogs (P5-d) [deliverable 5.4]

Plain code, no AI in the loop (the L1 lock: "Plain code supervises; no AI in the control loop").
The watchdogs observe the running scheduler fleet. New package `factory/watchdogs/`.

### 5.1 Drift watchdog — `watchdogs/drift.py`
Runs each scheduler tick over RUNNING jobs. Trigger set (any ⇒ pause + `DRIFT` alert):
| Signal | Detection (plain code, from job_store + forensics) |
|---|---|
| tokens-without-progress | `cost_so_far_usd` climbs across N ticks while `updated_ts` phase/diff-size is unchanged |
| scope creep | the job's uncommitted diff (via `git diff` in the worktree) touches files outside the task's declared footprint (`spec`/node acceptance) |
| stuck loop | same transcript-tail signature repeats across ticks (hash of `forensics` transcript tail) |
| guard/config edit | the worktree diff touches a ring path OR a config file → **immediate pause** (this is the pre-emptive belt before the submission-time `check_diff`) |

**Action:** transition the job to a paused state and emit `DRIFT`. Pause reuses the existing
`NEEDS_ATTENTION` terminal (no new state needed) OR a new forward-only `PAUSED_DRIFT` state
appended to `_ALLOWED_TRANSITIONS` (`job_store.py:99`, additive like P4-a's RESUMABLE) — **design
choice for P5-d: append `PAUSED_DRIFT`** so a drift-pause is distinguishable from a QA failure in
the morning card, with edges `RUNNING→PAUSED_DRIFT` and `PAUSED_DRIFT→{ADMITTED, NEEDS_ATTENTION}`.
The drift watchdog also SIGTERMs the worker process-group (reusing the launcher's kill) after
`forensics.capture_pre_kill` (ordering per `forensics.py:9-13`).

### 5.2 Regression sentinel — `watchdogs/regression_sentinel.py`
Runs **immediately after a merge** (not on a slow poll — plan §5.4 "runs immediately after a
merge"). It is invoked in the broker's post-merge callback / the supervisor's `MERGING→DONE`
transition path, not on the 60s cron.
- **Re-test main:** check out the just-merged `main`, run the repo suite.
- **On a break:** (a) enqueue a **revert job** whose spec is "revert merge <sha>" → held action
  via broker (a revert to a protected branch is still a held, owner-approved merge card —
  never an auto-push; `never-graduates.md` forbids the bypass); (b) **pause any queued task that
  branched from the now-suspect main** — query `job_store` for QUEUED/ADMITTED jobs whose
  `base_branch` resolves to the suspect main sha, transition them to `PAUSED_DRIFT`/hold until the
  revert lands. This bounds the blast window.
- **Trust feedback:** on a confirmed post-merge regression, write a `reverted` outcome to the
  trust ledger (`trust_ledger.record_outcome_on_conn`, outcome `reverted` — the column the ledger
  reserved for exactly this, `trust_ledger.py:22-23`), which auto-revokes the tier via
  `compute_tier`'s streak logic.

### 5.3 Memory-zone throttle — `watchdogs/memory_zone.py`
GREEN/YELLOW/RED based on host RSS / free memory. Exposes a `zone() -> str` and a
**`throttled() -> bool`** that is wired into the scheduler's existing `throttled_fn` seam
(`scheduler.py:114, :234-237`) — no scheduler change beyond passing this callable in. RED ⇒
`throttled()` True ⇒ scheduler admits nothing new; YELLOW caps concurrency at 2–3 (the plan's
"before more than 2–3 parallel workers"); GREEN ⇒ full cap. This is a pure read + threshold, so
it composes with the existing budget/quota throttle by OR.

### 5.4 Watchdog-watches-watchdog — `watchdogs/watchdog_supervisor.py`
A supervisor self-check that runs each tick: confirms each watchdog ran within the last
`WATCHDOG_STALE_S`, and **respawns a dead watchdog**. It writes its own heartbeat; if the
top-level supervisor is itself dead, the `job_store` supervisor-lock stale-steal
(`job_store.py:538`, 15-min TTL) already recovers the tick loop — the watchdog-supervisor layers
on top for the *watchdog* threads specifically. Fail-safe: if the watchdog-supervisor cannot
confirm the drift or sentinel watchdog is alive, it pauses new spawns (throttle True) rather than
running an unwatched fleet.

### 5.5 Worktree GC — `watchdogs/worktree_gc.py`
- **Disk pre-flight gate:** a `disk_ok() -> bool` (free bytes ≥ floor) wired into `throttled_fn`
  (OR with memory-zone). Below floor ⇒ block new spawns.
- **Reap:** merged/abandoned worktrees older than a retention window are `git worktree remove`d
  (only for jobs in terminal DONE/FAILED/NEEDS_ATTENTION states; never a live pgid). Preserves the
  forensics bundle (which lives in `out_dir`, not the worktree — `forensics.py:110`).

### REQ-03 done-when
Each watchdog's trigger + action ✔ (§5.1–5.5); drift-pauses-churning-worker flow ✔ (§5.1 + WD-1);
sentinel-opens-revert flow ✔ (§5.2 + WD-3).

---

## 6. REQ-04 — Structured failure forensics (P5-e) [deliverable 5.5]

Extends `factory/forensics.py` (which today captures the *raw bundle*: transcript/test/diff/meta)
with a **structured post-mortem record** that the scorecard + retro consume. The raw-bundle
functions (`capture_pre_kill`, `capture_post_mortem`) are UNCHANGED (they must never raise on the
crash path — `forensics.py:110-124`); the new layer is additive.

New: `factory/forensics_store.py` — a SQLite table in the jobs DB + a `classify(job_row, bundle)`
pure function.

```sql
CREATE TABLE IF NOT EXISTS failure_forensics (
  job_id        TEXT PRIMARY KEY,
  stage         TEXT NOT NULL,    -- QUEUED|RUNNING|TEST|REVIEW|MERGING (where it died)
  error_class   TEXT NOT NULL,    -- timeout|cost_stop|test_fail|review_reject|crash|ring_violation|drift|injection
  cost_burned_usd REAL NOT NULL,  -- jobs.cost_so_far_usd at failure
  retry_worthy  INTEGER NOT NULL, -- 0/1: deterministic test_fail=maybe; cost_stop=no; flake=yes
  bundle_path   TEXT,             -- pointer to the forensics.capture_* bundle dir
  detail        TEXT,
  captured_ts   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ff_class ON failure_forensics(error_class, captured_ts);
```

`classify(job_row, bundle) -> ForensicRecord` — pure, plain code (no AI): derives `stage` from the
job's state at failure, `error_class` from `fail_reason` + the failing-test file + kill_reason in
`meta.json`, and `retry_worthy` from a fixed rule table (cost_stop ⇒ not retry-worthy; a flaky
test-retry that flip-flopped ⇒ retry-worthy; a `ring_violation` ⇒ never retry-worthy — it is an
attack signature, escalate). `record(conn, ForensicRecord)` writes co-transactionally with the
job's terminal transition (sole writer = supervisor).

**Feed to scorecard/retro:** the scorecard's `outcome='failed'` samples read `error_class` from
here; the retro's GATHER step (§4.1 step 1) queries `failure_forensics` grouped by `error_class`
to find the **recurring failure class** the exit gate demands a retro diff measurably reduce.

### REQ-04 done-when
Forensics schema ✔ (§6); feed to scorecard (failed samples carry error_class) + retro
(GATHER groups by error_class) ✔ (§6).

---

## 7. REQ-05 — Task breakdown (file-disjoint) + STAGED entry

### 7.1 Task table (P5-a..e)
Dependencies respect the plan's P5-1..5 (`hermes-fable-plan.md` §P5 task table). Each task is
file-disjoint so a fan-out is possible; the retro depends on scorecard+forensics.

| Task | Owns (files, all NEW unless noted) | Model | Depends on | Maps to plan |
|---|---|---|---|---|
| **P5-a Scorecard + routing view** | `factory/scorecard.py`, `factory/routing_view.py`; additive refactor of `model_router.admissible_ladder` to compose orderings from `routing_view` (surgical, ordering-only) | sonnet | P2 job_store, P4 router (exist) | P5-1 / 5.1 |
| **P5-b Retro + ring gate (CROWN)** | `factory/retro.py`, `factory/retro_ring_gate.py` | sonnet | P5-a, P5-e, immutable_ring (exists), broker_client (exists) | P5-3 / 5.3 |
| **P5-c Skill-improve + pattern bank** | `factory/skill_improve.py`, `factory/pattern_bank.py` | opus | P5-a, P5-b (shares ring gate) | P5-2 / 5.2 |
| **P5-d Watchdogs** | `factory/watchdogs/{drift,regression_sentinel,memory_zone,watchdog_supervisor,worktree_gc}.py`; additive `PAUSED_DRIFT` state in `job_store._ALLOWED_TRANSITIONS`; pass `throttled_fn` into Scheduler (no scheduler edit) | opus | P4 scheduler + forensics (exist) | P5-4 / 5.4 |
| **P5-e Failure forensics** | `factory/forensics_store.py`; additive to `factory/forensics.py` (new fns only, raw path untouched) | sonnet | P2 job_store (exists) | P5-5 / 5.5 |

**Suggested execution order:** P5-e → P5-a (both feed everyone) → P5-b (crown) → P5-c → P5-d
(parallelizable with P5-b/c; only shares the additive `job_store` state edit — sequence that one
edit).

### 7.2 Per-component RED tests (4–6 each; falsifiable; the crown + gate tests bolded)

**P5-a scorecard/routing (SC):**
- SC-1: after several completed jobs, `scorecard.rollup()` returns one row per (model,task_type)
  with correct `merged_clean_rate`/`mean_cost`; empty DB → empty rollup.
- SC-2: **routing table changes from scorecard data** — seed the scorecard so `bugfix` favors a
  cheaper confirmed model; assert `routing_view.task_type_table()['bugfix'][0]` differs from the
  hand-written `_FIRST_PARTY_LADDER[0]`, and that `select_tier(repo,'bugfix',...)` returns it
  *while still clearing residency* (a false-policy repo still gets zero OR rungs). **[FRESH-CONTEXT GATE]**
- SC-3: cold start (`n < MIN_SAMPLES`) falls back to the hand-written order (the default is the
  prior) — no crash, deterministic.
- SC-4: a scorecard row for a model ABSENT from `capabilities.json` does NOT enter any ladder
  (view orders, never extends — F12 anti-regression). Removing the catalog-intersection makes
  this leak (RED).
- SC-5: the residency invariant survives the refactor — a `allow_openrouter:false` repo's
  `admissible_ladder` has zero openrouter rungs even after scorecard reordering.

**P5-b retro + ring gate (RT) — CROWN:**
- RT-1: the retro emits a proposed diff + rationale and produces a `broker_client.enqueue_action`
  held action; it NEVER writes the diff to disk directly (spy on the apply path — zero direct
  writes). **[FRESH-CONTEXT GATE: never applied silently]**
- RT-2: an approved retro diff (owner tap → broker apply) measurably reduces a seeded recurring
  failure class on the next window (the exit-gate assertion, exercised end-to-end on a fixture).
- RT-3: **a retro diff touching `factory/cost_stops.py` (and, parametrized, `broker/x.py`,
  `trust-policy.md`, `docs/factory/never-graduates.md`) is REJECTED by `check_retro_diff` and
  produces ZERO held actions** — even with a rationale claiming improvement. Deleting the
  `immutable_ring.check_diff` call makes this leak. **[CROWN FRESH-CONTEXT GATE]**
- RT-4: ring-gate inherits the hardened evasions — a retro diff that *renames into* `broker/`,
  or symlinks onto `trust-policy.md`, or uses `../../broker/x`, is rejected (delegates to
  `check_diff`'s canonicalization).
- RT-5: an un-parseable / empty proposed diff → rejected, no held action (fail-closed inherited).
- RT-6: gate runs BEFORE enqueue — assert ordering with a spy (a `RingViolation` means
  `enqueue_action` was never called).

**P5-c skill-improve + pattern bank (SK):**
- SK-1: a weekly run over seeded histories produces a concrete skill-file diff as a held action
  (never silent — shares RT-1's discipline).
- SK-2: **a skill-improve diff touching a ring path is rejected by the shared ring gate**
  (the crown wall is not bypassable via the weekly lane either).
- SK-3: `pattern_bank.query(task_spec)` returns a relevant prior `merged_clean` solution for a
  matching new task; no match → empty, no crash.
- SK-4: pattern bank populates on a `merged_clean` outcome co-transactionally (a rejected job adds
  no pattern).

**P5-d watchdogs (WD):**
- WD-1: **the drift watchdog pauses a synthetically-churning worker** (cost climbs, diff/phase
  static across N ticks) → job in `PAUSED_DRIFT` + a `DRIFT` alert emitted; `capture_pre_kill`
  ran before the SIGTERM. **[FRESH-CONTEXT GATE]**
- WD-2: a scope-creep worker (diff touches a file outside the declared footprint) → paused +
  DRIFT.
- WD-3: **the regression sentinel opens a revert on an injected post-merge break** — inject a
  failing main after merge → a revert held-action is enqueued AND every queued task branched from
  the suspect main sha is paused; a `reverted` trust-ledger outcome is written. **[FRESH-CONTEXT GATE]**
- WD-4: memory-zone RED makes `throttled()` True → scheduler `tick()` admits nothing (wire into
  the existing `throttled_fn` seam; assert zero admissions).
- WD-5: watchdog-supervisor respawns a killed watchdog thread; with a watchdog confirmed dead and
  un-respawnable, new spawns are throttled (fail-safe, not fail-open).
- WD-6: worktree GC blocks new spawns below the disk floor and reaps a terminal-state worktree
  while preserving its forensics bundle; never reaps a live-pgid worktree.

**P5-e forensics (FF):**
- FF-1: a failed job produces a `failure_forensics` row with all fields (stage, error_class,
  cost_burned, retry_worthy, bundle_path).
- FF-2: `classify` maps a cost_stop failure → `retry_worthy=0`; a flip-flopping flaky test →
  `retry_worthy=1`; a `ring_violation` → `retry_worthy=0` + escalate flag.
- FF-3: the scorecard reads `error_class` from a failed sample (the feed works end-to-end).
- FF-4: the retro GATHER groups failures by `error_class` and surfaces the recurring class
  (the input to RT-2).

### 7.3 STAGED entry (2-week cost trend)
The exit gate's "two representative weeks show cost-per-merged-PR trending down" is **STAGED** —
it needs two weeks of real overnight runs, so it cannot pass at design/build time. Add to
`docs/plans/harness/fable/STAGED-GATES.md`:

> **P5-TREND (STAGED).** Cost-per-merged-PR trends down across two representative weeks, measured
> by `factory/cost_trend.trend(store, window_a=week1, window_b=week2)` returning
> `direction='down'`. Owner-verified after two weeks of real fleet operation with the scorecard
> and retro live. Until then, P5 is gated on the *mechanism* (scorecard reorders the router; one
> approved retro diff reduces a failure class) not the *two-week outcome*.

### REQ-05 done-when
File-disjoint P5-a..e task table ✔ (§7.1); 4–6 RED tests/component incl the crown ring-rejection
test RT-3 ✔ (§7.2); STAGED entry ✔ (§7.3).

---

## 8. Design-for-TDD notes + the crown, restated

- Every component is a plain module with an injectable store/clock (mirroring `compute_tier`'s
  pure-function + `scheduler`'s `clock=`/`spawn_fn=` seams) so RED tests need no real fleet.
- No AI in any control path: scorecard/routing_view/watchdogs/forensics are plain SQLite + git.
  AI appears ONLY inside the retro/skill-improve *proposal* step, and its output is inert text
  until it clears the ring gate and the owner tap.
- **The crown, one more time:** `retro_ring_gate.check_retro_diff` calls the *identical*
  `immutable_ring.check_diff` the workers pass. `trust-policy.md`, `never-graduates.md`, `broker/`,
  `broker_client.py`, `cost_stops.py` are already RING_PATHS (`immutable_ring.py:45-59`), so a
  retro cannot even *propose* weakening them — the diff is rejected by plain code before the owner
  sees it. Self-modification is contained to skills, prompts, and non-ring policy, all
  owner-approved, none silent.

## 9. Escalations / open items (UNVERIFIED — flag to verifier)
- The additive `PAUSED_DRIFT` job_store state (§5.1) is the one shared edit across P5-d;
  alternatively reuse `NEEDS_ATTENTION`. Chose a new state for morning-card distinguishability —
  a maintainer may prefer reuse. **Owner/verifier call.**
- `cost_stops.py` and `broker/` as ring members were inferred from `RING_PATHS` +
  `never-graduates.md`; `broker/` dir contents were not opened this session (broker_client.py is at
  repo root). The ring gate reuse is correct regardless (check_diff owns the list). **UNVERIFIED:
  broker/ dir layout.**
- The pattern bank's cross-repo transfer depends on a built code-review-graph graph per repo;
  cold repos degrade to same-repo-only. Acceptable; noted for P5-c.
