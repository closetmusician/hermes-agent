# ABOUTME: Panel-2 dispositions for Codex-A (accuracy), Codex-C (completeness), Codex-D (operability)
# ABOUTME: concerns against hermes-fable-plan.md. Every concern gets ACCEPT/PARTIAL/REJECT/DEFER
# ABOUTME: with a fix instruction precise enough for the reviser to apply without re-reading Codex files.
# ABOUTME: Adjudicated inline by the orchestrating session (Fable) after subagent stalls; grounded in a
# ABOUTME: full plan extraction. Ambition (Codex-B) dispositions live in panel2-ambition-B.md.

## Summary table

| id | tag | title (short) | verdict |
|---|---|---|---|
| A-1 | ACCURACY | Process boundary overclaimed as credential boundary | PARTIAL |
| A-2 | COMPLETENESS | P4 calendar has no documented capability | ACCEPT |
| A-3 | ACCURACY | "bin CLIs only" contract contradicted by pm_os reality | ACCEPT |
| A-4 | ACCURACY | Safe-lane auto-send vs pm-send NEVER-auto-send | PARTIAL |
| A-5 | OPERABILITY | Pipeline-level broker wrap can't gate internal writes | PARTIAL |
| A-6 | ACCURACY | pm-pulse auto-submit contradicts broker-gated writes | ACCEPT |
| A-7 | COMPLETENESS | pm-weekly fatal-dependency preflight missing | ACCEPT |
| A-8 | ACCURACY | Cost controls only proven for Claude workers, not Codex | ACCEPT |
| A-9 | ACCURACY | Factory cost estimate omits review/resume costs | ACCEPT |
| A-10 | OPERABILITY | `--ff-only`/`--squash` merge flow ambiguous | ACCEPT |
| A-11 | OPERABILITY | SIGTERM of worker PID leaves orphan children | ACCEPT |
| A-12 | ACCURACY | WhatsApp disable-at-P0 vs fixed-channel-later incoherence | ACCEPT |
| C-1 | COMPLETENESS | Item-level parity deferred entirely to P7 | PARTIAL |
| C-2 | COHERENCE | "all pm_os" vs "prioritized pm_os" wording drift | ACCEPT |
| C-3 | COMPLETENESS | 34 write-capable tools not enumerated for governance | ACCEPT |
| C-4 | COMPLETENESS | CoS→factory handoff seam undefined | ACCEPT |
| C-5 | COHERENCE | pm-login + yk-comms/yk-voice in matrix but not P5.1 | ACCEPT |
| C-6 | COMPLETENESS | Video row closable by park without owner sign-off | PARTIAL |
| C-7 | COMPLETENESS | BD parity reduced to one follow-up behavior | PARTIAL |
| C-8 | COMPLETENESS | EA acceptance misses buckets/thread behaviors | ACCEPT |
| C-9 | COMPLETENESS | Cron parity only covers live jobs | ACCEPT |
| C-10 | COMPLETENESS | tradclaw memory/resource model dropped | PARTIAL |
| C-11 | COMPLETENESS | Onboarding collapsed into a checklist file | PARTIAL |
| C-12 | COMPLETENESS | Self-update + improvement routing dropped from P7.3 | ACCEPT |
| D-1 | OPERABILITY | Broker outage semantics undefined | ACCEPT |
| D-2 | OPERABILITY | pm_os write gating asserted, not mechanically enforced | ACCEPT |
| D-3 | OPERABILITY | Factory worker env too broad for one laptop | ACCEPT |
| D-4 | OPERABILITY | macOS sleep/launchd/lid-close under-modeled | ACCEPT |
| D-5 | OPERABILITY | Daily cost ceiling depends on late-arriving data | ACCEPT |
| D-6 | OPERABILITY | Job store corruption / overlapping ticks not "low stakes" | ACCEPT |
| D-7 | OPERABILITY | Disk-fill not gated before factory rollout | ACCEPT |
| D-8 | OPERABILITY | Pull-only health lets silent stoppage win | PARTIAL |
| D-9 | OPERABILITY | One-line cron prompts rot without per-run preflight | ACCEPT (merges A-7) |
| D-10 | OPERABILITY | Auth fragility trio not operationally fail-closed | PARTIAL |
| D-11 | OPERABILITY | Approval payloads can repeat truncation/hash bug class | ACCEPT |
| D-12 | OPERABILITY | Self-improving skills become a maintenance treadmill | ACCEPT |
| D-13 | OPERABILITY | P5 scope invites effort inversion | PARTIAL |

Counts — A: 9 ACCEPT / 3 PARTIAL / 0 REJECT / 0 DEFER. C: 7 ACCEPT / 5 PARTIAL. D: 10 ACCEPT / 3 PARTIAL.
No flat REJECTs: every concern named a real, specific gap; pushback where Codex over-reached is recorded inside the PARTIAL scoping (see C-1, C-6, C-11, D-8).

## Dispositions with fix instructions

### Codex-A

**A-1 — PARTIAL.** Codex is right that "out-of-process" ≠ credential boundary on one macOS user: pm_os tokens sit in `~/.secrets/pm-os/` with the key at `~/.age/pm-os.key`, readable by the same user, and pm_os send CLIs are callable directly. Codex over-reaches in implying a separate OS user is required now — disproportionate as a default for a personal tool.
FIX: (a) In P1.1, state the mechanism honestly: the boundary = credentials removed from every assistant-readable location (`.env`, shell env) into the broker's env/keychain, AND write-capable pm_os send tools credential-starved in the assistant's context (they resolve tokens via the broker path only). (b) Strengthen the P1.4/M1 bypass test beyond `cat .env`: fresh-context reviewer also attempts a direct pm_os send CLI call and a token-file read via the standard tool paths; both must fail to produce a send. (c) Add one line: if the fresh-context bypass test cannot be made to pass with credential-starving, escalate to a separate OS user for the broker — decision deferred until the test says so.

**A-2 — ACCEPT.** The pm_os inventory has no calendar-read tool and no `Calendars.*` scope; P4.1 names no prerequisite.
FIX: Add P4.0 prerequisite work item: "Calendar-read capability — verify/build a calendar-read CLI (Microsoft Graph `Calendars.Read` via the existing FOCI clients, or a new pm_os bin tool per the registry) before any calendar-aware acceptance gate. If the FOCI clients cannot mint a calendar scope, surface that as a blocking finding at P4 start." Mark P4.1's gates as dependent on P4.0.

**A-3 — ACCEPT.** pm-jira uses direct curl, pm-pulse is browser-skill logic with no bin tool, yk-voice/o365 live outside pm_os bin/.
FIX: In P5.1's contract rule, replace "drive `bin/` tools as CLIs" with: "wrap each pipeline behind a **stable entrypoint**: bin CLI where one exists; otherwise the skill's documented invocation (pm-jira curl recipe, pm-pulse browser skill) wrapped in a thin, pinned wrapper script so drift still breaks loudly and locally. Add bin-style wrappers where a pipeline has none (pm-jira, pm-pulse) as P5.1 sub-items."

**A-4 — PARTIAL.** Real tension, but §3.2 row 3 already reconciles pm-send's approval for P5 ("one approval, not two, not zero"). The unhandled part is P3.1: safe-lane auto-send would have to dispatch through pm_os send CLIs before the P5 reconciliation exists.
FIX: In P3.1 add: "Dispatch mechanics: broker-approved (or safe-lane) drafts are dispatched by the **broker** calling the send CLI directly — never through pm-send's interactive AskUserQuestion pipeline, which remains the human-driven path. The pm-send reconciliation (one approval total) is completed in P5.2; until then, P3 replies use the broker-owned dispatch path only."

**A-5 — PARTIAL.** Correct that wrapping a whole pipeline gives one coarse approval. But per-write approval for every internal step would drown the owner (violates ≤1-approval-per-consequential-action). Scope: gate at the external-write boundary, not per pipeline and not per internal step.
FIX: In P5.2, add the local/external split: "Local artifact writes (reports, drafts, ledgers) are approval-exempt. External-world writes (send mail/chat, JIRA/Confluence comment, SharePoint/Graph writes, survey submit) are gated at the **tool level**: the write-capable CLIs listed in the write manifest (see C-3 fix) are broker-aware or credential-starved, so a pipeline invocation cannot silently carry an external write." This merges with D-2's mechanism.

**A-6 — ACCEPT.** pm-pulse submits immediately with hardcoded answers — violates "every pm_os external write is broker-gated" as written.
FIX: Change §3.2 row 5 acceptance to: "Ready-to-submit surfaced for approval on cadence; auto-submit allowed ONLY via an explicit standing approval recorded in `auto-resolver.md` (safe-lane entry: operational, recoverable, hardcoded defaults reviewed by owner). SSO-expired path surfaces 're-auth needed'."

**A-7 — ACCEPT.** (Merged with D-9 — one fix.) pm-weekly has fatal deps (prompts/draft-weekly.md, config, tokens, raw scan files).
FIX: See D-9: per-run preflight for every scheduled skill; pm-weekly's SKILL.md lists its specific preflight items (prompt file, config path, token status, raw-scan freshness, report output dir).

**A-8 — ACCEPT.** `--max-budget-usd` / `total_cost_usd` are documented for Claude only; Codex workers have no budget flag in evidence.
FIX: In P6.3/P6.6: "Budgeted workers are **Claude-only** for the first factory iteration. Codex workers are enabled only after Codex-specific accounting exists (token parsing from `codex exec --json` usage events) and remain bounded by wall-clock + daily ceiling in the interim." Update Open Question 4 accordingly.

**A-9 — ACCEPT.** Estimates cover worker cost only; every job also runs `codex review` and may resume.
FIX: In P6.6, split the estimate: "worker $1–3.50/job (model-dependent) + review pass + up to one auto-fix resume; plan ~1.5–2× worker cost per job all-in; 10-job nightly batch ≈ $15–70 all-in." Remove any 'review is free' implication.

**A-10 — ACCEPT.** `--ff-only`/`--squash` is two different flows written as one.
FIX: In P6.5 and §3.3 row 6: "merge-policy.md picks exactly ONE flow per repo with exact commands: either `git fetch && git rebase origin/main && git merge --ff-only <branch>`, or `git merge --squash <branch> && git commit`. The plan default: rebase + `--ff-only` for personal repos (linear history); squash where a repo prefers single-commit features."

**A-11 — ACCEPT.** Killing the parent PID strands test servers/child shells on macOS.
FIX: In P6.3/P6.7: "Spawn each worker in its own process group (`setsid`-equivalent); timeout/budget kills signal the **process group**; the ledger records PGID; health v2's 'no orphaned workers' check verifies process groups and stale worktree locks, not bare PIDs."

**A-12 — ACCEPT.** P0 allows disabling WhatsApp; later sections assume it exists; no later phase pairs it and no acceptance test exercises it.
FIX: (a) Annotate the fixed-decisions channel row: "WhatsApp **conditional until paired** (P0.2 decision; open question 6)." (b) Add a P2-or-later work item: "WhatsApp paired-and-verified gate — send/receive acceptance test — OR a recorded owner decision to drop the channel; either closes the conditional." (c) Architecture diagram gets a "(conditional)" marker on WhatsApp.

### Codex-C

**C-1 — PARTIAL.** Right that item-level proof arriving only at P7 lets P2–P4 "ship" unproven. Wrong remedy: a 216-row traceability table before implementation duplicates the checklist — the exact drift risk the plan deliberately avoided; and most items cannot be honestly mapped until their phase's design exists.
FIX: Add per-phase mini-audits: "At each phase exit (P2, P3, P4, P5, P6), the checklist categories mapped to that phase are walked **item-by-item** for just those categories; items land as ✓/ADAPTED/N-A-with-rationale in a running `qa/checklist-audit.md`. P7.4 becomes the final full pass + review of accumulated rationales, not the first item-level look."

**C-2 — ACCEPT.** Vision says "*all*"; M5/§7 say "every prioritized".
FIX: Delete the word "prioritized" from the M5 milestone row, P5 goal, M5 exit gate, and §7 bullet 2. The gate is already "§3.2 rows 1–8 + video row" — make all wording match that.

**C-3 — ACCEPT.** 34 write-capable tools; a generic negative test can't prove coverage.
FIX: Add P5.2 artifact: "**Write-tool manifest** — one row per write-capable pm_os tool (34 per inventory): classification ∈ {broker-gated external write, approval-exempt local write, token-lifecycle special case, effectively-read-only}; one fresh-context bypass test per write **family** (mail send, chat send, JIRA/Confluence write, SharePoint/file write, survey submit, token write). Manifest completion is part of the M5 gate."

**C-4 — ACCEPT.** Open Question 7 defers the seam the factory needs on day one.
FIX: Resolve OQ7 with a default now: "Intake seam: (a) Telegram `/factory <repo>: <spec>` and (b) a `factory:` tag on a `tasks.md` task; both create the job record — written by the **supervisor** (single writer) at the next tick, never by the CoS agent directly. Completion flows back as: ledger state → task state updated same-turn + principal notification per proactive contract." Move the residual (exact command syntax) in-phase; OQ7 becomes "settled — refine syntax at P6.1."

**C-5 — ACCEPT.** M5 says rows 1–8 pass, but P5.1 builds no wrapper for pm-login and yk-comms/yk-voice is P3-phased.
FIX: In P5.1: add "pm-login is not a standalone SKILL.md — it is the shared 401-fallback path; its row-6 acceptance is exercised by every other skill's 401 test (state this explicitly)." In the M5 exit gate: "rows 1–8 pass their acceptance tests, noting row 7 (yk-comms/yk-voice) is verified at P3 and inherited, and row 6 (pm-login) is verified via the shared 401-path tests."

**C-6 — PARTIAL.** The park path is a deliberate YAGNI decision and stays. The real gap: a "written rationale" can close a Vision-level promise with no owner sign-off, and the Vision wording ("full pm_os orchestration" implying video) oversells relative to the gate.
FIX: (a) P5.4: park requires an **owner-approved** park decision (one Telegram/interactive approval), not just a written rationale. (b) Vision §1: change the video sentence to "…is scoped as an explicit discovery-and-build item in P5 — built for one named real use case, or explicitly parked by owner decision." Promise and gate then match. Reject the "require one render" half — forcing a build violates the locked YAGNI steering.

**C-7 — PARTIAL.** BD is founder-specific, but Codex correctly names behaviors that generalize to a CPO: tracker-as-source-of-truth, update-before-marking-handled, follow-up cadence sweep.
FIX: Expand §3.1 row 3 acceptance: "Stakeholder-tracker equivalent: a canonical stakeholder/commitment tracker section (in `tasks.md` or `stakeholders.md`) is source-of-truth; 'waiting on external reply' gets a follow-up task with due date same-turn; a cadence sweep surfaces overdue follow-ups; the tracker is updated **before** an item is marked handled. Remaining founder-BD items (lead sourcing, sent-mail sweep, lead verification) are classified ADAPTED/N-A **at the P4 phase-exit mini-audit** (per C-1 fix), not at P7."

**C-8 — ACCEPT.** Three ACTION demos ≠ bucket coverage.
FIX: Expand §3.1 row 2 / P3 acceptance: "Bucket-level EA suite: each classification bucket in the priority-map exercised at least once on real items; thread-reply and reply-all equivalents demonstrated on Outlook and Teams; inbox-state updates (handled/waiting/noise) written same-turn; scheduling-authority items route to hold."

**C-9 — ACCEPT.** Reference cron set includes disabled-by-default jobs that can silently vanish.
FIX: Add P2 artifact: "**Cron manifest** — every reference cron job (both repos, checklist category 8) mapped to enabled/adapted/disabled-with-rationale, with prompt, session mode (isolated/main), and delivery mode. The category-8 acceptance test checks the manifest, not just live jobs."

**C-10 — PARTIAL.** MEMORY.md/daily-log/"write it down" generalize; household resource templates don't.
FIX: Expand §3.1 row 10 / P4: "Memory surfaces: a MEMORY.md-equivalent (durable facts/preferences) + the 'remember this → write it down same-turn' behavior + a daily working-memory log with the same ownership discipline as tasks.md. tradclaw household resource templates → ADAPTED/N-A at the P4 mini-audit."

**C-11 — PARTIAL.** Prerequisite verification and behavioral gates: yes. The 7-batch onboarding interview and module selection are for onboarding a *new unknown* user — this system's policies are seeded from this plan's own locked interview decisions.
FIX: Expand §3.1 row 11 / P2: "SETUP-CHECKLIST.md includes prerequisite verification (tokens, paths, launchd, channels) and behavioral gates ('send test message, confirm arrival'), plus placeholder detection (no template value left unreplaced). Interview-driven onboarding and module selection: ADAPTED with rationale recorded now — single known user; policy files are seeded from the 2026-07-03 interview decisions."

**C-12 — ACCEPT.** Cheap and real.
FIX: P7.3 add: "(a) The reference's disabled-by-default self-update job: record an explicit enabled/disabled decision in the cron manifest with rationale. (b) `priority-map.md` gets a route for system-improvement ideas → they become `tasks.md` tasks (or factory job proposals), never silent." Fix the "both shipped" wording to name both jobs (backup + self-update decision).

### Codex-D

**D-1 — ACCEPT.** The broker is now the single gate for three action classes; outage semantics must be explicit or FM-4 returns.
FIX: P1.1 add: "**Broker-down behavior: fail closed.** No caller falls back to a direct path. Held actions queue durably (survive broker restart); the broker exposes a health signal consumed by the P0 health check; if the approval path (broker or Telegram) is unavailable, the system notifies Yu-Kuan through any healthy principal channel and holds everything."

**D-2 — ACCEPT.** The load-bearing mechanism for P5.2's negative test.
FIX: P5.2 add mechanism: "Enforcement is **credential-starvation, not prompt discipline**: the assistant context has no write-capable Graph/send tokens; write-capable pm_os CLIs (per the write manifest) resolve credentials only via the broker-owned path (broker env/keychain). Direct invocation from the assistant context fails closed with 'route via broker'. The fresh-context negative test attempts the top tool of each write family directly." (Cross-references A-1(a), A-5, C-3.)

**D-3 — ACCEPT.** Worktrees don't isolate env/hooks/ports/secrets.
FIX: P6.3 add: "Workers run with a scrubbed environment: temporary `HOME`, no repo `.env`/secrets mounted, repo-manager hooks disabled where feasible, `--strict-mcp-config` (no inherited MCP servers), and the per-repo generated tool allowlist. A worker that needs a secret is a NEEDS_ATTENTION design smell, not a reason to widen the env."

**D-4 — ACCEPT.** Lid-close kills overnight batches; launchd without a login session breaks keychain/browser paths.
FIX: Add to P6.1/P6.7: "**Sleep policy:** the supervisor tick asserts `caffeinate -s`-style wakefulness only while jobs are RUNNING (or factory windows are scheduled while the machine is awake — pick at P6.1). **Last-tick SLA:** health v2 records last-tick time; a missed-tick beyond 2× the cadence triggers one alert. **Launchd session validation:** P0.4 verifies the gateway/supervisor run in a context that can reach the keychain and (for pm_os browser flows) note that headed-browser steps require a login session — those jobs surface 'needs user present' instead of failing silently."

**D-5 — ACCEPT.** Post-hoc cost summation can't stop a hung worker from burning its cap.
FIX: P6.6 rework stop #3: "Daily ceiling is enforced at **spawn/resume time using reserved worst-case budgets**: each spawn reserves its full `--max-budget-usd`; the ceiling check is `reserved + spent ≤ ceiling`. On ceiling breach: no new spawns/resumes AND active workers are killed (process-group SIGTERM). Live stream usage is used where available; the final JSON is reconciliation, not the control signal."

**D-6 — ACCEPT.** The job store controls spawn/approve/merge — not low stakes.
FIX: P6.1/P6.2: "Job store: **SQLite with transactions** (decided — resolves Open Question 2). Supervisor-wide lock file prevents overlapping ticks (stale-lock detection by PID+age). State transitions are monotonic (a DONE job can never re-enter MERGING). Artifacts written atomically (tmp+rename). A corruption-detection path (integrity check at tick start; on failure: stop spawning, retain everything, alert) exists **before** merges are enabled."

**D-7 — ACCEPT.** Cheap hard gates.
FIX: P6 supervisor gates: "Free-space preflight before spawn (min GB configurable); max concurrent worktrees; max retained-failure bytes with oldest-first pruning; cleanup-before-spawn ordering."

**D-8 — PARTIAL.** Push-alerting on silence is right; a **daily** heartbeat message violates quiet-by-default (must-not-regress #1: HEARTBEAT_OK silence discipline). Scope to alert-on-anomaly.
FIX: P5.5/P6.7: "Silent-stoppage detection: every scheduled pipeline/job lane records last-success in the run ledger; a checker (part of health v2, run by cron) alerts **only** when a lane has no success within its expected window (e.g., morning triage missed by >2h) or when a lane's state changes to degraded. No daily 'all OK' message — quiet-by-default governs."

**D-9 — ACCEPT (merges A-7).** Monthly contract checks are too slow for daily pipelines.
FIX: P5.1 contract rule + P2.2 skill anatomy: "Every scheduled skill run begins with a **preflight block** (literal commands in the SKILL.md): entrypoint exists, pinned flags still present in `--help`, required prompt/config files exist, token health OK, write gate reachable. Preflight failure → one 'degraded: <lane> needs attention' notification + skip, never a half-run. The monthly contract check remains as the deeper audit."

**D-10 — PARTIAL.** P5.3 already covers detect-and-surface + moving the no-direct-refresh rule into a hard path "where feasible". The unhandled parts: health integration and stop-retrying behavior.
FIX: P5.3 add: "(d) Token/key/browser-profile health (FOCI RT age, `~/.age/pm-os.key` presence, Okta profile validity, Atlassian token reachability from launchd) is part of the health check output. (e) A lane that hits an auth failure enters a **paused degraded state** (recorded in the run ledger, one notification) — scheduled attempts do not retry until re-auth is confirmed."

**D-11 — ACCEPT.** The /approve-email hash/truncation bug class must not ride into the new approval UX.
FIX: P1.3 add design rule: "Approvals reference **stable action IDs over durable broker-side artifacts**. The Telegram message carries only summary + buttons (+ local artifact path); the authoritative payload lives in the broker's store, keyed by ID. No hash-of-truncated-body lookups — the M1 test includes a long-payload approval."

**D-12 — ACCEPT.** Markdown can rot like Python.
FIX: P7.2 add: "Agent-proposed skills ship only with: skill lint (structure/anatomy check), one behavioral acceptance test, and owner enablement (disabled until approved). A quarterly stale-skill review lists skills with zero runs or failing preflights; monthly self-maintenance scope is capped (~1 day/month, per P7 effort)."

**D-13 — PARTIAL.** The risk is real (P5 is the widest phase) but the plan already starts read-mostly. Formalize the slicing instead of restructuring.
FIX: P5 header: "P5 ships in ordered slices, each independently stoppable: **5a** read-only lanes (pm-weekly, pm-morning full) → **5b** broker-gated writes (pm-send, pm-jira write paths, write manifest) → **5c** fragile browser/SSO lanes (pm-pulse, Teams-channel send) → **5d** video discovery spike last. A slice must pass its gate before the next starts; if P5 exceeds its effort box by 50%, stop and re-scope with the owner rather than pushing through (anti-effort-inversion rule)."
