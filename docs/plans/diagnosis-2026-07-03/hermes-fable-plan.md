# ABOUTME: Factory-first build plan for hermes as a self-supervising, self-improving autonomous software factory.
# ABOUTME: The chief-of-staff comms layer is the factory's control panel, not the product. v1 (commit 58f5e56ad) preserved in git.

# Hermes Fable Plan — Autonomous Software Factory (v2, factory-first)

**Date:** 2026-07-04
**Status:** DRAFT, pending owner approval. This version inverts v1's spine (v1 built a personal assistant first and bolted a code-building bot on at the end; v2 builds the code-building factory first). It also rewrites the whole document in plain language and adds a new reliability phase (Phase R) so nothing runs overnight on an un-hardened machine.

---

## §0 — What This Is, and the North Star

### What is this? (read this first)

**Hermes is a program that runs on Yu-Kuan's Mac.** Today it can do one AI task at a time with a human watching. It has real reliability holes: a safety guard that the AI can reach around, an approval feature (`/approve-email`) that is broken four different ways, and a codebase that has drifted ~5,000 commits behind the open-source project it was forked from, which makes upgrades painful.

**When this plan is done, hermes will be an autonomous software factory.** You hand it coding work — a spec document, a Jira ticket, an existing repo, or a fresh idea — and overnight it splits the work into ordered tasks, runs a fleet of AI "workers" (background AI programs, no visible window) that write and test the code on isolated copies of the repo, checks every change through tests plus a second independent AI review, and by morning presents you with finished pull requests you approve from your phone with one tap.

**A night in the life:** In the evening you drop a spec into the queue and go to sleep. At 3am the AI workers build the feature, write the tests, run them, and have a second AI review the diff. In the morning your phone shows a pull request ready to merge and a 20-second summary — you read it over coffee and tap Approve.

**Everything else in this document is how we build that**, safely and in an order where the first useful result arrives early.

### The product is the factory; comms is the control panel

The Telegram/email/pm_os surface is how you *talk to* the factory (drop a voice note that becomes a spec'd task; approve a pull request with one tap; read a morning briefing) and how the factory *reaches out* (updating a Jira ticket, sending a message you approved). v1 built an excellent assistant and added a pull-request bot as an afterthought; v2 builds the factory and repurposes that assistant work as the factory's control panel (§P6).

> **Term:** *pm_os* — a separate personal-assistant codebase Yu-Kuan already owns that handles email, calendar, and Jira. This plan reuses its plumbing rather than rebuilding those integrations.
> **Term:** *pull request (PR)* — a proposed set of code changes submitted for review before being merged into the main codebase. The factory produces PRs; you approve or reject them.

### Why now

The building blocks are unusually complete. Hermes already has, running in production: an API gateway, a scheduler that fires jobs on a timer (cron), SQLite databases for sessions and task state, a policy engine, adapters for 30+ AI model providers, workflow-orchestration skill files, and the pm_os intake plumbing. The factory is mostly *assembling existing parts* plus a genuinely-new core: a job ledger, a trust ledger, a model router, a cost meter, a task-splitter, and — the one part we confirmed is missing — the harness that launches AI workers as real background processes. We are honest about which parts are reuse and which are new code.

> **Term:** *gateway* — the always-on hermes server process that receives messages and dispatches work; supervised by macOS launchd (the Mac's service manager).
> **Term:** *SQLite* — a small file-based database; the factory keeps all its state in SQLite files on disk so a crash never loses progress.
> **Term:** *skill* — a Markdown instruction file that tells an AI worker how to do a specific kind of task. Skills are how the factory is extended and, later, how it improves itself.

### North-star metrics (measured continuously; reported in the weekly retro; §P5 keeps them honest)

| Metric | Direction | Definition (in plain terms) |
|---|---|---|
| **Cost-per-merged-PR** | ↓ trending | Total money the factory spends on AI, divided by the number of pull requests actually merged. The single number that proves the whole thing is getting cheaper. |
| **Autonomous-merge rate** | ↑ as trust is earned | Fraction of merges the factory landed on its own (under earned auto-merge) versus the ones that waited for a human tap. |
| **Overnight completion rate** | ↑ | Fraction of jobs that go from intake all the way to a ready pull request with zero human touch before you wake up. |
| **Morning-decision load** | → about 5 taps | How many decisions you must make at wake time. Target: a handful, batch-approvable. |

**A guiding non-goal:** this is *not* a lights-out, human-free factory. The realistic 2026 ceiling is "mostly autonomous, with a human required for anything irreversible or high-stakes" (production deploys, deletions, spending money). We aim there deliberately.

---

## §1 — Locked Decisions

Owner-confirmed on 2026-07-03. These override anything in v1 that conflicts.

| # | Lock | Why (one line) |
|---|---|---|
| **L1** | **Earned-trust autonomy.** The factory starts by asking for approval on *everything*. Each repo-plus-task-type combination earns the right to auto-merge by building a track record. You can pick profiles (ask-for-everything ↔ auto-merge on personal non-production repos). Trust is data and is revoked on the first regression. | Always requiring a tap wastes confidence you've earned; always auto-merging is reckless. A measured track record is the only honest signal. |
| **L2** | **Paid subscriptions first, OpenRouter as the default overflow.** Use the Claude Max and Codex subscriptions primarily; when they hit a wall, spill over to OpenRouter. Route each task to a cost-appropriate model. A per-repo switch keeps confidential code off third-party model hosts. | Subscriptions are already paid for; overflow keeps the night productive past quota walls at a fraction of the price. |
| **L3** | **Take work from all four sources:** spec documents, Jira queues (via pm_os), existing `~/Code` repos, and brand-new (greenfield) ideas. | The vision is "eats a queue of work," not "takes one task at a time." |
| **L4** | **Local-first now, but designed so a second machine or cloud workers slot in later without a rewrite.** The worker-launcher is *new code* (a real OS process with a proper kill mechanism), not a thin wrapper over the existing in-process helper. | Retrofitting a second machine later is expensive; designing for it now is cheap. |

> **Term:** *OpenRouter* — a pay-per-use service that forwards your API calls to many different AI models, often at roughly one-tenth to one-fiftieth the price of a frontier subscription. Used here as the overflow tier when the paid subscriptions are exhausted.
> **Term:** *repo* (repository) — one codebase under git version control.

**Surviving v1 locks (carried forward, still valid):**

| Lock | Why |
|---|---|
| **Clean-upstream reset** — start from a fresh copy of the open-source project and re-apply our small changes on top, rather than merging the ~5,000-commit fork. | *A fork that has drifted 5,000 commits behind its source is expensive to upgrade — every monthly upgrade has to reconcile thousands of conflicting changes. Keeping our own changes small and "on top" makes future upgrades cheap.* |
| **The broker lives outside the assistant's process.** | *A guard that runs inside the same program as the AI shares the AI's permissions and can be reached around (the AI literally read a credential off disk and edited its own guard once — the failure that started this whole effort). A separate process that holds the credentials, which the AI cannot reach, cannot be bypassed.* |
| **Plain code supervises; no AI in the control loop.** | *The scheduling/supervision logic is ordinary code reading a database, not an AI. An AI supervisor burns tokens babysitting and runs out of memory — the single most common way these systems fail.* |
| **One worktree per job, a per-repo merge lock, and rebase-retest-merge.** | *Matches 2026 field practice; gives isolation without heavyweight containers.* |
| **Scrubbed worker environment + test gate + independent second-AI review.** | *Checking the work is the real bottleneck, not producing it.* |

> **Term:** *broker* — a small, separate program (its own OS process) that is the *only* thing holding send/push/API credentials. The assistant asks the broker to do consequential actions (send a message, push code, merge a PR); the broker checks the rules and either does it or holds it for your approval. Because credentials live only in the broker and are removed from everywhere the AI can read, the AI cannot act on its own.
> **Term:** *worktree* — an isolated checkout of a git repo on disk. Each worker gets its own worktree so parallel workers never overwrite each other's files.
> **Term:** *fresh-context verified* — a gate is checked by a person or a separate AI agent that has no prior knowledge of the task, so the builder can never rubber-stamp its own work.

---

## §2 — Architecture

Six layers. The control logic is plain code over Markdown files and SQLite; AI reasoning lives only in the workers and in the review/task-splitting steps.

> **Glossary for the diagram below** (each term is used again later, defined once here):
> - **Intake** — the four ways work enters: spec docs, Jira queues, existing repos, greenfield ideas.
> - **Task-splitter (decomposition engine)** — a one-time AI pass per project that turns a spec into an ordered list of small tasks, each with its acceptance tests written first.
> - **Dependency graph (DAG)** — the ordered task list: each task records which other tasks must finish before it can start ("directed acyclic graph" — a graph with no cycles).
> - **Fleet supervisor** — the plain-code scheduler that decides which tasks run now, in parallel or in order.
> - **Model router** — plain code that picks which AI model handles each task and steps down to cheaper providers when the primary is throttled.
> - **Worker** — a background AI process (`claude -p` = the Anthropic CLI, `codex exec` = the OpenAI CLI, or an OpenRouter model) that actually writes the code, run without a visible window and controlled by scripts.
> - **Checkpoint/resume** — saving a job's progress at each phase boundary so a crashed worker can be restarted mid-job instead of starting over.
> - **429 / rate limit** — HTTP status 429 means "you're sending requests too fast / you've hit your quota." The factory detects this explicitly and steps down to the next provider.
> - **Verification gauntlet** — the chain of checks every change must pass: acceptance tests → the repo's own test suite → an independent second-AI review → an end-to-end demo drive.
> - **Trust ledger** — a table recording, per repo and task-type, how past merges went (clean / needed a fix / rejected / had to be reverted). It's the data that earns or revokes auto-merge.
> - **Cost meter + kill-switch** — per-job spend tracking plus three independent ways to stop runaway spending.

```
                          ┌──────────────────────── INTAKE (×4) ─────────────────────────────┐
   spec docs ───▶ split into tasks    Jira queue ───▶ poll + normalize     ~/Code repo ───▶ onboard + profile
   greenfield idea ─▶ scaffold plan   (via pm_os plumbing)                  voice note ─▶ transcribe + spec
                          └───────────────────────────────┬──────────────────────────────────┘
                                                          ▼
                                         ┌──────────────────────────────┐
                                         │  TASK-SPLITTER (AI, one pass  │  interrogate the spec for gaps →
                                         │  per project) → ordered,      │  if too vague, park it and ask
                                         │  acceptance-test-first        │  the owner (NEVER guess) →
                                         │  dependency graph of tasks    │  otherwise emit the task graph
                                         └───────────────┬──────────────┘
                                                          ▼
   ┌─────────────────────────── FLEET SUPERVISOR (plain code, fires on a timer) ──────────────────────────────┐
   │  Runs independent tasks in parallel, serializes dependent ones, throttles when a provider is low on quota │
   │  reads/writes: job ledger (SQLite) · trust ledger · run history · cost meter · compute inventory          │
   │  Not one-state-per-tick — it reasons about the whole fleet: reallocate, split, checkpoint/resume, watch    │
   └───┬───────────────────────┬───────────────────────┬───────────────────────┬───────────────────────┬──────┘
       ▼                       ▼                       ▼                       ▼                       ▼
  MODEL ROUTER          WORKER LAUNCHER           CHECKPOINT/RESUME       VERIFICATION            COST METER +
  subscription          ┌─ local-subprocess ─┐    + 429 FAILOVER          GAUNTLET                KILL-SWITCH
  frontier → OpenRouter │ launch / check /   │    when throttled, re-      acceptance tests →      per-job budget,
  ladder; per-task-type │ kill / collect     │    launch the job on the    repo tests →            wall-clock cap,
  routing table,        ├─ remote-worker ────┤    next cheaper provider    independent 2nd-AI       daily ceiling;
  learned from the      │ (stub — proves a   │    from the last saved       review →                drain gracefully
  scorecard             │  2nd machine fits) │    phase (not mid-task)      demo drive
                        └────────────────────┘
       │                       │                       │                       │                       │
       └───────────────────────┴───────────┬───────────┴───────────────────────┴───────────────────────┘
                                            ▼
                        ┌──────────────────────────────────────────┐
                        │  BROKER + TRUST LEDGER (separate process)  │  the enforcement boundary (credentials
                        │  approval lanes · graduation rules ·       │  removed from the AI); ONE approval
                        │  revocation · fails closed                 │  surface for every gate (send/push/merge)
                        └───────────────────┬────────────────────────┘
                                            ▼
   ┌─────────────────── MORNING SURFACES (the control panel / comms, §P6) ────────────────┐
   │ standup briefing · one-tap PR cards (summary · reasoning · reversibility · risk)       │
   │ question queue · decision replay · demo-reel-of-the-night (later phase)                │
   └───────────────────────────────────────────────────────────────────────────────────────┘
                                            ▲
   ┌──────────────────── LEARNING LOOPS (§P5 — feed everything above) ──────────────────────┐
   │ per-model scorecard → routing table · weekly skill-improvement pass ·                   │
   │ nightly retro PROPOSES skill edits (owner approves the diff) · drift/budget watchdogs   │
   └───────────────────────────────────────────────────────────────────────────────────────┘
```

**Component notes** (each says what it reuses and what is new):

- **Intake pipelines (×4).** *Jira poller is new code:* there is no Jira reader in pm_os today; the existing `jira-update` skill reads *one* ticket at a time via a `curl` call and an API token, but it's a prompt, not a background poller. We build a small poller that reuses that skill's auth pattern and turns tickets into job specs. *Spec docs:* the `prd-writer`/`prd-review`/`eng-stories` skills feed the task-splitter. *Existing repos:* the `codebase-mapping` skill plus the code-review-graph tool (both already present) run an onboarding pass that writes a `factory-repo-profile.md`. *Greenfield:* a scaffold-plan job type. *Voice notes* ride the gateway's existing audio-transcription path.
- **Task-splitter (new — the biggest missing piece).** A one-time AI pass per project: take the spec, attack it for gaps (using the `prd-review` skill), then grade the spec for the classic failure kinds (undefined acceptance criteria, vague dependencies like "or equivalent," missing field names). If it's too ambiguous, the job parks and files clear questions — each with 2–3 proposed answers — into a `questions.md` file instead of guessing. If it's clear, it emits an ordered, acceptance-test-first task graph. Reuses the `eng-stories` skill's behavioral breakdown as the seed for each task card.
- **Fleet supervisor (plain-code scheduler — new logic on existing plumbing).** Extends the hermes scheduler (`cron/scheduler.py`, which fires every 60 seconds with a file lock so two ticks never overlap) and the task-queue dispatcher that already runs inside the gateway process (`kanban.dispatch_in_gateway`, on by default; the older standalone dispatcher service is deprecated — use the gateway one). It does *not* just advance one state per tick; it reasons about the whole fleet — fan out independent tasks, serialize dependent ones, throttle when a provider is low on quota, reallocate a stuck job. Still plain code, still crash-proof because all state is on disk.
  > **Term:** *kanban dispatcher* — the component that hands queued tasks out to workers and tracks which worker owns which task (named after the kanban board metaphor of cards moving through columns).
- **Worker launcher (new code).** A `Worker` contract with four operations: `launch / check / kill / collect-result`. The `local-subprocess` version launches `claude -p` or `codex exec` as a real OS process in its own **process group** (a Unix mechanism that lets you kill a process *and all its children* in one shot), watches it via the modification-time of its output file (not just "is the process alive"), and kills it by sending SIGTERM to the whole process group. None of this exists in the current in-process helper (`delegate_tool.py`), which runs work on threads inside the assistant, not as separate processes. A `remote-worker` *stub* ships alongside — not to actually run, but to prove the launcher has no assumptions that only work locally, so a second machine drops in later. *What the old helper is still good for:* its orchestrator-side plumbing (fan-out, per-task result collection) is useful scaffolding the supervisor drives the process pool from; and `kanban_tools.py` gives us the worker-owns-task rows. But the process launcher itself is new.
- **Model router (cost-aware; new logic on existing adapters).** A `model-routing.md` file: try the subscription frontier models (Claude Max, Codex) first, then step down the OpenRouter ladder. **Model names in this document are candidates, not truth** — the P0 capability probe (§P0.2) checks every candidate name against each provider's live catalog at startup and writes the confirmed routing table. What we've actually confirmed present locally today is `glm-5` (via ZAI/Novita) and `GLM-5.1-FP8` (via GMI) — *not* `GLM-5.2`; the probe resolves the real best-available model per category instead of trusting a name here. Categories: long-horizon software-engineering overflow, best-value coder, tool-use-reliable, and cheap grunt work. The per-task-type routing table (bugfix / feature / refactor / test / docs) is *learned over time* from the scorecard (§P5). Builds on the 30+ provider adapters already in `plugins/model-providers/`.
- **Broker (enforcement, ships in P1a) + trust ledger (policy, ships in P1b).** The broker — the separate credential-holding process — is the *enforcement* boundary and is all the first merge needs, so it ships in **P1a**. The trust ledger is the *policy* the broker later enforces; it ships in **P1b, after P2**, because there are no real merge outcomes to learn from until P2 has run. The ledger (`trust-ledger.md` + SQLite) records per-(repo × task-type) results: merged-clean / merged-with-a-fix / rejected / reverted-later. A `trust-policy.md` file maps track record to an autonomy tier (0 = always ask; 1 = auto-merge on personal non-production repos after a threshold; 2 = broader). Owner profiles are just a config switch over the ledger. The trust ledger is new columns and tables added to the same database the policy engine already uses to record decisions — not a new database (`audit`/`policy`/`workflow` tables extended). Generalizes the email-approval state machine.
- **Checkpoint/resume + 429 failover (new phase layer).** Each job phase runs with a fresh AI agent (so context doesn't pile up), and the job's phase state — `{job_id, phase, gates_cleared, artifact_paths, handoff_brief_path}` — is saved to disk. This is a *new* layer: the existing `checkpoint_manager.py` does per-turn file rollback by commit hash and knows nothing about factory phases. That stays useful as the *within-a-worker* undo tool; the phase-level checkpoint protocol on top is new. When a worker hits a 429, the supervisor catches it explicitly (a naive retry can drain a month's quota in minutes) and re-launches the job down the model ladder — see the handoff note below.
- **Cost meter + kill-switch with graceful drain.** Logs tokens and dollars per job (input + output + cache × price). Three independent stops: a per-job dollar cap, a wall-clock timeout that kills the process group, and a daily reserved-budget ceiling. At 80% of the ceiling it stops launching new jobs and lets in-flight ones finish; at 100% it snapshots and parks cleanly. A compute inventory (`compute.md`: remaining Max quota, Codex quota, OpenRouter balance, per-provider rate limits) feeds the router. Hermes already tracks per-account token usage (`agent/account_usage.py`) but has no per-job-dollar layer — this adds it.
- **Verification gauntlet.** Acceptance tests first (the `e2e-test-writer` skill) → the repo's own test suite (deterministic) → an independent `codex review --base main` (a *different* AI model reviews the diff) → an end-to-end demo drive via the `browse` skill. One bounded auto-fix retry, one review-fix retry, then it's marked NEEDS_ATTENTION — no endless flip-flopping. Reuses the existing QA skills wholesale (`garry-review`, `qa`, `e2e-test-writer`).
- **Cross-provider handoff on failover.** Claude's tool-call threading doesn't map one-to-one onto OpenRouter chat models, so failover does *not* replay a raw transcript. Instead it re-launches from the **last cleared phase boundary** with a **compact handoff brief** — the spec, the acceptance tests, the diff so far, and a short state summary — saved on the job's phase record. Fresh-agent-per-phase makes this natural: the portable unit is the brief, not the conversation. A 429 in the middle of a phase rewinds to that phase's start and resumes from the brief on the next provider.
- **Learning loops (§P5).** A per-model, per-task-type scorecard feeds the routing table. A weekly pass reads recent job histories and improves the skill files. A nightly retro *proposes* edits to skills as diffs you approve. Drift and budget watchdogs (including a watchdog that watches the watchdog).
- **Morning surfaces (§P6).** A standup briefing; one-tap PR cards with everything you need to decide (summary, the reasoning and alternatives, whether it's reversible, the diff size, a risk score); a question queue; a digest ranked by confidence; decision replay; and eventually a demo reel of the night's work.

---

## §3 — Phases

The order is deliberately "factory first," but split so the first result you can see and use lands early:

**P0 → Phase R → P1a → P2 (first overnight PR, about day 26–30) → P1b → P3 → P4 → P5 → P6 → P7.**

Trust graduation (P1b) deliberately comes *after* the first real merges (P2) it learns from. Each phase lists: **goal · what it builds on · deliverables · a binding exit gate (measurable) · rough effort.** Effort figures are focused solo-dev days — directional, not commitments; dependencies are hard gates. Every binding gate is **fresh-context verified** (checked by someone/something with no prior knowledge of the task) — never self-verified.

### PHASE 0 — Factory-ready host

**Goal:** The Mac can host long-lived background worker fleets without the OS killing them, the worker tools and all AI providers are version-pinned and probed, and the control plane knows how much compute it has.
**Builds on:** a clean upstream hermes checkout (the fork is currently ~61 commits ahead and ~5,000 behind); `gateway/run.py` under launchd supervision; the `check-token-health.js` wake-gate pattern from pm_os.
**Deliverables:**
- **0.1 Clean base + branch migration.** Make a fresh checkout from `origin/main` into `~/Code/hermes-factory`; bring the `.env` file and the `~/.hermes/` config; do *not* merge the fork or bring the plugin god-file edits. Then migrate the current `feat/governance-plugins` branch (61 commits): *keep* the three governance plugins (control-room, email-send-guard, tool-registry-guard) and their `~/.hermes/` state — these *are* the broker/trust foundation — by re-applying them as clean commits on the fresh checkout *before* P1a starts building on them; *drop* the plugin god-file edits and the ~5,000-commit fork drift. All later phases target `~/Code/hermes-factory`. (Positive: fresh clone, plugins re-applied, gateway comes up clean. Negative: `git merge origin/main` into the fork, which re-triggers the 5,000-commit conflict.)
- **0.2 Capability probe (`probe.py` → committed `capabilities.json`).** The one deliverable every model-name and runtime claim in this plan is validated against. At startup it asserts: the `claude` and `codex` versions; that `--max-budget-usd` (per-job spend cap) is supported; that git worktrees work; that JSON/schema output works; and that each candidate OpenRouter model name actually exists in that provider's live catalog (name resolves, key valid, 1-token ping) before it may enter the routing table. The phantom `GLM-5.2` (which appears in no provider config) is exactly the failure this catches: a missing or renamed model becomes a loud startup failure, never a silent 3am failover to nothing.
- **0.3 `compute.md` inventory with *measured* quota math.** Record Claude Max quota + reset window, Codex quota, OpenRouter balance, and each provider's per-minute token/request ceilings (at 3am the real limit is tokens-per-minute, not the monthly pool). *Produce measured, not guessed, numbers:* run one real task through the full gauntlet, meter its actual token use (a complex task runs 50K–200K tokens), and from the per-minute ceiling *derive how many tasks can run per night*. This number is what the P4 gate's "≥5 tasks" is checked against, and it sizes the overflow ladder. Without it, the P4 gate is untestable and the machine may hit the rate limit at task 2 or 3.
- **0.4 Sleep/wake + session discipline + credential pre-warm.** Keep the Mac awake during runs with an assertion that is *independent of the gateway* — a dedicated `caffeinate` anchor under its own launchd service — so a gateway crash does not drop the sleep-block and kill the batch. Validate the launchd session context (keychain reach; steps needing a visible browser require a login session → surface "needs user present," never fail silently). *Credential pre-warm:* a token-health job fires around 10pm so a 3am login refresh (which may require multi-factor auth that nobody can answer at 3am) never blocks the batch. Schedule off-peak so night batches don't compete with your daytime interactive use for quota.
- **0.5 Jobs-first health signal.** One command/file that answers: which providers are reachable, what's the compute state, are any workers running, when was the last scheduler tick? Designed jobs-first from day one (v1 deferred this; here it's central). It distinguishes a hermes bug from a network outage.
- **0.6 Kill zombie platforms.** Disable Discord (no adapter created); pair-or-disable WhatsApp cleanly — no 300-second retry loop. Cheap hygiene, do it now. (The full WhatsApp verify gate is in Phase R, described next.)

**Binding exit gate:** 24h uptime on the clean base with the three governance plugins re-applied; `probe.py` + `capabilities.json` committed and truthful (every routing-table model name confirmed against a live catalog); `compute.md` carries a *measured* tokens/task figure and a derived tasks/night ceiling from a real proxy run; a killed gateway restarts once cleanly at the `hermes-factory` path *and* the caffeinate anchor survives that crash; the awake policy holds the Mac awake through a simulated 20-minute job; zero zombie errors in `errors.log` over 30 minutes.
**Effort:** 4–5 days.

### PHASE R — Fix What's Broken Today

**Goal:** Close the *known reliability holes* before anything runs unattended overnight. You do not run an autonomous factory on a host that still silently skips a config it couldn't load, refreshes login tokens from three different places, or keeps a broken connector retrying forever. These are cheap, they harden the very host and broker the factory sits on, and they belong on the critical path — nothing merges overnight on an un-hardened machine.
**Builds on:** the clean base (P0.1); the governance plugins re-applied (P0.1); the pm_os token tooling (`ensure-tokens.js`, `check-token-health.js`); the existing health signal (P0.5).
**Deliverables:**
- **R.1 Fail-closed config loading.** Today, if a mandatory config or plugin fails to load, hermes carries on as if it were fine — a fail-*open* behavior with no consumer for the "this didn't load" signal. Change it so a mandatory-load failure makes the system *refuse to start* (or refuse to enter the affected lane) rather than run half-configured. A guard you thought was active but silently wasn't is worse than no guard.
  - **Deliverable:** startup aborts with a clear error when a config/plugin marked mandatory fails to load.
  - **Gate:** deliberately break one mandatory plugin's load; confirm the system refuses to start with a named error, and does *not* come up in a degraded-but-silent state.
- **R.2 WhatsApp: paired-and-verified before it's live.** P0.6 stops the retry loop; this closes the conditional properly. The WhatsApp connector only counts as "on" once it is *both* paired *and* has passed a real send-and-receive round trip. Until then it stays cleanly off, not retrying.
  - **Deliverable:** a pairing + verify step that flips WhatsApp to active only on a successful round-trip test.
  - **Gate:** an unpaired WhatsApp stays off with no retry noise; a paired one passes a real send/receive before being marked live.
- **R.3 One owner for token refresh.** Right now more than one code path can try to refresh login tokens, which races and corrupts them. Make exactly one component (`ensure-tokens.js`) responsible for refreshing tokens; no other component refreshes directly — they ask the owner or read the already-refreshed token.
  - **Deliverable:** a single-refresher rule, enforced; all other call sites read, never refresh.
  - **Gate:** a fresh-context review finds no direct-refresh call outside the single owner; a simulated expired token is refreshed exactly once, by the owner.
- **R.4 Surface SSO/Okta session expiry to the owner.** When the corporate single-sign-on (Okta) browser session expires, the affected lane must surface a visible "re-auth needed" to you instead of failing silently or looping. Multi-factor auth needs a human, so the honest move is to *ask*, clearly.
  - **Deliverable:** SSO-expiry detection that raises a "needs re-auth" notification on the affected lane.
  - **Gate:** simulate an expired Okta session; confirm a clear "re-auth needed" reaches the owner and the lane pauses (see R.5) rather than retrying blindly.
  > **Term:** *SSO / Okta* — corporate single sign-on. One login (guarded by multi-factor auth) grants access to many work tools; its browser session expires periodically and can only be renewed by a present human.
- **R.5 Paused/degraded states for broken-auth connectors.** A connector whose auth is broken must enter an explicit *paused/degraded* state — visible in the health signal, not retrying — until re-auth happens. No connector should hammer a dead endpoint.
  - **Deliverable:** a `paused`/`degraded` lane state, shown in the P0.5 health output, entered automatically on auth failure and cleared on re-auth.
  - **Gate:** break one connector's auth; confirm it shows `degraded` in the health signal and stops retrying until re-authed.
- **R.6 Setup checklist with behavioral gates.** A `SETUP-CHECKLIST.md` where each item is a *behavioral* check ("a test message actually arrived"), not a box someone ticks by hand. The rule: any unchecked box means "not done." This is the antidote to "76 of 76 unit tests pass, therefore it works" — passing tests are not the same as a working, wired-up system.
  - **Deliverable:** a checklist whose every item is verified by observed behavior, with placeholder/stub detection.
  - **Gate:** the checklist flags a deliberately-stubbed-but-untested lane as *not done* even though its code "exists."
- **R.7 Per-scheduled-skill preflight block.** Every skill that runs on a schedule must begin with a preflight block that checks its own prerequisites before doing anything: the right entry point exists, required flags are supported, needed files are present, the token is valid, and the write-gate (broker) is reachable. A scheduled job that can't run should say so up front, not fail halfway.
  - **Deliverable:** a mandatory preflight-block anatomy, present in every scheduled skill.
  - **Gate:** a scheduled skill with a missing prerequisite fails its preflight cleanly and reports why, instead of partially executing.

**Binding exit gate:** a mandatory-load failure makes hermes refuse to start (R.1); WhatsApp is either off-and-quiet or paired-and-verified (R.2); a fresh-context review finds exactly one token-refresh owner (R.3); a simulated SSO expiry surfaces "re-auth needed" and the lane enters `degraded` rather than looping (R.4/R.5); the setup checklist catches a stubbed lane (R.6); a scheduled skill missing a prerequisite fails its preflight with a clear reason (R.7).
**Effort:** 3–5 days.

### PHASE 1a — Broker + approval lanes + worker launcher

**Goal:** The two keystones the *first PR merge* actually needs — one enforcement boundary the assistant cannot improvise around, and the worker-launch interface that keeps a second machine retrofittable. Trust graduation is deliberately *not* here — it has nothing to graduate on until P2 produces real outcomes, so it moves to P1b. This split lands your first overnight-built PR about two weeks sooner.
**Builds on:** the policy engine + its audit/policy/workflow tables; the email-approval state machine (the template for the human-gate pattern); the old helper's orchestrator-side plumbing + `kanban_tools.py` ownership rows (the worker *process launcher* itself is new).
**Deliverables:**
- **1a.1 Send/action broker (separate process).** A tiny separate program owns all consequential-action egress — *including every `git push`*; the assistant sends a narrow request; the broker holds the credentials and enforces an allow-list, the safe-lane check, and the approval requirement. **Stated honestly:** on a single Mac user account, being a separate process is not by itself a credential wall — the wall is that credentials are *removed* from everywhere the assistant can read (`.env`, shell env, token files) and placed only in the broker's environment/keychain, *and* write-capable tools are starved of credentials in the assistant's context. IPC (how the assistant talks to the broker) is a unix domain socket with JSON-RPC — see BD1 in §8. **Fails closed:** no caller falls back to a direct path; held actions queue durably and survive a restart; broker health feeds the P0 health signal.
  > **Term:** *IPC (inter-process communication)* — how two separate programs talk to each other. Here: a unix domain socket (a local-only channel, no network exposure) carrying JSON-RPC messages.
- **1a.2 Generic held-action approval surface.** Replace the broken `/approve-email` machinery. Approvals reference **stable action IDs pointing at durable broker-side records** — the Telegram message carries a summary, buttons, and a local artifact path; the authoritative payload lives in the broker's store, keyed by ID. No more "look up the action by hashing a truncated copy of the message body," which is the exact bug class that broke `/approve-email` four ways. It's generic — a "held action {type, summary, payload}" — so message-sends, pm_os writes, and merge approvals all ride the same path. The test includes a long-payload approval (the truncation bug's trigger).
- **1a.3 Safe-lane policy (the 5-condition auto-send test).** The broker auto-sends a consequential action *only if all five* hold, otherwise it holds for approval: (1) the signal is understood, (2) the source of truth is known, (3) it's operational not strategic, (4) authority is clear, (5) the mistake is recoverable. This is the broker's core policy and belongs here, not deferred to the comms phase. Start conservative (safe-lane near-empty) and widen it as trust builds. (Positive: "reply 'got it' to a scheduling confirmation from a known colleague" auto-sends. Negative: "email the board a status update" always holds.)
- **1a.4 Worker launcher (new code).** The `Worker` contract: `launch / check / kill / collect-result`. The `local-subprocess` version launches a real OS process (its own process group, its own worktree, output-modification-time liveness check, SIGTERM-to-the-process-group kill) — new code, not a wrapper over the in-process helper. A `remote-worker` stub compiles against the same interface, proving no local-only assumption leaks into the supervisor. Design-now, implement-one.
- **1a.5 Credentials out of assistant reach.** Send/API keys, stored Microsoft tokens, and OpenRouter keys move to the broker environment / macOS keychain. A fresh-context reviewer attempts a direct send-CLI call and a token-file read; both must fail to produce a send.

**Binding exit gate (fresh-context verified):** from Telegram, approve a held action (including one long payload) and confirm it executes; the safe-lane correctly auto-sends one recoverable/operational action and holds one strategic one; attempt a direct assistant bypass (send-CLI + token-file read + a raw `git push`) and confirm all three are impossible; the `remote-worker` stub compiles and is dispatched-to by the same supervisor code path as a real local subprocess; a local subprocess is demonstrably killed via SIGTERM-to-process-group.
**Effort:** 7–9 days (broker + approval + safe-lane + subprocess launcher; trust ledger deferred to P1b).

### PHASE 2 — One worker, end to end (first magic)

**Goal:** One task → worktree → build with the full test-first gauntlet → PR → approval-gated merge. The whole factory in miniature, proving the spine before fleets. **This is time-to-first-magic:** you wake to a real, overnight-built PR you approve from your phone — target about day 26–30.
**Builds on:** git worktrees; `codex review`; `claude -p`/`codex exec`; the QA gauntlet skills (`e2e-test-writer`, `garry-review`, `qa`); the P1a broker + held-action approval; `checkpoint_manager.py` for within-worker rollback. (No trust ledger yet — every merge is plain held-for-approval; graduation lands in P1b.)
**Deliverables:**
- **2.1 Job store + ledger + intake seam (new schema).** Per job: id, repo, base branch, spec, worker (claude|codex|openrouter), model, budget in USD, timeout in minutes, state, worktree path, branch, process-group id, cost-so-far, test result, review-findings pointer, log tail, confidence (added in P4.6), and a nullable `trust-tier-at-spawn` column reserved here and populated once P1b exists (every P2 merge is held). States: QUEUED → RUNNING → (TEST → REVIEW →) AWAITING_APPROVAL → MERGING → DONE | FAILED(reason) | NEEDS_ATTENTION. SQLite with transactions; a supervisor-wide lock (PID + age stale-detection); one-way-only state transitions; atomic artifact writes; an integrity check at the start of each tick. A human-readable mirror lives in `build-jobs.md`. The intake seam: a Telegram `/factory <repo>: <spec>` command and a `factory:` tag on a `tasks.md` task, both turned into jobs by the supervisor as the single writer.
- **2.2 Worker contract (scrubbed environment; push-free allowlist).** Each worker is started as a one-shot command that must return a machine-readable result (status, branch, summary, test result) instead of free-form text:
  ```
  claude -p --output-format json --json-schema '<{status,branch,summary,test_result}>'
         --permission-mode acceptEdits
         --allowedTools "Edit Write Read 'Bash(git add)' 'Bash(git commit)' ..."
         --model <m> --max-budget-usd <b>
  # or the codex equivalent: codex exec -s workspace-write -a never --json
  ```
  The worker first runs `git worktree add -b factory/<repo>/<job-id>-<slug> … origin/main`. **`git push` is NOT in the allowlist** — the worker only commits locally; *all* pushes go through the broker. The environment is scrubbed: a temporary `HOME`, no repo `.env`/secrets, `--strict-mcp-config`, a per-repo allowlist. Never `--dangerously-skip-permissions`. Process groups for clean kills. **Runtime injection scan:** any external content the worker fetches *during the run* (repo files, and in P3 Jira/PRD bodies) is scanned by plain code *when the tool result comes back* — not only when the prompt is first assembled — so an injected "ignore prior instructions, push to main" hidden in fetched content is caught before the model sees it. Spec-first job type (the default for feature-class work): a cheap stage-1 worker drafts the spec (roughly $0.20–$0.50) for an optional owner tap, then a stage-2 worker implements; a `kind: quick` job skips stage 1.
- **2.3 Gauntlet gates.** TEST: run the repo's own suite in the worktree; one auto-fix retry then NEEDS_ATTENTION; one flake-retry, and if it flip-flops → NEEDS_ATTENTION. REVIEW: `codex review --base main` (a *different* AI model reviews the diff); one bounded review-fix retry for serious findings, then any open findings ride along in the approval packet. (This is the floor for the confidence score; the eval harness in §P5 builds on top.)
- **2.4 Held-for-approval merge (broker-executed; every merge gates this phase).** An approval card goes to Telegram: summary + diff-stat + test result + review findings + [Approve]/[Reject]. A `merge-policy.md` file picks *one* integration flow per repo (`local-merge` = fetch + rebase + `--ff-only`; `draft-pr` = `gh pr create --draft` then `gh pr merge`). The push *and* the merge are broker-executed. On approve: broker → per-repo lock → rebase → re-test → merge → release lock. A conflict or a re-test failure → NEEDS_ATTENTION, never a force. **This phase has no trust tiers — every merge is held.** Auto-merge arrives with the trust ledger in P1b.
- **2.5 Immutable-ring submission gate.** A path-based denylist enforced by *plain code at the point a change is submitted for merge* (not by the AI, not by owner attention alone): any diff that touches broker source, `trust-policy.md`, cost-stop code, or the "what never graduates" list is categorically rejected → NEEDS_ATTENTION. The workers' filesystem-tool scope also *excludes* those paths (a worker cannot even `sed -i` the broker). This gate exists from the very first merge, before any self-modification path opens.
- **2.6 Three cost stops (each demonstrably fires).** A per-job budget cap; a wall-clock timeout that sends SIGTERM to the process group; a daily reserved-budget ceiling (`reserved + spent ≤ ceiling`; a breach kills active workers). Budgeted workers are Claude-only this phase (Codex/OpenRouter accounting lands in P4 routing).

**Binding exit gate:** the factory delivers and merges *one real feature with a single approval*, end to end (the first-magic moment); a fresh-context negative test confirms no ungated merge to a protected branch *and* that a diff touching an immutable-ring path is rejected at the submission gate; the budget, timeout, and idle-kill each fire in a controlled test (against process groups); a `kind: quick` job skips the spec stage while a feature-class job produces a schema'd stage-1 spec before implementing.
**Effort:** 10–14 days.

### PHASE 1b — Trust ledger + graduation (learns from P2's real outcomes)

**Goal (the deferred keystone):** now that P2 produces real merged/rejected/reverted outcomes, build the policy that earns autonomy from them. Trust graduation *cannot* be built before P2 — it has no data to graduate on.
**Builds on:** P2's job-ledger outcome columns; the policy engine's workflow table; the P1a broker approval surface (graduation proposals *and* auto-merge both ride it); the P5 scorecard's confidence input once it exists (until then, P2 outcomes only).
**Deliverables:**
- **1b.1 Trust ledger.** `trust-ledger.md` + SQLite: per-(repo × task-type) outcomes — merged-clean / merged-with-a-fix / rejected / reverted-later — populated from P2's completed jobs. Extends the policy engine's workflow table.
- **1b.2 Graduation rules + profiles (thresholds pinned, owner-tunable).** `trust-policy.md`: track record → autonomy tier. **Default thresholds (tunable via config):** tier-1 (auto-merge, personal non-production only) requires **≥10 consecutive merged-clean outcomes, 0 reverts, over a window of ≥30 days, per repo × task-type**; **auto-revoke to tier-0 on the first human-rejected outcome or the first post-merge regression.** ("Merged-clean" means merged with no human fix and no later revert.) The per-job confidence floor from §P4.6 becomes an additional AND-condition once P4 lands; until then the count/window/revert rules suffice, and since the window is 30 days, real graduation can't happen before P4 exists anyway. Graduation is a *proposal* you approve; demotion is automatic. Owner-selectable profiles run from ask-for-everything to auto-merge-on-personal-non-prod.
- **1b.3 Retrofit P2.4's merge gate.** The held-for-approval card now consults the tier: tier-0 → held (as in P2); tier ≥1 on personal non-prod → auto-merge + notify. One code path, parameterized by tier.

**Binding exit gate (fresh-context verified):** a repo × task-type demonstrably *graduates* held→auto once the *configured* threshold is met (seed the ledger with ≥10 synthetic clean outcomes) *and* demonstrably *demotes* on an injected bad outcome; the "what never graduates" list (§5) is unbypassable; work/Diligent repos stay tier-0 regardless of record.
**Effort:** 4–6 days.

### PHASE 3 — Task-splitter + fleet scheduler + intake pipelines

**Goal:** Turn single-task into queue-eating. A spec/Jira-epic becomes a task graph; the supervisor schedules a fleet; all four intake sources are wired (Jira first — the plumbing exists).
**Builds on:** the `prd-review`/`eng-stories`/`prd-writer` skills; `codebase-mapping` + code-review-graph for repo onboarding; the `jira-update` skill's `curl` + API-token auth pattern (the Jira *poller* is new code — no reader exists today); the gateway-embedded kanban dispatcher for fan-out.
**Deliverables:**
- **3.1 Task-splitter + spec-review gate.** Spec/PRD/Jira-epic → attack it for gaps → grade its ambiguity → emit an ordered, acceptance-test-first task graph. High ambiguity → park it and file `questions.md` with proposed answers. **The ambiguity grader is itself an AI and can hallucinate acceptance criteria:** so a normalized spec is held for one owner tap (or auto-cleared only for tier ≥1 repos) before any worker spawns — the spec is a reviewable held-action, not a silent input. A cheap "is this buildable / what's ambiguous" pre-pass protects the whole batch. **Grading rubric:** score each spec on undefined-acceptance-criteria count, vague ("or equivalent") dependencies, and missing field names; above the configured park-threshold → park to `questions.md`, below → proceed.
- **3.2 Fleet scheduler over the task graph.** The supervisor reasons about the fleet: fan out independent tasks, serialize dependent ones (following the `depends_on` edges), throttle when provider quota is low, and cap concurrency *at the measured tasks/night ceiling from P0.3* (start at 1–2, stagger spawns 30–60 seconds apart to avoid a rate-limit burst). All factory SQLite databases use write-ahead-logging + a busy-timeout so parallel workers don't collide. Still plain code, still durable.
- **3.3 Jira-queue intake (source #2 — new poller).** Build a poller (reusing the `jira-update` auth pattern) for assigned tickets → normalize to job specs → enqueue. Relocate the Atlassian token to a launchd-reachable store; surface a 401 as "re-auth needed" (per Phase R.4). Jira write-back: move the ticket to In Progress on start, drop the PR link + summary on completion — broker-gated. **The per-repo `allow_openrouter` gate applies:** work/Diligent-linked tickets never route to third-party-hosted models.
- **3.4 Repo-onboarding pipeline (source #3).** Point it at a `~/Code` repo → `codebase-mapping` + code-review-graph build a map, detect the test/lint/build commands + conventions → write a `factory-repo-profile.md` + seed a `merge-policy.md` row. Slow the first time, fast every time after.
- **3.5 Spec-doc + greenfield intake (sources #1, #4).** A dropped spec doc → the task-splitter. A greenfield idea → a scaffold-plan job type (project skeleton + first task graph). A voice note → transcribe → task-splitter, riding the gateway's audio path.

**Binding exit gate:** a real Jira epic (or spec doc) splits into an ordered task graph with acceptance tests per node; the ambiguity grader parks ≥1 genuinely-ambiguous spec into `questions.md` instead of guessing; the scheduler fans out ≥2 independent tasks concurrently and serializes ≥1 dependent; a fresh `~/Code` repo onboards to a committed `factory-repo-profile.md` + `merge-policy.md` row in one pass.
**Effort:** 12–16 days.

### PHASE 4 — Overnight autonomy

**Goal:** The thing you asked for: throw a queue at it in the evening, wake to merged/held/needs-attention with cost and confidence. Checkpoint/resume, 429 failover, multi-provider routing, and the morning surfaces all go live.
**Builds on:** the P2 job store + P3 scheduler; `checkpoint_manager.py` within-worker rollback + the new phase-checkpoint layer; `account_usage.py` + `compute.md`; the pm_os `run-morning.js` briefing generator (reused for the factory digest); the P1a broker for approval cards; the P1b trust ledger for auto-merge tiers.
**Deliverables:**
- **4.1 Checkpoint/resume/re-plan.** Fresh agent per phase; resumable phase state on disk (new layer). A worker that dies at 3am is re-launched from the last cleared phase (via the handoff brief) or re-scoped — not parked-until-morning. Self-healing retry with forensic capture: before killing a job, snapshot the transcript tail + failing test + `git diff` + model/prompt; try one bounded self-heal from that bundle; otherwise park it as NEEDS_HUMAN with the bundle attached.
- **4.2 429 failover ladder + multi-provider routing.** The supervisor catches a 429 explicitly (the P0 compute ceilings inform when to throttle) and re-launches *from the last phase boundary with the compact handoff brief* (not a raw transcript) down the *probe-confirmed* routing ladder (Claude Max → Codex → the resolved overflow models, e.g. `glm-5` → Kimi → DeepSeek — never a hard-coded `GLM-5.2`). Enable Codex + OpenRouter workers with their cost accounting (parse token usage from the `--json` output). **The per-repo `allow_openrouter` gate is enforced in the router:** a repo flagged false never dispatches to a third-party-hosted tier — it waits for subscription capacity instead. The ledger records which model finished each job.
- **4.3 Overnight queue + batch mode.** An evening-queued task graph runs overnight under the sleep policy; the morning approval packet is one card per completed job plus a digest *ranked by the computed confidence score* (batch-approve the safe ones, scrutinize the risky ones). Weekly throughput is recorded in the ledger.
- **4.4 Morning surfaces (the daily-touched product).** A standup briefing in yk-voice: shipped / blocked (+ the blocking question) / cost (+ a cost-per-merged-PR trend arrow) / queued-for-tonight. One-tap PR cards with everything you need to decide: summary, the reasoning + alternatives, whether it's reversible, the diff-stat, the confidence score, and a risk score from code-review-graph's impact analysis. The question queue is surfaced tap-to-answer. Reuses the pm_os `run-morning.js` generator.
- **4.5 Cost-per-merged-PR trend board.** The north-star number, trending, in the briefing and the weekly retro.
- **4.6 Per-job confidence score.** A single number from 0 to 1 computed *at the moment a job reaches AWAITING_APPROVAL* and saved as a `confidence` column. **Inputs:** test pass rate (+ coverage delta), the review verdict (finding severity/count), diff size & blast-radius (from the living map), the spec-ambiguity score (from P3.1's rubric), the model tier used, and historical success for this repo × task-type (from the P5 scorecard; before the scorecard exists this term defaults to neutral). **First formula:** a weighted product of normalized terms, clamped to [0,1] — `conf = w_test·test + w_review·(1−severity) + w_blast·(1−blast) + w_amb·(1−amb) + w_hist·hist` (weights owner-tunable, summing to 1). The morning digest ranks by it; the trust ledger consumes it as the graduation confidence floor; the P4 gate references it.
- **4.7 Voice-note → spec'd task (delight pull-forward).** Its only hard dependency — the task-splitter — ships in P3, so pull one wow moment forward to land with the first overnight batch: mumble an idea to Telegram → transcribe (gateway audio path) → task-splitter → a spec'd task card (or a question if too vague). The full comms voice surface still consolidates in P6; this is a deliberate early taste.

**Binding exit gate (the flagship, 3 consecutive nights; bound to a committed seed queue):** ≥5 tasks drawn from a **`p4-gate-queue.md` committed to the repo *before* the gate run** (no cherry-picking), containing **≥2 feature-class + ≥1 refactor-class jobs across ≥2 repos, none authored to be trivially green**, go intake → PR-ready with **zero human intervention before 7am**; total cost under the nightly ceiling (the number from OQ2); at least one job demonstrably fails over to a lower model tier (via the handoff brief) and finishes; at least one crashed worker resumes from a phase checkpoint (not parked); the morning packet is ranked by the confidence score and is batch-approvable; a fresh-context reviewer confirms the cost accounting matches actual provider spend within tolerance.
**Effort:** 14–18 days.

### PHASE 5 — Learning loops + self-supervision

**Goal:** Every run makes the next one better and cheaper; the fleet watches itself and intervenes mid-run.
**Builds on:** the job ledger with outcome tracking; the full-text session search for the pattern bank; hermes's native skill self-creation + `~/.claude/scripts/synthesize-lessons.py` for the skill-improvement pass; the trust ledger; the confidence score; code-review-graph for risk/drift signals.
**Deliverables:**
- **5.1 Per-model, per-task-type scorecard.** Log per job: model, task type, outcome (merged-clean / needed-fixes / rejected / reverted), review-findings count, cost, wall-time. This becomes the data-driven routing table — `model-routing.md` stops being hand-written. A model leaderboard is a view over it.
- **5.2 Weekly skill-improvement pass.** A weekly pass reads recent job histories and updates/adds skill files (this is a technique from recent AI-agent research where prompt templates are automatically improved from run histories; published benchmarks show roughly a 10% improvement in task success rate). A pattern bank: merged solutions distilled into searchable entries, queried before a new worker starts. Cross-repo knowledge transfer via code-review-graph's cross-repo search.
- **5.3 Nightly retro that proposes skill diffs.** A retro worker reads the failure bundles + review findings and proposes concrete edits to skills/prompts/policies — arriving as a *diff you approve*, never a silent self-edit (the exact trap that started this project). **Every retro diff passes through the same immutable-ring submission gate (§P2.5):** any diff touching broker source, `trust-policy.md`, or a cost stop is categorically rejected by plain code before it ever reaches your attention — a degenerate retro cannot even *propose* weakening a trust rule. Trust auto-graduation proposals ride this path.
- **5.4 Self-supervision watchdogs.** Plain-code drift detection: tokens-burned-without-progress, scope creep beyond the declared file footprint, stuck loops, edits to guard/config files → pause + a `DRIFT` alert. A scope guard flags writes outside a task's declared footprint. A regression sentinel: after an auto-merge, re-run the suite on main; on a break, open a revert PR + a fix task. **Cadence + graph pause:** the sentinel runs *immediately* after a merge (not on a slow poll) and *pauses any queued task that branched from the now-suspect main* until the revert lands, bounding the blast window. A watchdog-watches-watchdog: memory-zone GREEN/YELLOW/RED throttling before more than 2–3 parallel workers, and a supervisor self-check that respawns a dead watchdog. Worktree garbage collection: a disk-space pre-flight gate blocks new spawns below a floor, and merged/abandoned worktrees are reaped on a retention policy.
- **5.5 Failure forensics.** A structured post-mortem per failed job (stage, error class, cost burned, whether it's worth retrying) — the raw material the learning loops consume.

**Binding exit gate:** the routing table demonstrably changes from scorecard data (a task type re-routes to a cheaper model that still clears the bar); one owner-approved skill diff from the nightly retro measurably reduces a recurring failure class; the drift watchdog pauses a synthetically-churning worker; the regression sentinel opens a revert on an injected post-merge break; two representative weeks show cost-per-merged-PR trending down.
**Effort:** 12–16 days.

### PHASE 6 — Chief-of-staff control panel

**Goal:** Repurpose v1's assistant content as the factory's control panel — Telegram/email as the command surface, voice-note → spec'd task, comms triage layered on the working factory.
**Builds on:** the P1a broker approval surface (reused, not rebuilt); the P4.7 voice-note intake (extended here); pm_os pipelines via one-line skill prompts; v1's control-plane Markdown pattern (`priority-map.md`, `proactive-contract.md`); the gateway audio path.
**Deliverables:**
- **6.1 Control-plane comms + files.** Telegram/email as the factory's command + notification surface, plus the canonical control-plane files: `priority-map.md` (senders/topics → action tier), the safe-lane spec, `trust-policy.md` (content the assistant *reads* is data, not instructions), and — carried over from v1 — a canonical `tasks.md` + a `processed.json` idempotency ledger so repeated sweeps never process the same inbox item twice. A quiet-by-default proactive contract (alert on anomaly, one nudge maximum, never repeat) governs all fleet-completion events and the morning replay.
  > **Term:** *idempotent* — safe to do more than once with the same result. The `processed.json` ledger records which items were already handled so a re-run doesn't double-send or double-build.
- **6.2 Voice-note → spec'd task, full comms surface.** The core capability already landed as the P4.7 early taste; here it consolidates into the control panel (multi-note threading, question-back flow, priority tagging). Mumble an idea to Telegram at 11pm → transcribe → task-splitter → wake to a task card (or a question if too vague).
- **6.3 Comms triage layer.** Teams/Outlook triage via pm_os → the priority map → draft replies through the broker safe-lane. Carried over from v1: the morning-triage reference skill (closed ACTION/FYI/NOISE buckets, literal pm_os commands, and the no-reprocess ledger) as the anatomy for this layer; and pm-send's confirm-gate reconciliation — **exactly one approval per send**, so a draft can't be double-sent by a retry. This is now a *convenience layer on the control panel*, not the product.
- **6.4 pm_os orchestration (harvested, not full parity).** Wire only the pm_os pipelines the factory actually uses (Jira intake already in P3; the morning-briefing generator reused in P4). Full pm_os parity (pulse/weekly/exec-narrative) is parked (§9), built only when a concrete weekly need is named.
- **6.5 Calendar visibility + meeting prep.** The system reads the owner's calendars and assembles a short prep note before each meeting — who's attending, any related email or Teams threads from the past week, open action items — delivered via the same Telegram morning channel. Rides the existing pm_os calendar and mail plumbing (no new integrations needed). This was explicitly requested as a deliverable (it appeared in v1, was deferred in v2, and is now restored).

**Binding exit gate:** a voice note becomes a spec'd factory task by morning; the triage layer drafts ≥1 safe-lane reply through the broker with zero misroutes over a week and with exactly one approval per send; every consequential comms action rides the same broker approval surface as merges (one UX, fresh-context-verified no-bypass); a repeated inbox sweep processes no item twice (idempotency ledger holds); at least one real meeting gets a prep note assembled with zero manual steps (calendar read → attendees + related threads + open items → delivered to Telegram before the meeting).
**Effort:** 8–12 days.

### PHASE 7 — Compounding assets

**Goal:** The flywheel's outer ring — cross-repo transfer, demo reels, weekly retro, greenfield provisioning; and the dual-upstream discipline that keeps it all cheap to maintain.
**Builds on:** the pattern bank + cross-repo graph search; the `browse` skill (for demo reels); the scorecard + trust ledger (for the retro); the clean-upstream discipline.
**Deliverables:**
- **7.1 Dual upstream tracking.** A monthly hermes upstream pull — cheap because our local delta is out-of-core Markdown + broker + supervisor. pm_os is coupled only via pinned CLI contracts written into the skill files; a monthly contract check localizes any breakage.
- **7.2 Demo-reel-of-the-night.** For a feature with a runnable surface, a post-build worker drives the new flow via the `browse` skill and captures a 20-second recording (or an annotated before/after for backend work); it's embedded in the PR card.
- **7.3 Weekly factory retro.** First-person and warm: "merged 14 PRs across 3 repos for $6.20; `glm-5` is your best-value refactorer; cost-per-merged-PR dropped from $0.71 to $0.44." Personality + real metrics + visible self-improvement.
- **7.4 Greenfield provisioning + cross-repo transfer.** The greenfield scaffold job type matured; a pattern solved in repo A offered to repo B via cross-repo graph search. Plus "explain this merge like I've been away a week."
- **7.5 Living codebase map.** code-review-graph rebuilt incrementally after each merge, so the blast-radius/risk on the next task is always current — feeding risk scores, scope guards, and confidence.
- **7.6 End-to-end capstone (the composition exit gate).** Prove the whole thing composes: a Telegram *thread* → a spec → a *Jira ticket* → a factory build → a merged change, with the Jira ticket updated with the PR link and summary along the way. This is the "everything works together" milestone, not just individual pieces.

**Binding exit gate:** one upstream pull completed at under a day of cost; one demo reel embedded in a real PR card; the weekly retro ships with real trending metrics; a cross-repo pattern transfer lands in a second repo; the living map refreshes automatically after a merge; **and the capstone runs end to end — a thread becomes a spec, a Jira ticket, a factory build, and a merged change with the ticket written back (7.6).**
**Effort:** ongoing; about 1 day/month + a 2–3 day capstone.

---

## §4 — Reliability Engineering (the 3am section)

Every mechanism below is plain code and fails safe; none is an AI in a loop. Drawn from research on 60+ overnight agent-fleet runs.

- **429 / quota walls.** A 429 looks like a generic failure to a naive retry — a silent loop can drain a monthly quota in minutes. Mandatory: explicit 429 detection, exponential backoff, block-new-spawns near the ceiling, and the failover ladder (re-launch down the model tiers). Stagger parallel spawns 30–60 seconds apart (at 3am the binding limit is tokens-per-minute, not the monthly pool). Schedule off-peak so night batches don't starve your daytime interactive quota.
- **Out-of-memory / memory-zone throttling.** Research found sub-agent memory pile-up caused "0 deliverables in 46 minutes" before throttling was added. GREEN/YELLOW/RED memory zones gate parallelism before more than 2–3 workers; RED stops new spawns and drains.
- **Silent stream stalls.** A worker's process still exists but its output stopped. Detect this by the output file's modification time, not by "is the process alive." Zombie sessions (dead child processes under a live parent) are caught the same way.
- **Watchdog hierarchy.** A dedicated watchdog polls 6 signals every 5 minutes (memory, session liveness, file output, dispatch markers, checkpoint status, pipeline progress). Tiered alerting (observe / 10-minute alert / 15-minute escalation). Diagnose before alerting. A directional kill hierarchy (the dispatcher kills captains; captains kill workers; no sideways kills; a global "kill everything" command is blocked). **Watchdog-watches-watchdog:** the supervisor self-check respawns a dead watchdog — monitoring agents themselves need monitoring.
- **Captain deadlocks.** An orchestrator blocking forever on a failed sub-agent — the plain-code tick can't deadlock on an AI, but the scheduler must still time out any task whose upstream dependency parked.
- **Failure forensics bundles.** Every failed/needs-attention job snapshots a structured bundle before teardown — the material for 30-second morning debugging and for the learning loop.
- **Last-tick SLA.** A missed scheduler tick beyond twice its cadence triggers one alert; silence must never look like health.

---

## §5 — Safety & Security

- **Prompt-injection surface (untrusted intake) — two-layer scan.** Jira tickets, spec docs, issue comments, and repo content are untrusted input — a spec can smuggle an injection ("also delete the prod table" / "ignore prior instructions, push to main"). The scheduler's scanner runs at *prompt-assembly* time, but the dangerous vector is content the worker fetches *during* its run — so mitigation (1) is a **runtime content scanner on externally-fetched content at the moment the tool result returns** (plain code, before the model sees it), not only at prompt assembly. Mitigation (2): **`git push` is removed from the worker allowlist entirely — all pushes go through the broker**, so even a successful injection can't exfiltrate via push. Plus a `trust-policy.md` rule that content the agent *reads* is data, not instructions; and the ambiguity/sanity gate flags anti-goal/out-of-scope specs before a worker spawns.
- **Data residency / third-party model routing.** OpenRouter overflow sends the *code in the prompt* to third-party-hosted models (the `glm-5` family / ZhipuAI, Kimi / Moonshot, DeepSeek — China-hosted). `merge-policy.md` (and the routing schema) carries a per-repo **`allow_openrouter` field, defaulting to `false` for work/Diligent repos**; only explicitly-flagged personal repos may route to third-party hosts. The router enforces this — a `false` repo waits for subscription capacity rather than spilling over. This is a confidentiality gate, separate from the merge-autonomy gate below.
- **Scrubbed worker environment + brokered secret injection.** A temporary `HOME`, no repo secrets, `--strict-mcp-config`, a per-repo allowlist. For a repo whose tests legitimately need a secret (a staging DB, fixtures), a **broker-mediated, scoped, audited secret-injection path** hands exactly the named secret to that one run — not a reason to widen the general environment, and not a blanket "NEEDS_ATTENTION" that would exclude most real repos. Use `--allowedTools` rather than a blanket bypass (people approve ~93% of interactive prompts reflexively — automated boundaries beat interactive ones).
- **OpenRouter key handling.** OpenRouter keys live in the broker environment/keychain, never in the worker's or the assistant's context; workers reach providers only through the router, which the broker gates.
- **Auto-merge blast radius + revocation.** Auto-merge only on graduated repo × task-type tiers; the regression sentinel re-tests main after a merge and opens a revert on breakage; a first regression demotes the tier automatically. The daily-ceiling kill-switch bounds worst-case spend.
- **Self-modification hazard (enforced, not just labeled).** Factory merges into hermes/pm_os themselves are restart-gated per `merge-policy.md` — never hot-swap a running gateway/supervisor. Two structural enforcements, both plain code, neither reliant on owner attention: (a) the **immutable-ring path denylist checked at the merge-submission gate (§P2.5)** categorically rejects any diff touching broker source, `trust-policy.md`, or cost-stop code; (b) the workers' filesystem-tool scope *excludes* those paths, so the unsandboxed-terminal `sed -i` hole cannot reach guard source in the first place. The broker boundary stops exfiltration; this gate stops the guard being edited.
- **What NEVER graduates.** Production deploys, deletions, and financial actions stay mandatory-human forever. Work repos (Diligent) stay ask-for-everything regardless of track record. Only personal non-production repos can ever reach auto-merge.

---

## §6 — Self-Improvement Contract

Exactly what the factory may change about itself, as three concentric rings.

| Ring | Scope | How it changes |
|---|---|---|
| **May self-modify (proposal → owner-approved diff)** | Skill files, prompts, the `model-routing.md` table, policy weights (`priority-map.md` / safe-lane), pattern-bank entries | The nightly retro / weekly pass emits a *diff*; you approve it via the broker approval surface; a skill lint + one behavioral test + your enablement are required (disabled until approved); a quarterly prune of stale skills. |
| **May auto-tune within bounds (data-driven, no diff)** | Scorecard-derived routing *within the ladder*, trust-tier graduation *proposals*, backpressure thresholds within a configured min/max | Bounded by owner-set config; graduation is always a proposal, never blind; demotion is automatic on a regression. **Every auto-tune writes a line to `tuning-log.md`** — timestamp, what changed, from→to, and the data that triggered it — surfaced in the weekly retro. This closes the "why did it route everything to DeepSeek at 3am?" observability gap. |
| **Immutable (never self-modifies)** | The broker enforcement boundary, the trust rules themselves, the three cost stops, the "no AI in the supervisor loop" principle, the "what never graduates" list, and restart-gating of self-merges | **Enforced by the immutable-ring submission gate (§P2.5) + the worker allowlist exclusion, not by a label** — a factory job touching these is *rejected at submission*, not merely flagged. Human-initiated and restart-gated only. |

The line is: the factory writes software, so forbidding it from proposing improvements to its own non-core operating skills would be self-defeating — but every proposal is a reviewable diff, and the enforcement/trust/cost core is off-limits to self-modification (the original failure, made structural).

---

## §7 — Milestones (binding gates)

Phase order: P0 → **Phase R** → **P1a** → **P2** → **P1b** → P3 → P4 → P5 → P6 → P7 (resequenced so the first owner-visible win lands around day 26–30, before the full trust machinery). Cumulative day estimates are directional (focused solo-dev days, not commitments).

| Milestone | Ships after | ~Cum. day | You can use it for | Binding gate |
|---|---|---|---|---|
| **M1 — Factory-ready host** | P0 | ~5 | A box that can run a fleet and honestly report its compute/fleet state | 24h uptime; `probe.py` + `capabilities.json` + measured `compute.md` truthful; a gateway-independent caffeinate holds through a job |
| **M1.5 — Broken holes closed** | Phase R | ~9 | A host that refuses to run half-configured and surfaces auth problems instead of looping | Fail-closed config load; WhatsApp paired-and-verified or off; single token-refresh owner; SSO expiry surfaced; degraded lanes; preflight + setup-checklist gates pass |
| **M2 — Enforcement + dispatch** | P1a | ~17 | Every consequential action gated; a second machine retrofittable | Bypass impossible incl. raw `git push` (fresh-context); safe-lane auto-sends one / holds one; remote-worker stub dispatched by the same path as a real subprocess; SIGTERM-to-process-group kill works |
| **M-Magic — First overnight PR** | P2 | **~27–31** | **You wake to a real, overnight-built PR and approve it from your phone** | One real feature intake→merge with a single approval; no ungated protected-branch merge; immutable-ring diff rejected; cost stops fire |
| **M3 — Earned autonomy** | P1b | ~33 | Autonomy earnable *and* revocable from real merge outcomes | Graduate held→auto after the *configured* threshold + demote on an injected bad outcome; Diligent repos stay tier-0 |
| **M4 — Queue eater** | P3 | ~48 | A spec/Jira epic → a scheduled fleet across a task graph | Epic splits; ambiguity parks to questions; spec held for review; fan-out + serialize; a repo onboards |
| **M5 — Overnight autonomy** | P4 | ~65 | Throw a queue at night, wake to merged/held with cost + confidence | 3 nights on the committed `p4-gate-queue.md` (≥2 feature + ≥1 refactor, ≥2 repos): ≥5 tasks intake→PR, zero human before 7am, under ceiling, ≥1 failover, ≥1 phase-resume, digest ranked by confidence |
| **M6 — Self-improving** | P5 | ~80 | Routing + trust + skills learn from outcomes | Routing changes from data; a retro diff cuts a failure class (immutable-ring-gated); watchdog + sentinel fire; cost-per-PR trending down |
| **M7 — Control panel** | P6 | ~91 | Comms as the control panel; full voice-note surface | Broker gates all comms; one UX, no bypass; idempotency ledger holds (voice-note→task already live since P4.7) |
| **M8 — Compounding** | P7 | ongoing | Cheap upkeep + demo reels + cross-repo transfer + retro + the end-to-end capstone | Upstream pull <1 day; demo reel in a PR card; cross-repo transfer; weekly retro trends; the thread→spec→Jira→merge capstone runs |

---

## §8 — Bootstrap Actions (day one) + Open Questions

**First moves (do in order, stop after Action 4, report):**
1. **Confirm ground truth.** `launchctl list | grep hermes`; tail `~/.hermes/logs/errors.log`; note which platforms are connected versus zombie-retrying; capture any new crash signature verbatim.
2. **Measure the fork delta before cloning.** `git fetch origin; git diff --stat origin/main...HEAD`; list the non-email/guard commits; confirm which local changes are portable → this feeds P0.1.
3. **Probe the compute substrate + measure tokens/task.** Verify `claude`/`codex` versions + `--max-budget-usd` support; resolve each candidate OpenRouter model name against the live catalog (`glm-5` etc. — *not* `GLM-5.2`) and 1-token ping each; capture Max/Codex quota + reset windows; run one proxy task through the gauntlet and meter its tokens → seed a measured `compute.md` (P0.3).
4. **Stop the bleeding.** Disable Discord (blank token + disable flag); pair-or-disable WhatsApp with a note; restart the gateway; confirm 30 minutes of zero zombie noise.

**BD1 — Bootstrap-blocking decision (resolve BEFORE P1a starts):** **the broker's IPC mechanism.** P1a builds the broker on day one and its crash-safety, restart, and launchd-non-GUI reachability all depend on this. **Recommendation (adopt unless the owner objects): a unix domain socket + JSON-RPC** — local-only (no network exposure), works from a launchd non-GUI session, simple message framing, restarts cleanly after a crash. A file-queue and a local HTTP server are the rejected alternatives (slower / network-exposed respectively). This is a decision, not an open question.

**Open questions for the owner:**
- **OQ1 — Owner trust profile default** at launch: is ask-for-everything (safest) confirmed as the day-one default? Which specific personal repos are eligible to *ever* reach auto-merge (P1b)?
- **OQ2 — Nightly dollar ceiling + per-repo sub-ceilings:** the hard numbers for the kill-switch (P0/P2.6); the P4 gate's "under the ceiling" is undefined until this is answered.
- **OQ3 — OpenRouter budget posture:** is overflow spend capped monthly, or pay-as-you-go with the daily ceiling as the only bound?
- **OQ4 — First target repos** for P2/P3 (which `~/Code` repos are the safe proving ground — personal, well-tested, low-blast-radius)?
- **OQ5 — Jira scope:** which queues/labels are factory-eligible (work-repo PRs stay ask-for-everything per §5 — confirm the filter)?
- **OQ6 — Concurrency ceiling** the machine tolerates overnight (informs the parallel-worker count and memory-zone thresholds); the *quota*-derived ceiling comes from P0.3's measured math — this OQ is the *hardware/memory* bound, whichever binds first.

---

## §9 — NOT in Scope / Parked

- **Containers/VMs/Docker per worker** — worktrees give the isolation a personal tool needs; container orchestration is over-engineering (v1 lock, kept).
- **A Celery/Temporal-grade orchestrator or an AI supervisor** — a plain-code timer tick over SQLite is the whole system (surviving lock).
- **A lights-out factory (zero human in the loop)** — deliberately capped; irreversible/high-stakes actions stay human.
- **pm-weekly / the weekly operating packet** — *what it is:* the weekly status-email pipeline from v1's pm_os parity. *Why parked:* the factory needs no weekly packet to build code; it's a comms convenience. *Carried from v1 — deliberately deferred, not forgotten.*
- **pm-pulse survey** — *what it is:* the auto-filled Diligent weekly pulse survey. *Why parked:* pure work-admin convenience, orthogonal to the factory. *Carried from v1 — deliberately deferred, not forgotten.*
- **Glean enterprise search** — *what it is:* a skill that answers questions from the corporate Glean index. *Why parked:* repo onboarding uses code-review-graph, not Glean, so there's no factory dependency; wire it only when a concrete weekly question needs it. *Carried from v1 — deliberately deferred, not forgotten.*
- **Stakeholder trackers + `product-operating-model.md` as a CPO artifact** — control-panel convenience, not the product; park as vNext.
- **CRM/Salesforce connector** — a new load-bearing integration; start Glean-mediated only when a concrete weekly question is named.
- **Merging the 5,000-commit fork; extending the Yahoo send guard; any in-process safeguard** — the failure patterns that started this (v1 locks, kept).
- **Worker eval golden-task suite** — high value but needs a curated golden set first; add after the §P5 scorecard has data.

---

## Appendix A — Non-spine capability ideas (ranked, phase-slotted)

The 8 spine ideas are woven into core phases: PRD decomposition (P3), voice-note (P4.7 → consolidated P6), ambiguity interceptor (P3), failover ladder (P4), scorecard (P5), trust ledger (P1b), briefing + one-tap approval (P4). The rest, ranked by wow × feasibility (Wow and Effort are 1–10 / S-M-L rough sizes) with a suggested slot:

| # | Idea | Wow | Effort | Suggested slot | Notes |
|---|------|-----|--------|----------------|-------|
| 4 | Budget kill-switch + graceful drain | 8 | S | P0/P2.6 | Safety floor; cheap + critical, folded early |
| 29 | Cost-per-merged-PR trend board | 8 | S | P4.5 | The north-star metric surfaced |
| 18 | "Should I even build this?" sanity gate | 8 | S | P3.1 | Rides task-splitting; duplicate/anti-goal check |
| 10 | Ready-to-merge digest (ranked by confidence) | 7 | S | P4.3 | Sorts morning review time |
| 25 | Living codebase map (auto-refreshed) | 7 | S | P7.5 | Feeds risk/scope/sanity |
| 30 | Model leaderboard | 7 | S | P5.1 | A view over the scorecard |
| 2 | Self-healing retry + forensic capture | 8 | M | P4.1 | Overnight resilience |
| 9 | Decision replay | 8 | M | P4.4/P6 | Narrated trust-builder |
| 15 | Pattern bank of past solutions | 8 | M | P5.2 | On the full-text session search |
| 16 | Drift detection | 8 | M | P5.4 | Plain-code self-supervision |
| 19 | Regression sentinel (post-merge watch) | 8 | M | P5.4 | Makes auto-merge safe |
| 22 | Jira write-back | 8 | M | P3.3 | Keeps the board honest |
| 23 | Watch-repo-issues mode | 8 | M | P3.5/P7 | Open-source backlog chipping |
| 26 | Self-growing skill library | 8 | M | P5.2 | Curated + usage stats |
| 27 | Cross-repo knowledge transfer | 8 | M | P7.4 | Portfolio-wide memory |
| 28 | Weekly factory retro | 9 | M | P7.3 | Delight anchor |
| 32 | "Explain this merge like I've been away" | 8 | M | P7.4 | Re-onboard in 60 seconds |
| 5 | Live tail on demand (`/watch`, `/steer`) | 7 | M | P6 | Reach into the fleet from bed |
| 17 | Scope guard (footprint enforcement) | 7 | M | P5.4 | Pre-flight footprint as a runtime rail |
| 24 | Repo onboarding ritual | 7 | M | P3.4 | Already a P3 deliverable |
| 31 | Named worker personas | 6 | S | P4.4 | Charming, near-zero cost |
| 8 | Demo reel of the night | 10 | L | P7.2 | High-wow, deferred by effort |
| 12 | Retro rewrites its own skills | 10 | L | P5.3 | Core self-improvement, gated |
| 13 | Worker eval suite (golden tasks) | 8 | L | post-P5 (§9) | Needs a golden set first |

---

## Appendix B — Provenance & revision history

**Sources (research and internal analysis this plan draws on):**
- Research on state-of-the-art autonomous software factories in 2026 (SOTA findings on harness-vs-reasoning split, cost-aware multi-provider routing, verification-as-bottleneck, and Level-4 autonomy ceiling).
- Research on 60+ overnight agent-fleet runs (the reliability failure modes in §4: 429 walls, memory-zone throttling, silent stalls, watchdog hierarchy).
- An internal 10x-ambition critique that recommended inverting v1's spine to factory-first (ships the first owner win about two weeks sooner).
- An internal substrate inventory (which existing hermes/pm_os organs are reuse vs. new code — every "builds on" claim traces to it).
- An internal capability wishlist (32 ideas; the 8 spine items woven into core phases, the rest in Appendix A).
- The v1 plan (the reliability spine, broker, cost stops, and worktree discipline — much of which survives here).

**Revision history:**
- **v1** (git commit `58f5e56ad`, chief-of-staff-first, factory as Phase 6) — preserved verbatim in git.
- **v2** — factory-first rewrite; inverts the spine. Adversarial-panel review resolved all P0/P1 findings: worker-launcher reclassified as new code, prompt-injection scan moved to runtime + broker-only pushes, probe-validated model names, and the P1a/P2/P1b resequence for an earlier first win. Then a superset pass folded back seven v1 reliability items into a new Phase R and restored calendar/meeting-prep to the parked list, and a plain-English pass rewrote the whole document for a zero-context reader.

No code is written under this document; it is a plan only.
