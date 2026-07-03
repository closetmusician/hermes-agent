# ABOUTME: Adversarial critique (Research Track D) of the v1 remediation plan against the EXPANDED chief-of-staff goal.
# ABOUTME: v1 targeted reliable triage/send parity with clawchief/tradclaw. The expanded goal adds two capabilities v1 never
# ABOUTME: contemplated: (Cap#1) a build assistant supervising headless Codex/Claude sessions on isolated repo branches, and
# ABOUTME: (Cap#2) full pm_os orchestration (weekly recaps, daily triage, video creation, arbitrary PM workflows).
# ABOUTME: Framing: personal single-user tool for Yu-Kuan on his own machine — capability-first, safeguards proportionate (predictability, not adversarial defense).

**Date:** 2026-07-03
**Track:** D — adversarial critique of `remediation-plan.md` v1 vs the expanded goal.
**Verdict in one line:** v1 is a *correct and reusable spine for the reliability half of the product* but is **structurally silent on ~half the expanded goal** — the two new capabilities are net-new subsystems, not extensions of the send/triage pipeline, and v1's phase map ends exactly where the expanded goal begins.

---

## 1. What v1 omits entirely relative to the expanded goal

v1 was written to reach parity with clawchief/tradclaw (triage → draft → safe-lane send → quiet proactive nudges) on a reliable base. Measured against the expanded goal, the following are **absent, not merely under-specified**:

**A. The build assistant ("AI factory") — Capability #1 — is not mentioned once.**
- No concept of supervising *headless coding sessions* (Codex/Claude Code) at all. v1's only "agent" is the hermes chief-of-staff agent talking to a send broker. The factory is a *fleet-of-workers* pattern with a fundamentally different control loop (spawn → monitor → test → gate merge), and v1 has no phase, no skill, no state file, and no policy for it.
- No **build-job ledger** (which sessions are running, on which repo/branch, what task, what status, what test result). This is the Cap#1 analogue of `tasks.md` and it does not exist in v1.
- No **merge-approval policy**. v1's safe lane governs *outbound messages to third parties*. It says nothing about *merging code into Yu-Kuan's repos* — a different risk class with a different recoverability profile (a bad merge to `main` of pm_os or hermes is high-scope, but a merge to an isolated branch is trivially recoverable). The safe-lane's 5 conditions don't map cleanly onto "should this diff land."
- No **worktree / branch isolation mechanism** for the workers, no **test-gate definition** ("what does 'tested' mean before a one-tap merge is offered"), and no **one-tap merge approval surface** on Telegram (v1's approval surface only carries held *messages*, not held *diffs* with test output).
- No **cost/concurrency control** for N parallel model sessions (see §6).

**B. Full pm_os orchestration — Capability #2 — is present only as a *tool dependency*, never as an *orchestration target*.**
- v1 references pm_os exactly as "the hardened toolbelt behind the Teams/Outlook briefing" (Phase 2.2). It treats pm_os as a *library the triage skill calls*. The expanded goal is the inverse: hermes must **reliably drive all of pm_os** — `pm-weekly`, `pm-morning`, `pm-jira`, `pm-pulse`, `pm-send`, video creation, and "arbitrary future PM workflows." That is an **orchestration layer over a second codebase**, and v1 has no phase for it.
- **Video creation** (a named expanded-goal workflow) appears nowhere. pm_os has no video binary today (grep confirms — the capability would ride hermes's `plugins/video_gen/{fal,xai}` providers or a new pm_os pipeline), so this is genuinely new surface v1 never scoped.
- v1 does not address the **pm_os skill layer** as a first-class control-plane concern. pm_os already ships ~10 skills (`pm-morning`, `pm-weekly`, `pm-jira`, `pm-send`, `pm-pulse`, etc.) with their own token lifecycle, FOCI-refresh hazards (see pm_os/CLAUDE.md TOKEN-7), and `--confirm`/`PM_OS_AGENT=1` write gates. v1 never reconciles hermes's safe-lane with pm_os's *own* independent approval/confirmation model. Two governance systems, unmerged.

**C. Cross-capability primitives v1 lacks even for its own scope, but which the expanded goal makes mandatory:**
- A **unified job/run ledger** generalizing `processed.json` to long-running background jobs (a briefing is instantaneous; a build session or a video render is minutes-to-hours and needs status polling, not idempotency-dedup).
- A **notification-of-completion contract** for async work ("session 3 finished, tests green, ready to merge") — v1's proactive contract is about *nudges from inbox state*, not *completion events from spawned jobs*.
- **Concurrency/resource policy** — v1 assumes one agent doing one thing at a time.

---

## 2. Where v1 is under-specified for "complete" — acceptance criteria that wouldn't prove completeness

Even within v1's own scope, several exit gates prove *"it worked once,"* not *"the capability is complete and durable."* Against the expanded goal the gap widens:

- **Phase 0 exit ("24h uptime, zero zombie errors, truthful health signal").** Proves stability of a *single-agent* gateway. Does not prove the gateway can host *background job supervision* — the moment Cap#1 spawns long-lived subprocess workers, "healthy" must also mean "the N worker sessions are accounted for and not orphaned." v1's health check answers "which platforms are connected," not "which jobs are running." Under-specified for the expanded runtime.
- **Phase 1 exit ("approved send arrives; direct send impossible; no credential reachable").** Excellent for *message* egress. But it is silent on the *other* dangerous capability the expanded goal introduces: **code merge and pm_os write-ops**. "Complete" for the expanded goal requires the analogous proof: *the agent cannot merge to a protected branch or run a pm_os `--confirm` write without going through an approval boundary.* v1's broker doesn't cover git or pm_os writes, so Phase 1's "no dangerous capability in-process" claim is only *half* true post-expansion.
- **Phase 2 exit ("three useful weekday briefings, no double-processing").** Proves *one* pm_os workflow (morning triage). The expanded goal demands *all* of pm_os drivable. Completeness criteria should enumerate each pm_os workflow with a per-workflow gate, not generalize from one.
- **Phase 3 exit ("one week, zero unwanted sends").** Correct for replies. Says nothing about *unwanted merges* or *unwanted pm_os writes*, which are the expanded goal's equivalent failure class.
- **Phase 4 is explicitly open-ended ("ongoing, incremental")** and folds "self-improving skills" + "upstream tracking" into a catch-all. For a months-long build this is where Cap#1 and Cap#2 *should* live as first-class phases — instead they're invisible inside "evolve."

General pattern: **v1's acceptance criteria are single-instance behavioral demos ("a briefing arrived"), not coverage assertions ("every named workflow, tested").** For "complete," each capability needs an enumerated checklist gate (§7).

---

## 3. The right LIGHTWEIGHT isolation for the build assistant (and where over-building looms)

**The instinct in the prompt is correct: `git worktree` per job + approve-before-merge is very likely sufficient.** This is a personal single-user tool; the workers are Yu-Kuan's own coding agents on Yu-Kuan's own repos. The threat model is *predictability and recoverability*, not adversarial escape. The right mechanism:

- **One `git worktree` + one throwaway branch per job.** `git worktree add ../wt/job-<id> -b factory/job-<id>`. Isolation is filesystem-level and free; branches are cheap; a bad job is deleted with `git worktree remove`. Recoverability is total because nothing touches a tracked branch until merge.
- **Approve-before-merge as the only gate.** Worker drafts on its branch → runs the repo's own test command → posts diff summary + test result to Telegram → Yu-Kuan taps approve → hermes runs `git merge --ff-only` (or `--squash`) into the target branch. Merge is the *only* privileged step, and it is human-gated. This is structurally identical to v1's message-approval model — **reuse the broker's approval surface, don't invent a second one.**
- **A thin build-job ledger** (`build-jobs.md` or JSON): id, repo, branch, task prompt, worker (codex|claude), status (running|tests-green|tests-red|awaiting-merge|merged|abandoned), last-log-tail. This is the whole state model.

**Where over-building looms — call these out explicitly:**
- **Containers/VMs/Docker per worker.** Unnecessary. Worktrees give directory isolation; the workers are trusted first-party agents on the owner's machine. Adding container orchestration is the Cap#1 equivalent of the in-process denylist over-engineering — solving an adversarial problem that doesn't exist here.
- **A bespoke job scheduler/queue service.** hermes already has a cron/scheduler and a gateway process; reuse them. Do not build a Celery/Temporal-grade orchestrator for a handful of personal build jobs.
- **Fine-grained per-file merge policies / diff-risk scoring ML.** A branch is either merged (human tap) or not. Resist encoding "auto-merge if diff < N lines" — that is the safe-lane over-reach applied to code, and code merges are exactly where auto-anything is least warranted early.
- **A custom sandbox for the workers' shell.** The workers are Codex/Claude Code with their own sandboxing; hermes's job is to *spawn, monitor, and gate the merge*, not to re-sandbox them.

**The one non-trivial isolation subtlety** (worth a design note, not a subsystem): worktrees of the *same repo* share the object store and can race on `git gc`/index; keep each job in its own worktree dir and never let two jobs target the same branch. And self-modification hazard — if a factory job edits *hermes itself or pm_os itself*, the merge must not hot-swap a running gateway. Gate factory merges of the orchestrator's own repos behind an explicit restart, don't live-merge.

---

## 4. Proposed phase/milestone map (extends v1's 5 phases)

Keep v1 Phases 0–3 essentially as-is (they are the reliability spine). Split v1's open-ended Phase 4 and *insert two new capability phases*. Each phase names its thin usable slice.

| Phase | Name | Thin usable slice (ship first) | Milestone |
|---|---|---|---|
| **0** | Stabilize & new base | *(unchanged)* gateway stays up, zombies dead, honest health | M0 |
| **1** | Reliable send primitive | *(unchanged)* out-of-process broker + 5-condition safe lane; direct send impossible | M1 |
| **2** | Control plane + first briefing | *(unchanged)* declarative policy files + daily Teams/Outlook briefing to Telegram | M2 |
| **3** | Triage + safe replies | *(unchanged)* draft replies; safe-lane auto-sends; rest proposed | M3 |
| **4** | Proactive CoS + canonical state | *(v1's 4.1/4.2, trimmed)* calendar/meeting-prep + quiet-by-default; canonical `tasks.md` matured | M4 |
| **5 (NEW)** | pm_os orchestration layer | Drive **one** additional pm_os workflow end-to-end via a one-line skill prompt (start with `pm-weekly`, since it's read-mostly), extend the same broker to gate pm_os `--confirm` writes | **M5 — pm_os parity** |
| **6 (NEW)** | Build assistant (AI factory) | Spawn **one** headless worker on **one** repo, isolated worktree, run repo tests, post diff+result to Telegram, one-tap `--ff-only` merge | **M6 — one-job factory** |
| **7 (NEW, was v1 4.3/4.4)** | Sustainable evolution | Monthly upstream pull; agent-proposed new skills (markdown, reviewed); factory can propose skills *for itself* | **M7 — self-extending** |

**Slotting rationale:** Cap#2 (pm_os) comes *before* Cap#1 (factory) because pm_os orchestration is an *extension of the existing triage/send pipeline* (it reuses the broker, the skill layer, the approval surface — low new-surface, high immediate value: weekly recaps + daily triage are already-wanted workflows). The factory is genuinely new machinery (job supervision, worktrees, merge gating) and should follow once the control-plane and approval surface are proven. Both are **thin-slice-first**: one workflow, one job — not the whole breadth on day one.

---

## 5. New subsystems / policy files / skills / infra the expanded goal needs (that v1 never mentions)

**For Cap#1 (build assistant):**
- `build-jobs.md` (or `.json`) — **build-job ledger** (canonical state for running/complete jobs). New.
- `merge-policy.md` — **merge-approval policy**: what "tested" means per repo (the repo's own test command), what branches are protected (`main`, `feat/*` shared), that merge is always human-tapped, and the self-modification rule (factory merges to hermes/pm_os require restart, never live). New.
- **Worktree lifecycle infra** — `git worktree add/remove` wrappers, orphan cleanup, disk-budget cap. New, lightweight.
- **Worker-spawn skill** — `factory-job/SKILL.md`: closed states, literal spawn commands (`codex ...` / `claude ...` headless invocation), numbered phases (spawn → poll → test → post → merge/abandon). New.
- **Completion-event delivery** — extend the notification contract so async job completion surfaces to Telegram (distinct from inbox nudges).
- **Concurrency/cost policy** — `factory-limits.md`: max parallel sessions, per-day model spend cap, kill-idle-after-N-minutes. New (see §6).

**For Cap#2 (pm_os orchestration):**
- **pm_os skill layer in hermes** — a `SKILL.md` per pm_os workflow (`pm-weekly`, `pm-morning`, `pm-jira`, `pm-pulse`, `pm-send`) that wraps the pm_os binary as one-line cron prompts (mirrors v1's Phase 2.2 pattern, but for the *whole set*, not just morning). New.
- **Broker extension to non-message egress** — the out-of-process boundary must also gate pm_os `--confirm`/`PM_OS_AGENT=1` writes and Graph write-ops, reconciling hermes's safe-lane with pm_os's own confirm-gate (today they are two independent governance systems). New reconciliation, not in v1.
- **Video pipeline exposure** — a `video-create/SKILL.md` + wiring to `plugins/video_gen/{fal,xai}` (or a new pm_os video binary). Genuinely new capability surface; needs its own approval treatment (renders cost money/time; treat as hold-by-default).
- **Token-lifecycle awareness** — pm_os's FOCI token chain is fragile (TOKEN-7); orchestration must call `ensure-tokens.js` / route to `/pm-login` on 401 rather than blindly refreshing. v1 never touches this because it treated pm_os as a black box.

**Cross-cutting:**
- **Unified run/job ledger** generalizing `processed.json` → long-running jobs with status polling.
- **Health check v2** — must report running jobs + worker sessions, not just channel connectivity.

---

## 6. Practical risks

1. **Parallel-session cost blow-up (Cap#1).** N headless Codex/Claude sessions each burn tokens continuously (a coding session is not a single call — it's a long agentic loop). Without a spend cap and idle-kill, the factory is an open-ended cost faucet. v1 has zero cost model. **Mitigation:** hard concurrency cap (start N=1–2), per-day spend ceiling, auto-kill idle workers.
2. **Orchestrator context exhaustion juggling many sessions.** The hermes agent supervising several jobs will blow its own context if it holds full worker transcripts. This is the exact failure the diagnosis warned about (god-file / context sprawl) reappearing at the orchestration layer. **Mitigation:** the orchestrator must hold only *ledger rows* (id/status/test-result/log-tail), never full worker output — poll a file, don't stream a transcript. This is a hard design constraint, not a nice-to-have.
3. **pm_os breadth & fragility (Cap#2).** pm_os is a large, independently-evolving second codebase with brittle FOCI token chains, `--confirm` gates, and 50+ bin scripts. "Drive *everything* in pm_os" is an unbounded surface; a pm_os change can silently break a hermes skill. **Mitigation:** wrap each pm_os workflow as a versioned one-line skill; treat pm_os as a *stable external CLI contract*, not an internal module; per-workflow acceptance gates so breakage is localized.
4. **Two upstreams to track, not one.** v1 already flags the hermes-upstream tracking burden (Phase 4.3). The expanded goal *doubles* it: hermes upstream **and** pm_os evolution. **Mitigation:** keep hermes's pm_os coupling to the stable CLI surface only (never import pm_os internals); pin/verify the pm_os bin contract in the skill files.
5. **Two governance systems unreconciled.** hermes safe-lane vs pm_os `--confirm`/audit-log. If not merged, an action can slip a gate by taking the pm_os path. This is *literally the FM-3 "guards protect the wrong path" failure re-emerging across the repo boundary.* **Mitigation:** Phase 5 must route pm_os writes through the same broker.
6. **Self-modification hazard.** A factory job editing hermes or pm_os itself, then merged live, can corrupt the running orchestrator. **Mitigation:** §3's restart-gated self-merge rule.

---

## 7. Acceptance criteria that would prove "complete" for the expanded goal (per capability, testable)

**Baseline (clawchief/tradclaw parity) — inherit v1's M2/M3/M4 gates, plus:**
- Canonical `tasks.md` is the single legible source of pending work; a fresh-context reader can answer "what's pending?" from it alone.
- Quiet-by-default proven: a full day with nothing actionable emits zero nudges.

**Cap#1 (build assistant) — complete when:**
- Spawn ≥2 concurrent headless workers on ≥2 isolated worktrees of a real repo; ledger shows both with live status.
- Each worker runs the repo's own test command; result (green/red) posts to Telegram with a diff summary.
- One-tap approve triggers a real `--ff-only`/`--squash` merge into the target branch; decline leaves the branch untouched and cleans the worktree.
- **Negative test:** the hermes agent *cannot* merge to a protected branch without the human tap (verified by a fresh-context reviewer, mirroring the M1 verification discipline).
- Spend cap and idle-kill demonstrably fire (start a worker, let it idle, confirm it's reaped).

**Cap#2 (pm_os orchestration) — complete when:**
- Each named pm_os workflow (`pm-morning`, `pm-weekly`, `pm-jira`, `pm-pulse`, `pm-send`, video-create) runs via a one-line skill prompt and delivers its artifact to the right surface.
- Every pm_os *write* (`--confirm`/Graph write) routes through the broker; a fresh-context test confirms the agent cannot write via the pm_os path bypassing approval.
- Token-expiry path is graceful: on 401, the workflow surfaces "re-auth needed" rather than silently failing or blindly refreshing (TOKEN-7 safety).
- Video: one render produced end-to-end, held for approval before any send/publish.
- **Coverage assertion, not single-instance:** a checklist enumerates every pm_os workflow with its own pass, so "complete" means the *set*, not one demo.

**Cross-cutting completeness:**
- Health check v2 truthfully reports channels **and** running jobs **and** worker sessions.
- A normal week runs briefing + triage + replies + ≥1 pm_os workflow + ≥1 build job with ≤1 approval prompt per genuinely-consequential action and zero annoyance-muting.

---

## 8. Where v1 gets things RIGHT and should be kept unchanged (for reuse)

Be fair — most of v1 is sound and should be **reused verbatim**:

- **The governing principle** ("behavior in declarative single-owner files; capability behind an out-of-process boundary") is exactly right and **generalizes cleanly** to both new capabilities: the build-job ledger and merge-policy are just more single-owner files; merge-gating is just another out-of-process capability boundary. Keep as the north star.
- **Phase 0 (stabilize + new base)** is a hard prerequisite for *everything* and needs no change beyond the health-check-v2 extension. Keep.
- **Phase 1 (out-of-process send broker + 5-condition safe lane)** is the reliability keystone and the **reusable approval surface** for both new capabilities — merge approval and pm_os-write approval should ride the *same* broker and the *same* Telegram approval UX. Do not build parallel approval systems. Keep and extend, don't replace.
- **The hybrid-reset decision** (clean upstream base, mine the fork, keep local delta small/additive/out-of-core) is *more* important with the expanded goal, because factory + pm_os layers add local surface — keeping them as skills/policy/ledgers (not hermes core edits) is what keeps upstream merges cheap. Keep.
- **The decision matrix** (fix/port/rewrite/replace/abandon per subsystem) is a good reusable tool; just add rows for the new subsystems in §5.
- **Milestone-per-phase, thin-slice-first discipline** is exactly the right shape for a months-long build and should govern the two new phases too (one workflow, one job — not the whole breadth). Keep.
- **Fresh-context verification** for the M1 gate (don't self-verify the boundary) is the single best process idea in v1 and should be applied to the new negative tests in §7. Keep and replicate.
- **The "quiet-by-default proactive contract"** (Phase 4.2) directly serves Cap#1's completion notifications and Cap#2's recaps — reuse it as the notification-discipline for async job events, not just inbox nudges.

**Net:** v1 is a correct *foundation and reliability spine*, not a wrong plan. The expanded goal doesn't invalidate it — it appends two capability phases (pm_os orchestration, then the factory) and a set of new single-owner artifacts, all of which *reuse* v1's broker, approval surface, skill-anatomy, and out-of-core discipline. The critique is one of **completeness and sequencing, not correctness.**
