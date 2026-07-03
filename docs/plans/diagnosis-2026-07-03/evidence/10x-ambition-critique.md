# ABOUTME: Adversarial ambition critique of hermes-fable-plan.md against the SELF-SUPERVISING autonomous software factory vision.
# ABOUTME: Every phase P0-P7 rated for under-reach; names ≥12 structural gaps by severity; states what v1 substrate survives.

**Date:** 2026-07-03
**Reviewer mandate:** Attack the plan for insufficient ambition. The plan was built for a chief-of-staff (CoS) product with the factory PARKED as vNext-lite. Yu-Kuan has rejected that framing: the product is an **intelligent, self-supervising, self-improving, self-learning autonomous software factory** that ingests PRDs / specs / Jira queues / existing repos / greenfield ideas and orchestrates fleets of headless Codex + Claude Code agents to build software overnight at high confidence with minimal oversight. The CoS surface is the factory's **control plane**, not the product.

**Verdict in one line:** The plan is an excellent *reliability spine and a competent one-job PR bot*, but it is **architecturally miscommitted** — the factory is Phase 6 of 8, sequenced behind five phases of email triage, scoped to "one worker, one repo, one-tap merge," and structurally missing every mechanism that makes a factory *self-supervising* rather than *human-in-every-loop*. The locked decisions Yu-Kuan just made (graduated trust, multi-provider routing, four intake sources, worker abstraction for horizontal scale) appear in the plan as **zero, one flag, one source, and one sentence** respectively. This is not a plan for the stated product; it is a plan for a different, smaller product with a factory bolted on late.

---

## Part 1 — Phase-by-phase ambition audit (P0–P7)

For each phase: **(a)** what it builds, **(b)** why it under-reaches against the factory vision, **(c)** the 10X version.

### P0 — Stabilize & new base (§4 "PHASE 0", L209-219)

**(a) Builds:** Clean upstream hermes checkout; kill Discord/WhatsApp zombies; a 1-line health signal; launchd supervisor sanity. M0 = "gateway stays up, honest health."

**(b) Under-reach:** P0 stabilizes a *single-agent chat gateway*. A factory's P0 baseline is not "the gateway stays up" — it is "the machine can host N long-lived subprocess fleets without the OS killing them, the worker runtime is version-pinned and verified, and the control plane knows what compute it has." The plan's health design note (L216: "structure health output so a `jobs` section can be added later") treats fleet-awareness as a future append, not a P0 concern. The `caffeinate`/sleep policy — existential for overnight runs — is deferred all the way to P6.1 (L291). Discord/WhatsApp cleanup is CoS-channel hygiene, irrelevant to a factory.

**(c) 10X P0 — "Factory-ready host, day one."** Baseline = (1) worker-runtime capability probe committed as an artifact: `claude`/`codex` versions, `--max-budget-usd` support, worktree support, **and** the OpenRouter/GLM-5.2/Kimi paths reachable (the locked multi-provider requirement), all asserted at boot; (2) sleep/wake policy and launchd session-context validation settled *now* because they gate every unattended run, not at P6; (3) a compute-inventory file (`compute.md`): Claude Max quota, Codex quota, OpenRouter balance, per-provider rate ceilings — the substrate the cost router needs; (4) health signal designed jobs-first, not channels-first. M0 should read "the box can run an overnight fleet and honestly report fleet + compute state," not "the chat bot doesn't crash."

### P1 — Send broker + safe lane (§4 "PHASE 1", L221-231)

**(a) Builds:** Out-of-process send broker holding all outbound credentials; 5-condition safe lane; fold `/approve-email` into broker; credentials out of assistant reach. M1 = "direct send impossible."

**(b) Under-reach:** This is the plan's genuine keystone and it is *correct* — but it is scoped to **message egress** and generalizes to consequential actions only as a *hold-for-human-approval* surface. The factory's core trust primitive is not "hold everything for a tap" — it is **graduated trust that earns autonomy from track record** (Yu-Kuan's locked decision #1). The broker as designed has one lever: safe-lane-or-hold. There is no **trust ledger**, no per-repo/per-task-type track record, no mechanism by which "this repo + this task type has 20 clean merges" ever *reduces* the approval burden. The plan explicitly forbids the graduation path — "Not auto-merge heuristics... Merge is always human-tapped" (L330). That is the anti-thesis of the locked graduated-trust decision. The plan hard-codes PR-only-forever where Yu-Kuan asked for PR-only-*that-earns*-auto-merge.

**(c) 10X P1 — "The trust ledger is the keystone, not the broker."** The broker survives as the *enforcement* boundary, but the *policy* it enforces becomes a graduated trust model: `trust-ledger.md` (or SQLite) records per-(repo × task-type) outcomes — merged-clean / merged-with-fix / rejected / reverted-later. A `trust-policy.md` maps track record → autonomy tier: tier 0 = every action held; tier 1 = auto-merge on personal non-prod repos after N clean; tier 2 = auto-merge broader. User-selectable profiles (the locked "PR-only everywhere" vs "auto-merge personal non-prod") are a config switch over this ledger. The M1 gate becomes: "a repo/task-type demonstrably graduates from held to auto after clean track record, and demonstrably *demotes* on a bad outcome." The broker still makes ungated *message* send impossible; but code merge autonomy is *earned and revocable*, not *forbidden*.

### P2 — Control plane + first briefing (§4 "PHASE 2", L233-243)

**(a) Builds:** Declarative policy files; first morning-triage briefing skill with preflight anatomy; cron manifest; WhatsApp gate. M2 = "a daily briefing you read."

**(b) Under-reach:** Entirely CoS. Six-to-eight days (L236) spent on inbox triage policy files before a single line of factory machinery exists. The reusable idea here — declarative single-owner control-plane files + skill preflight anatomy — is exactly right and should be *retargeted* at the factory: the factory needs a control plane far more than the inbox does. But as written, P2 builds `priority-map.md` for *email senders*, not `repo-profiles.md` for *the fleet*.

**(c) 10X P2 — "Control plane for the fleet, briefing as a view over it."** The declarative control plane's first job is factory config: `repo-profiles.md` (per-repo test command, protected branches, integration flow, trust tier, worker/model defaults), `intake-sources.md` (the four locked sources and their pollers), `model-routing.md` (provider preference order: Claude Max → Codex → OpenRouter/GLM-5.2/Kimi overflow). The morning briefing is repurposed as the factory's **morning replay surface** (overnight jobs: merged / held / needs-attention / cost) — which is the actual product Yu-Kuan wants to wake up to — with inbox triage as a *secondary* pane. Preflight anatomy applies to worker-spawn skills, not just triage skills.

### P3 — Triage + safe replies (§4 "PHASE 3", L245-253)

**(a) Builds:** Draft-reply skill routing through broker; PR-monitor as a skill. M3 = "safe-lane replies auto-send."

**(b) Under-reach:** Pure CoS. This phase has *no factory content whatsoever* and consumes 6–9 days (L248). In a factory-first plan, "the agent drafts and the safe lane auto-sends" is a proof of the graduated-trust mechanism — but applied to *email*, it proves nothing about *code*. The plan is using its most valuable weeks to perfect a capability (auto-reply to colleagues) that is orthogonal to the stated product.

**(c) 10X P3 — "First worker, not first reply."** In a factory-first sequencing, the third milestone is **one headless worker producing one tested branch**, not one auto-sent email. The safe-lane/trust mechanism gets exercised against a *real merge decision* (its actual purpose) this early, not against a scheduling confirmation. Email auto-reply is demoted to a CoS nicety shipped opportunistically, not a gating milestone.

### P4 — Proactive CoS + canonical state (§4 "PHASE 4", L255-265)

**(a) Builds:** Calendar/meeting-prep, matured `tasks.md`, `product-operating-model.md` skeleton, quiet-by-default enforced. M4 = "calendar-aware CoS."

**(b) Under-reach:** 7–9 more days (L258) of CoS depth. Calendar-read, meeting prep, and stakeholder trackers are genuinely useful *to a CPO* and genuinely irrelevant *to a software factory*. Four full phases (P1–P4, ~24–33 days) elapse before the factory begins. The canonical-state discipline (`tasks.md` as single legible source) is the right *pattern* — but the factory's canonical state (the job ledger, the trust ledger, the run ledger) is where that discipline earns its keep, and it is nowhere in P4.

**(c) 10X P4 — "Overnight batch is the milestone, not meeting prep."** By the fourth milestone a factory-first plan should have **overnight batch mode working** — the thing Yu-Kuan explicitly asked for ("build software overnight"). Canonical state = the job ledger + trust ledger + run ledger, matured and legible. Meeting prep is parked as CoS-vNext. The `product-operating-model.md` idea survives but reoriented: it tracks *what the factory is building and its confidence*, not *the CPO's bets*.

### P5 — pm_os orchestration (§4 "PHASE 5", L267-284)

**(a) Builds:** Every pm_os pipeline drivable via one-line skills; broker-gated pm_os writes; fragility-trio fixes; exec-narrative discovery; one PM artifact type. M5 = "pm_os parity." 10–14 days (L270).

**(b) Under-reach:** This is the single most *misprioritized* phase against the new vision. pm_os orchestration is **Diligent-work automation** — valuable, but it is the CoS control-plane's convenience layer, not the factory. Ten-to-fourteen days on `pm-pulse`, `pm-weekly`, Oracle survey auto-fill, and exec-narrative video spikes is a large investment sequenced *before* the factory that Yu-Kuan says is the product. Note pm_os *does* contain the Jira read/write plumbing (`pm-jira`, evidence §2.4) that the factory's **Jira-queue intake** (locked source #2) needs — so a *slice* of P5 is load-bearing, but the phase as a whole is CoS-serving.

**(c) 10X P5 — "Intake pipelines, harvested from pm_os — not pm_os parity."** Retarget: the factory needs *intake* from all four locked sources. pm_os's Jira read plumbing becomes the **Jira-queue intake pipeline** (poll assigned tickets → normalize to job specs → enqueue). The other three intakes (PRD/spec docs → spec-to-jobs decomposition; existing `~/Code` repos → repo onboarding + profiling; greenfield → scaffold-and-build) are net-new and belong *here*, at the intake layer. Full pm_os pipeline parity (pulse, weekly, exec-narrative) is demoted to CoS-vNext. What survives from P5 as factory-critical: broker write-gating (reused), the fragility fixes *only for the Jira path*, and the run-ledger (P5.5) which is the factory job-store precursor.

### P6 — Build assistant / AI factory (§4 "PHASE 6", L286-304)

**(a) Builds:** Plain-code cron-tick supervisor over SQLite; job store + intake seam; worker contract (Claude-only budgeted first); test + review gates; broker-gated one-tap merge; three cost stops; batch mode (M6.5). Milestones M6/M6.5.

**(b) Under-reach — this is where the ambition gap is widest, because this IS the product and it is scoped as a feature:**
- **"One worker, one repo, one-tap merge" is the whole factory's exit gate** (L303). That is a *demo*, not a factory. The stated product runs *fleets*.
- **Supervisor is a stateless cron tick advancing "each job one state" per tick.** Correct for durability, but it has *no intelligence*: no task decomposition (a PRD is one opaque `spec` string, L226/L292 — there is no engine that breaks a PRD into a dependency-ordered job DAG), no scheduling policy beyond a concurrency cap, no self-supervision (nothing watches *the fleet's* health and reallocates), no learning (the tick is stateless plumbing by explicit design, L291).
- **Model routing is one sentence** (L296: "default Sonnet, escalate to Opus for hard, Haiku for rote") and **Codex workers are deferred** (L296, L462) and **OpenRouter/GLM-5.2/Kimi — the locked overflow requirement — appear NOWHERE in P6.** The plan's cost dial is intra-Anthropic model choice; the locked decision is *multi-provider* routing with OpenRouter overflow. This is a direct contradiction of a locked decision.
- **No checkpoint/resume for overnight runs** beyond "one auto-fix resume" (L297). A worker that dies at 3am on a 5-job batch has no supervisor-driven recovery/re-plan; it becomes NEEDS_ATTENTION and waits for the human. That defeats "build overnight with minimal oversight."
- **No eval harness / confidence scoring.** "High confidence" (Yu-Kuan's explicit bar) is proxied by "tests pass + codex review + human reads diff." There is no per-job confidence score, no historical eval of "does this repo's test suite actually catch regressions," no post-merge outcome tracking to *learn* confidence.
- **No worker abstraction.** The locked decision is a **worker abstraction so a second machine or cloud workers slot in without rearchitecting.** P6 spawns workers as local subprocesses with local PGIDs and local worktrees (L294, L296). There is no dispatch interface, no worker-identity/location concept — a second machine cannot slot in without rearchitecting exactly the spawn/monitor core. Direct contradiction of locked decision #4.
- **Batch mode is "3–5 scoped jobs"** (L301). The vision is fleets ingesting *queues*.

**(c) 10X P6 — split into the factory's real layers:**
1. **Task-decomposition engine.** A PRD/spec/Jira-epic → a dependency-ordered DAG of scoped jobs (the plan's "spec-first job type," L296, is the seed but it decomposes *one* job, not a *project*). This is the single biggest missing organ.
2. **Fleet supervisor, not per-job tick.** Still plain-code + durable, but it reasons about the *fleet*: DAG scheduling (fan-out independent jobs, serialize dependents), per-worker-type quota, backpressure, and **checkpoint/resume/re-plan** for overnight resilience (a dead worker gets respawned from checkpoint or re-scoped, not parked).
3. **Cost-aware multi-provider router** (`model-routing.md`): Claude Max → Codex subscription → OpenRouter (GLM-5.2, Kimi, best-in-class open) as *first-class overflow*, routed by cost/quota/task-difficulty — the locked requirement, made real.
4. **Worker abstraction / dispatch interface.** A `Worker` contract (spawn, poll, kill, collect-result) with a `local-subprocess` implementation first and a stub `remote-worker` implementation proving a second machine slots in. This is a design-now, implement-one decision — cheap now, un-retrofittable later.
5. **Eval harness + confidence score.** Per-job confidence from test-coverage delta, review-severity, historical repo pass rate; post-merge outcome tracking (did it get reverted? did it break CI later?) feeding the trust ledger.
6. **Failure forensics.** When a job fails/needs-attention, a structured post-mortem artifact (what stage, what error class, cost burned, retry-worthy?) — the raw material for the learning loop, not just a retained worktree (L300).

M6 becomes "an overnight fleet ingests a Jira queue and produces a morning replay of merged/held/needs-attention jobs with confidence scores and cost," not "one feature, one tap."

### P7 — Sustainable evolution (§4 "PHASE 7", L306-320)

**(a) Builds:** Dual upstream tracking; self-improving skills (agent proposes markdown skills, human reviews); calibration loop; friction→factory lane; item-level completeness review; cross-domain capstone; product-intelligence radar. M7 = "self-extending."

**(b) Under-reach — the "self-improving / self-learning" the vision centers is confined to the LAST phase and defanged to "propose markdown, human reviews":**
- **Self-improvement = "agent proposes new *skills* as markdown, reviewed by Yu-Kuan, never new Python"** (L312). For a CoS that is prudent. For a *self-improving software factory*, it is a straitjacket: the factory's whole point is that it *writes software* — forbidding it from improving its own (non-core) code, and routing every self-improvement through a human markdown review, is the opposite of self-improving. The "friction→factory lane" (L314) gestures at self-improvement but caps it at markdown/out-of-core and human-initiated.
- **The learning loop is a monthly calibration backtest** (L313) over *triage classification and safe-lane sends* — i.e., it learns to *triage email better*, not to *build software better*. There is no loop that learns from build outcomes: no "this repo's jobs at Opus succeed 90% but at Sonnet 40%, auto-route it to Opus," no "specs of this shape decompose poorly, flag them," no "this test suite has caught 0 real regressions, downweight its confidence."
- **Self-supervision is absent as a named capability.** Nothing continuously watches the fleet and *intervenes* — reallocates a stuck job, splits a too-big job, escalates a cost anomaly *while the batch runs*. The supervisor is a dumb tick by design.

**(c) 10X P7 — "The learning loops ARE the product, distributed across all phases."** Self-learning is not a phase; it is a property. The trust ledger (P1), eval harness (P6), and failure forensics (P6) feed continuous loops: outcome-based trust graduation, model-routing auto-tuning from cost/quality outcomes, spec-quality learning from decomposition success, confidence-calibration from post-merge reverts. The factory proposes *code* changes to its own non-core modules through its *own pipeline* (dogfooding), trust-gated like any repo. P7's actual job is the *dual-upstream discipline* (which survives — it is load-bearing for staying cheap) and the *governance of* the learning loops, not their invention.

---

## Part 2 — Structural gaps: missing entirely for a self-supervising factory

Severity: **P0** = the product does not meet its stated definition without it; **P1** = the product is crippled/manual-heavy without it; **P2** = important, degrades leverage.

| # | Gap | Severity | Why it's structural | Plan reference |
|---|---|---|---|---|
| G1 | **Task-decomposition engine** (PRD/spec/Jira-epic → dependency-ordered job DAG) | **P0** | Vision is "throw PRDs / spec / Jira *queues* at it." Plan's job spec is one opaque string (L226, L292); "spec-first" (L296) refines *one* job, never decomposes a *project*. Without this, the human is the decomposer — the exact oversight the vision removes. | absent; L296 is the seed only |
| G2 | **Multi-provider cost-aware model router** (Claude Max → Codex → OpenRouter/GLM-5.2/Kimi overflow) | **P0** | Locked decision #2. Plan routes only within Anthropic models (L296); OpenRouter/GLM/Kimi appear nowhere; Codex workers deferred (L462). Direct contradiction of a locked decision. | contradicted; L296, L462, OQ4 |
| G3 | **Graduated-trust / trust ledger** (per-repo × task-type track record → earned auto-merge, revocable) | **P0** | Locked decision #1. Plan hard-codes "merge always human-tapped, not auto-merge heuristics" (L330) — the opposite. No ledger, no graduation, no demotion, no user-selectable profiles. | contradicted; L330, L227 |
| G4 | **Worker abstraction / dispatch interface** (second machine or cloud workers slot in) | **P0** | Locked decision #4 (hybrid, local-first, worker abstraction). Plan spawns local subprocesses with local PGIDs/worktrees (L294-296); no dispatch interface, no worker identity/location. Un-retrofittable if not designed now. | contradicted; L294-296 |
| G5 | **Fleet supervisor with DAG scheduling** (fan-out/fan-in, dependency ordering, backpressure) | **P0** | Plan's supervisor advances "each job one state per tick" independently (L291) — a job *list*, not a *plan*. A DAG- scheduled fleet is the difference between a factory and a batch queue. | absent; L291, L301 |
| G6 | **Checkpoint / resume / re-plan for overnight runs** | **P1** | Vision = "build overnight, minimal oversight." Plan's only recovery is one auto-fix resume (L297); a dead/stuck worker → NEEDS_ATTENTION and waits for morning human (L291). Overnight resilience is exactly what's missing. | weak; L291, L297 |
| G7 | **Eval harness + per-job confidence score** | **P1** | Vision bar is "high confidence." Plan proxies confidence with pass/fail tests + one review + human eyeballs (L297-298). No confidence score, no eval of whether a suite catches regressions, no calibration. | absent; L297-298 |
| G8 | **Outcome-based learning loop for BUILD** (post-merge reverts / CI-breaks / model-quality → routing + trust) | **P1** | "Self-learning" is the vision's core adjective. Plan's only loop learns *email triage* (L313). Nothing learns from build outcomes. | absent; L313 |
| G9 | **Intake pipelines for all four locked sources** (PRD docs, Jira queues, existing repos, greenfield) | **P1** | Locked decision #3 = ALL four. Plan's intake seam is Telegram `/factory` + a `tasks.md` tag (L292) — a *manual* single-item filing, not source pollers. Jira plumbing exists in pm_os but isn't wired as factory intake. | weak; L292, L431 |
| G10 | **Self-supervision / active fleet intervention** (watch running fleet, reallocate/split/escalate mid-run) | **P1** | "Self-supervising" is in the product name. Plan's supervisor is a dumb durable tick by explicit design (L291); nothing intervenes while a batch runs. | absent by design; L291 |
| G11 | **Repo-onboarding / profiling** (ingest an existing `~/Code` repo: detect test cmd, build, conventions, risk) | **P1** | Locked intake source. Plan assumes `merge-policy.md` per-repo config is hand-authored (L298, L345); at fleet scale over many repos, onboarding must be (semi-)automated. | absent |
| G12 | **Failure forensics** (structured post-mortem per failed job: stage, error class, cost, retry-worthiness) | **P2** | Plan retains a FAILED worktree 24h for manual inspection (L300). That's an artifact, not forensics; it's the raw material the learning loop (G8) needs and can't consume as-is. | weak; L300 |
| G13 | **Morning replay / batch review surface as a first-class product** | **P2** | The thing Yu-Kuan wakes up to. Plan has a "morning approval packet: one Telegram message per job + one-line digest" (L301) — functional but thin (no confidence, no cost rollup, no re-run/redirect controls, no fleet view). | thin; L301 |
| G14 | **Compute / quota inventory + budget-vs-quota awareness** | **P2** | Multi-provider routing (G2) needs to know remaining Claude Max quota, Codex quota, OpenRouter balance to route overflow. Plan tracks per-job $ and a daily ceiling (L299) but no provider-quota model. | absent; L299 |
| G15 | **Spec/PRD quality gate before decomposition** | **P2** | Garbage PRD → garbage job DAG → wasted overnight compute. A cheap "is this spec buildable / what's ambiguous" pre-pass protects the whole batch. The plan's spec-first stage (L296) is per-job, post-decomposition. | absent |

**Count: 15 structural gaps** (4 at P0 severity, 6 at P1, 5 at P2). Four of the P0 gaps (G2, G3, G4 and arguably G1) are not merely omissions — they **contradict Yu-Kuan's locked decisions**, which is the more serious finding: the plan was frozen against an older framing and its "locked" sections now conflict with the new locks.

---

## Part 3 — What SURVIVES from v1 into the factory plan

The plan is not worthless — a substantial substrate is load-bearing and should be carried forward *verbatim or lightly retargeted*. Do not throw these away:

**Survives verbatim (reliability substrate):**
- **P0 stabilization + clean-upstream hybrid reset** (L209-219, L14). A stable, upstream-tracking base is a hard prerequisite for anything; the "replace don't merge the 5,000-commit fork" decision is *more* important as local surface grows. Keep — extend with G14 compute inventory and jobs-first health.
- **The out-of-process broker as the enforcement boundary** (P1.1, L226). Credentials-removed-from-assistant-reach, fail-closed, one audited egress path is the correct spine. It survives as the *enforcement* layer under the new *graduated-trust policy* (the policy changes, the boundary doesn't).
- **Cron-tick + durable SQLite job store + idempotent-per-tick + monotonic transitions + supervisor lock** (P6.1-6.2, L291-292). The durability model is right and crash-proof. It survives as the *substrate* under the new *fleet/DAG scheduler* (the scheduler gets smarter; the durable-tick foundation stays).
- **Worktree-per-job isolation + per-repo merge lock + rebase-retest-merge** (P6.3, P6.5, L293-298). The isolation and serialized-integration mechanics are exactly right and match 2026 field practice. Keep.
- **Three independent cost stops** (per-job budget cap, wall-clock SIGTERM to PGID, reserved-budget daily ceiling — P6.6, L299). Keep and *extend* to be provider-aware (G14).
- **Test gate + independent `codex review` gate + schema'd worker results + "trust the diff not the transcript"** (P6.3-6.4, L296-298). Keep — this is the *floor* of confidence; G7's eval harness builds *on top*, doesn't replace it.
- **Scrubbed worker environment** (temp HOME, no secrets, strict MCP, tight allowlist — P6.3, L296). Keep; correct and cheap.
- **Fresh-context negative-test verification discipline** (M1/P5/P6 gates, L231, L388). The single best process idea in the plan — never self-verify a boundary. Apply it to the new trust-graduation and worker-abstraction gates too.

**Survives retargeted (patterns, redirected at the fleet):**
- **Declarative single-owner control-plane files** (§2 L38, P2.1). The *pattern* is right; retarget the *content* from email policy (`priority-map.md`) to fleet policy (`repo-profiles.md`, `model-routing.md`, `trust-policy.md`, `intake-sources.md`).
- **Skill preflight anatomy** (P2.2, L239). Retarget from triage skills to worker-spawn / intake skills.
- **Run ledger for long-running work** (P5.5, L281). This *is* the job-store precursor; promote it out of the pm_os phase into the factory core.
- **pm_os Jira read/write plumbing** (evidence §2.4). Harvest as the Jira-queue intake pipeline (G9); the rest of pm_os parity is CoS-vNext.
- **Quiet-by-default proactive contract** (P4.3, L263). Retarget as the notification discipline for fleet-completion events and the morning replay — alert-on-anomaly, not per-job spam.
- **Self-modification restart-gating** (L129, L415). Keep; it's exactly the guardrail dogfooding (factory improving its own non-core code) needs.

**Genuinely CoS-only (park as vNext, don't delete — they're real, just not the product):** email triage/reply (P2-P3 CoS content), calendar/meeting prep (P4.1), pm_os pipeline parity beyond Jira intake (pulse/weekly/exec-narrative, P5), stakeholder trackers, `product-operating-model.md` as a *CPO* artifact. These are a *good CoS product* — but they are the control-plane's convenience layer, not the factory.

---

## Part 4 — Single biggest recommendation

**Scrap the phase ordering and the "factory as Phase 6 feature" framing. Rebuild the plan factory-first, with the four locked decisions as P0-P1 architecture, not P6 details.**

Concretely, invert the spine:
1. **P0:** factory-ready host + compute inventory + worker-runtime probe (incl. OpenRouter/GLM/Kimi reachability).
2. **P1:** the broker **and** the trust ledger together — graduated-trust is the keystone, not hold-forever. Worker abstraction defined here (local impl now, remote stub proving it slots in).
3. **P2:** one worker → tested branch → trust-gated merge (the plan's current P6 M6, pulled forward to milestone 2).
4. **P3:** task-decomposition engine + fleet DAG scheduler + intake pipelines (Jira queue first, then repos, PRD docs, greenfield).
5. **P4:** overnight batch of a *queue* with checkpoint/resume + morning replay surface + confidence scores + cost-aware multi-provider routing live.
6. **P5:** the learning loops (outcome→trust, outcome→routing, forensics→re-plan) + self-supervision.
7. **P6+:** CoS control-plane surface (email/Telegram/pm_os) layered *on top* of the working factory — the demoted former P2-P5.

The current plan is a reliability spine with a PR-bot demo appended. The product Yu-Kuan described is a self-supervising fleet with a control-plane surface appended. **Same excellent substrate, inverted center of gravity.** The broker, cron-tick durability, worktree isolation, cost stops, and verification discipline all survive and are load-bearing — but the *organizing principle* must become the trust ledger + decomposition engine + fleet scheduler + multi-provider router + learning loops, which are today either one sentence, one flag, or entirely absent, and in four cases directly contradict the locks.
