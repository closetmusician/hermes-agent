# Hermes Fable Plan — Comprehensive Personal Chief-of-Staff Assistant

**Date:** 2026-07-03
**Status:** REVISED DRAFT — adversarial review applied, second-order check clean; pending owner approval
**Provenance:** Synthesized from the evidence files in `evidence/` (`pmos-capability-inventory.md`, `clawchief-feature-checklist.md`, `ai-factory-research.md`, `v1-plan-critique.md`, plus the original `diagnosis.md`/`comparison.md`). This plan **extends** `remediation-plan.md` (the v1 plan): v1's Phases 0–3 are preserved as the reliability spine, its Phase 4 is trimmed, and three new phases (P5–P7) are appended per the Track D critique. No code is written under this document — plan only.
The adversarial gauntlet ran 2026-07-03 (4 Codex agents + Claude adjudication); dispositions are in Appendix A.

**Framing (locked):** Personal single-user tool on Yu-Kuan's own macOS machine, accounts, and repos. Capability-first; safeguards proportionate to a personal tool — the goal is predictability ("don't surprise me"), not adversarial defense. Worktree isolation + approve-before-merge suffices for the build assistant; a simple approval step suffices for outbound messages.

**Fixed decisions (locked — do not re-litigate):**

| Dimension | Decision |
|---|---|
| Foundation | Hybrid reset onto a clean upstream hermes checkout; reuse ideas from the fork, don't maintain it |
| Timeline | Months of depth, phased for fast incremental value — each phase ships a usable slice |
| Autonomy | Propose-first + 5-condition safe lane |
| Channels | Telegram + Teams/Outlook (via pm_os) + WhatsApp (**conditional until paired** — P0.2 decision, open question 6; closed by P2.4). Yahoo deprioritized; Discord off |

**Governing principle (from v1, unchanged):** Behavior lives in declarative, single-owner markdown files; the send capability lives out-of-process; everything is additive and out-of-core so upstream merges stay cheap. Every time you are about to add logic to a god-file or a guard-the-assistant-from-itself patch, stop — that is the pattern that failed (FM-1/FM-2/FM-3). Put the behavior in a markdown skill/policy file and the capability behind a process boundary. This principle generalizes cleanly to the two new capabilities: the build-job ledger and merge policy are just more single-owner files; merge-gating is just another out-of-process capability boundary.

---

## 1. Vision & scope

**Baseline CoS assistant.** Everything clawchief/tradclaw do, adapted to Yu-Kuan's CPO context: inbox/chat triage across Teams/Outlook (via pm_os) delivered to Telegram; a `priority-map.md` mapping senders/topics to action tiers; an auto-resolver that handles only the safe, routine, easily-reversible items itself and drafts everything else for one-tap approval; a single canonical `tasks.md`; meeting-notes ingestion with a processed ledger; calendar-aware meeting prep; and a quiet-by-default proactive contract (HEARTBEAT_OK silence, at most one nudge, never repeat) — because notification fatigue is the documented #1 killer of autonomous assistants (`evidence/clawchief-feature-checklist.md`, Category 16). The normative definition of "everything they do" is the 216-item checklist in `evidence/clawchief-feature-checklist.md`.

**Build assistant ("AI factory").** A distinct subsystem that supervises multiple headless `claude -p` / `codex exec` coding sessions drafting software changes on isolated git worktrees/branches of Yu-Kuan's own repos: intake a task, spawn a budget-capped worker, monitor it, run the repo's own tests, get an independent second-model review (`codex review`), surface diff + test result + review findings to Telegram for a one-tap approval, then merge serially behind a per-repo lock. "A team of tireless junior devs working on branches" — Yu-Kuan reviews and merges. The supervisor is plain code, not an LLM (`evidence/ai-factory-research.md` §4 recommendation), so it cannot exhaust context or burn tokens babysitting.

**Full pm_os orchestration.** Hermes reliably drives *all* of `~/Code/pm_os` — the 8 documented pipelines (pm-morning, pm-weekly, pm-send, pm-jira, pm-pulse, pm-login, yk-comms/yk-voice, glean) plus arbitrary future PM workflows — via one-line cron prompts naming SKILL.md wrappers, treating pm_os as a stable external CLI contract (never importing its internals). pm_os write operations route through the same approval surface as outbound messages. Exec-narrative/media creation — launch decks, demo scripts, narrated video — is genuinely new surface (Track A confirmed pm_os has **no** video pipeline today — `pmos-capability-inventory.md` §2.9) and is scoped as an explicit discovery-and-build item in P5: built for one named real use case, or explicitly parked by owner decision.

---

## 2. Architecture overview

Four layers, each with a single responsibility:

1. **Runtime — clean upstream hermes.** Gateway (Telegram in/out, WhatsApp) + cron host. Zero behavioral logic added to core; local delta is plugins/skills/policy only, so monthly upstream pulls stay cheap (the opposite of the current ~5,000-commit trap).
2. **Control plane — declarative markdown (the brain).** Policy files (`priority-map.md`, `auto-resolver.md`, `trust-policy.md`, `proactive-contract.md`, `merge-policy.md`), skills (`SKILL.md` per workflow, reference anatomy: closed buckets, literal fenced commands, numbered bounded phases, good-output examples), and canonical state (`tasks.md` + `processed.json` + `build-jobs` ledger). A behavior change is a text edit, not a deploy.
3. **Work-tools surface — pm_os.** 49 `bin/` tools, 8 pipelines, FOCI auth, tool-registry conventions (`pmos-capability-inventory.md`). Hermes drives these as CLIs per the registry — never custom Graph calls (that is literally how the original incident happened).
4. **Build assistant — distinct subsystem.** Plain-code cron-driven supervisor + durable job store + worktree-isolated coding workers + broker-gated merge approval. It shares the approval surface and the out-of-core discipline with everything else but has its own state machine and ledger.

**Governance unification (resolves Track D risk #5 — "two unreconciled governance systems").** The out-of-process send broker (P1) is THE single shared approval surface. Its approval pattern — narrow request, allow-list/policy check, safe-lane test, hold-for-approval, one-tap on Telegram, audited execution — is extended to gate all three consequential-action classes:

| Consequential action class | Gate | Phase |
|---|---|---|
| Outbound message to a third party | Send broker: allow-list + 5-condition safe lane + approval | P1 |
| pm_os write operation (`--confirm` / `PM_OS_AGENT=1` / Graph write) | Broker approval path wraps the pm_os invocation | P5 |
| Code merge to a tracked branch | Broker approval path carries diff + test + review; supervisor executes merge | P6 |

One approval UX on Telegram for all three. Without this, an action can slip a gate by taking the path with the weaker gate — FM-3 ("guards protect the wrong path") re-emerging across the repo boundary. Principal-directed notifications (briefings, approval prompts, job-completion events **to Yu-Kuan** on an approved channel) are exempt — the broker's rule is "the agent cannot act on the world without the broker," and notifying its own principal is always allowed (v1 Phase 2 send-boundary clarification, kept verbatim).

### Request flow — CoS lane and factory lane

```
CHIEF-OF-STAFF LANE (inbound signal → outbound action)

 Teams/Outlook       Telegram        WhatsApp
 (pm_os read CLIs)   (gateway)       (gateway, conditional)
        \                |               /
         v               v              v
   +---------------------------------------+
   | TRIAGE (skill, cron: one-line prompt) |
   | reads: priority-map.md, trust-policy  |
   | buckets: ACTION / FYI / NOISE         |
   | writes: tasks.md + processed.json     |
   +-------------------+-------------------+
                       |
              ACTION → draft reply (yk-comms → yk-voice)
                       |
                       v
        +---------------------------+
        | auto-resolver 5-condition |
        | safe-lane test            |
        +------+-------------+------+
               |             |
        all 5 hold      any fails
               |             |
               v             v
        +-------------------------------+
        |   SEND BROKER (out-of-proc,   |
        |   holds ALL send credentials) |
        |   allow-list check → send OR  |
        |   hold → Telegram [Approve]   |
        +---------------+---------------+
                        |
                        v
              outbound send (Teams/Outlook via
              pm_os send CLIs, Telegram,
              WhatsApp (conditional))

FACTORY LANE (task intake → merged code)

 task intake (Telegram /factory cmd / factory: tag
              in tasks.md / CLI)
        |
        v
 +-----------------------------------------------+
 | SUPERVISOR — plain-code cron tick (no LLM)     |
 | reads/writes durable job store (SQLite)        |
 +--+--------------------------------------------+
    |
    | INTAKE → QUEUED {repo, spec, model, budget_usd, timeout_min}
    v
  SPAWN: git worktree add -b factory/<repo>/<job-id>-<slug>
         worker = claude -p --output-format json --json-schema …
                  --permission-mode acceptEdits --allowedTools "…"
                  --max-budget-usd <b>
                (or codex exec -s workspace-write -a never --json)
    v
  MONITOR: liveness / wall-clock timeout / budget accounting per tick
    v
  TEST GATE: repo's own test command in the worktree (deterministic)
    v
  REVIEW GATE: codex review --base main (independent second model)
    v
  APPROVAL: diff stat + test result + review findings → Telegram
            one-tap [Approve]/[Reject]  (same broker approval UX)
    v
  INTEGRATE: per-repo lock → rebase onto latest main → re-run tests →
         repo's single flow (local merge or draft-PR merge,
         per merge-policy.md) → release lock
    v
  CLEANUP: worktree remove, branch delete, prune
           (FAILED worktrees retained 24h for inspection)
```

Key contract rules (from `ai-factory-research.md` §5): workers STOP after producing branch + schema'd result — they never merge or touch `main`; the supervisor owns all git integration; every gate writes a durable artifact so a later tick (or Yu-Kuan) can see why a job is where it is; the supervisor holds only ledger rows (id/status/test-result/log-tail), never full worker transcripts.

**Self-modification rule:** factory merges into hermes or pm_os themselves must not hot-swap a running gateway/toolchain — such merges are gated behind an explicit restart step in `merge-policy.md` (Track D §3).

---

## 3. Capability completeness matrix

Mapping is at **category level**: 16 clawchief/tradclaw categories + 8 pm_os pipelines + the build-assistant capability set + cross-cutting rows. **`evidence/clawchief-feature-checklist.md` is the normative item-level artifact (216 checkbox items + 10 must-not-regress behaviors); the P7 exit review verifies item-by-item against it.** The 216 rows are deliberately NOT duplicated here — a copy would drift from the source of truth. Adaptation rule for the exit review: items bound to clawchief's Gmail/gog stack map to the pm_os Outlook/Teams equivalents; founder-specific (BD/outreach) and household-specific (tradclaw) items may be marked ADAPTED or N/A **with a written rationale each** — silent skips fail the review.

### 3.1 Clawchief/tradclaw categories (baseline CoS)

| # | Category (checklist section) | Phase | Category acceptance test |
|---|---|---|---|
| 1 | Signal triage & policy layer (priority-map, auto-resolver) | P2 (files) / P3 (enforced) | A real inbound item is classified through `priority-map.md` tiers; the auto-resolver's 4 resolution modes demonstrably route one item each way |
| 2 | Executive-assistant skill (thread-aware triage, reply discipline, classification buckets) | P3 | Bucket-level suite on real items: every priority-map classification bucket exercised at least once; thread-reply and reply-all equivalents demonstrated on Outlook and Teams; inbox-state updates (handled/waiting/noise) written same-turn; scheduling-authority items route to hold; agent reads full thread and drafts in yk-voice through the broker, with ≥1 draft correctly holding |
| 3 | Business development (tracker-as-source-of-truth, follow-up cadence) | P4 (ADAPTED) | Stakeholder-tracker equivalent: a canonical stakeholder/commitment tracker section (in `tasks.md` or `stakeholders.md`) is source-of-truth, updated **before** an item is marked handled; a "waiting on external reply" item gets a follow-up task with its own due date same-turn; a cadence sweep surfaces overdue follow-ups. Remaining founder-BD items (lead sourcing, sent-mail sweep, lead verification) classified ADAPTED/N-A at the P4 exit mini-audit |
| 4 | Daily task manager (`tasks.md` canonical, same-turn writes) | P4 | Fresh-context reader answers "what's pending?" from `tasks.md` alone; a task state change is written same-turn |
| 5 | Daily task prep (2am isolated, silent, promote/archive) | P4 | Prep run promotes a due-today backlog item, archives yesterday's completions to `tasks-completed.md`, emits nothing to Telegram |
| 6 | Heartbeat orchestration (delegating checklist, HEARTBEAT_OK) | P4 | A heartbeat run with nothing actionable emits exactly `HEARTBEAT_OK` and nothing else |
| 7 | Meeting-notes ingestion (ledger idempotency) | P4 | The same meeting doc swept twice is processed once; ledger records docId + processedAt + status |
| 8 | Cron job design (one-line prompts, explicit delivery mode, isolated vs main) | P2 onward | Every live cron job's prompt is ≤1 sentence naming a skill; each declares delivery mode explicitly |
| 9 | Location awareness (travel-blocked tasks, no-book-on-uncertainty) | P4 | With a travel block on calendar, a home-dependent task is not nudged; an uncertain-availability slot is not booked |
| 10 | Workspace artifacts & state (single-writer ownership, TOOLS-style env file, memory surfaces) | P2/P4 | Single-writer table documented and honored; environment values live in one env-notes file read by skills; a MEMORY.md-equivalent holds durable facts/preferences with the "remember this → write it down same-turn" behavior; a daily working-memory log follows the same ownership discipline as `tasks.md`. tradclaw household resource templates → ADAPTED/N-A at the P4 mini-audit |
| 11 | Onboarding & setup (behavioral acceptance checklist) | P2 | `SETUP-CHECKLIST.md` covers prerequisite verification (tokens, paths, launchd, channels) and behavioral gates ("send test message, confirm arrival") plus placeholder detection (no template value left unreplaced); "any unchecked box = not done" is explicit. Interview-driven onboarding and module selection: ADAPTED with rationale recorded now — single known user, policy files seeded from the 2026-07-03 interview decisions |
| 12 | Safety, trust boundary & behavioral calibration | P2 | `trust-policy.md`: instructions only from Yu-Kuan on approved channels; an embedded-instruction email body demonstrably does NOT trigger action (FM-8 negative test) |
| 13 | Channel delivery (one primary route, no spraying) | P2/P3 | Proactive updates go to exactly one configured channel+target; no update is sent to two places |
| 14 | Platform alignment & architecture (primitives documented, no parallel concepts) | P2 (ADAPTED) | Hermes-equivalent primitives (gateway sessions, cron, skills precedence) documented in one file; skills reference them, not invented concepts |
| 15 | Self-maintenance & failure handling (allowlisted backup, graceful degradation) | P7 | Backup job commits only when diff non-empty, allowlisted paths only, silent unless push fails; a calendar-access failure degrades gracefully |
| 16 | Quiet-by-default contract | P2 (file) / P4 (proven) | A full day with nothing actionable produces zero nudges; no near-identical nudge repeats without materially new information |

### 3.2 pm_os pipelines (from `pmos-capability-inventory.md` §2)

| # | Pipeline | Phase | Acceptance test |
|---|---|---|---|
| 1 | pm-morning (daily triage) | P2 (first slice) / P5 (full) | Weekday 08:00 briefing arrives on Telegram, correctly ranked; ledger prevents re-processing (M2 gate) |
| 2 | pm-weekly (weekly status email) | P5 (first P5 slice — read-mostly) | One-line cron prompt produces `reports/{date}-weekly.md`; draft surfaced to Telegram for review |
| 3 | pm-send (draft dispatch) | P5 | Every dispatch routes through the broker approval; pm_os's own AskUserQuestion gate is reconciled (one approval, not two, not zero) |
| 4 | pm-jira (JIRA/Confluence monitor) | P5 | Runs on cadence from launchd context; `$ATLASSIAN_API_TOKEN` sourcing fixed so a non-interactive run does not 401 (fragility 6.3) |
| 5 | pm-pulse (biweekly survey) | P5 | Ready-to-submit surfaced for approval on cadence; auto-submit allowed ONLY via an explicit standing approval recorded in `auto-resolver.md` (safe-lane entry: operational, recoverable, hardcoded defaults reviewed by owner); SSO-expired path surfaces "re-auth needed" instead of failing silently |
| 6 | pm-login (SSO/token lifecycle) | P5 | On 401 anywhere, workflows route to `ensure-tokens.js`/pm-login rather than blind refresh; FOCI RT never rotated without explicit authorization (TOKEN-7 rule). Not a standalone SKILL.md — verified via every other skill's 401-path test |
| 7 | yk-comms / yk-voice (voice transformation) | P3 | Every outbound draft passes audience routing + voice styling before broker submission. Verified at P3; inherited by M5 |
| 8 | Glean enterprise search | P5 | A skill answers an enterprise-knowledge question via `glean-search.js` wrapper (never raw `glean chat`) |
| — | Exec-narrative/media creation (NEW — no pm_os pipeline exists, §2.9) | P5 (discovery-and-build item) | One artifact (deck, demo script, or render) produced end-to-end via the skill for one named real use case, held for approval before any send/publish; or an owner-approved park decision |

### 3.3 Build-assistant capability set (from `ai-factory-research.md`)

| # | Capability | Phase | Acceptance test |
|---|---|---|---|
| 1 | Job intake + durable job store | P6 | A job filed via Telegram/CLI appears in the ledger as QUEUED with repo/spec/budget/timeout |
| 2 | Worktree-isolated spawn (`factory/<repo>/<job-id>-<slug>`) | P6 | Two concurrent jobs on the same repo run in separate worktrees/branches; git refuses same-branch double-checkout (respected, not fought) |
| 3 | Monitor: liveness, wall-clock timeout, budget accounting | P6 | An idle/stuck worker's **process group** is reaped at timeout (no orphaned children); `total_cost_usd` summed across resumes; per-job cap refuses resume past budget |
| 4 | Test gate (repo's own suite, deterministic) | P6 | Red tests block the approval offer; one auto-fix resume allowed, then NEEDS_ATTENTION |
| 5 | Review gate (`codex review --base main`) | P6 | Review findings attached to the approval prompt for every job |
| 6 | One-tap merge approval (broker UX) | P6 | Approve triggers rebase + re-test + the repo's single integration flow from `merge-policy.md` (local merge or draft-PR merge) behind the per-repo lock; Reject leaves branch untouched, cleans worktree |
| 7 | Three independent cost stops | P6 | Per-job `--max-budget-usd` (Claude workers), wall-clock SIGTERM to the process group, reservation-based daily ceiling kill-switch — each demonstrably fires in a controlled test |
| 8 | Cleanup + failure retention | P6 | Merged job's worktree/branch removed; FAILED worktree retained 24h then pruned |
| 9 | Health check v2 (jobs + workers, not just channels) | P6 | Health output truthfully reports running jobs and worker sessions alongside channel connectivity |
| 10 | Negative test: no ungated merge | P6 | Fresh-context reviewer confirms the hermes agent cannot merge to a protected branch without the human tap (M1 discipline replicated) |

---

## 4. Phase / milestone map (P0–P7)

Milestones: each phase ships an independently usable slice; you can stop after any milestone and still be ahead.

| Milestone | Ships after | You can use it for |
|---|---|---|
| **M0 — Stable & quiet** | P0 | Gateway stays up, no zombie spam, honest health status |
| **M1 — Safe send** | P1 | Any channel sends only via the out-of-process broker; the FM-1 class of bug is architecturally impossible |
| **M2 — First briefing** | P2 (slice) | A daily Teams/Outlook triage delivered to Telegram you actually read |
| **M3 — Triage + reply** | P3 (slice) | Draft replies proposed; safe-lane ones auto-sent |
| **M4 — Proactive CoS** | P4 | Calendar-aware, meeting-prep, canonical `tasks.md`, quiet-by-default proven |
| **M5 — pm_os parity** | P5 | Every pm_os pipeline (§3.2 rows 1–8 + exec-narrative row) runs on cadence via one-line skill prompts; pm_os external writes broker-gated |
| **M6 — One-job factory** | P6 | One headless worker → tested branch → one-tap merge, end to end |
| **M6.5 — Batch mode** | P6 | 3–5 scoped jobs run overnight; morning approval packet + one-line batch digest |
| **M7 — Self-extending** | P7 | Cheap upstream pulls, agent-proposed skills, item-level completeness verified |

> Effort figures are focused solo-dev days, directional not commitments. Dependencies are hard gates.

**Phase-exit mini-audits (running completeness proof).** At each phase exit (P2 through P6), the checklist categories mapped to that phase are walked **item-by-item** for just those categories; each item lands as ✓/ADAPTED/N-A-with-rationale in a running `qa/checklist-audit.md`. P7.4 is then the final full pass plus a review of the accumulated rationales — not the first item-level look. This keeps item-level proof current without duplicating the 216-row checklist into this plan (copies drift; the evidence file stays normative).

### PHASE 0 — Stabilize & establish the new base (M0)

**Goal:** A gateway that stays up and tells the truth about its health, on a clean upstream base, with no zombie platforms.
**Dependencies:** none — start here. **Effort:** 2–3 days.

- **0.1 Clean base (thin slice first).** New checkout from latest upstream (`origin/main`, currently `v2026.7.1` / ~v0.18). Do **not** merge into the fork. Bring `.env` + `~/.hermes/` config; do **not** bring plugin god-file edits. *Acceptance:* `hermes --version` reports the new version; gateway starts; `git log` shows upstream, not the fork commits. *Positive example:* `git clone` upstream into `~/Code/hermes-cos`, copy `.env`, gateway comes up clean. *Negative example:* `git merge origin/main` into `feat/governance-plugins` — re-triggers a large merge conflict and carries the liability forward. Do not.
- **0.2 Kill zombie platforms.** Disable Discord so the adapter is not created (not just failing to connect). WhatsApp: pair the bridge (`hermes whatsapp`) or disable cleanly with a note in `tasks.md` — no 300s retry loop. *Acceptance:* zero Discord/WhatsApp errors in `errors.log` over 30 min. *Decision criterion:* a platform stays enabled only if it can currently connect.
- **0.3 Honest health signal.** One command/file/endpoint answering "which platforms are connected, when did I last succeed," distinguishing hermes bug from machine network outage (the 3 restarts on 2026-07-03 were DNS, and there was no way to tell). First reverify whether the observability dashboard still crashes `[UNVERIFIED — `main.py:273` is now benign .env-loader code; may already be fixed]`; either way replace the heavyweight dashboard with the 1-line health check. **Design note for P6:** structure the health output so a `jobs` section can be added later without redesign (health v2).
- **0.4 Supervisor sanity.** launchd plist points at the new checkout; `KeepAlive` throttling backs off on fast-crash loops. Validate the launchd session context: the gateway (and later the broker/factory supervisor) must run where it can reach the keychain; headed-browser steps require a login session, so jobs that need one must surface "needs user present" instead of failing silently. *Acceptance:* kill the gateway; it restarts once, cleanly, at the right path.

**Exit gate:** 24 hours of uptime, zero zombie errors, truthful health signal.

### PHASE 1 — Reliable send path: the out-of-process send broker (M1)

**Goal:** Close the FM-1 failure class by design — one reliable send path the assistant cannot improvise around. **This is the keystone; do not shortcut it.** It is also the approval surface every later phase reuses.
**Dependencies:** P0. **Effort:** 5–7 days.

- **1.1 Send broker (out-of-process).** A tiny separate process owns all outbound sends **to third parties** on every channel. The agent sends a narrow request ("send this, to this approved recipient, on this channel"); the broker — not the agent — holds credentials and enforces: recipient allow-list (outbound has none today — FM-3), safe-lane check, approval for everything else. *Positive example:* agent writes a request to a queue/socket; broker validates recipient ∈ allow-list, checks safe-lane, sends or holds. Agent has no token, no SMTP, no Graph client. *Negative example:* the current model — an in-process plugin inspecting `tool_name` substrings in the same process as an assistant that once read the token off disk and modified the safeguard. Rebuilding any variant of this is the failure. **Why out-of-process is non-negotiable:** an in-process safeguard shares the assistant's permission boundary and can be reached around; a process boundary cannot. Credentials move into the broker's environment/keychain. **The boundary, stated honestly:** on a single macOS user, "out-of-process" alone is not a credential boundary — the boundary is credentials removed from every assistant-readable location (`.env`, shell env, token files on the standard tool paths) into the broker's env/keychain, **and** write-capable pm_os send tools credential-starved in the assistant's context (they resolve tokens via the broker path only). **Broker-down behavior: fail closed.** No caller falls back to a direct path. Held actions queue durably and survive a broker restart; the broker exposes a health signal consumed by the P0 health check; if the approval path (broker or Telegram) is unavailable, the system notifies Yu-Kuan through any healthy principal channel and holds everything.
- **1.2 The 5-condition safe lane (auto-resolver).** Broker auto-sends only if ALL five hold: (1) signal understood, (2) source of truth known, (3) operational not strategic, (4) authority clear, (5) mistake recoverable. *Positive:* "got it, will do" to a scheduling confirmation from a known colleague → auto-send. *Negative:* anything to the board → hold, always. Start with the lane empty or near-empty; widen as trust builds (clawchief's own learning: "start with the auto-resolver," conservative).
- **1.3 Fold approval into the broker.** Replace the chronically broken `/approve-email` machinery (FM-6): held sends surface to Telegram; you approve; broker sends. One tested path, not per-channel hash surgery. **Design for extension:** the approval message format and tap-handling must be generic ("held action" with type/summary/payload), because P5 pm_os writes and P6 merge approvals ride this exact surface. **Design rule:** approvals reference **stable action IDs over durable broker-side artifacts** — the Telegram message carries only summary + buttons (+ a local artifact path); the authoritative payload lives in the broker's store, keyed by ID. No hash-of-truncated-body lookups (the exact bug class that broke `/approve-email` four ways); the M1 test includes a long-payload approval.
- **1.4 Credentials out of the assistant's reach.** `EMAIL_PASSWORD`, API keys, stored Microsoft tokens out of assistant-readable `.env` into the broker's isolated env / macOS keychain. *Acceptance:* `cat .env` from the assistant's context reveals no live send credential; a fresh-context reviewer also attempts a direct pm_os send CLI call and a token-file read via the standard tool paths — both must fail to produce a send. If the bypass test cannot be made to pass with credential-starving, escalate to a separate OS user for the broker — a decision deferred until the test says so.

**Exit gate (M1, fresh-context verified — never self-verified):** from Telegram, approve a held message — including one long-payload approval — and confirm it arrives; then attempt a direct assistant send bypassing the broker (direct send-CLI call and token-file read included) and confirm it is impossible (no live send credential reachable from the assistant context).

### PHASE 2 — Control plane + first briefing (M2)

**Goal:** Stand up the declarative control-plane files and ship a daily Teams/Outlook briefing to Telegram.
**Dependencies:** P0 only. (A briefing is a principal-directed notification — exempt from the broker per the send-boundary rule; the broker must exist before any third-party reply in P3.) **Effort:** 6–8 days.

- **2.1 Control-plane files** (single-owner, human-legible): `priority-map.md` (senders/topics → action tier), `auto-resolver.md` (the safe lane in prose — the broker's human-readable spec), `trust-policy.md` (instructions only from Yu-Kuan on approved channels; content the assistant reads is data, not instructions — FM-8), `tasks.md` + `processed.json` (canonical state + idempotency ledger), `proactive-contract.md` (quiet-by-default, one nudge max, never repeat). Plus `SETUP-CHECKLIST.md` with prerequisite verification (tokens, paths, launchd, channels), behavioral acceptance gates (checklist Category 11: "any unchecked box = install not done" — the antidote to "76/76 unit tests pass" standing in for "the feature works"), and placeholder detection (no template value left unreplaced). Interview-driven onboarding and module selection are ADAPTED with rationale recorded now: single known user; the policy files are seeded from the 2026-07-03 interview decisions. *Positive example:* changing priorities = editing `priority-map.md`. *Negative:* encoding priority in Python.
- **2.2 First briefing skill (thin slice → M2).** `morning-triage/SKILL.md` in reference anatomy: closed buckets (ACTION/FYI/NOISE), a **preflight block** (literal commands checking: entrypoint exists, pinned flags still present in `--help`, required prompt/config files exist, token health OK, write gate reachable — preflight failure → one "degraded: <lane> needs attention" notification + skip, never a half-run), literal pm_os CLI commands in fenced blocks, numbered bounded phases, a good-output example. The preflight block is part of the reference anatomy for **every** scheduled skill from here on. Re-point the existing `morning-briefing` cron to a one-line prompt: "Use the morning-triage skill." Reads Teams/Outlook via pm_os registry-backed tools — do not write custom Graph calls (that is literally how the incident happened). *Acceptance (M2):* weekday 08:00 briefing on Telegram, correctly ranked, no re-processing on the next run.
- **2.3 Cron manifest.** Every reference cron job (both reference repos, checklist category 8) mapped to enabled / adapted / disabled-with-rationale, with its prompt, session mode (isolated/main), and delivery mode. The category-8 acceptance test checks the manifest, not just live jobs — disabled-by-default reference jobs cannot silently vanish.
- **2.4 WhatsApp paired-and-verified gate.** Close the P0.2 conditional: pair the bridge and pass a send/receive acceptance test, OR record an owner decision to drop the channel. Either outcome closes the conditional in the fixed-decisions channel row.

**Exit gate:** three consecutive weekday briefings you find useful, delivered reliably, no double-processing; cron manifest complete; WhatsApp conditional closed one way or the other.

### PHASE 3 — Triage + safe replies (M3)

**Goal:** The agent drafts replies; safe-lane ones auto-send via the broker; everything else is proposed.
**Dependencies:** P1 **and** P2. **Effort:** 6–9 days.

- **3.1 Draft-reply skill.** For ACTION items, draft in yk-voice (reuse the yk-comms → yk-voice pipeline). Every draft routes through the broker: safe-lane → auto-send; else → hold for Telegram approval. **Dispatch mechanics:** broker-approved (or safe-lane) drafts are dispatched by the **broker** calling the send CLI directly — never through pm-send's interactive AskUserQuestion pipeline, which remains the human-driven path. The pm-send reconciliation (one approval total) completes in P5.2; until then, P3 replies use the broker-owned dispatch path only. *Acceptance (bucket-level suite on real items):* every priority-map classification bucket exercised at least once; thread-reply and reply-all equivalents demonstrated on Outlook and Teams; inbox-state updates (handled/waiting/noise) written same-turn; scheduling-authority items route to hold; at least one draft correctly holds. *Positive:* "confirm attendance" to a known colleague auto-sends. *Negative:* anything to the board, or any first-contact external recipient, holds.
- **3.2 PR-monitor as a skill.** Re-point the existing `pr-monitor` cron to a one-line skill prompt; summaries to Telegram; PR comments proposed, never auto-posted.

**Exit gate (M3):** one week — safe-lane replies auto-send correctly (audited after the fact, zero mistakes); non-safe drafts all waited; zero unwanted or misrouted sends. Fresh-context audit of the week's sends.

### PHASE 4 — Proactive CoS + canonical state (M4)

**Goal:** Calendar awareness, meeting prep, quiet proactive discipline, and the matured canonical-state layer that makes the clawchief baseline complete. (v1's Phase 4, trimmed: upstream tracking and self-improving skills move to P7.)
**Dependencies:** P3. **Effort:** 7–9 days.

- **4.0 Calendar-read prerequisite.** Verify or build a calendar-read capability **before** any calendar-aware gate: Microsoft Graph `Calendars.Read` via the existing FOCI clients, or a new pm_os bin tool per the registry — the pm_os inventory today has neither a calendar-read tool nor a `Calendars.*` scope. If the FOCI clients cannot mint a calendar scope, surface that as a blocking finding at P4 start. All 4.1 gates depend on this item.
- **4.1 Calendar + meeting prep** (depends on 4.0). Calendar-read skill (check ALL calendars before treating a slot as free — reference learning) + meeting-prep skill delivering attendees/last-thread/open-items to Telegram before meetings. **Post-meeting path:** ingested meeting notes → decisions appended to a decision log → follow-up tasks created in `tasks.md` same-turn → follow-up drafts proposed through the broker. *Positive:* 30 min before a meeting, a prep note arrives; one real meeting produces logged decisions plus at least one follow-up task and draft. *Negative:* booking over an existing event because only one calendar was checked. (Pre-meeting orchestration — agenda drafts, pre-read packets, likely-objections briefs — is explicitly parked; see §11.)
- **4.2 Canonical state matured.** `tasks.md` with the reference section structure (Today with Principal/Assistant owner subsections, Backlog-with-due-date, Recurring, Rules embedded at the bottom); `tasks-completed.md` archive with single-writer discipline (only daily-task-prep writes it); daily-task-prep as a 2am isolated silent cron job (promote due-today items, archive yesterday's completions, never wipe Today); meeting-notes ingestion with the `docId/processedAt/status` ledger; heartbeat as an orchestrating checklist that delegates to skills and ends in `HEARTBEAT_OK` when nothing needs attention; location-awareness rules (checklist Categories 4–7, 9). **Memory surfaces:** a MEMORY.md-equivalent for durable facts/preferences, the "remember this → write it down same-turn" behavior, and a daily working-memory log under the same ownership discipline as `tasks.md`. **`product-operating-model.md` skeleton:** bets/initiatives, owner, status, key decisions (with dates), open risks, next-best-action — single-writer, seeded manually with Yu-Kuan; refreshed weekly from P5 onward. The stakeholder/commitment tracker (§3.1 row 3) lands here too, as a `tasks.md` section or `stakeholders.md`.
- **4.3 Proactive discipline enforced.** `proactive-contract.md` proven in practice: silence when nothing needs you; never repeat a nudge without materially new information; one delivery route, no spraying.

**Exit gate (M4):** a normal week where briefing, triage, replies, and meeting prep all run with at most one approval prompt per genuinely-consequential action and zero annoyance-muting; a fresh-context reader answers "what's pending?" from `tasks.md` alone; a full quiet day emits zero nudges.

### PHASE 5 — pm_os orchestration layer (M5) — NEW

**Goal:** Hermes reliably drives all pm_os pipelines (§3.2 rows 1–8 + the exec-narrative row) via one-line skill prompts; pm_os external writes are broker-gated; the known fragilities are fixed; exec-narrative/media capability is scoped.
**Dependencies:** P2 (skill/cron pattern proven) and P1 (broker, for write-gating). Sequenced before the factory because pm_os orchestration is an extension of the existing pipeline (reuses broker, skills, approval surface — low new surface, high immediate value); the factory is genuinely new machinery. **Effort:** 10–14 days.
**Slicing (ordered, each independently stoppable):** **5a** read-only lanes (pm-weekly, full pm-morning) → **5b** broker-gated writes (pm-send, pm-jira write paths, the write-tool manifest) → **5c** fragile browser/SSO lanes (pm-pulse, Teams-channel send) → **5d** exec-narrative discovery spike last. A slice must pass its gate before the next starts; if P5 exceeds its effort box by 50%, stop and re-scope with the owner rather than pushing through.

- **5.1 Skill layer (thin slice first: `pm-weekly`).** One `SKILL.md` per pipeline (`pm-weekly`, then `pm-jira`, `pm-pulse`, full `pm-morning`, `pm-send`, `glean`), each wrapping the pipeline behind a **stable entrypoint**: a `bin/` CLI where one exists; otherwise the pipeline's documented invocation (pm-jira's curl recipe, pm-pulse's browser-skill logic) wrapped in a thin, pinned wrapper script so drift still breaks loudly and locally — building bin-style wrappers where a pipeline has none (pm-jira, pm-pulse) is a 5.1 sub-item. Every skill has closed outcome states, the token-failure fallback ("on 401 → pm-login path"), and the P2.2 preflight block; pm-weekly's SKILL.md lists its specific preflight items (prompt file, config path, token status, raw-scan freshness, report output dir). pm-login is **not** a standalone SKILL.md — it is the shared 401-fallback path; its §3.2 row-6 acceptance is exercised by every other skill's 401 test. Cron prompts stay one line. **Contract rule:** pin the expected entrypoint surface (tool + flags) in each SKILL.md; never import pm_os internals; the monthly contract check (P7.1) remains the deeper audit behind the per-run preflights. **pm-weekly v2 — the weekly operating packet:** the weekly skill's second iteration produces status email draft + linked evidence + risks/asks + Jira delta (from pm-jira data) + follow-up tasks written to `tasks.md`, and refreshes `product-operating-model.md` (status/deltas) same-turn. *Acceptance:* a fresh-context reader answers "what are the current bets and their status?" from the file alone. (An optional deck rides the 5.6 artifact skill once it exists — not a v2 requirement.)
- **5.2 Broker gating of pm_os external writes.** The local/external split: local artifact writes (reports, drafts, ledgers) are approval-exempt; external-world writes (send mail/chat, JIRA/Confluence comment, SharePoint/Graph writes, survey submit) are gated at the **tool level**. Enforcement is **credential-starvation, not prompt discipline**: the assistant context holds no write-capable Graph/send tokens; write-capable pm_os CLIs resolve credentials only via the broker-owned path (broker env/keychain), so direct invocation from the assistant context fails closed with "route via broker" — a pipeline invocation cannot silently carry an external write. pm_os's own confirm-gate is reconciled so the user is asked exactly once. **Write-tool manifest (M5 gate artifact):** one row per write-capable pm_os tool (34 per the inventory), classified ∈ {broker-gated external write, approval-exempt local write, token-lifecycle special case, effectively-read-only}, with one fresh-context bypass test per write **family** (mail send, chat send, JIRA/Confluence write, SharePoint/file write, survey submit, token write). *Acceptance (negative test, fresh-context):* the top tool of each write family, invoked directly from the assistant context, fails to produce an external write.
- **5.3 Reliability work items — the fragility trio, operationally fail-closed** (from `pmos-capability-inventory.md` §6, each an explicit task):
  1. **FOCI RT rotation (§6.1):** every OAuth RT exchange invalidates the prior RT (TOKEN-7 incident). Orchestration always goes through `ensure-tokens.js`; direct `foci-device-login.js --refresh`/`--foci-exchange` calls require explicit user authorization — encode this in the skills, and where feasible move the rule from CLAUDE.md prose into a hard path (e.g. broker/wrapper denies the direct call).
  2. **Okta browser session (§6.2):** `pm-pulse` and Teams-channel send depend on the `~/.agent-browser/pm-os` session, which expires independently of FOCI tokens and needs headed MFA (user present). Skills must detect the expired-session state and surface "re-auth needed, run /pm-login" instead of failing silently or retrying.
  3. **`$ATLASSIAN_API_TOKEN` in `~/.zshrc` (§6.3):** not available to launchd/non-interactive contexts → pm-jira 401s with an empty report. Fix: move the token to a launchd-reachable store (keychain or the broker's env) and have the skill read it there; add the 401-means-auth-not-empty-inbox distinction.
  4. **Auth health + paused lanes:** token/key/browser-profile health (FOCI RT age, `~/.age/pm-os.key` presence, Okta profile validity, Atlassian token reachability from launchd) is part of the health-check output. A lane that hits an auth failure enters a **paused degraded state** (recorded in the run ledger, one notification) — scheduled attempts do not retry until re-auth is confirmed.
- **5.4 Exec-narrative/media capability — discovery-and-build item.** The CPO need is async narrative; video is one output format. Track A confirmed there is **no** video pipeline in pm_os (§2.9) — this is new surface, not orchestration of an existing tool. Scope: (a) discovery spike — evaluate the narrative need (launch deck, demo script, narrated video) against available providers: hermes's `plugins/video_gen/{fal,xai}` `[UNVERIFIED: presence noted in Track D; working state not tested]` and the Office tooling via the 5.6 artifact path; (b) build the minimal skill for **one** concrete real use case Yu-Kuan names; (c) renders/decks cost money/time → hold-by-default in the broker. If the spike finds no concrete use case or no workable provider, park — parking requires an **owner-approved** park decision (one Telegram/interactive approval), not just a written rationale; do not build speculatively (YAGNI). Either outcome closes the matrix row.
- **5.5 Run ledger for long-running work.** Generalize `processed.json` to a run/job ledger with status polling for minutes-long pipeline runs (pm-weekly, renders), plus a completion-event notification contract ("weekly draft ready") that obeys the proactive contract. **Silent-stoppage detection:** every scheduled pipeline/job lane records last-success in the run ledger; a cron-run checker extending the P0 health signal (absorbed into health v2 at 6.7) alerts **only** when a lane has no success within its expected window (e.g. morning triage missed by >2h) or when a lane's state changes to degraded — no daily "all OK" message; quiet-by-default governs. This ledger is also the direct precursor of the P6 job store. *Acceptance:* a lane past its expected window (or newly degraded) produces exactly one alert; a day where every lane succeeds produces zero ledger-driven messages.
- **5.6 PM artifact skill — one type first.** Pick **one** artifact type with Yu-Kuan (e.g. status deck or PRD draft to SharePoint); ship `pm-artifact/SKILL.md` producing it through registry tools, broker-gated on the SharePoint write. The SKILL.md doubles as the template for further artifact types — the full artifact-factory family is explicitly parked (§11) until this first type earns weekly use.

**Exit gate (M5):** every pm_os pipeline (§3.2 rows 1–8) runs on cadence via a one-line skill prompt and passes its per-pipeline acceptance test — noting row 7 (yk-comms/yk-voice) is verified at P3 and inherited, and row 6 (pm-login) is verified via the shared 401-path tests; pm_os external writes are broker-gated with the write-tool manifest complete and one bypass test per write family passed; the fragility trio has fixes or explicit degraded-mode behavior; exec-narrative row closed (built for one named use case, or owner-approved park); one PM artifact type ships end-to-end.

### PHASE 6 — Build assistant / AI factory (M6, M6.5) — NEW

**Goal:** One-tap-merge factory: task in, tested + reviewed branch out, human approves, supervisor integrates. Thin slice first: **one** worker, **one** repo, end to end — then concurrency, then batch mode.
**Dependencies:** P1 (approval surface), P5.5 (run-ledger pattern). **Effort:** 10–15 days.

- **6.1 Supervisor: plain-code cron-driven stateless tick loop** (`ai-factory-research.md` §4 Option A — decided). A short-lived script fires every N minutes (hermes cron or launchd); each tick reads the durable job store, advances each job one state, and exits. All state on disk; every tick idempotent; a laptop reboot loses nothing. **The supervisor is not an LLM session** — it cannot exhaust context and costs no tokens to babysit. LLM reasoning is reserved for workers and the review gate. **Store integrity:** the job store is SQLite with transactions; a supervisor-wide lock file prevents overlapping ticks (stale-lock detection by PID+age); state transitions are monotonic (a DONE job can never re-enter MERGING); artifacts are written atomically (tmp+rename); a corruption-detection path (integrity check at tick start; on failure: stop spawning, retain everything, alert) exists **before** merges are enabled. **Resource gates before any spawn:** free-space preflight (min GB configurable), max concurrent worktrees, max retained-failure bytes with oldest-first pruning, cleanup-before-spawn ordering. **Sleep policy (pick at 6.1):** the tick asserts `caffeinate -s`-style wakefulness only while jobs are RUNNING, or factory windows are scheduled while the machine is awake — lid-close must not silently kill an overnight batch. *Positive example:* a tick finds a RUNNING job whose process group is dead → marks FAILED, retains worktree, posts one notification. *Negative example:* a persistent Opus "manager" session holding all worker transcripts in context — this is the documented #1 failure of agentic orchestration and the exact god-file/context-sprawl trap reappearing at the orchestration layer. Do not.
- **6.2 Job store + ledger + intake seam.** Per-job: id, repo, base, spec/prompt, worker (claude|codex), model, budget_usd, timeout_min, state, worktree path, branch, PGID, start time, cost-so-far, test result, review findings pointer, log tail. States: QUEUED → RUNNING → (TEST → REVIEW →) AWAITING_APPROVAL → MERGING → DONE | FAILED(reason) | NEEDS_ATTENTION. Human-readable mirror `build-jobs.md` for glanceability. **Intake seam (settled):** (a) Telegram `/factory <repo>: <spec>` and (b) a `factory:` tag on a `tasks.md` task — both create the job record, written by the **supervisor** (single writer) at its next tick, never by the CoS agent directly. Completion flows back the same way: ledger state → task state updated same-turn + principal notification per the proactive contract. Exact command syntax refined at 6.1.
- **6.3 Worker contract.** Per job: `git worktree add -b factory/<repo>/<job-id>-<slug> ../.factory-worktrees/<repo>/<job-id> origin/main`, then either
  `claude -p --output-format json --json-schema '<{status,branch,summary,test_result}>' --permission-mode acceptEdits --allowedTools "Edit Write Read 'Bash(git *)' <test cmds>" --model <m> --max-budget-usd <b> --system-prompt "<worker contract: implement the spec, run tests, commit, STOP>"`, or
  `codex exec -s workspace-write -a never --json -o result.json -C <worktree> "<spec>"`.
  **Never** `--dangerously-skip-permissions` / `bypassPermissions` as default; the allowlist is the safety lever. **Scrubbed environment:** workers run with a temporary `HOME`, no repo `.env`/secrets mounted, repo-manager hooks disabled where feasible, `--strict-mcp-config` (no inherited MCP servers), and the per-repo generated tool allowlist — a worker that needs a secret is a NEEDS_ATTENTION design smell, not a reason to widen the env. **Process groups:** each worker spawns in its own process group (`setsid`-equivalent); timeout/budget kills signal the process group, not the bare PID; the ledger records the PGID. **Budgeted workers are Claude-only for the first factory iteration** — Codex workers are enabled only after Codex-specific accounting exists (token parsing from `codex exec --json` usage events) and remain bounded by wall-clock + daily ceiling in the interim. Model routing is the primary cost dial: default Sonnet, escalate to Opus only for flagged-hard jobs, Haiku/low-effort for rote. **Spec-first job type (default for feature-class jobs):** stage 1 — a worker drafts the spec (requirements, acceptance criteria, test list, risk notes, affected surfaces) as schema'd output (~$0.2–0.5); the owner may approve/redirect cheaply at spec stage (optional tap). Stage 2 — the implementation worker receives the approved spec. Rote/small jobs skip stage 1 (`kind: quick` at intake). *Acceptance:* a feature-class job's stage-1 spec lands in the ledger as a schema'd artifact before any implementation worker spawns; a `kind: quick` job demonstrably skips stage 1.
- **6.4 Gates.** TEST: the repo's own suite in the worktree, deterministic, non-negotiable; one auto-fix resume on failure (`claude --resume <sid> "tests failed: <output>, fix"`), then NEEDS_ATTENTION; one retry to filter flakes — a flip-flop marks NEEDS_ATTENTION, never auto-merges. REVIEW: `codex review --base main` — an independent second model reviewing the diff (agent-checks-agent beneath the human). Findings attach to the approval prompt. After REVIEW, one bounded **review-fix resume**: the worker addresses P1-severity review findings within remaining budget, then one re-test + re-review; still-open P1 findings ride the approval packet as known issues. Never more than one loop — flip-flopping marks NEEDS_ATTENTION.
- **6.5 Approval + serialized integration.** Approval prompt to Telegram (broker UX): summary + diff stat + test result + review findings + [Approve]/[Reject]. **`merge-policy.md` picks exactly ONE integration flow per repo, with exact commands** — `integration: local-merge | draft-pr`: *local-merge* = `git fetch && git rebase origin/main && git merge --ff-only <branch>` (default for remote-less/tiny personal repos; the squash variant `git merge --squash <branch> && git commit` where a repo prefers single-commit features); *draft-pr* = the supervisor runs `gh pr create --draft` when the branch is ready, and the approval tap runs `gh pr merge` (default for repos with a GitHub remote — CI and review history for free). Approval UX identical; only the artifact differs. On approve: acquire per-repo lock → fetch → rebase onto latest main → re-run test gate on the merged result → execute the repo's flow → release lock. Conflict or re-test failure → NEEDS_ATTENTION, never force. On reject: branch untouched, worktree cleaned. `merge-policy.md` also defines per-repo test command, protected branches, and the self-modification restart rule.
- **6.6 Three independent cost stops** (each must demonstrably fire): (1) per-job `--max-budget-usd` hard cap (Claude workers — see 6.3); (2) per-job wall-clock SIGTERM to the process group; (3) daily spend ceiling, enforced at **spawn/resume time using reserved worst-case budgets** — each spawn reserves its full `--max-budget-usd`, the ceiling check is `reserved + spent ≤ ceiling`, and on breach no new spawns/resumes are allowed AND active workers are killed (process-group SIGTERM). Live stream usage is used where available; the final JSON is reconciliation, not the control signal. Plus: concurrency cap (start N=1–2), overflow billing OFF on the plan (the API-credit pool bounds worst-case: a runaway burns the month's automation credits, not an unbounded bill — `ai-factory-research.md` §6). Expected steady-state cost, all-in: worker $1–3.50/job (model-dependent) + the review pass + up to one auto-fix and one review-fix resume ≈ 1.5–2× worker cost per job; a 10-job nightly batch ≈ $15–70 all-in.
- **6.7 Health check v2.** Extend the P0 health signal: channels + running jobs + worker process groups + last-tick time. "Healthy" now includes "no orphaned workers" — verified against process groups and stale worktree locks, not bare PIDs. **Last-tick SLA:** a missed tick beyond 2× the cadence triggers one alert (silence must not look like health).
- **6.8 Batch mode (M6.5).** After the M6 single-feature gate: 3–5 scoped jobs queued in the evening run overnight (subject to the 6.1 sleep policy) and produce a **morning approval packet** — one Telegram message per completed job (summary + diff stat + test + review) plus a one-line batch digest. Weekly throughput (jobs merged/declined/NEEDS_ATTENTION) is recorded in the ledger. This rides the existing queue, budgets, and approval UX — no new machinery.

**Exit gate (M6):** the build assistant delivers and merges **one real feature with a single approval**, end to end. Then the concurrency criteria: ≥2 concurrent workers on ≥2 worktrees with live ledger status; decline path verified; the fresh-context negative test (no ungated merge to a protected branch) passes; budget/timeout/idle-kill demonstrably fire (against process groups).
**Exit gate (M6.5):** one overnight batch of 3–5 jobs produces a morning approval packet; weekly throughput recorded in the ledger.

### PHASE 7 — Sustainable evolution (M7) — NEW (was v1's 4.3/4.4)

**Goal:** The system stays cheap to keep and grows by markdown, not Python; completeness is verified item-by-item.
**Dependencies:** P4 (baseline complete); P5/P6 for their respective review sections. **Effort:** ongoing; ~1 day/month + a 2–3 day exit review.

- **7.1 Dual upstream tracking.** Monthly hermes upstream pull — cheap because the local delta is control-plane markdown + broker + supervisor, all out-of-core. *Decision criterion:* if a change requires editing a hermes god-file, reconsider whether it belongs in a skill/policy/broker instead. pm_os is the second upstream: hermes couples only to the pinned CLI contract in the SKILL.md files; a monthly contract check (do the pinned tools/flags still exist?) localizes breakage.
- **7.2 Self-improving skills.** The agent proposes new skills as markdown, reviewed by Yu-Kuan — never new Python. The factory may propose skills for itself through the same review path. Guardrails: agent-proposed skills ship only with a skill lint (structure/anatomy check), one behavioral acceptance test, and owner enablement (disabled until approved); a quarterly stale-skill review lists skills with zero runs or failing preflights; monthly self-maintenance scope stays capped (~1 day/month per this phase's effort box).
  - **Calibration loop** (scheduled monthly within the maintenance budget): a triage-classification backtest over the last N weeks (pm_os `backtest-*` tools), a safe-lane audit mining false-holds/false-sends, and pm-send feedback synthesis (`synthesize-feedback.js`) — each run ends in a proposed policy diff (priority-map / auto-resolver edits) through the normal review path. Proposals only; no silent policy drift.
  - **Friction→factory lane:** preflight failures, degraded lanes, and calibration findings accumulate in a friction log; a monthly pass turns the top items into factory job proposals (skills/wrappers/tests — markdown and out-of-core code only). Hermes/pm_os-core changes stay human-initiated; factory merges into the orchestrator's own repos remain restart-gated per `merge-policy.md`.
- **7.3 Self-maintenance jobs** (checklist Category 15): (1) allowlisted backup (commit only when diff non-empty, allowlisted paths only, silent unless push fails); (2) the reference's self-update job — an explicit enabled/disabled decision recorded in the cron manifest with rationale (disabled by default). Both explicit opt-ins. `priority-map.md` gets a route for system-improvement ideas — they become `tasks.md` tasks (or factory job proposals), never silent.
- **7.4 Item-level completeness exit review.** A fresh-context reviewer walks `evidence/clawchief-feature-checklist.md` item-by-item (216 items + 10 must-not-regress), checking each box from the running system, marking ADAPTED/N-A only with written rationale. This is the review that makes "everything clawchief does" provable rather than asserted.
- **7.5 Cross-domain capstone.** One real Teams/Outlook thread is turned — via existing skills — into: a drafted spec (PM artifact path), a Jira epic/story (jira-update path), and a factory job whose branch/draft-PR links back to the Jira item; every consequential step broker-gated. This is a composition test of existing primitives — no new infrastructure permitted; if it needs new machinery, that is a finding, not a build ticket.
- **7.6 Product-intelligence radar.** Weekly skill over Glean/Jira surfacing (a) decisions gone stale, (b) duplicate or conflicting work, (c) customer/account mentions needing attention — each with citations and a proposed action, delivered inside the weekly operating packet (never as extra pings; the proactive contract governs). *Acceptance:* one radar item per month leads to a real action taken.

**Exit gate (M7):** one upstream pull completed at <1 day cost; one agent-proposed skill shipped through review; the cross-domain capstone (7.5) completed once end-to-end; the item-level exit review complete with every item ✓/ADAPTED/N-A-with-rationale.

### What we explicitly are NOT doing (updated)

- **Not** merging ~5,000 commits into the fork. (Replace, don't merge.)
- **Not** extending the Yahoo email-send guard. (Deprioritized channel; wrong-layer, unsafe mechanism.)
- **Not** building any in-process safeguard. (Cannot hold when the assistant has shell access.)
- **Not** adding chief-of-staff features before the send broker exists. (Sequencing got backwards for 7 weeks; not again.)
- **Not** containers/VMs/Docker per factory worker. (Worktrees give the isolation a first-party personal tool needs; container orchestration is the Cap#1 equivalent of the in-process-denylist over-engineering.)
- **Not** a Celery/Temporal-grade job orchestrator or an LLM supervisor. (Plain-code cron tick over SQLite is the whole system.)
- **Not** auto-merge heuristics ("merge if diff < N lines" / diff-risk scoring). (Merge is always human-tapped; code merges are where auto-anything is least warranted early.)
- **Not** re-sandboxing the workers' shell. (Claude/Codex bring their own sandboxing; hermes spawns, monitors, and gates the merge.)
- **Not** duplicating the 216-item checklist into this plan. (The evidence file is normative; copies drift.)

---

## 5. New subsystems

| Subsystem | What it is | Owner artifacts |
|---|---|---|
| **Send broker** (P1) | Out-of-process send/approval service; holds all send credentials; enforces allow-list + safe lane + approval; generic "held action" surface reused by P5/P6 | broker process + its keychain/env; `auto-resolver.md` as its human-readable spec |
| **Build-assistant supervisor** (P6) | Plain-code cron tick loop: spawn/monitor/gate/notify/merge over a durable job store; per-job worktrees; per-repo merge lock; three cost stops | supervisor script, job store (SQLite), `build-jobs.md` mirror, worktree lifecycle wrappers (`add`/`remove`/`prune` + 24h failure retention) |
| **pm_os skill layer** (P5) | One SKILL.md per pipeline wrapping pm_os `bin/` CLIs with pinned contracts, token-failure fallbacks, closed outcome states; cron prompts stay one line | `pm-weekly/SKILL.md`, `pm-morning/SKILL.md`, `pm-jira/SKILL.md`, `pm-pulse/SKILL.md`, `pm-send/SKILL.md`, `glean/SKILL.md` |
| **Video/media capability** (P5) | Discovery-and-build item: no pm_os video pipeline exists (evidence §2.9); minimal path via hermes video_gen providers `[UNVERIFIED]` or new pm_os pipeline; hold-by-default; owner-approved park allowed (P5.4) | discovery note + `video-create/SKILL.md` (or owner-approved park decision) |
| **Canonical state** (P2/P4/P5/P6) | Single legible answer to "what does the agent think is pending/running?" | `tasks.md` + `tasks-completed.md`, `processed.json` idempotency ledger, run ledger (P5.5), build-job ledger (P6.2) |
| **Policy files** (P2/P6) | Declarative single-owner behavior | `priority-map.md`, `auto-resolver.md`, `trust-policy.md` (input-handling: content read ≠ instructions), `proactive-contract.md`, `merge-policy.md` (per-repo test command, protected branches, human-tap rule, self-modification restart rule), `SETUP-CHECKLIST.md` |

---

## 6. What to reuse vs build — decision matrix

v1's matrix is carried forward unchanged for its original rows (base repo → replace with clean upstream; gateway → port from upstream; email-send-guard → abandon; control-room/tool-registry-guard → replace with out-of-process boundary; outbound send → rewrite as the broker; `/approve-email` → replace with broker approval; cron → port base, workflows to skills; Telegram → port + verify; Teams/Outlook → integrate via pm_os; WhatsApp → fix-or-disable-with-intent; Discord → disable; Yahoo → dormant; state → add canonical layer on top; skills → rewrite in reference anatomy; policy → create; observability → 1-line health check). New rows:

| Subsystem | Decision | Reason |
|---|---|---|
| **Factory supervisor** | **Build** (plain code, ~small script) | No off-the-shelf fits the one-box personal shape; Option A tick loop is a day of code; LLM-as-supervisor (harness agent teams) rejected — context exhaustion + token cost scale with the run (`ai-factory-research.md` §4C) |
| **Job store** | **Build** (SQLite with transactions — §10 OQ2 SETTLED, see 6.1) | Trivial schema; durability plus overlapping-tick safety are the requirements; do not adopt a queue service |
| **Worker runtime** | **Reuse** `claude -p` / `codex exec` as-is | Both natively support headless, budgets, schemas, worktrees; zero wrapping beyond the spawn command |
| **Review gate** | **Reuse** `codex review --base main` | Purpose-built non-interactive review; independent second model for free |
| **Worktree lifecycle** | **Build thin wrappers** over `git worktree` | add/remove/prune + retention window; native git is the mechanism |
| **Merge approval surface** | **Extend** the P1 broker | Do not build a second approval system — one Telegram UX for messages, pm_os writes, merges (Track D §8: "reuse the broker's approval surface, don't invent a second one") |
| **pm_os pipelines** | **Reuse** via pinned CLI contract | pm_os is hardened + registry-backed; hermes wraps, never imports internals |
| **pm_os skill wrappers** | **Build** (markdown only) | Thin SKILL.md files; the extension surface by design |
| **pm_os fragility fixes** (FOCI rule hardening, Okta-expiry surfacing, Atlassian token relocation) | **Build** (small, targeted) | Reliability prerequisites for unattended cadence runs (evidence §6.1–6.3) |
| **Video/media** | **Discover, then build minimal or park** | No existing pipeline (evidence §2.9); YAGNI unless a concrete use case survives the P5.4 spike |
| **`merge-policy.md` + `factory-limits` values** | **Create** (markdown/config) | New policy surface; single-owner files per the governing principle |
| **Health check v2** | **Extend** P0 health signal | Same artifact, add jobs/workers section |

---

## 7. Acceptance criteria for "complete"

The project is complete when all of the following hold:

- **Every clawchief checklist item verified ✓** — the P7 exit review walks all 216 items + 10 must-not-regress behaviors in `evidence/clawchief-feature-checklist.md` against the running system; ADAPTED/N-A only with written rationale.
- **Every pm_os pipeline (§3.2 rows 1–8 + exec-narrative row) runs on cadence ✓** — coverage assertion, not a single demo: each §3.2 row passes its own gate.
- **Build assistant delivers + merges one real feature with a single approval ✓.**
- **≥1 cross-domain workflow (thread → spec → Jira → merged change) completed end-to-end ✓** — the P7 capstone: the three pillars compose, not just coexist.
- **A normal CPO week runs with ≤1 approval per consequential action ✓** — briefing + triage + replies + ≥1 pm_os workflow + ≥1 build job, zero annoyance-muting.

Per-capability criteria (from Track D §7):

**Baseline:** inherit M2/M3/M4 gates; fresh-context reader answers "what's pending?" from `tasks.md` alone; a full quiet day emits zero nudges.

**Build assistant:** ≥2 concurrent workers on ≥2 isolated worktrees with live ledger status; each worker's test result posts to Telegram with a diff summary; one-tap approve executes a real merge and decline leaves the branch untouched; **negative test** — the hermes agent cannot merge to a protected branch without the human tap, verified by a fresh-context reviewer; spend cap and idle-kill demonstrably fire.

**pm_os orchestration:** each named workflow runs via a one-line skill prompt and delivers its artifact to the right surface; every pm_os write routes through the broker — fresh-context test confirms no bypass path; on 401 the workflow surfaces "re-auth needed" rather than silently failing or blindly refreshing (TOKEN-7 safety); exec-narrative row closed (one artifact — deck, demo script, or render — produced end-to-end for a named use case, or an owner-approved park decision).

**Cross-cutting:** health check v2 truthfully reports channels AND running jobs AND worker sessions; all fresh-context negative tests (M1 send, P5 pm_os write, P6 merge) are run by a reviewer with no stake in the build, never self-verified.

**Leverage scorecard (measured, reported, not pass/fail):** over two representative weeks at M7 — safe-lane actions auto-resolved/week, factory jobs merged/week, PM artifacts produced/week, cross-domain workflows completed, and a one-line hours-saved estimate. Parity gates prove replication; leverage must be visible. Reported in the exit review; informs vNext (§11), does not block completion.

---

## 8. Sequenced first actions

The literal first moves are the 3 bootstrap actions in `next-steps.md`, unchanged in substance. Do them in order; stop after Action 3 and report. Do not skip ahead to features or touch the email-send-guard plugin — that pull is the trap (`diagnosis.md` §1).

1. **Confirm ground truth.** Is the gateway actually up, which platforms are connected vs zombie-retrying? (`launchctl list | grep hermes`; tail `~/.hermes/logs/errors.log`; count Discord "No bot token" and WhatsApp reconnect errors; check `exit_nonzero` in `gateway-exit-diag.log`.) If a new crash signature appears that isn't in `evidence/phase0-state-snapshot.md`, capture verbatim and report before proceeding.
2. **Stop the bleeding.** Disable Discord so the adapter is not created (blank token + enable flag — present-but-empty is still a zombie). WhatsApp: pair the bridge (`hermes whatsapp`) or disable with a note in `tasks.md` — never leave the 300s retry loop. Restart the gateway; confirm 30 minutes of zero Discord noise.
3. **Measure the fork delta before cloning.** `git fetch origin; git diff --stat origin/main...HEAD`; list non-email/guard commits (`git log --oneline origin/main..HEAD | grep -viE "email|guard|approve|imap|smtp"`); confirm the local add-on plugins. Expected outcome: a short list of genuinely-portable local changes, written into `tasks.md` as the Phase 0.1 input. If substantial non-email work surfaces, stop and report — it changes the "port small delta" assumption. Action 3 ends at measuring; the clean checkout itself is Phase 0.1, post-approval.

---

## 9. Risks & mitigations

| # | Risk | Source | Mitigation |
|---|---|---|---|
| 1 | **Parallel-session cost blow-up** — N headless coding sessions are long agentic loops, not single calls | Track C §6/§7, Track D §6.1 | Three independent stops: per-job `--max-budget-usd`, wall-clock timeout, daily ceiling kill-switch. Plus concurrency cap (N=1–2 to start), model routing (Sonnet default), overflow billing OFF — the capped API-credit pool bounds the worst case to one bad month, not an unbounded bill |
| 2 | **Supervisor context exhaustion** juggling many sessions | Track C §4, Track D §6.2 | **Solved by the plain-code supervisor decision:** the loop is not an LLM session and literally cannot exhaust context. Hard design constraint on the CoS agent side: it holds only ledger rows (id/status/test-result/log-tail), never full worker transcripts — poll a file, don't stream a transcript |
| 3 | **pm_os fragility trio** — FOCI RT rotation breaks the whole chain (TOKEN-7); Okta browser session expires independently and needs headed MFA; `$ATLASSIAN_API_TOKEN` in `.zshrc` is invisible to launchd | `pmos-capability-inventory.md` §6.1–6.3 | Each is a named P5.3 work item: route all token ops through `ensure-tokens.js` and harden the no-direct-refresh rule; detect-and-surface "re-auth needed" instead of silent failure; relocate the Atlassian token to a launchd-reachable store |
| 4 | **Dual upstream tracking** — hermes upstream AND an independently-evolving pm_os | Track D §6.4 | Local hermes delta stays additive/out-of-core (cheap monthly pulls); pm_os coupling limited to a pinned CLI contract in SKILL.md files with a monthly contract check; breakage is loud and localized, never silent |
| 5 | **Merge conflicts between parallel factory jobs** (the #1 reported multi-agent issue) | Track C §7 | Serialized merge behind a per-repo lock; rebase-onto-latest-main + re-test before merge; intake-time warning when two queued jobs target overlapping paths; keep jobs feature-scoped; conflict → NEEDS_ATTENTION, never force |
| 6 | **Silent false success** — a worker "reports done" without doing the work | Track C §7 | Never trust the agent's word: schema'd result + independent deterministic test gate + `codex review` diff review + human reads the diff stat. Trust the diff and green tests, not the transcript |
| 7 | **Governance bypass via the second system** — an action slips the gate by taking the pm_os or git path | Track D §6.5 (FM-3 reborn) | Resolved by design: single broker approval surface for all three consequential-action classes (§2); fresh-context negative tests for each path in the exit criteria |
| 8 | **Self-modification hazard** — a factory job edits hermes/pm_os and a live merge corrupts the running orchestrator | Track D §3/§6.6 | `merge-policy.md` restart rule: factory merges into the orchestrator's own repos are gated behind an explicit restart, never live |
| 9 | **Safe-lane miscalibration** — auto-sends something that should have held | v1 P1/P3 | Start with the lane empty/near-empty; widen only on evidence; weekly after-the-fact audit during P3; any mistake shrinks the lane |
| 10 | **Flaky test gate** blocking or (worse) waving through merges | Track C §7 | One retry to filter flakes; a flip-flop marks NEEDS_ATTENTION rather than auto-merging; human remains final approver |

---

## 10. Open questions

Locked decisions are not listed. Genuinely unresolved items are settled in-phase; items settled in review stay listed with their SETTLED note for traceability:

1. **Broker IPC mechanism** (unix socket vs file queue vs local HTTP) — validate against the macOS setup early in P1; the broker design is a hypothesis until then (v1 caveat, still true).
2. **Job store format — SETTLED in review:** SQLite with transactions (see 6.1); durability plus overlapping-tick safety drove the decision.
3. **Telegram approval mechanics** (inline buttons vs reply commands) for the generic held-action UX — decide at P1.3 with P5/P6 payloads in mind.
4. **Default factory worker** (Claude vs Codex) and the model-routing table per job type — decide from the first few P6 jobs' cost/quality data. Constraint: budgeted workers are Claude-only for the first iteration (6.3); the open part is the model-routing table and when to enable Codex workers.
5. **Video capability home** (hermes `video_gen` plugins vs new pm_os pipeline) and whether a concrete use case exists at all — the P5.4 discovery spike answers this; park is an acceptable answer.
6. **WhatsApp pairing timing** — pair at P0.2 or defer with a `tasks.md` note; depends on whether the bridge pairs cleanly on the new base.
7. **CoS→factory intake seam — SETTLED in review:** Telegram `/factory <repo>: <spec>` and a `factory:` tag on a `tasks.md` task, both materialized by the supervisor as single writer (see 6.2); exact command syntax refined at P6.1.

---

## 11. vNext — explicitly parked

Ideas from the adversarial review judged real but out of scope now. Each failed the "stays out-of-core, declarative, and rides primitives this plan already builds" test — or would re-create the effort-inversion failure — at this stage. Revisit after M7.

- **Customer/revenue signal layer (CRM/Salesforce connector).** Start Glean-mediated (Salesforce content Glean already indexes) when a concrete weekly question is named; a direct connector is new load-bearing integration surface with no existing pm_os foundation to orchestrate.
- **Full PM artifact factory** (launch briefs, roadmap sheets, decision logs, board decks as a family). Expand from the single P5 artifact type only after that first type earns weekly use.
- **Pre-meeting orchestration** (agenda drafts, pre-read packets, likely-objections briefs). Add after the post-meeting decision/follow-through loop (4.1) proves useful; avoids proactive-contract pressure.

---

## Appendix A — Adversarial review dispositions

The two-panel gauntlet ran 2026-07-03 against the first synthesis draft: **Panel 1** — four Codex adversarial agents with distinct mandates (A accuracy, B ambition, C completeness, D operability), 52 concerns total, raw outputs in `evidence/review-codex-{A,B,C,D}.md`. **Panel 2** — Claude adjudication (triage of A/C/D + dedicated ambition reconciliation for B, guarded by the out-of-core/declarative test), recorded in `evidence/panel2-triage-ACD.md` and `evidence/panel2-ambition-B.md`, then applied to this document. Every concern has a disposition below; none were silently dropped.

Verdict counts — A: 9 ACCEPT / 3 PARTIAL. C: 7 ACCEPT / 5 PARTIAL. D: 10 ACCEPT / 3 PARTIAL. B: 14 FOLD (2 of them split FOLD+PARK) / 1 PARK / 0 new phases / 0 REJECT.

### Codex-A — Accuracy & validity

| id | verdict | disposition | where applied |
|---|---|---|---|
| A-1 | PARTIAL | Boundary = credential removal + starving pm_os send tools, not process separation alone; bypass test widened beyond `cat .env`; separate-OS-user escalation only if the fresh-context test fails (full separation now would be disproportionate) | P1.1, P1.4, M1 gate |
| A-2 | ACCEPT | No calendar tool/scope exists in pm_os; calendar-read capability is now an explicit prerequisite | P4.0 (new), 4.1 dependency |
| A-3 | ACCEPT | "bin CLIs only" contradicted by pm-jira curl / pm-pulse browser; contract reframed to stable entrypoints + thin pinned wrappers where no bin tool exists | P5.1 contract rule |
| A-4 | PARTIAL | P5 reconciliation already existed (§3.2 row 3); the P3-era gap fixed: broker-owned dispatch path, never pm-send's interactive pipeline | P3.1 dispatch mechanics |
| A-5 | PARTIAL | Per-internal-step approval would drown the owner; scoped to local/external write split with tool-level gating via the write manifest | P5.2 |
| A-6 | ACCEPT | pm-pulse auto-submit contradicted broker-gating; now approval-surfaced or standing approval recorded in `auto-resolver.md` | §3.2 row 5 |
| A-7 | ACCEPT | pm-weekly fatal deps (prompt file, config, tokens) now covered by per-run preflight (merged with D-9) | P5.1, P2.2 skill anatomy |
| A-8 | ACCEPT | Budget flags proven for Claude only; budgeted workers Claude-only first; Codex workers need accounting + stay wall-clock/ceiling-bounded | P6.3, §10 OQ4 |
| A-9 | ACCEPT | Estimates now split worker/review/resume; ~1.5–2× worker cost all-in | P6.6 |
| A-10 | ACCEPT | One merge flow per repo with exact commands; default rebase + `--ff-only` | P6.5, merge-policy.md spec |
| A-11 | ACCEPT | Workers spawn in own process groups; kills signal the PGID; health v2 checks process groups + stale worktree locks | P6.3, P6.7 |
| A-12 | ACCEPT | WhatsApp marked conditional-until-paired; paired-and-verified gate added (closed by P2.4) or owner decision to drop | Fixed decisions, §2 diagram, P2.4 |

### Codex-B — Ambition (reconciled)

| id | verdict | disposition | where applied |
|---|---|---|---|
| B-1 | FOLD | Nightly batch mode: 3–5 jobs overnight, morning approval packet, weekly throughput recorded | P6.8, M6.5 milestone |
| B-2 | FOLD | Thread→spec→Jira→factory capstone as a composition test of existing primitives (no new infrastructure permitted) | P7.5, §7 criterion |
| B-3 | FOLD+PARK | One PM artifact type first (owner-picked), skill doubles as template; full artifact family parked | P5 artifact item; §11 |
| B-4 | FOLD | Weekly product-intelligence radar over Glean/Jira, delivered inside the weekly packet, proactive-contract compliant | P7.6 |
| B-5 | PARK | CRM/Salesforce connector is new load-bearing integration surface; start Glean-mediated when a concrete question is named | §11 |
| B-6 | FOLD | Spec-first job type (default for feature-class jobs): cheap spec stage with optional owner tap, then implementation | P6.3 |
| B-7 | FOLD | One bounded review-fix resume after REVIEW; open P1 findings ride the approval packet; never more than one loop | P6.4 |
| B-8 | FOLD | Per-repo `integration: local-merge | draft-pr`; draft-PR default where a GitHub remote exists | P6.5, merge-policy.md |
| B-9 | FOLD | Monthly calibration loop (backtests, safe-lane audit, feedback synthesis) ending in proposed policy diffs — proposals only | P7.2 |
| B-10 | FOLD+PARK | Post-meeting path (decisions → log → tasks → drafts) folded; pre-meeting packet parked | P4.1; §11 |
| B-11 | FOLD | pm-weekly v2 = weekly operating packet (email + evidence + risks/asks + Jira delta + tasks) | P5.1 pm-weekly slice |
| B-12 | FOLD | Friction log → monthly factory job proposals (markdown/out-of-core only); core changes stay human-initiated, restart-gated | P7.2 |
| B-13 | FOLD | Video reframed as exec-narrative/media capability — deck/script/render for one named use case; owner-approved park remains acceptable | P5.4, §3.2 row |
| B-14 | FOLD | `product-operating-model.md`: bets/decisions/risks/next-best-action; P4 skeleton, refreshed weekly from P5 | P4.2, P5 |
| B-15 | FOLD | Leverage scorecard at M7 — measured and reported, not pass/fail (hard 10x gates would invite gaming) | §7 |

### Codex-C — Completeness & coherence

| id | verdict | disposition | where applied |
|---|---|---|---|
| C-1 | PARTIAL | Full 216-row pre-implementation traceability table rejected (duplicates the normative checklist — drift risk); per-phase item-level mini-audits added instead, P7.4 becomes the final pass | Phase exit gates, `qa/checklist-audit.md` |
| C-2 | ACCEPT | "prioritized" deleted; promise and gates now uniformly "every pm_os pipeline (§3.2 rows 1–8 + exec-narrative row)" | §1, M5, P5, §7 |
| C-3 | ACCEPT | Write-tool manifest: all 34 write-capable tools classified; one bypass test per write family; part of the M5 gate | P5.2 |
| C-4 | ACCEPT | Intake seam defined: `/factory` command + `factory:` task tag, supervisor as single writer; completion flows back to task state | P6.2, §10 OQ7 settled |
| C-5 | ACCEPT | pm-login verified via shared 401-path tests (not a standalone skill); row 7 verified at P3 and inherited — M5 gate wording now says so | P5.1, M5 gate |
| C-6 | PARTIAL | "Require one render" rejected (violates locked YAGNI steering); park now requires owner approval, and the Vision wording descaled to match the gate | §1, P5.4 |
| C-7 | PARTIAL | Founder-BD items stay ADAPTED, but the generalizable behaviors (tracker-as-source-of-truth, update-before-handled, cadence sweep) are now required; BD items classified at the P4 mini-audit, not P7 | §3.1 row 3, P4.2 |
| C-8 | ACCEPT | Bucket-level EA suite: every bucket exercised, thread-reply/reply-all equivalents, same-turn inbox state | §3.1 row 2, P3 |
| C-9 | ACCEPT | Cron manifest maps every reference cron (incl. disabled-by-default) to enabled/adapted/disabled-with-rationale | P2 artifact, §3.1 row 8 |
| C-10 | PARTIAL | Memory surfaces (MEMORY.md-equivalent, write-it-down behavior, daily log) folded; household resource templates ADAPTED at P4 mini-audit | P4.2 |
| C-11 | PARTIAL | Prerequisite verification + behavioral gates + placeholder detection folded; 7-batch onboarding interview ADAPTED with rationale recorded (single known user, policies seeded from the locked interview) | §3.1 row 11, P2 |
| C-12 | ACCEPT | Self-update job decision recorded in cron manifest; priority-map route for improvement ideas; 7.3 "both" wording fixed | P7.3 |

### Codex-D — Operability & failure modes

| id | verdict | disposition | where applied |
|---|---|---|---|
| D-1 | ACCEPT | Broker-down = fail closed; durable held-action queue; broker health in health check; "approval path unavailable" notification | P1.1 |
| D-2 | ACCEPT | Enforcement = credential starvation, not prompt discipline; write CLIs resolve credentials only via the broker path; negative test per write family | P5.2 |
| D-3 | ACCEPT | Scrubbed worker env: temp HOME, no secrets, hooks disabled where feasible, strict MCP config, generated allowlists | P6.3 |
| D-4 | ACCEPT | Sleep policy (caffeinate-while-RUNNING or awake-scheduled windows), last-tick SLA alert, launchd session/keychain validation | P6.1, P6.7, P0.4 |
| D-5 | ACCEPT | Daily ceiling enforced at spawn/resume with reserved worst-case budgets; ceiling breach kills active workers; final JSON is reconciliation, not control | P6.6 |
| D-6 | ACCEPT | SQLite with transactions (settles OQ2); supervisor-wide lock; monotonic transitions; corruption path before merges enabled | P6.1, P6.2, §10 OQ2 |
| D-7 | ACCEPT | Free-space preflight, max worktrees, retained-failure byte cap, cleanup-before-spawn as hard supervisor gates | P6.1 |
| D-8 | PARTIAL | Daily "all OK" heartbeat rejected — violates quiet-by-default (must-not-regress #1); alert-on-anomaly only (no success within expected window, or state change to degraded) | P5.5, P6.7 |
| D-9 | ACCEPT | Per-run preflight block in every scheduled SKILL.md (entrypoint, flags, files, token health, write gate); monthly check stays as the deeper audit (merges A-7) | P5.1, P2.2 |
| D-10 | PARTIAL | Detect-and-surface already in P5.3; added: token/key/browser health in health output + paused degraded state instead of scheduled retries | P5.3 items 2, 4 |
| D-11 | ACCEPT | Approvals reference stable action IDs over durable broker artifacts; Telegram carries summary + buttons only; long-payload approval in the M1 test | P1.3 |
| D-12 | ACCEPT | Skill lint + behavioral test + owner enablement + quarterly stale-skill review + monthly scope cap | P7.2 |
| D-13 | PARTIAL | P5 restructure rejected (thin-slice start already present); ordered slices 5a–5d formalized with a stop-and-re-scope rule at +50% effort | P5 header |
