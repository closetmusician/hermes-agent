# Factory Substrate Inventory
<!-- ABOUTME: Precise inventory of locally-existing assets the autonomous software factory can be built FROM.
     ABOUTME: Covers hermes repo, pm_os bin tools, ~/.claude orchestration assets, and gaps needing net-new build. -->

> Generated: 2026-07-03  
> Branch: feat/governance-plugins (v0.14.0 base, 61 commits ahead of upstream/main, ~5000 behind)  
> Purpose: Identify REUSE AS-IS / ADAPT / BUILD NEW for the factory plan.

---

## 1. Hermes Repo — Core Architecture

### 1.1 Gateway (`gateway/`)
Production-grade. Running live under launchd at `/opt/homebrew/bin/hermes`. Core components:

| File | Lines | Assessment |
|---|---|---|
| `gateway/run.py` | 18,760 | Production runtime — GatewayRunner with per-platform connect/retry, circuit breaker, reconnect watcher, IMAP IDLE, update/restart |
| `gateway/platforms/base.py` | ~161K | Base adapter ABC: media routing, MEDIA: tag extraction, per-platform send/receive |
| `gateway/platforms/email.py` | — | IMAP polling + SMTP send; IMAP IDLE working (12 fix commits) |
| `gateway/session.py` | — | Session store: PII-hashed sender IDs, reset policies, disk persistence |
| `gateway/hooks.py` | — | Event hook system: `gateway:startup`, `session:start/end/reset`, `agent:start/step/end`, `command:*`. YAML + handler.py discovery from `~/.hermes/hooks/` |
| `gateway/platform_registry.py` | — | Plugin-extensible platform registry: PlatformEntry with `cron_deliver_env_var` |
| `gateway/session_context.py` | — | Per-job ContextVars for cron isolation (session/delivery state doesn't bleed between parallel jobs) |
| `gateway/slash_access.py` | — | Slash-command dispatch including plugin-registered commands |

**25+ platform adapters** (Telegram, Discord, Slack, WhatsApp, Signal, Matrix, Email, Feishu, DingTalk, WeCom, SMS, Webhook, etc.). Entry: `gateway/platforms/ADDING_A_PLATFORM.md` defines the plug-in contract.

**Factory relevance:** The gateway is the live message bus. A factory intake channel (PRD drop, Jira webhook, morning briefing delivery) can be wired as a platform adapter or webhook handler without rebuilding. The hook system (`agent:start/end`, `command:*`) is the correct injection point for factory observability.

### 1.2 Cron Scheduler (`cron/`)
Production-grade. 1,989 lines. 3 live jobs firing today.

- `cron/scheduler.py` — `tick()` called every 60s from gateway background thread. File-based lock (`~/.hermes/cron/.tick.lock`) prevents overlap.
- `cron/jobs.py` — `jobs.json` CRUD: get_due_jobs, mark_job_run, save_job_output, advance_next_run.
- Features: per-job profile override (isolated env snapshot/restore), wake-gate (`{"wakeAgent": false}` from pre-run script = silent skip), prompt-injection scanner on assembled prompt, `no_agent=True` mode (script-only, zero LLM cost), `context_from` (chain job outputs as context), `deliver=all/origin/platform:channel`, silent-marker suppression.
- `tools/cronjob_tools.py` — agent-callable CRUD tools: `cronjob`, `list_jobs`, `cronjob_output`.

**Factory relevance:** The cron engine is the factory's overnight execution trigger. REUSE AS-IS for scheduled factory runs. `context_from` chaining already supports pipeline fan-in. The `no_agent` mode is the cost-free data-collection layer. The injection scanner is the first defense against prompt-poisoned specs.

### 1.3 Plugin System (`plugins/`, `hermes_cli/plugins.py`)
Production-grade. Three governance plugins active on this branch:

**`plugins/control-room/`** (480+ lines, production-grade):
- SQLite `audit_log` + `policy_log` + `workflow_state` tables (thread-safe).
- YAML policy engine: triggers (tool_name + arg matchers), preconditions (workflow state checks), allow/block actions. Fail-closed: any exception in the guard chain blocks the tool call.
- HTTP monitoring server (`/api/tool-calls`, `/api/blocked`, `/api/workflows`, `/api/policies`).
- Hardcoded blocks: direct outlook JS invocation, write to guard source, `execute_code` importing hermes internals, `get-foci-token` extraction, credential file read/search.
- Policies shipped: email-block (SMTP/Graph sendMail patterns), token-read-deny (FOCI token path regex) — for `terminal`, `execute_code`, `write_file`, `patch`.
- **Gap (known, documented in architecture-findings.md):** `terminal` is a universal escape hatch; guards only cover specific tool names + substrings. Self-modification via `terminal sed -i` on guard source is NOT blocked.

**`plugins/email-send-guard/`** (669 lines, production-grade after 12+ fix commits):
- 5-state machine: EMPTY → LOADED → PREVIEWED → APPROVED → SENT.
- Content-addressed drafts (SHA256 of body), per-session scoping via `current_by_session`.
- 15-minute approval TTL. Approval consumed only on successful send (post_tool_call).
- Slash command `/approve-email` returns `approved_tool_call` dict for deterministic gateway dispatch.
- Tools: `email_load_draft`, `email_show_preview`.

**`plugins/tool-registry-guard/`** — blocks pip-installing banned Office packages.

**Factory relevance:** The `control-room` policy engine is the trust ledger and audit trail in one. `workflow_state` table is the factory's state machine store. `pre_tool_call`/`post_tool_call` hooks are the enforcement layer for any factory-defined policy (e.g., "no agent writes to production unless user approved"). ADAPT for factory policies; existing guard infrastructure is solid.

### 1.4 Agent Core (`agent/`, `run_agent.py`)
Production-grade (upstream, not locally modified).

| Module | Assessment |
|---|---|
| `run_agent.py` (4,309 lines) | `AIAgent` core loop — tool dispatch, multi-provider routing, streaming |
| `agent/conversation_loop.py` (4,194 lines) | Tool-calling loop |
| `tools/delegate_tool.py` (2,801 lines) | Sub-agent delegation: spawns child AIAgent instances with isolated context, restricted toolset, own task_id/terminal session. Supports parallel batch mode (concurrent.futures). |
| `tools/kanban_tools.py` (1,297 lines) | SQLite kanban board: create/claim/complete/fail tasks, worker-task ownership enforcement, orchestrator vs worker mode detection |
| `hermes_state.py` (3,279 lines) | SQLite SessionDB with WAL, FTS5 full-text search, session chains for compression |
| `tools/checkpoint_manager.py` (large) | Shadow git store: per-turn filesystem snapshots, rollback. Transparent — LLM never sees it. |
| `agent/account_usage.py` | Usage window tracking (percent used, reset_at) — per provider |
| `agent/iteration_budget.py` | Per-job iteration budget enforcement |
| `tools/approval.py` (1,424 lines) | Dangerous-command approval gate |

**Factory relevance:** `delegate_tool` + `kanban_tools` is the factory's multi-agent spawning + work queue. `checkpoint_manager` is the rollback/resume substrate (already exists, not factory-specific). `SessionDB` is session persistence. REUSE AS-IS.

### 1.5 ACP Adapter (`acp_adapter/`)
Prototype (thin stub, ~10 lines in __init__). Agent Communication Protocol server. Not production.

### 1.6 Skills (hermes repo, `skills/`)
- `skills/devops/kanban-orchestrator/SKILL.md` — decomposition playbook for multi-profile Kanban routing. Documents anti-temptation rules, profile-discovery step, dependency chaining via `parents=[]`.
- `skills/devops/kanban-worker/SKILL.md` — worker lifecycle.
- `skills/autonomous-ai-agents/claude-code/SKILL.md` — orchestrating Claude Code CLI via `-p` (print mode) for one-shot tasks; PTY mode for interactive.
- `skills/autonomous-ai-agents/codex/SKILL.md` — Codex CLI orchestration.
- `skills/autonomous-ai-agents/kanban-codex-lane/SKILL.md` — Kanban card → Codex lane routing.

---

## 2. Plan Evidence — What Exists and Works (Per Prior Docs)

### From `phase0-state-snapshot.md`
- Gateway running live (launchd, KeepAlive, 3 crashes today correlated with DNS outage / loadavg=24).
- 3 cron jobs active: `morning-briefing` (shells to `pm_os/bin/run-morning.js`), `weekly-status`, `pr-monitor`.
- Governance plugins confirmed active: state dirs exist in `~/.hermes/` for all three.
- Email IMAP connected; Telegram connected. Discord/WhatsApp in permanent retry (misconfigured).
- Branch is **61 commits ahead of upstream/main, ~5,000 behind** — significant merge work needed.

### From `architecture-findings.md`
- Tool dispatch chain: `tool_executor.py` → `pre_tool_call` hooks (first-block-wins) → `handle_function_call` → `post_tool_call`. Single-fire contract.
- Known guard hole: `terminal` is unsandboxed shell; credential/self-modification blocks don't cover terminal path.
- Email approval flow: all 4 bugs fixed; state machine correct.

### From `clawchief-feature-checklist.md`
- Clawchief (the reference system being replaced) has: HEARTBEAT_OK silence discipline, 5-condition safe-lane gate, same-turn source-of-truth writes, Gmail message-level search, meeting-note ledger idempotency, all-calendars conflict check, approved-channels trust boundary.
- None of these are in hermes yet; all need to be built as skills/policies on top of hermes infrastructure.

---

## 3. pm_os Bin Tools — Factory Intake Layer

`~/Code/pm_os/bin/` has 49 tools. Key ones for a factory:

### Intake / Signal Sources (read-only)
| Tool | Capability |
|---|---|
| `outlook-read-mail.js` | Outlook inbox read via Graph API (list, search, filter, thread) |
| `teams-read-chats.js` | Teams 1:1 and group chat read via native skype API |
| `teams-read-channels.js` | Teams channel messages via Graph/skype hybrid |
| `glean-search.js` | Enterprise search + AI chat over internal docs |
| `graph-workbook.js` | Excel read (roadmaps, trackers) |
| `graph-list-crud.js` | SharePoint list CRUD (project trackers) |
| `graph-org-chart.js` | Org chart / directory queries |

### Draft / Response Pipeline
| Tool | Capability |
|---|---|
| `run-morning.js` (86K) | 9-step async pipeline orchestrator: fetch → classify (haiku) → dedup → draft-responses (sonnet) → push to Outlook Drafts |
| `classify-messages.js` | Anthropic haiku classification with audience awareness |
| `draft-responses.js` (39K) | Audience-aware draft generation (yk-comms + yk-voice pipeline) |
| `run-weekly.js` | 6-step weekly email pipeline: extract → dedup → filter → draft → audit |
| `run-send.js` | Interactive dispatcher: human-confirm before send to Outlook/Teams |

### Write / Send
| Tool | Capability |
|---|---|
| `outlook-send-mail.js` | Send/reply/draft via Graph API (BLOCKED from agent by control-room) |
| `teams-send-chat.js` | Teams 1:1 send via Graph API |
| `office-browser-edit.js` | Conservative Office Online fallback edits |
| `ooxml-surgery.py` (119K) | Word/PPT/Excel XML editor: extract, replace, comment CRUD |

### Auth / Token Management
| Tool | Capability |
|---|---|
| `ensure-tokens.js` | Autonomous token lifecycle: check freshness, FOCI refresh, MFA if needed |
| `get-foci-token.js` | Scope router (BLOCKED from agent by control-room) |
| `check-token-health.js` | Health check, exit 0/1/2 — usable in wake-gate scripts |

**Factory relevance:** The morning/weekly pipelines are the factory's morning briefing generator substrate. `run-morning.js` already does: Teams + Outlook fetch → haiku classify → sonnet draft → Outlook push. A factory "morning intake" job is `no_agent=True` cron pointing at `run-morning.js`. REUSE AS-IS for signal ingestion. `check-token-health.js` is the wake-gate for any job needing auth.

---

## 4. ~/.claude Orchestration Assets

### 4.1 VIBE Protocol (`~/.claude/rules/vibe-protocol.md`)
Complete TDD orchestration protocol (full text in this session's context). Key elements factory can reuse:

- **Roles defined:** PM Interviewer → Architect (`eng-planning`) → QA Test Writer → Developer → QA Tester → Orchestrator (`lead-orchestrator`). Each is a subagent persona with explicit input/output artifacts.
- **Sentinel gates:** `.gate-pre-coder` (QA acceptance tests RED before spawning coder), `.gate-pre-qa` (TDD evidence before QA). Stored in `<project>/.agents/claude-governance/`.
- **R18 Real Testing:** SavepointConnection, real DB, no mocks on internal modules. Mocking entire core dependency = P0 auto-reject.
- **R13 N=1 Escalation:** 1 failed fix cycle → STOP and ask user.
- **Commit ordering guard:** `commit-order-guard.sh` hook enforces RED before GREEN commit sequence.
- **Two-category model:** Global evergreen (`~/.claude/`) vs per-run state (`<project>/.agents/claude-governance/`).

### 4.2 Governance Scripts (`~/.claude/scripts/`)
Shell-script enforcement layer that backs the VIBE protocol:

| Script | Purpose |
|---|---|
| `pre-agent-gate.sh` | Blocks agent start until phase gate cleared |
| `post-agent-audit.sh` | Verifies artifact presence after agent stop |
| `commit-order-guard.sh` | Enforces RED→GREEN commit sequence |
| `role-enforcement.sh` | Enforces orchestrator-never-codes rule |
| `qa-artifact-ownership-guard.sh` | Blocks QA from editing implementation files |
| `completion-claim-guard.sh` | Blocks "done" claims without evidence artifacts |
| `orchestrator-context-guard.sh` | Prevents context bleed between orchestrator and coders |
| `incident-freeze-enforcer.sh` | Blocks all changes during an active incident |
| `git-safety-hook.sh` | Prevents force-push to main, checks for secrets |
| `external-comms-hook.sh` | Guards outbound communications |
| `session-journal.py` | Logs session decisions, patterns, failures |
| `synthesize-lessons.py` | Synthesizes lessons from session journals |

### 4.3 Skills Relevant to Factory (`~/.claude/skills/`)
35+ skills registered. Factory-relevant:

| Skill | Purpose | Status |
|---|---|---|
| `lead-orchestrator` | Orchestrator persona (spawn subagents, NEVER code) | Production — full VIBE integration |
| `eng-planning` | Architecture + task decomposition + contract-first specs | Production |
| `eng-stories` | PRD → stories with behavioral depth | Production |
| `e2e-test-writer` | Write acceptance tests from spec before any code | Production |
| `garry-review` | Code quality review post-implementation | Production |
| `qa` | QA test + fix cycle | Production |
| `investigate` | 5-phase root-cause debugging (auto-freeze, 3-strike escalation) | Production |
| `handoff` | Lossless session context compression to <15 lines | Production |
| `codex` | Delegate to Codex CLI (alternative to Claude Code) | Production |
| `prd-writer` | Write PRDs from requirements | Production |
| `jira-update` | Jira ticket sync (epic + stories from design docs) | Production |

### 4.4 Codex Plugin (`~/.claude/plugins/cache/openai-codex/codex/`)
- `codex:rescue` — delegates investigation/fix to Codex as second opinion.
- `codex:setup` — verifies Codex CLI readiness.

### 4.5 Worktree Tooling
- `superpowers:using-git-worktrees` skill: worktree isolation per feature/task.
- `.claude/worktrees/` in hermes repo: 30+ agent worktree snapshots present.
- `EnterWorktree`/`ExitWorktree` deferred tools in harness.

---

## 5. Gaps — What the Factory Needs That Exists Nowhere

### 5.1 Task Queue Persistence (factory-grade)
**Gap:** `kanban_tools.py` (SQLite kanban.db) exists and handles worker lifecycle. But it has no factory-specific schema: no PRD/spec linkage, no upstream source (Jira ticket ID / PRD file hash), no cost field, no gate field tracking VIBE phase. The existing kanban DB is a general-purpose work queue, not a factory task ledger.  
**Need:** Factory task record schema — {prd_ref, spec_hash, jira_ticket, phase, gate_state, cost_usd, assigned_agent, output_artifacts[]}.

### 5.2 Trust Ledger (approval audit trail)
**Gap:** `control-room`'s `audit_log` + `policy_log` tables record tool calls and policy decisions. The `workflow_state` table stores KV state. But there is no named "trust ledger" concept: no record of who (human vs agent) approved what action, no approval chain for factory-level decisions (e.g., "merge this PR to production").  
**Need:** A trust ledger linking control-room workflow state entries to human approval events with timestamps and TTLs — essentially a generalization of email-send-guard's approval model applied to factory actions.

### 5.3 Model Routing Layer
**Gap:** Hermes has per-provider adapters (30+ in `plugins/model-providers/`) and `agent/model_metadata.py`. But there is no factory-specific model router that maps task type → model tier automatically (e.g., classification → haiku, architecture → opus, code → sonnet with extended thinking). `pm_os/bin/classify-messages.js` hardcodes haiku; `draft-responses.js` hardcodes sonnet. No unified policy.  
**Need:** A model routing policy table: {task_type → model_id, max_tokens, temperature, cost_ceiling}. Referenced by factory job definitions.

### 5.4 Cost Metering
**Gap:** `agent/account_usage.py` tracks provider usage windows (% used, reset_at) per provider. No per-job cost tracking in dollars or tokens. The kanban DB has no cost field. There is no budget gate that stops a run if cumulative cost exceeds a threshold.  
**Need:** Per-job token/cost logging (input + output + cache tokens × price), cumulative daily budget gate, cost report in morning briefing.

### 5.5 Morning Briefing Generator (factory status)
**Gap:** `pm_os/bin/run-morning.js` generates a PM briefing (Teams + Outlook triage). There is no factory-equivalent morning briefing: "what did the overnight factory build, what passed QA, what failed, what cost $X, what needs human review today."  
**Need:** Factory daily digest cron job. Output: completed tasks, test results, failed tasks + root cause, cost, PRs opened, items needing human gate (approve/reject queue).

### 5.6 Checkpoint / Resume for Factory Jobs
**Gap:** `tools/checkpoint_manager.py` does filesystem snapshots per turn (shadow git store). This covers rollback within a single agent run. But if a factory job crashes mid-run (agent dies, OOM, timeout), there is no way to resume from the last successful gate. The cron scheduler's `save_job_output` records output but has no mid-run state.  
**Need:** Factory job phase checkpointing: persist {job_id, phase, gate_cleared[], artifact_paths[]} so a restart can skip completed phases. The control-room `workflow_state` table is the natural store; needs a checkpoint protocol on top.

### 5.7 PRD / Spec Intake Normalization
**Gap:** No standardized intake format for factory jobs. VIBE protocol assumes a human-written PRD in `docs/`. A factory needs a machine-parseable spec format with: source (Jira ticket / file / Slack thread), acceptance criteria, owner, priority, model budget.  
**Need:** Spec schema (JSON or YAML frontmatter on markdown) + intake parser. `add-spec-frontmatter.sh` exists in `~/.claude/scripts/` but is for retrospective tagging, not intake.

### 5.8 Agent Fleet Orchestration (multi-repo, multi-worktree)
**Gap:** `delegate_tool.py` spawns sub-agents in the same process (concurrent.futures). `kanban-codex-lane` skill routes one card to one Codex process in one worktree. But there is no fleet manager: no persistent pool of agent processes, no queue-draining dispatcher that picks up kanban tasks and routes to available workers across multiple repos simultaneously.  
**Need:** A dispatcher daemon (building on kanban's existing SQLite DB and the `plugins/kanban/systemd/hermes-kanban-dispatcher.service` stub that already exists) capable of: idle-worker pool, task pickup, worktree isolation per task, crash recovery via restart.

---

## Summary Matrix

| Component | Status | Factory Use |
|---|---|---|
| Gateway + platform adapters | Production | Intake channel (webhook, email) + delivery |
| Cron scheduler + jobs.json | Production | Overnight execution trigger |
| control-room (audit + policy + workflow_state) | Production | Trust ledger base + tool enforcement |
| email-send-guard (approval state machine) | Production | Template for human-gate pattern |
| hook system (agent:start/end etc.) | Production | Observability injection point |
| delegate_tool (subagent spawning) | Production | Factory worker spawning |
| kanban_tools + kanban.db | Production | Work queue (needs factory schema extension) |
| hermes_state SessionDB | Production | Session persistence |
| checkpoint_manager | Production | Per-turn rollback (not factory phase rollback) |
| VIBE protocol (roles, gates, scripts) | Production | Quality bar enforcement for all factory output |
| lead-orchestrator + eng-planning skills | Production | Factory orchestration layer |
| pm_os/run-morning.js pipeline | Production | Signal ingestion + briefing (REUSE for morning intake) |
| pm_os auth tools (ensure-tokens, FOCI) | Production | Token lifecycle for all pm_os tool calls |
| pm_os outlook/teams read tools | Production | Factory intake sources |
| ACP adapter | Prototype | Not ready |
| Task queue (factory schema) | MISSING | BUILD NEW |
| Trust ledger (generalized approval chain) | MISSING | BUILD NEW (extend control-room) |
| Model routing policy | MISSING | BUILD NEW |
| Cost metering (per-job $) | MISSING | BUILD NEW |
| Factory morning briefing | MISSING | BUILD NEW (extend run-morning.js pattern) |
| Factory job phase checkpointing | MISSING | BUILD NEW (extend workflow_state) |
| PRD/spec intake normalization | MISSING | BUILD NEW |
| Fleet dispatcher daemon | PARTIAL (systemd stub exists) | ADAPT kanban dispatcher stub |
