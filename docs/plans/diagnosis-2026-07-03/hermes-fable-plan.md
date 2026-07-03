# Hermes Fable Plan — Comprehensive Personal Chief-of-Staff Assistant (WORKING DOC / RESUME POINT)

**Status:** IN PROGRESS — this file is a resume point for a fresh session, not the finished plan.
**Date:** 2026-07-03
**Author context:** Drafted under Fable 5, using Opus/Sonnet subagents for research.

## Context & framing (read this — it sets scope)

This is a **personal productivity project**: a single-user assistant that helps Yu-Kuan (a CPO) run his own workday. Everything here operates on **his own machine, his own accounts, and his own code repositories**, for ordinary product-management and software-engineering work. There is nothing multi-tenant, nothing targeting anyone else's systems, and no third parties involved.

An earlier version of the assistant had a **reliability bug**: while trying to help, it sometimes took an unintended shortcut — e.g. reaching for a stored login credential it didn't need, or editing its own configuration to get a task done — instead of following the intended workflow. That's a **correctness problem in a personal tool**, the same category as "the script did the wrong thing." The remediation is ordinary software engineering: give the assistant a clear, reliable path so it stops improvising, and keep the guardrails **proportionate to a personal single-user tool** — not enterprise hardening. The point of the guardrails is predictability and "don't surprise me," not defense against an adversary.

**Steering for this planning pass:**
- **Lead with capability.** The v1 plan is too small; the target is a genuinely complete CPO assistant.
- **Keep safeguards light and proportionate.** Approval-before-merge and working-on-a-copy are enough for the build features; I don't want reliability/guardrail discussion to crowd out capability planning.
- **No code today — plan only.**

> **Fresh session:** the comprehensive plan is **not yet written**. This doc captures (0) the goal + method, (1) what's done, (2) what's left. Resume at Section 2. Don't redo Section 1.

---

## 0. The goal & method

### The expanded goal
A **complete personal chief-of-staff assistant for a CPO (Yu-Kuan)** that:
- **Baseline:** does *everything clawchief/tradclaw do* — inbox/chat triage, a priority-map, an "auto-resolver" that handles the safe, routine, easily-reversible items itself and drafts everything else for review, gentle proactive nudges, quiet-by-default behavior, and a single canonical task list.
- **Capability #1 — the build assistant ("AI factory"):** supervises and coordinates **multiple headless Codex / Claude Code sessions** that draft software changes on isolated branches of Yu-Kuan's own repos, monitors their progress, runs the tests, and surfaces the results for a one-tap merge approval. Think "a team of tireless junior devs working on branches" — Yu-Kuan reviews and merges.
- **Capability #2 — full pm_os orchestration:** reliably drives *everything* in `~/Code/pm_os` — weekly recaps, daily triage, video creation, and arbitrary future PM workflows.

### Fixed decisions (from the earlier interview — do not re-ask)
| Dimension | Decision |
|---|---|
| Foundation | **Hybrid reset** onto a clean upstream hermes checkout; reuse ideas from the fork, don't maintain it |
| Timeline | Months of depth, **phased for fast incremental value** (each phase ships a usable slice) |
| Autonomy | **Propose-first + safe lane** — auto-handle only the routine/reversible; draft everything else for approval |
| Channels | **Telegram + Teams/Outlook (Diligent) + WhatsApp.** Yahoo email deprioritized; Discord turned off |

### Method (how we write the plan)
Fan out research to subagents, then synthesize the plan. Four tracks (exact prompts in §2.1):
1. **pm_os capability inventory** (Sonnet) — catalog every `~/Code/pm_os/bin` tool, existing pipelines, the login/token model, video/media capability.
2. **Clawchief/tradclaw complete feature checklist** (Sonnet) — a verifiable checkbox list of every skill, cron job, policy file, workspace artifact, onboarding step, and "must-not-regress" behavior, so "everything clawchief does" is provable.
3. **Build-assistant orchestration research** (Opus-capable) — how to run and coordinate several headless Claude Code + Codex sessions on one macOS box: headless invocation, git worktree isolation, a job queue, budgets, and a lightweight approve-before-merge step.
4. **Adversarial critique of the v1 plan vs the expanded goal** (Opus) — what v1 misses, where it's under-specified for "complete," the new phase/milestone map, new subsystems needed, acceptance criteria.

---

## 1. What's DONE (do not redo)

### 1a. Diagnosis + v1 remediation (complete, on disk)
The prior run produced a complete, reviewed diagnosis and a v1 (5-phase) improvement plan. All under `docs/plans/diagnosis-2026-07-03/`. (Note: these earlier files use incident-post-mortem wording — reliability failures described in engineering terms. Same substance as above; just older phrasing.)

| File | What it is | Reuse for the big plan |
|---|---|---|
| `diagnosis.md` | Root-cause analysis: 9 ranked reliability failure modes (over-broad permissions, effort inversion, safeguard-on-the-wrong-path, fail-open behavior, gateway instability, broken approval flow, upgrade debt, no input-handling policy, setup friction) + architecture verdict + gap analysis | The "why"; the permissions & effort-inversion findings shape the build-assistant and pm_os design |
| `comparison.md` | Hermes vs clawchief/tradclaw — 6 missing patterns (declarative policy, skills-as-control-plane, canonical state, quiet-by-default, onboarding/checklist, input-handling policy) + the safe-lane mechanism | The baseline capability spine |
| `interview-synthesis.md` | The 4 fixed decisions above + how they reshape priorities (center of gravity = Teams/Outlook, not Yahoo) | Decisions locked; don't re-ask |
| `remediation-plan.md` | **v1 plan** — 5 phases (0 Stabilize, 1 Reliable send path, 2 Control plane + briefing, 3 Triage + replies, 4 Proactive + maintenance) + decision matrix + milestone map (M0–M4) | The big plan EXTENDS this; keep 0–4, add build-assistant + full pm_os |
| `next-steps.md` | Exactly 3 "start Monday" bootstrap actions (confirm state, turn off the idle Discord connector, measure fork delta) | Still valid as the first moves |
| `evidence/` | 8 findings files backing the above | Source material |

### 1b. Live repo ground truth (verified 2026-07-03, from `evidence/phase0-state-snapshot.md`)
- Branch `feat/governance-plugins`, base **v0.14.0**; **61 ahead / ~5,000 behind** upstream (5,010 total / 4,085 first-parent). No `v0.17.0` tag; upstream uses date tags, latest `v2026.7.1` (~v0.18).
- Gateway runs under launchd `ai.hermes.gateway` (KeepAlive); it **restarted 3× that morning** during a machine DNS outage, with no health signal to explain why. **Discord** connector idles with no token (logs an error every 5 min); **WhatsApp** retries every 300s.
- venv healthy (`yaml` imports; CLI works). 3 cron jobs live: `morning-briefing` (calls `pm_os/bin/run-morning.js`), `weekly-status`, `pr-monitor`.
- Local add-on plugins present: `control-room`, `email-send-guard`, `tool-registry-guard`, `teams_pipeline`.

### 1c. This pass — research NOT yet run
The four research subagents were **drafted and launched but interrupted before they ran**. Their prompts are ready (§2.1). **No new evidence files exist yet** for: pm_os inventory, clawchief checklist, build-assistant research, v1 critique. Run these first.

---

## 2. What's LEFT (resume here)

### 2.1 Run the four research tracks (parallel subagents)
Launch these four in one batch.

**Track A — pm_os capability inventory (Sonnet).**
Map everything `~/Code/pm_os` can do (it's Yu-Kuan's own PM-automation toolbox). Inventory every `bin/` tool (purpose, actions, login/env needed, does-it-write? Y/N) by reading file headers / running read-only `--help` only — don't run any tool that sends or changes data. Capture existing pipelines (pm-morning, pm-weekly, pm-send, pm-jira, pm-pulse, teams-read, outlook-read, draft-responses, yk-comms/yk-voice, any video/media), the Microsoft Graph login/token model + services (Teams, Outlook, SharePoint, Jira, Confluence, Salesforce, Glean), and the tool-registry conventions (graph-workbook.js, graph-file-ops.js, ooxml-surgery.py, office bridge). → `evidence/pmos-capability-inventory.md`.

**Track B — clawchief/tradclaw complete feature checklist (Sonnet).**
Read `evidence/reference-repos-findings.md` first, then go deep into `~/Code/clawchief` and `~/Code/tradclaw`. Enumerate EVERY skill (name/trigger/IO/decision logic), cron job, policy file (priority-map, auto-resolver, input-handling policy, proactive contract, tasks, processed-ledger), workspace artifact, onboarding step, and must-not-regress behavior — as a literal checkbox list grouped by category, each item specific enough to verify "did we build this?" → `evidence/clawchief-feature-checklist.md`.

**Track C — build-assistant orchestration research (Opus-capable).**
How to run and coordinate several headless Claude Code + Codex CLI sessions on one macOS box so they can draft software changes on branches of Yu-Kuan's own repos. Local: `claude --help` (`-p/--print`, `--output-format`, `--permission-mode`, MCP, hooks, session resume — prefer normal permission prompts, not the skip-permissions flag), `codex --help` (exec mode, sandbox/approval options), git worktrees for per-session isolation, and this harness's own Workflow/Agent/background-task primitives as a supervisor model. Web (2026): multi-agent coding orchestration, per-agent worktree + branch, a job queue, test/review gates before merge, per-job token/time budgets, and a simple "approve before merge" step. Keep the gates lightweight — the goal is a smooth personal build workflow, roughly "a CI bot that opens PRs for me to approve," not a hardened system. → `evidence/ai-factory-research.md`.

**Track D — critique of v1 vs the expanded goal (Opus).**
Critique `remediation-plan.md` against the expanded goal (baseline + build assistant + full pm_os). Cover: what v1 omits, where it's under-specified for a "complete" build, the right *lightweight* isolation for the build assistant (worktree + approve-before-merge is likely enough — don't over-build), the new phase/milestone map, new subsystems/policy/skills/infra needed, practical risks (parallel-session cost, orchestrator context limits when juggling many sessions, pm_os breadth), and acceptance criteria proving "complete." The user wants capability-first framing, safeguards kept proportionate. → `evidence/v1-plan-critique.md`.

### 2.2 Synthesize the comprehensive plan (rewrite this file)
Using the four evidence files + the v1 plan, rewrite `hermes-fable-plan.md` into the full plan. Proposed structure (refine per Track D):

- **Vision & scope** — the CPO assistant: baseline + build assistant + pm_os, a paragraph each.
- **Architecture overview** — clean hermes base as the runtime (gateway + cron host); a declarative control plane (policy + skills + task ledger) as the brain; **pm_os as the "work tools" surface** (drive its `bin/` tools via skills); **the build assistant as a distinct coordinated subsystem** (supervisor + worktree-isolated coding sessions + a merge-approval step). Include a request-flow diagram.
- **Capability completeness matrix** — every clawchief checklist item (Track B) + every pm_os pipeline (Track A) + build-assistant capabilities, each mapped to a phase and an acceptance test. This is what makes it "complete" and verifiable.
- **Expanded phase/milestone map** — extend v1's 5 phases (validate against Track D):
  - **Phase 0 Stabilize** (from v1) — clean base, quiet the idle connectors, add a health signal.
  - **Phase 1 Control plane + baseline assistant** — policy files, task ledger, triage/priority/auto-resolver, quiet-by-default; reproduce the clawchief baseline.
  - **Phase 2 Reliable outbound + channels** — a single dependable send path for Telegram/Teams/WhatsApp, propose-first + safe lane.
  - **Phase 3 pm_os orchestration** — skills that drive pm_os pipelines: daily triage, weekly recap, jira, video creation; one-line cron + SKILL.md each; reliability + idempotency ledger.
  - **Phase 4 The build assistant** — supervisor subsystem: task intake → spawn headless coding sessions in worktrees → monitor + budget → run tests / review → approve-before-merge. Milestone: build one real feature end-to-end and merge it with a single approval.
  - **Phase 5 Proactive assistant + evolve** — calendar / meeting-prep, proactive nudges, self-improving skills, sustainable upstream tracking.
  - Each phase: goal, thin usable slice/milestone, specific artifacts (files/skills/policy), acceptance criteria, dependencies, effort. Keep v1's decision-matrix style.
- **New subsystems the big plan introduces** (seed list; expand from Track C/D):
  - **Build-assistant supervisor** — job queue + per-job worktree + status tracking + budget + merge-approval step.
  - **pm_os skill layer** — SKILL.md wrappers so cron prompts stay one line.
  - **Video/media pipeline** — whatever Track A finds pm_os supports, exposed as a skill.
  - **Canonical state** — `tasks.md` + processed ledger + a build-job ledger.
  - **Policy files** — priority-map, auto-resolver, input-handling policy, proactive-contract (from comparison.md) + a merge-approval policy for the build assistant.
- **What to reuse vs build** — decision matrix extended to the build-assistant + pm_os subsystems.
- **Acceptance criteria for "complete"** — from Track D; must include: every clawchief checklist item ✓, every prioritized pm_os pipeline runs on cadence ✓, build assistant delivers + merges one real feature with a single approval ✓, a normal CPO week runs with ≤1 approval per consequential action ✓.
- **Sequenced first-actions** — carry `next-steps.md`'s 3 bootstrap actions as the literal start.

### 2.3 Adversarial review gauntlet (two panels: Codex challenges, Claude responds)

A single fresh-context read-back is not enough for a plan this ambitious. Run a **two-panel adversarial review**: first a Codex panel that attacks the plan from independent angles, then a Claude panel that adjudicates every Codex concern and revises the plan. The point is to stress the plan on *two axes at once* — **is it correct?** and **is it ambitious enough?** — because this plan is as likely to fail by under-reaching (a glorified triage bot) as by being wrong.

Invocation note: the Codex agents run via the `codex` skill in its **adversarial modes** — `challenge` (tries to break the plan's reasoning) and `consult` (open-ended critique). Treat "`/codex:adversarial-review`" as shorthand for "run the codex skill in challenge+consult mode against this plan document." Each Codex agent gets the full `hermes-fable-plan.md` plus the evidence files it needs, and a **distinct mandate** so they don't converge on the same three points.

#### Panel 1 — Codex adversarial panel (minimum 3 agents; 4 recommended)

Run these as separate Codex sessions, each with one mandate. Each must return a **ranked list of concrete concerns**, every concern tagged `[ACCURACY]`, `[AMBITION]`, `[COMPLETENESS]`, or `[OPERABILITY]`, with a one-line "why it matters" and, where possible, a suggested fix. Vague concerns are rejected — demand specifics (file, phase, claim, or missing capability).

- **Codex-A — Accuracy & validity (`[ACCURACY]`).** Attack the plan's correctness: wrong assumptions, unproven claims stated as fact, dependency/sequencing flaws (does Phase N actually have what it needs from N−1?), places where the architecture won't work as described, `[UNVERIFIED]` items that are load-bearing, and any claim contradicted by the evidence files. Mandate question: *"Where will this plan simply not work, or rest on something untrue?"*
- **Codex-B — "Not thinking big enough" (`[AMBITION]`).** This is the explicitly requested lens. Assume the author under-reached. Where does the plan settle for a competent assistant instead of a true force-multiplier for a CPO? What 10× capabilities are absent (e.g. the build assistant shipping whole features unattended overnight; pm_os workflows Yu-Kuan hasn't thought to ask for yet; cross-domain moves like "turn this Slack thread into a spec, a Jira epic, and a branch with a draft PR")? What would make this the assistant a CPO brags about, not just uses? Mandate question: *"If this succeeds exactly as written, why is it still not enough — and what would 10× look like?"*
- **Codex-C — Completeness & coherence (`[COMPLETENESS]`).** Hold the plan against its own promise: *everything clawchief/tradclaw do + the build assistant + full pm_os*. Cross-check the capability-completeness matrix against the Track A (pm_os) and Track B (clawchief) checklists item by item — what's dropped, hand-waved, or lacks an acceptance test? Find internal contradictions, undefined seams between subsystems (how exactly does the chief-of-staff hand a task to the build assistant?), and phases without a real "done" gate. Mandate question: *"What did the plan promise and then quietly fail to deliver?"*
- **Codex-D — Operability & failure modes (`[OPERABILITY]`, recommended 4th).** Will it survive contact with reality on a single macOS box? Attack: parallel-session cost blowups, orchestrator context exhaustion when juggling many coding sessions, maintenance drag as upstream moves, and — most important — whether the plan reintroduces the *same* reliability traps the diagnosis identified (over-broad permissions creeping back, behavior migrating from markdown into god-files, safeguards that fail-open). Mandate question: *"Six months in, how has this plan rotted or fallen over?"*

Persist each Codex agent's raw output to `evidence/review-codex-<A|B|C|D>.md`.

#### Panel 2 — Claude reaction panel (adjudicate + revise)

Spawn a panel of **fresh-context Claude agents** (Opus-tier for judgment) to respond to the Codex panel. This panel does not rubber-stamp — its job is to take each Codex concern seriously *and* push back where Codex is wrong or where a "think bigger" idea would break the plan's discipline (e.g. scope creep that resurrects the effort-inversion failure).

1. **Triage (one agent per Codex axis, or one synthesizer over all four).** For every Codex concern, classify it: **ACCEPT** (valid, must fix), **PARTIAL** (real but needs scoping), **REJECT** (wrong or already handled — cite where), or **DEFER** (valid but belongs in a later phase / backlog, with a reason). No concern may be silently dropped; each gets a one-line disposition with justification.
2. **Ambition reconciliation (dedicated agent).** The `[AMBITION]` concerns get special handling: for each "think bigger" idea, decide whether it (a) folds into an existing phase, (b) becomes a new milestone/phase, or (c) is explicitly parked as "vNext" with a note on why it's out of scope now. Guard against scope creep that would repeat the diagnosis's core failure (shipping tactics while structure rots) — bigger is only better if it survives the "does this stay out-of-core and declarative?" test.
3. **Revise.** Apply all ACCEPT and scoped-PARTIAL items to `hermes-fable-plan.md` directly. Record every disposition (including REJECTs and DEFERs with reasons) in a new appendix section of the plan, **"Appendix A — Adversarial review dispositions,"** so the reasoning is auditable and nothing is lost.
4. **Second-order check.** One final fresh-context Claude agent reads the *revised* plan end-to-end for: new contradictions introduced by the edits, capability items still missing an acceptance test, and whether the ambition additions violated the out-of-core/declarative discipline. Fix findings.

#### Exit gate for §2.3
The review is done when: every Codex concern has a recorded disposition; all ACCEPT/PARTIAL fixes are applied; the AMBITION ideas are each folded-in, promoted, or explicitly parked; Appendix A exists; and the second-order check is clean. Only then present the plan for approval.

---

## 3. Fresh-session kickoff checklist
1. Read this file (§0–2) and skim `remediation-plan.md` + `comparison.md`.
2. Launch the four research subagents in §2.1 (one batch, parallel).
3. When they return, rewrite this file per §2.2 into the full plan.
4. Run the §2.3 **two-panel adversarial gauntlet** — Codex panel (≥3 agents: accuracy, ambition/"think bigger", completeness, +operability) → Claude reaction panel (triage every concern, reconcile ambition, revise, second-order check) → Appendix A dispositions. Then present for approval. **No code — plan only.**

**Guardrails for the fresh session:** decisions in §0 are locked (don't re-interview). This is a personal, single-user assistant on Yu-Kuan's own machine, accounts, and repos — lead with capability, keep safeguards proportionate (worktree isolation + approve-before-merge for the build assistant; a simple approval step for outbound messages). Everything additive/out-of-core so upstream merges stay cheap.
