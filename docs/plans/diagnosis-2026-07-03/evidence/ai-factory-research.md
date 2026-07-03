# ABOUTME: Research Track C for the "AI factory" planning effort — a personal, single-user
# ABOUTME: build-assistant subsystem that runs headless Claude Code + Codex CLI coding sessions
# ABOUTME: on one macOS machine, drafting changes on isolated branches with test + review gates
# ABOUTME: before a one-tap merge approval. Covers headless CLIs, worktree isolation, supervisor
# ABOUTME: architecture options, job lifecycle, cost/budget guidance, and risks. Written 2026-07-03.

# AI Factory Research (Track C)

**Framing:** Personal "CI bot that opens PRs for me to approve." Single machine, single user,
own repos. Gates are lightweight guardrails, NOT a hardened multi-tenant control plane. Optimize
for "wake up to 3 reviewable branches," not for isolation against hostile tenants.

Local environment verified 2026-07-03:
- `claude` (Claude Code) **2.1.170**
- `codex-cli` **0.139.0**
- `git` **2.50.1**

---

## 1. Headless Claude Code Capability Sheet

Headless = `claude -p "<prompt>"` (a.k.a. `--print`): runs the turn(s) and exits. Ideal for
scripted/cron/queue invocation. The workspace-trust dialog is auto-skipped in `-p` mode (and
whenever stdout is not a TTY), so **only point it at directories you trust**.

### Core flags (verified via `claude --help`, v2.1.170)

| Flag | Purpose / notes for the factory |
|---|---|
| `-p, --print` | Non-interactive: print response and exit. The headless entry point. |
| `--output-format text\|json\|stream-json` | `json` = single final result **including `total_cost_usd` and token counts** (use for budget accounting). `stream-json` = realtime JSONL events (use for live progress monitoring). Only valid with `--print`. |
| `--input-format text\|stream-json` | `stream-json` lets you feed the session realtime input (multi-turn driving). |
| `--include-partial-messages` | Emits partial chunks as they arrive (with `--print` + `stream-json`). Finer-grained progress. |
| `--include-hook-events` | Surface all hook lifecycle events in the stream (with `stream-json`). Lets the supervisor observe gate hooks. |
| `--json-schema '<schema>'` | Structured-output validation — force the final message into a known shape (e.g. `{status, branch, summary, test_result}`) so the supervisor parses reliably instead of scraping prose. |
| `--permission-mode <mode>` | `default`, `acceptEdits`, `auto`, `plan`, `dontAsk`, `bypassPermissions`. See permissioning below. |
| `--allowedTools "Bash(git *) Edit Read Write"` / `--disallowedTools` | Allowlist/denylist of tools. **This is the preferred safety lever for headless runs** — scope tools tightly instead of reaching for bypass. |
| `--tools "Bash,Edit,Read"` | Restrict to a subset of the built-in tool set (`""` disables all, `default` = all). |
| `--add-dir <dir>` | Grant tool access to extra dirs (e.g. a shared cache) beyond the worktree cwd. |
| `--model <alias\|full>` | `opus` / `sonnet` / `haiku` / `fable` aliases or full id (`claude-fable-5`). Route cheap jobs to Sonnet/Haiku, hard jobs to Opus. |
| `--fallback-model <a,b>` | Auto-fallback when primary is overloaded/unavailable (only with `--print`). Resilience for unattended runs. |
| `--effort low\|medium\|high\|xhigh\|max` | Reasoning effort dial — another cost/quality lever per job. |
| `--max-budget-usd <amount>` | **Hard per-invocation dollar cap** (only with `--print`). First-class per-job budget enforcement — the single most important factory flag. |
| `--mcp-config <files/json>` + `--strict-mcp-config` | Load only the MCP servers a job needs; `--strict` ignores ambient MCP config for reproducibility. |
| `--settings <file-or-json>` / `--setting-sources user,project,local` | Pin exactly which settings load — makes headless runs deterministic. |
| `--append-system-prompt` / `--system-prompt[-file]` | Inject factory role instructions ("you are a worker; branch, implement, test, commit, STOP"). |
| `--agents '<json>'` | Define custom agents inline for the session. |
| `--session-id <uuid>` / `--no-session-persistence` | Pin a session id (correlate job↔session) or disable persistence entirely. |
| `-r, --resume [id]` / `-c, --continue` / `--fork-session` | **Resume/continue** a prior session by id (or most recent in cwd). `--fork-session` branches a new id on resume. Enables checkpoint → budget-refill → continue, and post-review "address feedback" turns on the same context. |
| `--from-pr [num/url]` | Resume a session linked to a PR — useful for "iterate on my review comments" loops. |
| `-w, --worktree [name]` + `--tmux` | **Built-in worktree creation** for the session, optionally in a tmux pane. Claude Code can manage per-session worktrees itself. |
| `--bare` | Minimal mode: skips hooks, plugins, auto-memory, CLAUDE.md discovery, keychain. Fast, hermetic, predictable — good for pure worker runs where you inject context explicitly. Auth via `ANTHROPIC_API_KEY`/`apiKeyHelper` only. |
| `claude agents [--json]` | Manage **background agents**; `--json` lists active sessions for scripting without a TTY. A supervisor can poll this to see live workers. |

### Permissioning (recommendation)

Modes: `default` (prompt on risk), `acceptEdits` (auto-accept file edits, still gate other tools),
`auto` (classifier decides), `plan` (read-only planning), `dontAsk`, `bypassPermissions`.

**Recommended default for factory workers:** `--permission-mode acceptEdits` **plus a tight
`--allowedTools` allowlist** (e.g. `Edit Write Read "Bash(git *)" "Bash(npm test*)" "Bash(pytest*)"`).
This lets a worker edit files and run its own build/test loop unattended without prompting, while
still refusing arbitrary shell. Do **NOT** use `--dangerously-skip-permissions` /
`bypassPermissions` as the default. It is only justifiable as an explicitly-scoped option inside a
throwaway sandboxed worktree with no secrets and no network — and even then the allowlist path is
usually enough. Anthropic's own guidance reserves skip-permissions for "sandboxes with no internet
access."

### IO formats for the supervisor
- **Dispatch:** `claude -p --output-format json --json-schema … --max-budget-usd 3 …` → parse one
  JSON object with `total_cost_usd`, token counts, and your schema'd result.
- **Live monitoring:** `--output-format stream-json --include-partial-messages
  --include-hook-events` → tail JSONL for progress + gate-hook outcomes.

---

## 2. Codex CLI `exec` Capability Sheet

Non-interactive entry point: `codex exec [OPTIONS] [PROMPT]` (alias `codex e`). Prompt via arg or
stdin (`-`). Verified via `codex exec --help`, v0.139.0.

| Flag | Purpose / notes |
|---|---|
| `-s, --sandbox read-only\|workspace-write\|danger-full-access` | Filesystem sandbox policy. **`workspace-write` is the right default for a worker** (can edit its worktree, not the wider machine). `read-only` for review/analysis jobs. |
| `-a, --ask-for-approval untrusted\|on-request\|never` (top-level `codex`) | When to require human approval. For unattended exec use **`never`** (failures return to the model) paired with a real sandbox; `untrusted` only runs a trusted command set and escalates otherwise. `on-failure` is deprecated. |
| `--dangerously-bypass-approvals-and-sandbox` | Skip all prompts AND sandbox. "EXTREMELY DANGEROUS," for externally-sandboxed envs only. **Do not use** as factory default. |
| `-C, --cd <DIR>` | Set the working root — point it at the job's worktree. |
| `--add-dir <DIR>` | Extra writable dirs alongside the primary workspace. |
| `-m, --model <MODEL>` | Model selection (e.g. GPT-5.x family / o-series). |
| `-c, --config key=value` | Override any `~/.codex/config.toml` value (dotted paths, TOML-parsed). E.g. `-c model="o3"`, `-c 'sandbox_permissions=["disk-full-read-access"]'`. |
| `-p, --profile <name>` | Layer a named `$CODEX_HOME/<name>.config.toml` — pre-baked "worker" vs "reviewer" profiles. |
| `--json` | Emit events to stdout as **JSONL** — the monitoring stream. |
| `-o, --output-last-message <FILE>` | Write the agent's final message to a file — clean handoff to the supervisor. |
| `--output-schema <FILE>` | JSON Schema for the model's final response shape (structured result, mirrors Claude's `--json-schema`). |
| `--ephemeral` | Run without persisting session files to disk — hermetic worker runs. |
| `--ignore-user-config` / `--ignore-rules` | Ignore `config.toml` / execpolicy `.rules` for reproducible runs. |
| `--skip-git-repo-check` | Allow running outside a git repo (rarely needed here). |
| `codex exec resume [--last]` | **Resume a prior exec session** by id or most recent — checkpoint/continue support. |
| `codex exec review` / `codex review` | Non-interactive **code review** (see below). |

### Codex as the review gate (`codex review`)
`codex review` runs a review non-interactively and is purpose-built for the factory's review gate:
- `--uncommitted` — review staged + unstaged + untracked changes.
- `--base <BRANCH>` — review the branch diff vs a base (e.g. `main`).
- `--commit <SHA>` — review one commit's changes.
- `--title <TITLE>` and a custom prompt (`-` for stdin) — steer the review.

This gives an **independent second-model review** (Codex reviewing Claude's branch, or vice-versa)
without wiring a bespoke reviewer — exactly the "agent-checks-agent" layer beneath human approval.

### Also available
`codex mcp` / `codex mcp-server` (Codex as an MCP server over stdio), `codex apply` (apply the last
produced diff as a `git apply`), `codex sandbox` (run arbitrary commands in Codex's sandbox),
`codex fork`, `codex doctor`.

---

## 3. Isolation Model Recommendation — worktree-per-job

**Recommendation: one git worktree + one branch per job.** This is the near-universal 2026 pattern
for parallel coding agents ("every agent gets one worktree, no exceptions") and both CLIs support
it natively (Claude `--worktree`; Codex via `-C <dir>`).

### Pattern
```
# Supervisor, from the canonical repo clone:
git worktree add -b factory/<repo>/<job-id>-<slug> \
    ../.factory-worktrees/<repo>/<job-id> origin/main
# → worker runs with cwd = that worktree, branch already checked out.
```

### Branch naming
`factory/<repo>/<job-id>-<slug>` (e.g. `factory/hermes/j0421-fix-email-hash`). Namespaced,
greppable, sortable, and trivially `git worktree list`-able at a glance. Worktree dir mirrors it:
`../.factory-worktrees/<repo>/<job-id>`.

### Cleanup
```
git worktree remove <path>            # after merge OR abandon (use -f if dirty)
git worktree prune                    # reap stale admin entries
git branch -d factory/...             # delete merged branch (-D to force-drop abandoned)
```
Run `worktree remove` + `branch -d` in the merge/cleanup step; a periodic `worktree prune` reaps
crashes. Keep an "abandoned" retention window (e.g. keep dir 24h) so you can inspect failures.

### Gotchas (from 2026 field reports)
- **Shared object store, separate working dir + index:** cheap to branch, but each worktree is a
  full checkout → disk + I/O compound. `git worktree add --lock` for long jobs to avoid accidental prune.
- **Duplicated `node_modules` / build artifacts:** each worktree needs its own install → slow,
  disk-heavy. Mitigate with a shared package cache (`--add-dir` to the cache), pnpm/uv global store,
  or `git sparse-checkout set <paths>` to check out only what a job touches.
- **Runtime isolation ≠ filesystem isolation.** Worktrees do NOT isolate ports, dev servers,
  databases, `.env` secrets, or test state. Two workers running `npm run dev` collide on port 3000.
  For a personal tool this is usually fine (stagger jobs, or assign per-job port offsets / a
  `PORT=$((3000 + job_slot))` convention); note it as a known limit rather than building containers.
- **Hooks & lockfiles:** shared `.git/hooks` fire in every worktree; a stale `.git/worktrees/*/HEAD.lock`
  after a crash blocks operations — `git worktree prune` clears it.
- **Never run two jobs on the same branch** (git refuses to check out a branch already in another
  worktree, which is a feature — respect it).

---

## 4. Supervisor Architecture — Options & Recommendation

Three viable shapes for the coordinator on one macOS box. All share the same worker contract
(§1/§2) and job store; they differ in *who drives the loop*.

### Option A — Cron-driven stateless supervisor (hermes-cron "tick")
A short-lived script fires every N minutes (launchd/cron or hermes' own scheduler). Each tick:
reads the job store (SQLite/JSON), advances each job one state (spawn ready jobs up to a concurrency
cap, poll running workers, run gates on finished ones, post approval requests), then exits. No
long-lived process; all state is on disk.
- **Pros:** dead simple; crash-proof (state is durable, next tick resumes); no supervisor context to
  exhaust because the supervisor is *not an LLM session* — it's plain code; trivially observable.
- **Cons:** latency = tick interval; concurrency logic you write yourself; not "live."
- **Best when:** jobs are minutes-to-hours long and "check every 2 min" is fine (it almost always is
  for a personal PR bot).

### Option B — Long-running queue daemon
A persistent process (Python/Node/`launchd` KeepAlive) owns an in-memory queue + worker pool,
spawns `claude -p` / `codex exec` as subprocesses, tails their `stream-json`/JSONL for live progress,
enforces per-job timeouts/budgets, and drives gates on completion. Backed by a durable store so it
can recover on restart.
- **Pros:** real-time monitoring and budget kill-switches; immediate spawn on intake; natural place
  for a small local dashboard.
- **Cons:** more moving parts; must handle its own crash recovery, zombie subprocess reaping, and
  backpressure. This is essentially a mini Gas Town "Deacon/Witness."
- **Best when:** you want live streaming visibility and sub-minute reactivity.

### Option C — Harness Workflow / Agent-tool orchestration (LLM-as-supervisor)
Use the Claude Code harness's own primitives: a lead session uses the **Agent tool**
(`run_in_background: true` background subagents), **Monitor** (stream events from long-running
scripts), **TaskCreate/TaskList** (shared task list), and **Workflow** to coordinate. Or the
experimental **agent teams** (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`): a lead session spawns
teammates with a shared, file-locked task list and inter-agent mailbox, and hooks
(`TaskCreated`/`TaskCompleted`/`TeammateIdle`, exit code 2 = block + feedback) act as gates.
- **Pros:** zero bespoke queue code; task dependencies + gate hooks are built in; great for
  *interactive* "spin up 3 workers now and watch them."
- **Cons:** **the supervisor is itself an LLM session → context exhaustion and token cost scale with
  the run** (docs explicitly warn teams "use significantly more tokens"). Documented limitations:
  no session resumption for in-process teammates, task-status lag, one team per session, no nested
  teams, lead fixed for its lifetime. Not built to persist a job queue across days.
- **Best when:** short, interactive bursts — not an always-on unattended factory.

### RECOMMENDATION: **Option A (cron-driven stateless supervisor) as the backbone, with Option B's live-monitoring bolted on only where you want it.**

Rationale for a personal tool: the supervisor's job (spawn / poll / gate / notify / merge) is
deterministic plumbing that should **not** be an LLM — making it plain code sidesteps the single
biggest reported failure of agentic orchestration (supervisor **context exhaustion**) and the
biggest cost trap (a persistent Opus "manager" burning tokens to babysit). Cron ticks give
crash-proof durability for free: state lives in SQLite, every tick is idempotent, a laptop reboot
loses nothing. Reserve LLM reasoning for the *workers* and the *review gate* (Codex), never the
loop. Use `stream-json`/`--json` tails (Option B style) opportunistically for a job you're actively
watching, and lean on the Monitor tool for ad-hoc "ping me when branch X is ready" during a session.
Borrow three ideas from Gas Town without its $100/hr complexity: (1) a dedicated **merge step** that
serializes integration, (2) **health-check/timeout recovery** for stuck workers, (3) **durable,
git-backed job state** so crashes don't lose work.

---

## 5. Job Lifecycle Spec Sketch

State machine (persisted per job in SQLite/JSON; supervisor advances one step per tick):

```
INTAKE      → user files a job: {repo, base=main, prompt/spec, model, budget_usd, timeout_min}.
              Assign job-id. State = QUEUED.
SPAWN       → if running < concurrency_cap and repo not merge-locked:
              git worktree add -b factory/<repo>/<id>-<slug> <path> origin/main
              launch worker (detached), record PID + start time. State = RUNNING.
   worker  = claude -p --output-format json --json-schema <result> \
               --permission-mode acceptEdits --allowedTools "Edit Write Read 'Bash(git *)' <test cmds>" \
               --model <m> --max-budget-usd <b> --system-prompt "<worker contract: branch is ready,
               implement the spec, run tests, commit, then STOP with schema'd result>"
             (or: codex exec -s workspace-write -a never --json -o result.json -C <path> "<spec>")
MONITOR     → each tick: is PID alive? tail stream for progress/errors. Enforce:
              • timeout  → SIGTERM worker, State = FAILED(timeout).
              • budget   → --max-budget-usd is the hard stop; also sum total_cost_usd across resumes
                           and refuse to resume past the cap. State = FAILED(budget) if exceeded.
TEST GATE   → on worker exit: run the repo's own suite in the worktree (npm test / pytest / make check).
              Deterministic, strict, non-negotiable. FAIL → optionally one auto-fix resume
              (claude --resume <sid> "tests failed: <output>, fix"), else State = NEEDS_ATTENTION.
REVIEW GATE → codex review --base main  (independent second model) → capture findings + pass/fail.
              Optionally also a Claude self-review or garry-review pass. Attach findings to the job.
APPROVAL    → push branch, open a draft PR (gh pr create) OR emit a one-tap notification
              (summary + diff stat + test result + review findings + [Approve]/[Reject]). State = AWAITING_APPROVAL.
MERGE       → on human approve: acquire per-repo merge lock → git fetch → rebase/merge onto latest main
              → re-run test gate on the merged result → gh pr merge / git merge --ff-only → release lock.
              Conflict/failure → State = NEEDS_ATTENTION (escalate, don't force).
CLEANUP     → git worktree remove <path>; git branch -d factory/...; git worktree prune.
              Keep FAILED/abandoned worktrees for a retention window for inspection. State = DONE.
```

Key contract rules: workers **STOP** after producing their branch + schema'd result (they never
merge or touch `main`); the **supervisor owns all git integration**; every gate writes a durable
artifact so a later tick (or you) can see why a job is where it is.

---

## 6. Cost / Budget Guidance

### Realistic per-job token costs (2026 rates)
A representative agentic edit ≈ 200K input tokens (heavily cached) + ~30K output:
- **Sonnet 4.6:** ~**$1.05**/job (≈$0.60 in + $0.45 out)
- **Opus 4.8:** ~**$1.75**/job
- **Fable 5:** ~**$3.50**/job

So a nightly batch of ~10 mixed jobs realistically lands in the **$10–35/night** range — not the
$100/hr Gas Town number (that's 12–30 continuous parallel agents). Model routing is the primary cost
dial: default workers to **Sonnet**, escalate to Opus only for jobs flagged hard; use Haiku/low
effort for trivial ones.

### Billing reality (important)
Since **2026-06-15**, Agent SDK / headless `claude -p` usage bills against a **separate API-rate
credit pool** on subscription plans: **$20/mo (Pro), $100/mo (Max 5x), $200/mo (Max 20x)**. When the
pool empties, **headless automation simply stops** unless overflow-to-payment-method is enabled.
Implication: a runaway loop can't silently rack up an unbounded bill on a capped plan — but it *can*
burn your whole month's automation credits in one bad night. So still cap per-job.

### Enforcement mechanisms (layered)
1. **Per-job hard cap:** `claude --max-budget-usd <b>` on every worker invocation. First line of defense.
2. **Per-job wall-clock timeout:** supervisor SIGTERMs at `timeout_min` regardless of tokens.
3. **Cumulative accounting:** parse `total_cost_usd` from each worker's JSON result; sum across
   resumes; store per-job and per-day. Refuse to spawn/resume once a **daily budget ceiling** is hit
   (a supervisor-level kill switch — "per-pipeline hard cap" is what separates a bad night from a bad quarter).
4. **Model/effort routing:** cheapest model that clears the job; `--effort` low for rote work.
5. **Concurrency cap:** limit simultaneous workers (also bounds worst-case spend rate).
6. **Overflow billing OFF** on the plan unless you deliberately want spillover past the credit pool.

---

## 7. Risks & Mitigations (proportionate to a personal tool)

| Risk | Likelihood | Mitigation (lightweight) |
|---|---|---|
| **Runaway cost / infinite loop** | Med | `--max-budget-usd` per job + wall-clock timeout + daily ceiling kill-switch + capped credit pool. Three independent stops. |
| **Supervisor context exhaustion** | Med (if you pick Option C) | Make the supervisor plain code (Option A), not an LLM. Then it literally cannot exhaust context. |
| **Merge conflicts between parallel jobs** (the #1 reported issue — two workers editing the same file) | Med | Serialize the merge step behind a per-repo lock; rebase-onto-latest-main + re-test before merge; at intake, warn if two queued jobs target overlapping paths; keep jobs domain/feature-scoped. |
| **Flaky test gate** | Med | Deterministic gate, but allow one retry on failure to filter flakes; record both runs; if it flips, mark NEEDS_ATTENTION rather than auto-merging. Keep the human as final approver. |
| **Worker goes rogue on the filesystem/secrets** | Low | Tight `--allowedTools` allowlist + `acceptEdits` (not bypass); Codex `-s workspace-write`; no secrets in worktrees; never `--dangerously-skip-permissions` as default. |
| **Stuck / crashed worker** | Med | Timeout + liveness check each tick; on death → FAILED, worktree retained for inspection; `git worktree prune` reaps admin cruft. (Gas Town's "Witness" idea, minimized.) |
| **Silent worker "success" that didn't actually do the work** | Med | Schema'd result (`--json-schema`) + independent test gate + `codex review` diff review. Never trust "agent reported done" — trust the diff + green tests. |
| **Disk blowup from N worktrees × node_modules** | Low–Med | Shared package store (pnpm/uv), sparse-checkout, cleanup on merge, retention window on failures. |
| **Runtime collisions (ports/DB) between concurrent jobs** | Low | Per-job port offset convention; stagger jobs that need a live server; documented as a known limit, not engineered around. |
| **Lost job state on laptop reboot/crash** | Low | Durable SQLite/JSON job store + git-backed branches; every tick idempotent → resume cleanly. |

---

## 8. Sources

- Claude Code — Orchestrate teams of Claude Code sessions (agent teams docs): https://code.claude.com/docs/en/agent-teams
- Claude Code Headless Mode: The Complete Self-Hosting Guide (2026) — amux: https://amux.io/guides/claude-code-headless/
- Multi-Agent Orchestration: Running 10+ Claude Instances in Parallel (Part 3) — DEV: https://dev.to/bredmond1019/multi-agent-orchestration-running-10-claude-instances-in-parallel-part-3-29da
- Conductors to Orchestrators: The Future of Agentic Coding — O'Reilly Radar: https://www.oreilly.com/radar/conductors-to-orchestrators-the-future-of-agentic-coding/
- The Code Agent Orchestra — Addy Osmani: https://addyosmani.com/blog/code-agent-orchestra/
- Behold the Zerg! Parallel Claude Code Orchestration — RockCyber: https://www.rockcybermusings.com/p/behold-zerg-parallel-claude-code-orchestration
- Git Worktrees for AI Coding: How to Run Multiple Agents Without Conflicts — MindStudio: https://www.mindstudio.ai/blog/git-worktrees-parallel-ai-coding-agents
- Parallel Agentic Development With Git Worktrees: A Practical Playbook — MindStudio: https://www.mindstudio.ai/blog/parallel-agentic-development-git-worktrees
- Git Worktrees Need Runtime Isolation for Parallel AI Agent Development — Penligent: https://www.penligent.ai/hackinglabs/git-worktrees-need-runtime-isolation-for-parallel-ai-agent-development/
- How to Use Git Worktrees for Parallel AI Agent Execution — Augment Code: https://www.augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution
- Git Worktrees + Claude Code: The 2026 Playbook — Developers Digest: https://www.developersdigest.tech/blog/git-worktrees-claude-code-parallel-agents-guide
- Gas Town: Steve Yegge's Multi-Agent Factory and What It Means for Codex CLI — Codex KB: https://codex.danielvaughan.com/2026/04/08/gas-town-multi-agent-factory/
- 2026 Agentic Coding Trends - Implementation Guide — Hugging Face: https://huggingface.co/blog/Svngoku/agentic-coding-trends-2026
- Beyond One-Shot Prompts: 5 Claude Code Workflow Patterns — MindStudio: https://www.mindstudio.ai/blog/claude-code-agentic-workflow-patterns
- Agentic Code Review — Addy Osmani: https://addyosmani.com/blog/agentic-code-review/
- AI Coding Costs (2026): Claude vs Codex vs Gemini — Morph: https://www.morphllm.com/ai-coding-costs
- Claude Code API Cost (2026): Per-Token Math + How to Cut It — Morph: https://www.morphllm.com/claude-code-api-cost
- What Claude Code Actually Costs in 2026 — UsageBox: https://usagebox.com/articles/claude-code-cost-2026-per-token-per-month-june-deadlines
- Scale Parallel AI Agents Without Losing Quality (2026) — Unblocked: https://getunblocked.com/blog/scale-parallel-ai-agents/
- Parallel Concurrency in Production AI Agents: DAG Scheduling, Fan-Out/Fan-In — Zylos Research: https://zylos.ai/research/2026-04-26-parallel-concurrency-agent-execution/
- agent-orchestrator (spawns agents, handles CI fixes, merge conflicts, reviews) — GitHub/AgentWrapper: https://github.com/AgentWrapper/agent-orchestrator
- 9 Open-Source Agent Orchestrators for AI Coding (2026) — Augment Code: https://www.augmentcode.com/tools/open-source-agent-orchestrators

*Local capability data verified directly via `claude --help`, `claude agents --help`, `codex --help`,
`codex exec --help`, `codex review --help`, and `git worktree add/remove --help` on 2026-07-03.*
