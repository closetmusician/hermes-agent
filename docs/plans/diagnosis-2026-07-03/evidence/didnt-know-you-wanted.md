# ABOUTME: Grounded ideation — 32 capabilities for the hermes autonomous software factory that Yu-Kuan
# ABOUTME: didn't ask for but will love. Ranked by wow×feasibility; top 8 marked SPINE. Written 2026-07-03.

# Capabilities You Didn't Know You Wanted

**Context:** Grounding read of the fable plan (broker + factory supervisor + ledgers + triage + merge-gating spine), the AI-factory research (cron-driven plain-code supervisor, worktree-per-job, codex-review gate, layered budget enforcement), and hermes' existing primitives (cron routines since March, isolated subagents, RPC script injection, skill self-creation/self-improvement, FTS5 session search, Honcho user modeling, multi-model routing via `hermes model`).

**Design rule applied to every idea below:** must be buildable on 2026 agent tech, must ride primitives the plan already builds (broker, job ledger, worktree isolation, skill layer, cron host, comms layer), and must stay out-of-core + declarative. Nothing here requires a new runtime. The plan gives us the *machine*; these are the things that make the machine *feel alive, trustworthy, and compounding*.

Effort: S = <1 day, M = 2–4 days, L = ~1 week+. Wow: 1–10 gut-level "would he grin."

---

## THE 3AM EXPERIENCE (run hits a wall while he sleeps)

### 1. Rate-Limit Failover Ladder
When a worker hits a 429/quota wall on Claude Max or Codex, the supervisor doesn't stall until morning — it consults a declarative `model-ladder.md` (Claude Max → Codex sub → OpenRouter GLM 5.2 → Kimi → best-open) and re-dispatches the *same job with its accumulated context* to the next rung, tagging the job ledger with which model finished it. The morning briefing shows "T-14 finished on GLM 5.2 after Claude hit quota at 02:41 — cost delta +$0.06." This is the single highest-leverage 3am behavior: it converts a dead night into a productive one.
**Effort: M · Wow: 9 · Depends: job ledger, multi-model routing (exists), budget meter.**

### 2. Self-Healing Retry With Forensic Capture
On a worker crash or red test that a worker can't fix in N cycles, the supervisor snapshots a forensic bundle *before* killing the job: last 200 lines of worker transcript, the failing test output, `git diff` of the worktree, and the exact model/prompt. It then tries one bounded self-heal (re-plan from the failure, not blind retry) using the failure bundle as context. If self-heal fails, the job parks as `NEEDS_HUMAN` with the forensic bundle attached — so morning-you debugs in 30 seconds, not 30 minutes. No silent death, ever.
**Effort: M · Wow: 8 · Depends: job ledger, worktree isolation.**

### 3. Ambiguity Interceptor → Morning Question Queue
Before a worker burns tokens guessing, a lightweight pre-flight pass grades the task spec for ambiguity (undefined acceptance criteria, "or equivalent" deps, missing field names — the exact R17/R7 failure classes from your VIBE protocol). If ambiguity is high, the job does NOT start blind — it parks and files a crisp question into a `questions.md` queue with 2–3 proposed answers. You wake to "3 tasks are blocked on 4 questions" with tap-to-answer options, instead of 3 tasks built wrong. This is the "should I even be building this?" check made concrete.
**Effort: M · Wow: 9 · Depends: intake decomposition, comms layer.**

### 4. Budget Kill-Switch With Graceful Drain
A hard per-night $ ceiling (and per-repo sub-ceilings) enforced by the plain-code supervisor. On approach to 80% it stops *dispatching new* jobs but lets in-flight ones drain; on 100% it snapshots and parks everything cleanly. You wake to "stopped at $18.40 of $20 cap; 2 jobs parked mid-flight, resumable." No surprise $200 bill, ever — the documented nightmare of unsupervised fleets.
**Effort: S · Wow: 8 · Depends: budget meter (in plan), job ledger.**

### 5. Live Tail On Demand (Telegram `/watch T-14`)
Most nights you sleep. Some nights you wake at 2am curious. `/watch T-14` streams that worker's live tool-output to Telegram (rate-limited, last-line-wins), and `/steer T-14 "use the repo's existing Result type"` injects a redirect into the running worker without killing it. Optional and rare, but the *feeling* of being able to reach in and nudge the fleet from your phone in bed is pure delight.
**Effort: M · Wow: 7 · Depends: supervisor live-monitoring (Option B bolt-on), comms layer.**

---

## THE MORNING EXPERIENCE (what he wakes up to)

### 6. The Morning Standup Briefing
One Telegram/email message at wake time: what shipped (PRs ready), what's blocked (+ why, + the question), what it cost (total $ and cost-per-merged-PR trend arrow), and what's queued for tonight. Written in yk-voice, scannable in 20 seconds, every actionable item a tap. This is the daily artifact that makes the whole factory feel like a chief of staff reporting in, not a cron log you have to go read.
**Effort: M · Wow: 9 · Depends: job ledger, comms layer, cost meter.**

### 7. One-Tap PR Approval From Phone
Each ready PR arrives as a Telegram card: title, diff-stat, test result (green/red), codex-review verdict, and the *risk score*. Buttons: Merge / Request Changes / Open Full Diff / Ask a Question. Approve from a coffee line. The graduated-trust profile decides which PRs even need this (earned-auto-merge repos just notify; PR-only repos gate). This is the payoff moment of the whole autonomy design.
**Effort: M · Wow: 9 · Depends: broker approval surface (spine), merge-gating (spine).**

### 8. Demo Reel Of The Night
For any feature with a runnable surface, a post-build worker spins up the app in a sandbox, drives the new flow with the `browse` skill, and captures a 20-second screen recording (or annotated before/after screenshots for backend/API changes). The PR card embeds it. You *watch* the feature you slept through instead of reading a diff to imagine it. Nothing sells "the factory works" like seeing last night's feature move on your phone.
**Effort: L · Wow: 10 · Depends: browse skill (exists), sandbox runtime (exists), demo-capture skill.**

### 9. Decision Replay
A chronological, collapsible timeline of every consequential choice the fleet made overnight: "02:14 chose GLM 5.2 over Kimi for T-11 (cheaper, equal scorecard); 02:51 rejected worker's first impl (codex-review found N+1 query); 03:20 self-healed T-9's flaky test by …". Not raw logs — a *narrated* decision trace. Builds trust faster than anything, because you can audit the fleet's judgment and see where it's earning autonomy.
**Effort: M · Wow: 8 · Depends: job ledger with decision events, briefing skill.**

### 10. Ready-To-Merge Digest, Ranked By Confidence
When 6 PRs land overnight, don't dump 6 equal cards. Rank them: green-across-the-board low-risk changes first (batch-approve with one tap), risky/large/architecture-touching ones last (each needs a real look). Sorts your morning review time to where it matters. "Batch-approve the 4 safe ones?" is a genuine time-saver on a productive night.
**Effort: S · Wow: 7 · Depends: risk scoring (from code-review-graph impact analysis), briefing.**

---

## SELF-IMPROVEMENT LOOPS (every run makes the next better)

### 11. Per-Model Task-Type Scorecard
The factory logs, per completed job: model used, task type (bugfix/feature/refactor/test/docs), outcome (merged clean / needed fixes / rejected), review findings count, cost, wall-time. Over weeks this becomes a learned routing table: "for Python refactors, GLM 5.2 merges clean 78% at 1/5 the cost of Opus; for tricky async bugs, Opus wins." The model-ladder (idea #1) stops being hand-written and becomes *data-driven*. This is the compounding core of cost-aware multi-model routing.
**Effort: M · Wow: 9 · Depends: job ledger with outcome tracking.**

### 12. Post-Run Retrospective That Rewrites Its Own Skills
After each night, a retro worker reads the failure bundles and review findings, then proposes concrete edits to the factory's own skill/prompt/policy markdown: "workers kept forgetting to run migrations — add step to coder skill"; "codex-review flagged the same trust-boundary issue 3× — add a pre-check." Proposals arrive as a *diff you approve*, not silent self-modification (that's the FM-1 trap). The factory literally gets better at building software while you sleep. hermes already self-improves skills during use — this extends it to the factory's own operating skills.
**Effort: L · Wow: 10 · Depends: retro skill, skill self-improvement (exists), approval gate.**

### 13. Worker Eval Suite (Golden Tasks)
A growing set of golden tasks with known-good outcomes (drawn from your own merged PRs). Run new model/prompt configs against them before trusting them on real work. When you want to add Kimi to the ladder or try a new prompt, you *grade it first*: "Kimi scored 6/10 golden tasks, weak on multi-file refactors." Turns model adoption from vibes into evidence. This is the eval discipline your VIBE protocol demands, applied to the factory itself.
**Effort: L · Wow: 8 · Depends: scorecard, a curated golden-task set.**

### 14. Trust Ledger With Auto-Graduation Proposals
Per-repo, the ledger tracks: PRs merged, PRs merged-without-edits, human overrides, post-merge reverts. When a repo crosses a threshold (e.g. 20 clean auto-approvable merges, zero reverts), the factory *proposes* graduating it from PR-only to earned-auto-merge — with the evidence. You approve the trust bump; you never grant it blind. Autonomy is *earned and visible*, exactly your locked graduated-trust model, made self-driving.
**Effort: M · Wow: 9 · Depends: merge ledger, trust-policy.md, approval gate.**

### 15. Pattern Bank Of Past Solutions
Every merged solution gets distilled into a searchable pattern entry: problem shape → approach → the actual diff → gotchas. Before a new worker starts, it queries the bank ("have we solved auth-token-refresh in this repo before?") and reuses the pattern. hermes has FTS5 session search already — this is a *curated, factory-specific* layer on top, deduped and tagged. The 50th similar task takes a fraction of the tokens of the 1st.
**Effort: M · Wow: 8 · Depends: session search (exists), pattern-distill skill.**

---

## SELF-SUPERVISION (the fleet watches itself)

### 16. Supervisor-Of-Supervisors Drift Detection
A lightweight plain-code watchdog samples in-flight workers for drift signals: token-burn-without-progress (churning), scope creep (touching files outside the task's declared footprint — you already scope this in VIBE pre-flight), repeated identical tool calls (stuck loop), or editing guard/config files (the FM-1 signature). On drift, it pauses the worker and files a `DRIFT` alert. Cheap, plain-code, catches the "worker went rogue at 3am" class before it wastes the night.
**Effort: M · Wow: 8 · Depends: worker telemetry, supervisor.**

### 17. Scope Guard (Declared Footprint Enforcement)
Each task declares the files/dirs it may touch (Architect already identifies this in your protocol). The worker runs in a worktree where writes outside the footprint are flagged and require justification in the PR. Catches the "fixed the bug but also refactored 8 unrelated files" sprawl that makes PRs unreviewable. Turns your VIBE conflict-check pre-flight into a runtime rail.
**Effort: M · Wow: 7 · Depends: worktree isolation, task footprint declaration.**

### 18. "Should I Even Build This?" Sanity Gate
Before spawning, a fast pass checks: does this task duplicate an existing capability? Does it contradict a recent decision in the pattern bank or an ADR? Is it a known anti-goal? Occasionally you (or Jira) will queue something already done or already decided-against. A 5-second check saves a whole night's build on the wrong thing. The cheapest high-value supervision there is.
**Effort: S · Wow: 8 · Depends: pattern bank / codebase map, intake.**

### 19. Regression Sentinel (Post-Merge Watch)
After an auto-merged PR lands, a sentinel re-runs the repo's full suite + smoke test on main a few minutes later. If something breaks that the PR's own tests missed, it opens a revert PR *and* a fix task automatically, and pings you: "T-9's merge broke 2 tests on main — revert PR ready, fix task queued." Makes earned-auto-merge safe to actually grant, because the safety net is real.
**Effort: M · Wow: 8 · Depends: merge-gating, CI/test runner, sentinel cron.**

---

## INTAKE MAGIC (from raw idea to spec'd work)

### 20. PRD Interrogation → Decomposition Pipeline
Throw a PRD or rough brief at hermes; it runs your `prd-review`/`eng-stories` skills adversarially, surfaces ambiguities as questions (routed to the morning queue, idea #3), then decomposes into acceptance-test-first task cards with dependency edges — ready for the fleet. The intake magic you asked for: fuzzy idea in, buildable dependency-ordered queue out, with the questions it *couldn't* resolve flagged instead of guessed.
**Effort: M · Wow: 9 · Depends: existing prd/stories skills, ambiguity interceptor.**

### 21. Voice Note → Spec'd Task By Morning
Send a voice memo to Telegram at 11pm ("hey, the export button should remember the last format the user picked"). hermes transcribes (voice transcription exists), runs it through the interrogation pipeline, and by morning it's a proper task card — or a question if it was too vague. The chief-of-staff-as-mission-control moment: mumble an idea into your phone walking to bed, wake to a spec. This is the single most *magical-feeling* capability in the list.
**Effort: M · Wow: 10 · Depends: voice transcription (exists), interrogation pipeline (#20).**

### 22. Jira Write-Back (Factory Keeps The Board Honest)
The factory doesn't just read Jira — it writes back: moves tickets to In Progress when a worker starts, drops the PR link and a plain-English summary on completion, sets estimates from actual build time, and flags blockers with the specific question. Your board becomes a live mirror of the fleet with zero manual PM overhead. You already have `jira-update` and pm_os Graph tooling — this wires them to the factory lifecycle.
**Effort: M · Wow: 8 · Depends: jira-update skill (exists), job ledger, broker (writes gated).**

### 23. Watch-This-Repo's-Issues Mode
Point the factory at a repo's issue tracker with a label filter (`good-first-issue`, `factory-ok`). It polls, triages new matching issues through intake, and either auto-drafts a PR (earned-trust repos) or queues them for your morning go/no-go. Overnight, your own OSS side-projects or backlog repos quietly get chipped away. "I woke up and 3 stale issues had PRs" is a delight you'll feel weekly.
**Effort: M · Wow: 8 · Depends: intake pipeline, gh polling cron, trust-policy.**

### 24. Repo Onboarding Ritual
First time you point the factory at a new repo, it runs a onboarding pass: builds a codebase map (you have `codebase-mapping` + code-review-graph), detects the test command / lint / build, finds the contribution conventions, and writes a `factory-repo-profile.md`. Every subsequent job in that repo is smarter for it. The first run is slow; every run after is fast — compounding from job #1.
**Effort: M · Wow: 7 · Depends: codebase-mapping (exists), code-review-graph (exists).**

---

## COMPOUNDING ASSETS (the flywheel)

### 25. Living Codebase Map (Auto-Refreshed)
The code-review-graph is rebuilt incrementally after each merge, so blast-radius/impact analysis on the *next* task is always current. Risk scores (idea #10), scope guards (#17), and sanity gates (#18) all draw from it. The map isn't a one-time artifact — it's a living asset that sharpens with every PR the factory lands.
**Effort: S · Wow: 7 · Depends: code-review-graph (exists), post-merge hook.**

### 26. Reusable Skill Library That Grows Itself
The retro loop (#12) and pattern bank (#15) feed a growing library of factory skills. hermes already auto-creates skills after complex tasks — here they're *curated into a versioned library* with usage stats, so the best ones get promoted and dead ones pruned. Month 3's factory has a materially richer skill set than month 1's, without you writing a single skill by hand.
**Effort: M · Wow: 8 · Depends: skill self-creation (exists), retro loop, usage telemetry.**

### 27. Cross-Repo Knowledge Transfer
A pattern solved in repo A (say, a clean SSE-schema contract) becomes available to repo B via the cross-repo search the code-review-graph already supports. "We solved streaming-with-backpressure in hermes; apply the same shape here." Your accumulated solutions stop being siloed per-repo. This is where a personal factory starts to feel smarter than any single junior dev — it remembers across the whole portfolio.
**Effort: M · Wow: 8 · Depends: pattern bank, cross-repo graph search (exists).**

---

## DELIGHT (things that make him grin)

### 28. Weekly Factory Retro: "What I Learned"
Every Friday, a warm, first-person retro from the factory: "This week I merged 14 PRs across 3 repos for $6.20. I learned GLM 5.2 is your best-value refactorer. I got stuck twice on the same auth pattern — I've added a skill so I won't next time. My cost-per-merged-PR dropped from $0.71 to $0.44." Personality + real metrics + visible self-improvement. This is the artifact you'll screenshot and show people.
**Effort: M · Wow: 9 · Depends: scorecard, trust ledger, retro skill.**

### 29. Cost-Per-Merged-PR Trend Board
A single north-star number, trending down over time, on a tiny dashboard (or in the weekly retro). As the pattern bank fills, the scorecard sharpens routing, and skills compound, this number should *drop* — visible proof the flywheel is real. Watching $0.71 → $0.44 → $0.31 week over week is the most motivating metric a factory owner could have.
**Effort: S · Wow: 8 · Depends: cost meter, merge ledger.**

### 30. Model Leaderboard
A live standings table from the scorecard: models ranked by merge-clean-rate and cost-efficiency, per task type, with movement arrows. "GLM 5.2 ↑ overtook Codex for refactors this week." Turns your multi-model strategy into a spectator sport and — genuinely — helps you decide where to spend subscription vs overflow budget.
**Effort: S · Wow: 7 · Depends: scorecard.**

### 31. Named Worker Personas (Optional Flavor)
Workers get stable playful names per repo ("Mercury handles hermes, Atlas handles pm_os"). The briefing reads "Atlas shipped 3, Mercury got stuck once." Pure delight, near-zero cost, and it makes the fleet feel like a *team* you manage rather than anonymous processes. Trivial to add, disproportionately charming.
**Effort: S · Wow: 6 · Depends: job ledger (name field).**

### 32. "Explain This Merge Like I've Been Away A Week"
A command that summarizes everything the factory did to a repo since you last looked — narrative, not changelog. "While you were heads-down on the deck, I shipped the export-format memory, fixed the flaky calendar test, and refactored the auth module (that one's still in draft, wanted your eyes)." Perfect for Monday mornings or after a stretch away. Re-onboards you to your own codebase in 60 seconds.
**Effort: M · Wow: 8 · Depends: merge ledger, decision replay, briefing skill.**

---

## RANKING (wow × feasibility)

Feasibility inverse of effort (S=3, M=2, L=1). Score = wow × feasibility. SPINE = belongs in the core plan.

| # | Name | Wow | Eff | Score | SPINE |
|---|------|-----|-----|-------|-------|
| 6 | Morning Standup Briefing | 9 | M(2) | 18 | ★ SPINE |
| 7 | One-Tap PR Approval From Phone | 9 | M(2) | 18 | ★ SPINE |
| 11 | Per-Model Task-Type Scorecard | 9 | M(2) | 18 | ★ SPINE |
| 14 | Trust Ledger + Auto-Graduation | 9 | M(2) | 18 | ★ SPINE |
| 20 | PRD Interrogation → Decomposition | 9 | M(2) | 18 | ★ SPINE |
| 1 | Rate-Limit Failover Ladder | 9 | M(2) | 18 | ★ SPINE |
| 3 | Ambiguity Interceptor → Question Queue | 9 | M(2) | 18 | ★ SPINE |
| 21 | Voice Note → Spec'd Task | 10 | M(2) | 20 | ★ SPINE |
| 4 | Budget Kill-Switch + Drain | 8 | S(3) | 24 | (near-spine; cheap+critical) |
| 18 | "Should I Even Build This?" Gate | 8 | S(3) | 24 | |
| 29 | Cost-Per-Merged-PR Trend Board | 8 | S(3) | 24 | |
| 10 | Ready-To-Merge Digest (ranked) | 7 | S(3) | 21 | |
| 25 | Living Codebase Map | 7 | S(3) | 21 | |
| 30 | Model Leaderboard | 7 | S(3) | 21 | |
| 31 | Named Worker Personas | 6 | S(3) | 18 | |
| 8 | Demo Reel Of The Night | 10 | L(1) | 10 | (high-wow, defer) |
| 12 | Retro Rewrites Own Skills | 10 | L(1) | 10 | (high-wow, defer) |
| 2 | Self-Healing + Forensic Capture | 8 | M(2) | 16 | |
| 9 | Decision Replay | 8 | M(2) | 16 | |
| 15 | Pattern Bank | 8 | M(2) | 16 | |
| 16 | Drift Detection | 8 | M(2) | 16 | |
| 19 | Regression Sentinel | 8 | M(2) | 16 | |
| 22 | Jira Write-Back | 8 | M(2) | 16 | |
| 23 | Watch-Repo-Issues Mode | 8 | M(2) | 16 | |
| 26 | Self-Growing Skill Library | 8 | M(2) | 16 | |
| 27 | Cross-Repo Knowledge Transfer | 8 | M(2) | 16 | |
| 28 | Weekly Factory Retro | 9 | M(2) | 18 | (delight anchor) |
| 32 | "Explain This Merge" | 8 | M(2) | 16 | |
| 5 | Live Tail On Demand | 7 | M(2) | 14 | |
| 17 | Scope Guard | 7 | M(2) | 14 | |
| 24 | Repo Onboarding Ritual | 7 | M(2) | 14 | |
| 13 | Worker Eval Suite | 8 | L(1) | 8 | (needs golden set first) |

### The 8 SPINE candidates (rationale)

These form a self-reinforcing loop, not a feature list: **intake** (#20, #21) feeds work in cleanly; **ambiguity interceptor** (#3) stops garbage from becoming garbage builds; the **failover ladder** (#1) keeps the night productive; the **scorecard** (#11) turns every job into routing intelligence; the **trust ledger** (#14) turns clean history into earned autonomy; and the **briefing** (#6) + **one-tap approval** (#7) are the two daily-touched surfaces where you actually experience the factory. Cut any one and the loop leaks. Everything else in the list is an amplifier on this spine.

Two S-effort items (#4 budget kill-switch, #29 trend board) are cheap enough and load-bearing enough that they belong in the spine's first cut too — they're the safety floor and the north-star metric respectively. And #28 (weekly retro) is the delight anchor that makes the whole thing feel worth having.
