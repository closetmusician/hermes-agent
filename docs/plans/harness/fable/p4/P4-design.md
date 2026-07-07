# ABOUTME: P4 architecture design — OVERNIGHT AUTONOMY: phase-checkpoint + self-heal,
# ABOUTME: the 429 failover ladder + model ROUTER (residency-enforced), overnight queue +
# ABOUTME: morning packet + PR cards, per-job confidence score + cost-per-PR trend, voice-note
# ABOUTME: intake. Crown risk: the router NEVER lets an allow_openrouter:false job reach a
# ABOUTME: third-party host — it waits for subscription capacity. Zero code; arch only.

# P4 Design — Overnight Autonomy (throw a queue at night, wake to merged/held/needs-attention)

**Date:** 2026-07-07
**Status:** DRAFT (backlog mode — architecture only; adversarial review + verifier follow).
**Authoritative plan:** `docs/plans/diagnosis-2026-07-03/hermes-fable-plan.md` §P4 (tasks P4-1..7),
§4 (reliability), §5 (safety/residency). Owner params locked: nightly ceiling **$5**, OpenRouter
**≤$2/day**, concurrency **min(ceiling,3)=3**.

The crown constraint (P3 residency contract, pinned): **the 429 failover ladder builds the
router, and the router MUST enforce data-residency — an `allow_openrouter:false` job's failover
NEVER routes to OpenRouter; it WAITS for subscription capacity.** This design is built so that
constraint is *structurally* true, not merely policy-labelled — see §2 and §13.

---

## §0 — What P4 adds, in one paragraph

P3 gave us a fleet scheduler that eats a task graph one concurrency-capped tick at a time. P4
makes it *survive the night*: (1) a **phase-checkpoint** layer so a worker killed at 3am
re-launches from the last *cleared phase* via a compact brief instead of starting over, with a
**forensic bundle** snapshotted before every kill; (2) a **model router** that picks the worker
tier at launch and, on an explicit **429**, re-launches the job one rung down a **probe-confirmed
ladder** — the router being the single place tier is chosen, so residency is enforced there; (3)
an **overnight queue** that runs the P3 scheduler under a sleep policy and produces a **morning
packet** — one confidence-ranked, batch-approvable **PR card** per job; (4) a **per-job confidence
score** and a **cost-per-merged-PR trend board**; (5) a **voice-note** early taste. All control
logic is plain code; the only AI is inside the workers (unchanged from P2/P3).

---

## §1 — Verified reused interfaces (cited file:line — confirmed this session on branch `factory`)

| Interface | Location | How P4 uses it | Confirmed |
|---|---|---|---|
| `WorkerRunner.build_worker_env` — calls `assert_openrouter_allowed` **UNCONDITIONALLY** before the model-key lookup | `factory/worker_runner.py:362` | **The residency wall is already downstream of the router.** The router only *chooses a tier* and hands it to the runner via `WorkerRunSpec.worker`/`.model`/`.policy`; the guard fires before any env is built. §13. | ✅ `assert_openrouter_allowed(run_spec.repo, run_spec.worker, run_spec.policy)` at `:362`, before `_PROVIDER_MODEL_KEY.get` at `:364` |
| `assert_openrouter_allowed(repo, worker, policy)` — pure fail-closed predicate | `factory/residency_guard.py:57` | The router calls the SAME predicate as a *pre-flight* to skip a disallowed rung (so it waits instead of erroring); the runner's call is the hard wall. §13.2 | ✅ raises `ResidencyViolation` for `worker in THIRD_PARTY_WORKERS` unless `policy.allow_openrouter`; `None`→denied (`:78-88`) |
| `WorkerRunSpec` (worker, model, model_key, policy, budget_usd, timeout_min) | `factory/worker_runner.py:144` | The router populates `worker`/`model` per rung; `model_key` is the broker-injected key; `policy` carries residency. §2.2 | ✅ `policy: Optional[MergePolicy] = None` → fail-closed (`:173`) |
| `MergePolicy.allow_openrouter` (default `False`) + `load_merge_policy` / `default_policy` | `factory/merge_policy.py:78`,`:154`,`:169` | Residency source of truth per repo; the router reads it to pick the ladder. | ✅ defaults `False` at `:78` |
| `JobStore.transition(job_id, expected, target, *, extra=)` — one-way CAS | `factory/job_store.py:288` | P4 writes phase-checkpoint + confidence via the `extra` dict on existing transitions (additive columns, no new states needed for checkpoint). §3, §8 | ✅ CAS `UPDATE … WHERE id=? AND state=?` |
| `jobs.confidence REAL` column (nullable, reserved for P4.6) | `factory/job_store.py:134` | P4-6 populates it at AWAITING_APPROVAL. **Already exists — no migration.** | ✅ `confidence REAL, -- NULL until P4.6` |
| `jobs.model`, `jobs.worker` columns | `factory/job_store.py:122-123` | The ladder records which tier finished each job (P4.2). | ✅ |
| `DailyBudgetLedger.try_reserve / commit_spend / release_reservation` | `factory/cost_stops.py:206`,`:262`,`:305` | Cost accounting per worker folds `actual_spend_usd` back at completion; the trend board reads `daily_budget` + a new per-job cost log. §5, §6 | ✅ atomic CAS reserve at `:262`; `commit_spend` overshoot-tolerant at `:305` |
| `Scheduler.tick` / `run_forever(cadence_s)` — atomic admission, stagger, `CONCURRENCY_CAP=3` | `factory/scheduler.py:187`,`:250`,`:47` | The overnight queue IS the scheduler under a sleep policy; P4 adds a router call at spawn and a checkpoint-resume path into `tick`. §4 | ✅ `min(ceiling,3)=3` at `:47`; stagger 30-60s at `:55` |
| `Gauntlet` → `GauntletResult{test_result, review_findings}` | `factory/gauntlet.py:75` | Confidence reads `test_result` + `review_findings` (severity/count). §7 | ✅ |
| `two_stage.persist_spec_artifact` / phase-brief pattern (fresh worker per stage) | `factory/two_stage.py` | The checkpoint brief generalizes the two-stage handoff (spec→implement) to every phase boundary. §3 | ✅ two-stage already does fresh-agent-per-stage + persisted artifact |
| `tools/checkpoint_manager.py` — **within-worker** per-turn file rollback by commit hash | `tools/checkpoint_manager.py` | **REUSED AS-IS, NOT EXTENDED.** It stays the within-a-worker undo tool; the P4 phase-checkpoint is a *new disjoint layer on top* (job-level, not turn-level). See REQ-01 escalation below. | ✅ lives at `tools/`, not `factory/`; knows nothing about factory phases |
| `capabilities.json` — probe-confirmed live model catalog (P0-4) | repo root `capabilities.json` | The ladder rungs are drawn ONLY from probe-confirmed models; a phantom (`GLM-5.2`) never enters. §2.3 | ✅ `openrouter.models{}` with `live_catalog`/`ping_ok` flags |
| `pm_os/bin/run-morning.js` — the briefing generator | `~/Code/pm_os/bin/run-morning.js` | Reused as the morning-packet renderer (factory digest section). §4.3 | ✅ present |
| `trust_policy.compute_tier` (consumes `min_confidence`, `hist`) | `factory/trust_policy.py:210` | Confidence feeds the P1b graduation floor (`min_confidence` AND-gate). §7 | ✅ |

> **REQ-01 escalation resolved (checkpoint_manager.py).** The plan flags "checkpoint_manager.py may
> already exist — reuse/extend, cite it." It exists at **`tools/checkpoint_manager.py`** and is a
> *within-worker per-turn file-rollback-by-commit-hash* tool. It does **not** model factory phases.
> Per plan §2 ("the existing checkpoint_manager.py … stays useful as the within-a-worker undo tool;
> the phase-level checkpoint protocol on top is new"), P4 **does not edit it**. The new phase layer
> (`factory/phase_checkpoint.py`) is disjoint. This keeps files disjoint and avoids coupling a
> job-level protocol to a turn-level tool.

---

## §2 — Component map & new module names (file-disjoint)

Naming follows the domain-story rule (residency_guard, not OpenRouterPolicyEnforcer). Each module
is a single responsibility with a plain-code core (no AI in the loop).

```
factory/phase_checkpoint.py   REQ-01  PhaseCheckpoint schema + write/read + resume-from-phase.
                                      A resumable {job, phase, gates_cleared, artifacts, brief}
                                      record on disk. NEW layer; does NOT touch checkpoint_manager.
factory/forensics.py          REQ-01  capture_bundle(job, worktree, handle) → a {transcript_tail,
                                      failing_test, git_diff, model, prompt} bundle written BEFORE
                                      any kill. Pure I/O snapshot; no AI.
factory/self_heal.py          REQ-01  one bounded self-heal attempt from a forensic bundle, then
                                      NEEDS_HUMAN. Plain-code retry policy (N=1).
factory/model_router.py       REQ-02  THE ROUTER. select_tier(repo, task_type, policy, catalog)
                                      → RungSpec; next_rung(current, policy) on a 429. Residency
                                      pre-filter (calls assert_openrouter_allowed as a predicate).
                                      The ONE place worker/model is chosen at launch.
factory/failover.py           REQ-02  on_429(job, current_rung) → re-launch from last phase
                                      boundary with the handoff brief on next_rung. Explicit 429
                                      detection (not a naive retry).
factory/worker_cost.py        REQ-02  per-worker cost accounting: parse token usage from claude
                                      JSON / codex --json / OpenRouter usage → usd; folds into
                                      DailyBudgetLedger.commit_spend + a per-job cost log.
factory/overnight_queue.py    REQ-03  evening enqueue of a task graph under a sleep policy;
                                      drives the P3 Scheduler.run_forever with a night window.
factory/morning_packet.py     REQ-03  build the confidence-ranked packet: one PRCard per job +
                                      the question queue; renders via run-morning.js.
factory/pr_card.py            REQ-03  PRCard schema {summary, reasoning, alternatives,
                                      reversibility, diff_stat, confidence, risk} + serializer.
factory/confidence.py         REQ-04  compute_confidence(inputs, weights) — pure weighted sum,
                                      clamped [0,1]; reproducible from stored inputs.
factory/cost_trend.py         REQ-04  cost_per_merged_pr over 2 windows → {value, direction}.
factory/voice_intake.py       REQ-04  telegram audio → transcribe (gateway path) → task_splitter
                                      → spec'd task card (or a question if too vague).
```

**Reused, not rebuilt:** the scheduler (P3), job_store/cost_stops (P3+), worker_runner +
residency_guard (P2/P3), gauntlet (P2), broker (P1a) for card delivery + held merge, trust ledger
(P1b), code-review-graph for the risk/blast signal, run-morning.js for rendering.

---

## §3 — REQ-01: Phase-checkpoint + self-heal (P4-1)

### 3.1 The checkpoint schema (`factory/phase_checkpoint.py`)

Fresh agent per phase (already true for two_stage's spec→implement; P4 generalizes it). Between
phases the job's phase state is written to disk atomically so a crash never loses it.

**Phases** (the job's existing gauntlet-driven lifecycle, named as checkpoint boundaries):
`SPEC` → `IMPLEMENT` → `TEST` → `REVIEW` → `READY`. (`SPEC` present only for feature-class;
`kind:quick` starts at `IMPLEMENT` — matches `two_stage.plan_stages`.)

**`PhaseCheckpoint` record** (one JSON file per job at `<worktrees_root>/<job_id>/.factory/checkpoint.json`,
written via atomic temp-file + rename):

```json
{
  "job_id": "j-1a2b",
  "repo": "/Users/yklin/Code/acme",
  "phase": "IMPLEMENT",              // the LAST phase whose gate CLEARED
  "gates_cleared": ["SPEC"],         // ordered list of cleared boundaries
  "artifacts": {                     // paths, not inlined content
    "spec": ".factory/spec.json",
    "diff": ".factory/phase-implement.diff",
    "worktree": "/…/worktrees/j-1a2b-add-x"
  },
  "brief_path": ".factory/handoff-brief.md",  // the compact resume unit (§3.2)
  "branch": "factory/acme/j-1a2b-add-x",
  "rung": {"worker": "claude", "model": "claude-…", "tier": 0},
  "budget_usd": 2.0,
  "updated_ts": 1751846400000
}
```

**Write points.** `phase_checkpoint.clear_phase(job_id, phase, artifacts, brief_path, rung)` is
called by the supervisor the instant a phase gate passes — the same instant it issues the
`JobStore.transition(..., extra={...})` to the next state. Checkpoint write and state transition
are ordered: **write the checkpoint file first, then the CAS transition** (a crash between them
re-does the phase harmlessly — the phase is idempotent because it re-launches from the *prior*
cleared brief). This mirrors the "worktree-first" ordering discipline in `worker_runner.launch`.

### 3.2 Resume-from-phase (the mid-job kill flow)

`phase_checkpoint.resume(job_id)`:
1. Read `checkpoint.json`. If absent → job never cleared a phase → treat as fresh (re-enqueue at start).
2. The resume phase = the phase *after* the last `gates_cleared` entry (or the checkpoint `phase`
   itself if its gate is mid-flight). **We re-launch from the last CLEARED boundary, never mid-phase.**
3. Build a `WorkerRunSpec` whose `extra_prompt` = the contents of `brief_path` (reusing
   `worker_runner.WorkerRunSpec.extra_prompt` — the same seam two_stage uses to inject an approved
   spec into stage-2). Fresh agent, brief-driven.
4. The worktree is reused if intact (`git worktree list` shows the branch); if the worktree was
   reaped, `git worktree add` re-creates the branch from the checkpoint's recorded `branch`+base
   and re-applies the persisted phase diff before the resume worker starts.

**The handoff brief** (`handoff-brief.md`, ≤~2k tokens, plain code assembles it — no AI): the spec,
the acceptance tests, the diff-so-far summary, and a one-paragraph state note ("SPEC cleared;
IMPLEMENT in progress; N tests failing"). This is the portable unit — **the brief, not a raw
transcript** (plan §2 cross-provider-handoff note: Claude tool-call threading does not map onto
OpenRouter chat models, so we never replay a transcript). The brief makes resume work *across
providers* — the same mechanism REQ-02's failover reuses.

### 3.3 Self-healing retry with forensic capture (`factory/forensics.py`, `factory/self_heal.py`)

**Order is load-bearing: capture BEFORE kill.** When a job is about to be killed (stall detected
via output-mtime, a cost stop, a repeated test failure), the supervisor calls
`forensics.capture_bundle(job, worktree, handle)` *first*:

```
forensic-bundle/<job_id>/
  transcript-tail.txt     // last N KB of .factory/worker.out
  failing-test.txt        // the failing test name + assertion (from the suite run)
  phase.diff              // git diff in the worktree at kill time
  meta.json               // {model, prompt_hash, phase, rung, cost_so_far_usd, kill_reason}
```

Then `self_heal.attempt(bundle)`: **one bounded** re-launch (N=1, per the plan's "one bounded
self-heal") whose `extra_prompt` is the brief + a short "the previous attempt failed with: <failing
test / diff>" note. If the self-heal also fails → `JobStore.transition(..., "NEEDS_ATTENTION",
extra={fail_reason, forensic_bundle_path})`. **No endless flip-flopping** (matches the gauntlet's
bounded-retry discipline). The bundle rides the NEEDS_ATTENTION card so morning debugging is 30
seconds.

---

## §4 — REQ-02: 429 failover ladder + model ROUTER (THE crown task)

### 4.1 The router (`factory/model_router.py`) — where the tier is chosen

**Design fact that makes residency structurally safe:** `worker_runner.build_worker_env` already
calls `assert_openrouter_allowed(repo, worker, policy)` at `worker_runner.py:362`, *before* the
model-key lookup, *unconditionally*. So the router does **not** introduce new enforcement — it
introduces new *selection*, and the enforcement wall already sits downstream of it. The router's
job is to hand `WorkerRunner` a `WorkerRunSpec` whose `worker`/`model` are a valid rung; if the
router ever picks a third-party rung for a `false` repo, the runner's guard raises
`ResidencyViolation` and no env is built. The router's own residency check (below) is a *pre-flight*
so the job *waits* rather than erroring.

**`select_tier(repo, task_type, policy, catalog) → RungSpec`** picks the top admissible rung:
- The full ladder (probe-confirmed order): **Claude Max (tier-0) → Codex (tier-1) → OpenRouter
  overflow (tier-2+: `zhipuai/glm-5` → Kimi → DeepSeek, in the order `capabilities.json` confirms
  live)**. Names are **candidates**; the actual rungs are read from `capabilities.json`
  (`openrouter.models[*].live_catalog && ping_ok`). A rung whose model is not probe-confirmed is
  **not in the ladder** (the phantom-`GLM-5.2` guard from P0-4).
- **Residency pre-filter:** the router filters the ladder to `[rung for rung in ladder if
  _rung_admissible(rung, repo, policy)]`, where `_rung_admissible` returns `False` for a
  third-party rung when `assert_openrouter_allowed(repo, "openrouter", policy)` would raise (it
  calls the predicate in a try/except, or equivalently reads `policy.allow_openrouter`). For a
  `false`/unknown-policy repo the filtered ladder contains **only** first-party rungs (Claude,
  Codex). There is no OpenRouter rung to fall to — see §4.3.

**`next_rung(current_rung, repo, policy, catalog) → RungSpec | WAIT`** is the 429 step-down:
- Return the next admissible rung strictly below `current_rung`.
- **If no admissible rung remains** (e.g. a `false` repo has exhausted Claude+Codex, or the
  OpenRouter daily sub-ceiling ≤$2 is hit): return the sentinel **`WAIT_FOR_CAPACITY`** — NOT an
  OpenRouter rung and NOT a hard failure. §4.3.

### 4.2 The 429 failover flow (`factory/failover.py`)

**Explicit 429 detection** (a naive retry can drain a month's quota in minutes — plan §4). The
worker's JSON/stderr is parsed for an explicit rate-limit signal (HTTP 429 / provider quota
marker) at collect time. On a confirmed 429:

1. `forensics`-free (this is a *quota* stop, not a crash) — but the phase checkpoint already holds
   the last cleared boundary + brief.
2. `rung = failover.next_rung(current_rung, repo, policy, catalog)`.
3. If `rung is WAIT_FOR_CAPACITY` → §4.3.
4. Else re-launch the job **from the last phase boundary** (`phase_checkpoint.resume`) with the
   **compact handoff brief** (§3.2) on the new rung — *not* a raw transcript replay. The scheduler
   spawns it exactly as a fresh node (subject to the same atomic-admission slot + stagger).
5. `JobStore` records the finishing `model`/`worker` (existing columns) so the P5 scorecard learns
   which rung completed the job.

Exponential backoff + block-new-spawns-near-the-ceiling are the scheduler's existing throttle
posture (P3 §4.6, stagger 30–60s); failover adds the *tier step-down* on top.

### 4.3 Wait-for-capacity (the residency crown behavior)

When `next_rung` returns `WAIT_FOR_CAPACITY` for an `allow_openrouter:false` job, the job does
**not** fail and does **not** spill to OpenRouter. Instead:
- The job is parked in a new **`WAITING_CAPACITY`** sub-status (a job-store `extra` field
  `wait_reason="subscription-capacity"`; the state stays a live scheduler-visible state so the tick
  re-checks it). It holds its phase checkpoint + brief.
- The scheduler's tick re-evaluates waiting jobs each cadence: when a first-party rung is admissible
  again (429 backoff elapsed / quota window reset per `compute.md`), the job resumes from its
  checkpoint on the first-party rung.
- A **bounded wait** (owner-tunable, default = the quota reset window from `compute.md`, capped so a
  job can't wait forever): on expiry → NEEDS_ATTENTION with "subscription capacity unavailable
  within window", never a residency-violating fallback.

**This is the crown risk closed structurally:** a `false` repo's ladder never *contains* an
OpenRouter rung (router pre-filter), and even if a bug reintroduced one, the runner's
`assert_openrouter_allowed` wall (`worker_runner.py:362`) raises before an OpenRouter key is
placed. Two independent walls; the second is already in production. §13 bypass-tests both.

### 4.4 Per-worker cost accounting (`factory/worker_cost.py`)

Codex + OpenRouter workers are enabled this phase (P2 was Claude-only). Cost per worker:
- **claude:** parse `usage` (input/output/cache tokens) from the `--output-format json` result ×
  the model's price → usd.
- **codex:** parse token usage from `codex exec --json` output → usd (Codex has no `--max-budget-usd`
  per `capabilities.json` `codex.budget_flag:false`, so the cost stop is wall-clock + post-hoc
  metering, not a pre-cap; the daily-ceiling CAS still bounds the night).
- **openrouter:** parse the response `usage` block × the OpenRouter model price → usd; the separate
  **OpenRouter ≤$2/day** sub-ceiling is a second `DailyBudgetLedger`-style CAS keyed to a
  `provider="openrouter"` row (independent of the $5 nightly ceiling).

Each job's actual spend folds back via `DailyBudgetLedger.commit_spend(job_cap_usd, actual_spend_usd)`
(`cost_stops.py:305`, overshoot-tolerant) and is appended to a **per-job cost log** (`job_id, phase,
worker, model, input_tok, output_tok, usd, ts`) that the trend board (§6) and the P5 scorecard read.

---

## §5 — REQ-03: Overnight queue + morning packet + PR cards (P4-3/4)

### 5.1 Overnight queue (`factory/overnight_queue.py`)

The evening-queued task graph runs the **existing P3 scheduler** — no new scheduling engine. What
P4 adds:
- **A sleep-policy window.** `overnight_queue.run_night(graph, window)` enqueues the graph
  (`Scheduler.enqueue_graph`) and starts `Scheduler.run_forever(cadence_s=60)` under the P0.4
  caffeinate anchor. `window` = the off-peak hours; outside it, the tick stops launching NEW jobs
  (drains in-flight), matching the cost-stop 80%-ceiling drain posture.
- **Checkpoint-aware tick.** The scheduler's `tick` gains one branch: a `WAITING_CAPACITY` or
  crash-detected job is resumed via `phase_checkpoint.resume` (subject to the same
  atomic-admission slot). This is the only scheduler edit; it is additive.

Concurrency stays **min(ceiling,3)=3** (`scheduler.CONCURRENCY_CAP`); the daily-budget CAS
(`try_reserve`) and the OpenRouter $2 sub-CAS bound spend independently.

### 5.2 Morning packet (`factory/morning_packet.py`) + PR card (`factory/pr_card.py`)

At wake time (or on demand), `morning_packet.build()` reads all jobs that reached a terminal
overnight state and emits **one card per job, ranked by `confidence` (descending)** so the safe
ones batch-approve and the risky ones surface first for scrutiny.

**`PRCard` schema** (`factory/pr_card.py`):
```json
{
  "job_id": "j-1a2b",
  "repo": "acme", "branch": "factory/acme/j-1a2b-add-x",
  "summary": "Add rate-limit header to the API gateway",
  "reasoning": "…why this approach…",
  "alternatives": ["…considered X, rejected because…"],
  "reversibility": "reversible | irreversible",   // irreversible ⇒ never auto, always held
  "diff_stat": {"files": 3, "insertions": 84, "deletions": 12},
  "confidence": 0.83,                              // §7
  "risk": {"blast_radius": 0.2, "source": "code-review-graph"},  // §7
  "test_result": "pass",
  "review_findings": [],                           // ride-along from the gauntlet
  "state": "AWAITING_APPROVAL | NEEDS_ATTENTION",
  "forensic_bundle": null                          // path when NEEDS_ATTENTION
}
```

Delivery rides the **P1a broker held-action surface** (stable action IDs → durable broker-side
records; the Telegram message carries summary + buttons + a local artifact path — never a truncated
body hash). Batch-approve = a multi-select over the safe cards; each Approve is one held-action
execution through the broker (rebase→re-test→merge under the per-repo lock, exactly P2.4's path).

**Rendering** reuses `pm_os/bin/run-morning.js` as the generator (plan §4.4): the factory digest is
a new section fed the ranked cards; yk-voice standup lines (shipped / blocked+question / cost+trend
arrow / queued-for-tonight) sit above the cards.

### 5.3 Question queue (tap-to-answer)

Parked/ambiguous jobs (from P3's spec-review gate) and self-heal escalations surface as a
**question queue**: each question is a held-action `{type:"question", summary, proposed_answers[]}`
with 2–3 tap-to-answer buttons (the same 2–3-proposed-answers discipline as P3's `questions.md`).
An answer resumes the parked job from its checkpoint.

---

## §6 — REQ-04a: Cost-per-merged-PR trend board (`factory/cost_trend.py`, P4-5)

The north-star number. `cost_trend.compute(window_a, window_b)`:
- **cost-per-merged-PR** for a window = Σ(per-job cost log usd for jobs merged in the window) ÷
  (count of merged PRs in the window). The numerator is the per-job cost log (§4.4); the
  denominator counts `state=DONE` merges from the job store.
- Two windows (e.g. this week vs last) → **`{value_a, value_b, direction: down|flat|up}`**.
- Surfaced in the morning briefing (a trend arrow beside cost) and in the weekly retro (P5/P7).

`cost accounting matches provider spend within tolerance` (the P4 gate) is verified by reconciling
the per-job cost log Σ against `daily_budget.spent_today_usd` (`cost_stops.py`) for a synthetic
night — §13.

---

## §7 — REQ-04b: Per-job confidence score (`factory/confidence.py`, P4-6)

Computed **at the moment a job reaches AWAITING_APPROVAL** and written to the existing
`jobs.confidence` column (`job_store.py:134`) via `transition(..., extra={"confidence": c})`.

**Pure function, reproducible from stored inputs** (the gate's reproducibility requirement):
```
conf = w_test·test + w_review·(1 − severity) + w_blast·(1 − blast)
     + w_amb·(1 − amb) + w_hist·hist          # all terms normalized to [0,1]
clamp to [0,1]; weights owner-tunable, sum to 1
```

**Inputs (each stored on the job so recompute reproduces the score):**
| Term | Source | Normalization |
|---|---|---|
| `test` | gauntlet `test_result` (+ coverage delta if available) | pass=1, flaky=0.5, fail=0 |
| `severity` | gauntlet `review_findings` count × severity | 0 (clean) … 1 (P0 finding) |
| `blast` | code-review-graph impact/blast radius for the diff | normalized 0…1 (bigger blast → lower conf) |
| `amb` | P3.1 spec-ambiguity score (`ambiguity_rubric.py`) | 0 (crisp) … 1 (vague) |
| `hist` | P5 scorecard historical success for repo×task_type; **neutral 0.5 before the scorecard exists** | 0…1 |

Weights live in a tunable config (`confidence-weights.yaml` or a section of `trust-policy.md`),
default e.g. `w_test .35, w_review .25, w_blast .15, w_amb .10, w_hist .15` (sum 1). The stored
input vector `{test, severity, blast, amb, hist}` is persisted alongside `confidence` so
`recompute(inputs, weights)` reproduces the exact value — falsifiable (§13).

**Consumers:** the morning digest ranks by it (§5.2); the trust ledger uses it as the graduation
**confidence floor** (`trust_policy.min_confidence` AND-gate, `trust_policy.py:80`); the P4 gate
references it.

---

## §8 — REQ-04c: Voice-note → spec'd task (`factory/voice_intake.py`, P4-7)

The early taste (its only hard dep — the task-splitter — shipped in P3). Flow:
`Telegram audio → gateway audio-transcription path → transcript → task_splitter.split(transcript)
→ a spec'd task card (or a `questions.md` park if too vague)`.
- Reuses the gateway's **existing** audio path (no new transcription integration).
- Reuses P3's `task_splitter` + spec-review gate wholesale — a voice note is just another intake
  source feeding the splitter (plan §3.5 already wires voice as intake; P4-7 is the early
  single-note taste; full multi-note threading consolidates in P6).
- **Residency:** the splitter's own worker already routes through `build_worker_env` (P3 §13.1), so
  a work-linked voice note never egresses to a third-party splitter model.

---

## §9 — File-disjoint task breakdown P4-a..g (→ plan tasks P4-1..7)

Each lettered task owns disjoint files so a per-task subagent pair never collides. Column
"→ plan" maps to the plan's P4-1..7 rows.

| Task | → plan | Owns (new files) | Touches (additive only) | Depends on |
|---|---|---|---|---|
| **P4-a** Phase-checkpoint + self-heal | P4-1 | `phase_checkpoint.py`, `forensics.py`, `self_heal.py` | `supervisor.py` (call clear_phase + capture before kill); `scheduler.tick` (resume branch — shared with P4-b) | P3-3 |
| **P4-b** Router + ladder + residency + failover + cost | P4-2 | `model_router.py`, `failover.py`, `worker_cost.py` | `worker_runner.WorkerRunSpec` populated by router (no edit to the guard); `scheduler.tick` (WAIT re-check — shared with P4-a); reads `capabilities.json` | P4-a, P0-4 |
| **P4-c** Overnight queue | P4-3 | `overnight_queue.py` | `scheduler.run_forever` (night window wrapper) | P4-a, P3-3 |
| **P4-d** Morning packet + PR card + question queue | P4-4 | `morning_packet.py`, `pr_card.py` | broker held-action surface (P1a); `run-morning.js` (new digest section) | P4-f, P1a-4 |
| **P4-e** Cost-per-PR trend board | P4-5 | `cost_trend.py` | reads per-job cost log (P4-b) + `daily_budget` | P4-d, P4-b |
| **P4-f** Confidence score | P4-6 | `confidence.py`, `confidence-weights.yaml` | `job_store` confidence write via `transition(extra=)`; reads gauntlet + code-review-graph + ambiguity_rubric | P2-1, P3-1 |
| **P4-g** Voice-note intake | P4-7 | `voice_intake.py` | gateway audio path; `task_splitter` | P3-1 |

> **Shared-file note:** P4-a and P4-b both add a branch to `scheduler.tick` (resume-from-checkpoint;
> WAIT re-check). To keep them disjoint, land P4-a's `tick` edit first (adds a `_resume_ready()`
> hook), then P4-b extends the *hook's* candidate list — the two never edit the same lines. The
> orchestrator sequences a→b.

---

## §10 — Per-component RED tests (4–6 falsifiable each, TDD-first)

**P4-a phase-checkpoint + self-heal**
1. `test_clear_phase_writes_atomic_record` — clear SPEC → `checkpoint.json` exists with
   `gates_cleared=["SPEC"]`, `phase="IMPLEMENT"`; a partial write leaves the prior file intact.
2. `test_resume_relaunches_from_last_cleared_phase` — checkpoint at IMPLEMENT, kill → `resume`
   builds a `WorkerRunSpec` with `extra_prompt`=brief and starts at IMPLEMENT, **not** SPEC.
3. `test_resume_never_starts_mid_phase` — a checkpoint written mid-IMPLEMENT resumes at the
   IMPLEMENT *boundary* (last cleared), never at an arbitrary turn.
4. `test_forensic_bundle_captured_before_kill` — inject a stall → the bundle
   (transcript-tail + failing-test + diff + meta) exists on disk **before** the kill signal is sent
   (assert ordering via a spy).
5. `test_self_heal_is_bounded_N1` — a self-heal that also fails → job goes NEEDS_ATTENTION with the
   bundle path; no second self-heal is attempted.
6. `test_no_checkpoint_treated_as_fresh` — a job that cleared no phase → `resume` re-enqueues at start.

**P4-b router + ladder + residency + failover (the crown; bypass-tested)**
1. `test_false_repo_ladder_has_no_openrouter_rung` — `select_tier(repo, policy=allow_openrouter:false)`
   → the filtered ladder contains **only** first-party rungs (claude/codex); no third-party rung present.
2. `test_429_failover_steps_down_one_probe_confirmed_rung` — force a 429 on tier-0 → `next_rung`
   returns the next **probe-confirmed** rung (from `capabilities.json`), and the job re-launches from
   the phase brief and finishes.
3. `test_false_repo_429_waits_never_openrouter` — a `false` repo hits 429 on the last first-party
   rung → `next_rung` returns `WAIT_FOR_CAPACITY`; the job parks `WAITING_CAPACITY`, and **no
   OpenRouter dispatch occurs** (assert `WorkerRunner.launch` never called with `worker="openrouter"`).
4. `test_runner_guard_is_second_wall` — force the router to (buggily) emit an OpenRouter rung for a
   `false` repo → `build_worker_env` raises `ResidencyViolation` before any env is built (the
   existing `worker_runner.py:362` wall; no key placed).
5. `test_phantom_model_never_enters_ladder` — a candidate name absent from `capabilities.json`
   (`GLM-5.2`) is **not** a rung; the ladder is drawn only from `live_catalog && ping_ok`.
6. `test_cost_accounting_matches_synthetic_spend` — synthetic claude+openrouter token usage →
   `worker_cost` usd Σ equals the `daily_budget.spent_today_usd` after `commit_spend`, within
   tolerance; the OpenRouter $2 sub-ceiling CAS blocks the 3rd dispatch that would breach it.

**P4-c overnight queue**
1. `test_night_window_stops_new_spawns_outside_window` — outside the window the tick launches no NEW
   job but lets in-flight finish.
2. `test_concurrency_never_exceeds_three` — a graph with 6 ready nodes → ≤3 live at any tick
   (`CONCURRENCY_CAP`).
3. `test_evening_graph_runs_to_morning_packet` — enqueue a graph in the evening → by window-end the
   jobs reach terminal states and `morning_packet.build` returns cards.
4. `test_waiting_capacity_job_resumes_when_first_party_free` — a `WAITING_CAPACITY` job resumes on a
   first-party rung once quota is back.

**P4-d morning packet + PR card + question queue**
1. `test_packet_ranked_by_confidence_desc` — 3 jobs with confidences .9/.5/.2 → cards ordered .9,.5,.2.
2. `test_pr_card_has_all_fields` — a completed job → card carries summary, reasoning, alternatives,
   reversibility, diff_stat, confidence, risk (assert every key).
3. `test_batch_approve_rides_broker` — batch-approve 2 safe cards → 2 held-action executions through
   the broker (no direct merge path).
4. `test_question_queue_tap_answers_resume_job` — a parked job's question is answered by ID → the job
   resumes from its checkpoint.
5. `test_irreversible_card_never_batch_auto` — a card with `reversibility:irreversible` is excluded
   from batch-auto and always held.

**P4-e cost trend**
1. `test_cost_per_merged_pr_two_windows_direction` — synthetic cost log over 2 windows →
   `{value_a, value_b, direction}` correct.
2. `test_trend_down_when_cheaper` — window B cheaper per PR → `direction="down"`.
3. `test_zero_merges_window_is_safe` — a window with 0 merges → no divide-by-zero (returns null/None).

**P4-f confidence**
1. `test_two_differing_risk_jobs_get_materially_different_confidence` — a clean/small-blast job vs a
   P0-finding/large-blast job → confidences differ materially (e.g. >0.3 apart).
2. `test_recompute_from_stored_inputs_reproduces` — recompute from the persisted input vector →
   bit-identical (within float tolerance) to the stored `confidence`.
3. `test_weights_sum_to_one_enforced` — weights not summing to 1 → a load-time error.
4. `test_hist_neutral_before_scorecard` — with no scorecard, `hist=0.5` (neutral), not a crash.
5. `test_confidence_clamped_0_1` — pathological inputs → the result stays in [0,1].

**P4-g voice intake**
1. `test_audio_transcript_reaches_splitter` — a stub audio → transcript → `task_splitter.split` called.
2. `test_vague_voice_note_parks_as_question` — a too-vague transcript → a `questions.md` park, not a guess.
3. `test_clear_voice_note_becomes_task_card` — a crisp transcript → a spec'd task card.
4. `test_work_voice_note_residency_gated` — a work-linked note's splitter worker never routes
   third-party (guard fires).

---

## §11 — Fresh-context gate tests (synthetic, single-run — the 3-night live run is STAGED)

The flagship 3-consecutive-night gate is a live-run and is **STAGED** (§12). These synthetic
single-run tests exercise every gate condition deterministically so the design is verifiable
*without* waiting three nights:

| Gate condition (from plan §P4 exit gate) | Synthetic single-run test |
|---|---|
| A forced 429 → job re-launches on next tier + finishes | `test_forced_429_relaunches_next_tier_and_finishes` (drives §4.2 with a stubbed 429 + a stub worker that succeeds on tier-1) |
| An `allow_openrouter:false` repo NEVER dispatches to OpenRouter on failover | `test_false_repo_never_dispatches_openrouter_on_failover` (§4.3 + §13 — both walls) |
| A mid-job kill resumes from checkpoint (not parked) | `test_midjob_kill_resumes_from_checkpoint` (§3.2) |
| 2 differing-risk jobs get materially different confidence + recompute reproduces | `test_confidence_differentiates_and_reproduces` (§7) |
| Cost accounting matches a synthetic spend within tolerance | `test_cost_accounting_reconciles_synthetic_night` (§4.4 + §6) |
| Morning packet confidence-ranked + batch-approvable | `test_morning_packet_ranked_batch_approvable` (§5.2) |

All six are **single-run, no-real-money, no-real-provider** (the subprocess boundary + provider
429/usage are the only stubs; residency/router/confidence/cost logic is real). They are the RED
tests P4 lands before implementation.

---

## §12 — STAGED-GATES entry (the 3-night flagship)

Add to the plan's STAGED-GATES ledger:

```
STAGED GATE — P4 flagship "3 consecutive nights on p4-gate-queue.md"
  Status: STAGED (verified in synthetic single-run form §11; live run deferred).
  Why staged: the live gate needs (a) real overnight provider quota, (b) a committed
    p4-gate-queue.md seed (≥5 tasks: ≥2 feature + ≥1 refactor across ≥2 repos, none
    authored to be trivially green), (c) 3 real nights of wall-clock — none reproducible
    in a single CI run.
  Synthetic proxy that IS enforced now: the six §11 tests (forced 429 failover, false-repo
    never-OpenRouter, mid-kill resume, confidence differentiation+reproduce, cost
    reconciliation, ranked batch-approvable packet).
  Promotion criteria (owner-run, off-CI): 3 consecutive nights, each: ≥5 tasks intake→PR-ready
    with zero human before 7am; total cost < $5 nightly ceiling; ≥1 failover to a lower tier via
    the handoff brief; ≥1 crashed worker resumes from a phase checkpoint; morning packet
    confidence-ranked + batch-approvable; cost accounting matches provider spend within tolerance.
  Seed queue: docs/plans/harness/fable/p4/p4-gate-queue.md — committed BEFORE the run (no
    cherry-picking).
```

---

## §13 — The residency enforcement point (crown-risk detail, bypass-tested)

**Two independent walls; the second is already in production.**

1. **Router pre-filter (new, `model_router.py`).** The ladder handed to `next_rung`/`select_tier`
   is filtered so a `false`/unknown-policy repo's ladder contains **no third-party rung**. A 429 on
   the last first-party rung yields `WAIT_FOR_CAPACITY`, never an OpenRouter rung. This is *behavioral*
   correctness — the job waits for subscription capacity (plan §5, P3 contract).

2. **Runner guard (existing, `worker_runner.py:362`).** Even if wall 1 had a bug and emitted an
   OpenRouter rung, `build_worker_env` calls `assert_openrouter_allowed(repo, worker, policy)`
   **before** the model-key lookup (`worker_runner.py:362-364`), which raises `ResidencyViolation`
   (`residency_guard.py:85`) for `worker="openrouter"` + `allow_openrouter:false|None`. No env is
   built; no OpenRouter key is placed; the job parks NEEDS_ATTENTION. This wall predates P4 (wired
   in P3, closing P3-review P0-1) and is *unconditional*.

**Bypass test (§10 P4-b #3,#4 + §11):** force the router to emit an OpenRouter rung for a `false`
repo → assert (a) the router pre-filter never produced it in normal flow, and (b) if injected, the
runner guard raises before any dispatch. A `false` repo can reach OpenRouter through **neither**
path. `policy=None` (unknown residency) is treated as `false` at both walls — fail-closed.

**Cost-residency interaction:** the OpenRouter ≤$2/day sub-ceiling (a `provider="openrouter"`
`DailyBudgetLedger` CAS) only ever applies to `true` repos — a `false` repo has no OpenRouter spend
by construction, so the sub-ceiling and the residency wall are orthogonal (no double-counting).

---

## §14 — Open questions for the owner (none block the design; defaults chosen)

- **OQ-P4-1 Confidence weights.** Default `w_test .35 / w_review .25 / w_blast .15 / w_amb .10 /
  w_hist .15` (sum 1). Owner-tunable in config. *Assumed until told otherwise.*
- **OQ-P4-2 Wait-for-capacity cap.** Default = the `compute.md` quota-reset window; a job waiting
  past it → NEEDS_ATTENTION. *Assumed.*
- **OQ-P4-3 Night window hours.** The off-peak window is read from P0.4's schedule; P4 does not
  re-pick it. *Deferred to the existing schedule.*

---

**Verdict:** design complete, file-disjoint, TDD-first; the crown residency behavior is closed by
two independent walls (the second already in production at `worker_runner.py:362`). Zero code
written. Adversarial review + verifier follow.
