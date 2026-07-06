# ABOUTME: State-of-the-art survey for hermes AI factory planning, July 2026.
# ABOUTME: Covers headless CLIs, OpenRouter overflow models, factory patterns in the wild, self-improvement mechanisms, and overnight reliability engineering. Extends ai-factory-research.md without duplicating it.

# Factory SOTA 2026 — Extended Research

**Written:** 2026-07-03. **Scope:** extends `ai-factory-research.md`; read that first. All sections here cover ground NOT already documented there.

---

## 1. Claude Code Scheduling Layer — What the Existing Research Missed

### 1.1 Three Scheduling Tiers (Official Docs, 2026)

The existing research covers `claude -p` (headless exec) well. What it omits is the full scheduling layer that ships alongside it:

| Mode | Runs on | Requires machine on | Requires open session | Persistent across restarts | Local file access |
|---|---|---|---|---|---|
| **Cloud Routines** | Anthropic infra | No | No | Yes | No (fresh clone) |
| **Desktop scheduled tasks** | Your machine | Yes | No | Yes | Yes |
| **`/loop` + `CronCreate`** | Your machine | Yes | Yes | Restored on `--resume` if <7d | Yes |

**Cloud Routines** (shipped April 2026): define a task + cron schedule → Anthropic runs it without a local process. Triggers: time-based cron, webhook, or GitHub event. Minimum interval: 1 hour. No local file access (fresh clone per run). Outputs go to a file, webhook, or email. Permission prompts: none (fully autonomous). Use for: daily standups, nightly batch runs, PR babysitting that should continue when your laptop is off.

**Desktop scheduled tasks**: launchd/cron style, 1-minute minimum, full local file + MCP access. Use for: factory ticks that need the working tree.

**`/loop` with CronCreate**: session-scoped, 50 tasks max per session, 7-day auto-expiry, restored on `--resume`. The `loop.md` file (`.claude/loop.md` or `~/.claude/loop.md`) customizes the default maintenance prompt. A bare `/loop` fires Claude's built-in maintenance behavior: continue unfinished work → tend PR (review comments, CI failures, merge conflicts) → cleanup passes. This is a free "stay-on-top-of-the-branch" worker for any open session. Session-scoped tasks are also restored after `--resume` as long as they haven't expired.

**RemoteTrigger tool**: fires a named cloud routine by name from inside a session — enables event-driven dispatch ("when tests pass, trigger the deploy-staging routine").

**Key limit discovered**: Cloud Routines cannot access local files — they get a fresh clone. This means cloud routines are best for cron-driven GitHub Actions-style work (PR reviews, issue triage, status reports) NOT for jobs that need to write to the local worktree. Desktop tasks or `claude -p` launched by launchd are required for the factory's write-to-worktree pattern.

Source: https://code.claude.com/docs/en/scheduled-tasks

### 1.2 Rate Limit Reality (March 2026, The Register)

Anthropic admitted publicly: "people are hitting usage limits in Claude Code way faster than expected." Contributing factors documented:

- Quota reductions during peak hours (affecting ~7% of users)
- A promotion that doubled limits outside peak windows ended March 28
- Suspected caching bugs inflating token costs 10–20x in some versions

**Critical automation hazard (not in existing research):** Rate-limit errors from `claude -p` surface as generic failures and silently trigger retry loops. "One session in a loop can drain your daily budget in minutes." Factory supervisors MUST catch 429 errors explicitly, distinguish them from task failures, apply exponential backoff, and block spawning new workers when the rate-limit ceiling is near — not just enforce per-job budgets.

Source: https://www.theregister.com/2026/03/31/anthropic_claude_code_limits/

### 1.3 June 15 Billing Change — What Actually Happened

The announced credit-pool split (headless usage → separate $20/$100/$200 monthly pool) was **cancelled before it took effect**. As of July 2026, `claude -p` and Agent SDK usage draw from the same subscription pool as interactive sessions. This matters because:

1. Interactive + headless usage compete for the same quota. Running a nightly batch can reduce what's available for daytime interactive work.
2. The credit-pool architecture may return — design the supervisor to account for it as a possible billing model shift.
3. The $200 Max 20x plan remains the ceiling available under subscription; anything beyond requires direct API key billing (pay-per-token with no cap).

Sources: https://www.aicodex.to/articles/claude-subscription-credit-changes, https://venturebeat.com/technology/anthropic-reinstates-openclaw-and-third-party-agent-usage-on-claude-subscriptions-with-a-catch

---

## 2. Claude Code Architecture: What the ArXiv Paper Reveals

**Source:** "Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems" (arxiv 2604.14228, 2026)

Key findings for hermes:

**The harness is 98.4% of the code; reasoning is ~1.6%.** Claude Code's architectural lesson: invest in execution infrastructure (permissions, safety, compaction, recovery, hooks), not orchestration cleverness.

**Sidechain transcript architecture** for subagents: each subagent gets its own isolated context window stored in a separate file. Only the summary returns to the parent. This is how you scale without parent context inflation — and it's already built in.

**Graduated permission bubbles**: subagents can "bubble" unrecognized actions up to the parent terminal for approval. This is the native escalation path — a subagent that hits something outside its scope doesn't die, it asks the parent.

**Approval fatigue finding**: users approve ~93% of permission prompts. Automated safety boundaries (deny rules, tool allowlists, sandboxing) outperform interactive prompts for safety — the human can't be relied on as the gate. This validates the `--allowedTools` approach over `bypassPermissions`.

**Six open design directions the paper flags:**
1. Observability-evaluation gap (silent failures with no diagnostic)
2. Cross-session persistence (memory, longitudinal state)
3. Harness boundary evolution
4. Horizon scaling (scientific-program-scale tasks)
5. Governance at scale for multi-agent fleets
6. Long-term human capability degradation (27% of Claude tasks never attempted without it; 17% lower comprehension in AI-assisted conditions — monitor for this)

**Context compaction:** 5-layer compaction pipeline, auto-compact at ~95% window usage, 84% token reduction in 100-turn benchmark.

Source: https://arxiv.org/html/2604.14228v1

---

## 3. OpenRouter Overflow Layer — Detailed Model Landscape

### 3.1 Top Coding Models Available via OpenRouter (July 2026)

| Model | Input $/M | Output $/M | Context | Strengths | Tool Use |
|---|---|---|---|---|---|
| **DeepSeek V4 Flash** | $0.09 | $0.18 | 128K+ | Efficiency-optimized, strong coding | Good |
| **Xiaomi MiMo-V2.5** | $0.105 | $0.28 | 128K | "Pro-level agentic at half cost" | Good |
| **MiniMax M3** | $0.30 | $1.20 | 1M | Long-horizon agentic, tool use | Strong |
| **GLM-5.2 (Z.ai)** | $1.20 | $4.10 | 1M | Long-horizon, project-level SW eng | Very strong |
| **Kimi K2.6 (Moonshot)** | $0.73 | $3.49 | 128K+ | Sub-agent parallelism, coding | Strong |
| **DeepSeek V4 Pro** | $0.435 | $0.87 | 128K+ | Best coding score (89.8), full-codebase | Strong |
| **Qwen 3.6 Plus** | ~$1-2 | ~$3-6 | 1M | Top open-weight agentic coding | Very strong |
| **Claude Sonnet 4.6** | $3.00 | $15.00 | 200K | Frontier iterative dev | Excellent |
| **Claude Opus 4.8** | $5.00 | $25.00 | 200K | Complex reasoning, orchestration | Excellent |

Key data points:
- **GLM-5.2**: 744B total params (40B active MoE), MIT licensed, 1M context. OpenRouter intelligence index: top 10% on agentic performance. Strong at maintaining engineering context across long tasks. Released June 2026. Direct API: $1.40/$4.40 per M, with $0.26/M cached input.
- **Kimi K2.6**: Built for sub-agent parallelism, 81 overall benchmark, 88.7 coding score. $0.73/$3.49 per M — best cost/coding-score ratio in the open-weight tier.
- **DeepSeek V4 Pro**: 89.8 coding score (highest in the table), "notably fewer partial function calls or malformed JSON payloads" — best reliability for tool-use heavy pipelines.
- **MiMo-V2.5**: Xiaomi model, multimodal, "Pro-level agentic at half cost" — may be the 2026 budget surprise.
- **DeepSeek V4 Flash**: $0.09 input — the cheapest credible option for high-volume grunt work (test generation, doc writing, classification).

Sources: https://openrouter.ai/collections/programming, https://openrouter.ai/z-ai/glm-5.2, https://www.llmreference.com/compare/glm-5.2/kimi-k2-6, https://benchlm.ai/blog/posts/best-chinese-llm

### 3.2 Routing Strategy That Actually Works

The 2026 consensus on tiered routing:

- **Commodity lane** (DeepSeek Flash, MiMo, Kimi K2.6): high-volume, well-defined tasks — test generation, docstrings, boilerplate, classification, code review of small diffs. Cost: $0.09–$0.73/M input.
- **Mid tier** (DeepSeek V4 Pro, GLM-5.2, Qwen 3.6 Plus): complex codebase analysis, multi-step automation, architecture reasoning. Cost: $0.40–$1.40/M input.
- **Frontier** (Claude Sonnet/Opus): judgment calls, hard debugging, orchestration, user-facing review. Cost: $3–$5/M input.

The cost differential is 10–50x from cheapest to frontier. Teams that "maintain a routing policy — matching task class to the cheapest model that clears the quality bar — win." The policy needs revision every 60–90 days as the price floor moves.

**OpenRouter Fusion**: blends multiple models in a single request, routing sub-tasks to the cheapest model that can handle them. Can match frontier quality at ~50% cost for mixed workloads.

Tool-use reliability ranking (for agentic pipelines): DeepSeek V4 > GLM-5.2 > Kimi K2.6 > Qwen 3.6 Plus > commodity. All "perform significantly better inside a structured agent harness than in raw chat mode."

Sources: https://openrouter.ai/blog/tutorials/how-to-get-the-lowest-cost-llm-inference-on-openrouter/, https://www.mindstudio.ai/blog/what-is-model-fusion-openrouter-fusion-explained, https://www.mindstudio.ai/blog/best-open-source-llms-agentic-coding-2026

---

## 4. Autonomous Software Factory Patterns In The Wild

### 4.1 What Actually Ships vs Demo-ware

The clearest field data:

- **Devin (Cognition AI, 2026):** Claimed "51.5%" SWE-bench success rate; independent testing shows 15–30% on novel tasks. On well-scoped bugs with clear reproduction steps and bounded fix: ~78% success. Test suite generation: highest-ROI use case. Struggles with: vague feature requests, open-ended architecture, tasks without measurable success criteria. Cost: $500+/month for teams, cloud-hosted sandboxed env. Verdict: production-ready for bounded, testable tasks; not for open-ended work.

- **OpenHands (All Hands AI):** 77.6% on SWE-bench Verified — strongest open platform benchmark. Docker-based isolation (SSH into container, torn down post-session). Supports Kubernetes with VPC isolation, RBAC, Jira/Linear/Slack integrations. Raised $18.8M Series A, 70K+ GitHub stars. Has an SDK for building custom agents on top. Practical: agents complete ~38% of internal tickets end-to-end on first try, another 22% with a human nudge. Architecture: Localize → Edit → Test execution loop.

- **Sourcegraph internal OpenHands run:** 38% first-try end-to-end on their own monorepo. 22% complete after human nudge. This is a realistic production number for complex existing codebases.

- **StrongDM "no human code review" factory:** Rules: code written only by AI, reviewed only by AI, budget $1K/day tokens per engineer. Key inventions: "Gene Transfusion" (extract patterns from existing systems), "Semports" (cross-language porting), "Pyramid Summaries" (tiered doc for agent navigation), digital twin universe (clone third-party services for testing without rate limits). Verification: scenario-based satisfaction scores (probabilistic, not boolean). Economic question: only works if generated software ROI > token cost.

- **Factory.ai:** Positions around software delivery workflows beyond just coding. "Droids" can run on Factory cloud or on registered machines (BYOM). Focuses on review, testing, deployment coordination as well as code generation. Competitor to Devin at higher abstraction.

Sources: https://www.birjob.com/blog/ai-coding-agents-2026, https://simonwillison.net/2026/Feb/7/software-factory/, https://toolhalla.ai/blog/devin-vs-openhands-vs-swe-agent-2026

### 4.2 Overnight Orchestrator Real Results

**HAMY's overnight run (February 2026):** 15 tasks over 10 hours, 8-phase pipeline (triage → research → design → code → review). $90 in tokens. Key finding: "spawn fresh agents per pipeline phase" rather than one long-running agent — eliminates context exhaustion by design. Accepted slowness for correctness. Main psychological risk: "code landing that I didn't write and didn't directly oversee" = comprehension debt.

Source: https://hamy.xyz/blog/2026-02_ai-orchestrator-overnight

**Conductor tool (macOS):** Runs multiple Claude Code + Codex agents in parallel, each in its own git worktree, with a visual dashboard and diff-first review UI. Free (you pay API costs). This is the closest thing to hermes as a pre-built tool. Key design: each agent gets its own worktree, no merge conflicts, clean integration.

Source: https://www.augmentcode.com/tools/intent-vs-conductor-macos-agent-orchestrators

### 4.3 Verification Gauntlet — What Real Systems Use

Production verification stacks (2026 consensus):

1. **Plan approval**: lead/architect agent reviews approach before coding begins.
2. **Automated hooks**: lint, type-check, test on task completion (not optional).
3. **Dedicated reviewer agents**: read-only, 1 reviewer per 3–4 builders.
4. **Codex `codex review`**: independent second-model review of the diff vs base branch.
5. **Scenario/satisfaction testing** (StrongDM pattern): end-to-end user story specs as "holdout" sets, probabilistic satisfaction scoring.
6. **Human approval gate**: always present for Tier 4 (irreversible) actions; async for others.

"The bottleneck is no longer generation. It's verification." Systems that skip the verification gauntlet produce high output velocity but ship regressions.

### 4.4 Human Escalation Design That Works

From production deployments (DigitalApplied, 2026):

**Four-tier escalation model:**
| Tier | Type | Approval | Example |
|---|---|---|---|
| 1 | Read-only | Fully autonomous | Queries, lookups |
| 2 | Reversible | Autonomous + logging | Drafts, internal state |
| 3 | External/third-party | Async review | API calls, outside systems |
| 4 | High-risk/irreversible | Mandatory human | Deploys, deletions, financial |

"Approval logic must be enforced at the workflow execution layer, not negotiated by the AI at runtime."

**Async-first is production standard.** Sync approval fails due to gateway timeouts (29s on AWS API Gateway), OAuth token expiry (30 min), state drift (pagination cursors stale within minutes). Pattern: serialize state checkpoint → idempotency key → action hash → async human approval → resume from checkpoint.

**Context package for humans (35–45% faster resolution):** plain-language action summary, agent reasoning + alternatives, financial impact estimate, reversibility flag, before/after diff, session ID, approval deadline.

**Confirmation fatigue risk:** over-gating causes humans to click through reflexively. A prompt injection can exploit this. Reserve sync interruption for Tier 4 only.

Source: https://www.digitalapplied.com/blog/human-in-the-loop-escalation-design-ai-agents-2026

---

## 5. Self-Improvement Mechanisms That Are Real in 2026

### 5.1 What Actually Works in Production

**Verifiability is the constraint.** Self-improvement only works reliably in domains with objectively verifiable outcomes: code compiles or doesn't, tests pass or fail, math proofs check or not. Open-ended judgment (UX, strategy, relationships) lacks clean signals — explaining why production deployments concentrate in engineering/optimization.

**Eval-driven iteration (production example):** Meta's Ranking Engineer Agent (REA) runs continuous ML improvement cycles: hypothesis → train → debug → analyze → iterate. "Doubled average model accuracy across six advertising models." Karpathy's autoresearch loop: 700 experiments in 2 days discovering optimizations via autonomous code modification and evaluation.

**SkillForge / SkillOpt (2026 research, practically applicable):**
- **SkillForge** (arxiv 2604.08618): end-to-end skill creation–evaluation–refinement loop. "Automated evolution can surpass manually curated expert knowledge" in cloud support scenarios.
- **SkillOpt** (Microsoft, open-source, 2026): treats skill documentation as external state, not frozen prompts. Runs periodically over agent's past trajectories, updating skill docs. "Self-improving code-agent plugins" ecosystem emerging. DSPy-compatible.
- **CODESKILL** (arxiv 2605.25430): extracts multi-granularity procedural skills from coding-agent trajectories using RL with hybrid reward (skill quality + execution feedback). +9.69% pass rate over no-skill baseline on SWE-bench Verified.
- **SAGE**: agents write reusable functions, test them, save to persistent library. "+8.9% Scenario Goal Completion, 59% fewer output tokens" as skill library grows.

**Practical analog for hermes:** after each successful job, extract the approach into a `.claude/skills/` markdown file. After failed jobs, extract the failure pattern. SkillOpt-style: periodically (weekly cron) re-run a "skill distillation" pass over recent job trajectories to update or add skills. This is the VIBE skill-authoring loop already described in vibe-protocol.md — apply it to factory outcomes.

**Memory systems that are deployed:**
- **Mem0** ($24M Series A, 186M API calls/quarter, AWS integration): compresses history into optimized representations. Commercial, API-based.
- **SimpleMem**: +26.4% F1 improvement, 30x token reduction vs Mem0.
- **MemOS**: memory as managed system resource with versioning + async ingestion.

For hermes: the simplest production-grade version is file-based semantic embeddings (already the `~/.bun/bin/gbrain` pattern) — store job outcomes, failure modes, successful patterns as pages, retrieve via vector search before each job spawn.

Sources: https://o-mega.ai/articles/self-improving-ai-agents-the-2026-guide, https://arxiv.org/abs/2604.08618, https://arxiv.org/abs/2605.25430, https://venturebeat.com/orchestration/microsofts-open-source-skillopt-automatically-upgrades-ai-agent-skills-without-touching-model-weights

### 5.2 Graduated Trust Track Records

**The Devin arc**: improved from ~34% merged PRs to ~67% over time. Systems demonstrating reliable, verifiable improvements earn expanded autonomy. The progression: read-only intern → bounded autonomy → autonomous within approved domain → fully autonomous with monitoring.

**AURA framework (arxiv 2510.15739):** formal risk-assessment for agent autonomy promotion. McKinsey 2026: only 23% of organizations are scaling agents; AI trust maturity score 2.3/5. The gap is governance infrastructure, not model capability.

**For hermes:** implement a simple per-job-type track record. After N successful executions of a job class with no human rejection, the supervisor automatically reduces the human approval requirement for that class (e.g., minor bug fixes with green tests → auto-merge after Codex review passes). After a rejection, track record resets. This is graduated trust via outcome history, not static policy.

Sources: https://www.mckinsey.com/capabilities/tech-and-ai/our-insights/tech-forward/state-of-ai-trust-in-2026-shifting-to-the-agentic-era, https://arxiv.org/pdf/2510.15739

---

## 6. Overnight Reliability Engineering

### 6.1 Five Failure Modes (Empirical, 60+ Overnight Runs)

From the Captain-Dispatcher watchdog architecture study (60+ evaluation runs):

1. **Silent handoff stalls**: output never arrives, no error fires. Worker appears alive (PID exists) but has stopped producing. Detected only by output modification time check.
2. **Zombie sessions**: process appears alive, contains dead subprocesses. `ps` shows the parent; the actual work is stopped.
3. **Sub-agent memory accumulation**: each subagent accumulates memory → system-wide OOM. "0 deliverables in 46 minutes" in a 4-parallel run before memory-zone throttling was added.
4. **Captain-level deadlocks**: orchestrator blocks on a failed subagent indefinitely.
5. **Context exhaustion** (largely solved by fresh-agent-per-task pattern, but still occurs in long-running single agents).

### 6.2 Watchdog Architecture That Works

**Captain-Dispatcher architecture** (solved context exhaustion across 12/12 overnight runs, 445 files, 117 charts produced, 21 stall detections, 193 memory interventions):

- **Watchdog agent**: dedicated, continuously polling. Checks 6 signals every 5 minutes: memory, session liveness, file output, dispatch markers, checkpoint status, pipeline progress.
- **Captain self-check**: verifies Watchdog liveness on own cycle. "If dead, Captain respawns Watchdog."
- **Tiered alerting**: TIER_1 (observe only), TIER_2 (10+ min = alert), TIER_3 (15+ min = escalation).
- **Diagnosis-before-alerting**: before sending alert, Watchdog captures tmux output, checks for idle prompts, verifies live Claude processes.
- **Kill hierarchy**: directional — Dispatcher kills Captains; Captains kill agents. No horizontal kills. `tmux kill-server` unconditionally blocked. Prevents cascading failures.
- **Memory-zone throttling** (GREEN/YELLOW/RED zones): proactive memory management prevents OOM cascades. Most important finding from the study.

**Key design principle**: "Monitoring agents themselves require monitoring." No single layer suffices; compose independent failure-mode detectors.

Source: https://rz-ai-learning.com/posts/watchdog-multi-agent-monitoring/

### 6.3 Context Management for Long Tasks

**Auto-compact trigger**: 95% window usage (Claude Code default). Result: 84% token reduction in 100-turn benchmark (Anthropic). What to preserve in compaction: architectural decisions, unresolved bugs/blockers, implementation state, current objectives. What to discard: processed tool outputs, rejected traces, redundant confirmations.

**Four context failure modes** (DigitalApplied 2026):
1. Context poisoning: hallucinations compound → evict stale context
2. Context distraction: over-reliance on history at ~100K tokens → summarize
3. Context confusion: too many tools overwhelm model → JIT tool loading
4. Context clash: conflicting info from multi-turn runs (39% perf drop in o3) → subagent isolation

**Four mitigations in combination** (required for 2+ hour tasks):
- Write: structured notes + external checkpoints every N steps
- Select: JIT tool/doc loading
- Compress: fine-tuned compaction model (not off-the-shelf summarization)
- Isolate: subagents for parallel workstreams

**Fresh-agent-per-phase** (HAMY overnight run pattern): deterministic core spawns fresh Claude instance for each pipeline phase. Eliminates context accumulation across phases entirely. Trade-off: each spawn pays cold-start cost + loses conversational memory (must be injected via system prompt).

Source: https://www.digitalapplied.com/blog/context-engineering-agent-reliability-playbook-2026, https://zylos.ai/research/2026-03-31-context-window-management-session-lifecycle-long-running-agents/

### 6.4 Rate Limit Mitigation at 3am

Three-layer rate limiting (TrueFoundry pattern):
1. **Per-agent token bucket**: each agent tracks its own TPM/RPM and self-throttles.
2. **Shared gateway**: a single rate-limit gateway counts all agents' tokens collectively, applies backpressure before hitting API limits.
3. **Model-level fallback**: on 429, route to a secondary model (OpenRouter overflow) rather than failing. `--fallback-model` in `claude -p` for the Claude-to-Claude case; for cross-provider, the supervisor catches the 429 and re-dispatches via OpenRouter.

**Critical discovery (The Register)**: Rate-limit 429s look like generic failures to naive retry logic. Silent retry loops can drain an entire monthly quota in minutes. Explicit 429 detection + exponential backoff + daily-ceiling kill switch is mandatory for overnight automation.

For Max 20x subscription: per-minute limits still apply even though you have a higher monthly pool. The constraint at 3am is TPM (tokens per minute), not the monthly ceiling. Stagger job spawning with 30–60 second gaps between parallel launches to avoid burst TPM collisions.

Sources: https://www.truefoundry.com/blog/rate-limiting-ai-agents-preventing-llm-api-exhaustion, https://www.theregister.com/2026/03/31/anthropic_claude_code_limits/

---

## 7. What Demo-ware Looks Like vs. What Ships

**Demo-ware signals:**
- Single-repo, single-model, single-task demos with cherry-picked specs
- No persistent state across runs (starts fresh every time)
- No graduation path for autonomy (always requires same oversight level)
- No error taxonomy (all failures handled identically)
- No memory of past attempts
- "Success" defined by model self-report, not independent verification

**What production systems have:**
- Independent verification (different model or deterministic tests review the work)
- Failure taxonomy with class-specific recovery (budget exceeded ≠ stuck ≠ bad code)
- Durable state (SQLite/JSON job store survives crashes)
- Probabilistic success scoring, not boolean
- Track record per job class driving autonomy graduation
- Memory of past outcomes retrieved before spawning similar jobs
- Fresh agents per pipeline phase to avoid context accumulation
- Explicit 429 handling with backoff and model fallback
- Async human escalation with decision-ready context package

The "dark factory" (fully autonomous, no human in the loop) is achievable for high-volume, well-defined, highly testable tasks. It has produced documented incidents (1.9M row DB deletion from inadequate access controls). The practical ceiling for 2026: Level 4 autonomy — mostly autonomous with human escalation for Tier 4 actions (irreversible, high-stakes).

Sources: https://www.mindstudio.ai/blog/what-is-a-dark-factory-ai-coding, https://htdocs.dev/posts/from-conductor-to-orchestrator-a-practical-guide-to-multi-agent-coding-in-2026/

---

## 8. Sources

- Claude Code scheduled tasks docs: https://code.claude.com/docs/en/scheduled-tasks
- The Register on Claude Code quotas: https://www.theregister.com/2026/03/31/anthropic_claude_code_limits/
- Billing change cancelled: https://www.aicodex.to/articles/claude-subscription-credit-changes
- VentureBeat on OpenClaw reinstatement: https://venturebeat.com/technology/anthropic-reinstates-openclaw-and-third-party-agent-usage-on-claude-subscriptions-with-a-catch
- ArXiv Claude Code design space: https://arxiv.org/html/2604.14228v1
- OpenRouter programming models: https://openrouter.ai/collections/programming
- GLM-5.2 on OpenRouter: https://openrouter.ai/z-ai/glm-5.2
- GLM-5.2 vs Kimi K2.6 comparison: https://www.llmreference.com/compare/glm-5.2/kimi-k2-6
- BenchLM Chinese LLMs 2026: https://benchlm.ai/blog/posts/best-chinese-llm
- OpenRouter lowest-cost inference guide: https://openrouter.ai/blog/tutorials/how-to-get-the-lowest-cost-llm-inference-on-openrouter/
- OpenRouter Fusion: https://www.mindstudio.ai/blog/what-is-model-fusion-openrouter-fusion-explained
- MindStudio agentic coding open-source LLMs: https://www.mindstudio.ai/blog/best-open-source-llms-agentic-coding-2026
- AI Coding Agent Showdown 2026: https://www.birjob.com/blog/ai-coding-agents-2026
- StrongDM software factory: https://simonwillison.net/2026/Feb/7/software-factory/
- OpenHands: https://www.openhands.dev/
- OpenHands SWE-bench 77.6%: https://starlog.is/articles/developer-tools/openhands-openhands/
- Devin 2026 review: https://aitoolranked.com/blog/devin-ai-review
- HAMY overnight orchestrator: https://hamy.xyz/blog/2026-02_ai-orchestrator-overnight
- Conductor vs Intent macOS orchestrators: https://www.augmentcode.com/tools/intent-vs-conductor-macos-agent-orchestrators
- Human escalation design 2026: https://www.digitalapplied.com/blog/human-in-the-loop-escalation-design-ai-agents-2026
- Dark factory pattern: https://www.mindstudio.ai/blog/what-is-a-dark-factory-ai-coding
- From Conductor to Orchestrator guide: https://htdocs.dev/posts/from-conductor-to-orchestrator-a-practical-guide-to-multi-agent-coding-in-2026/
- Self-improving AI agents 2026: https://o-mega.ai/articles/self-improving-ai-agents-the-2026-guide
- SkillForge: https://arxiv.org/abs/2604.08618
- SkillOpt VentureBeat: https://venturebeat.com/orchestration/microsofts-open-source-skillopt-automatically-upgrades-ai-agent-skills-without-touching-model-weights
- CODESKILL: https://arxiv.org/abs/2605.25430
- McKinsey AI trust 2026: https://www.mckinsey.com/capabilities/tech-and-ai/our-insights/tech-forward/state-of-ai-trust-in-2026-shifting-to-the-agentic-era
- AURA autonomy risk framework: https://arxiv.org/pdf/2510.15739
- Watchdog multi-agent monitoring: https://rz-ai-learning.com/posts/watchdog-multi-agent-monitoring/
- Context engineering reliability playbook: https://www.digitalapplied.com/blog/context-engineering-agent-reliability-playbook-2026
- Context window management long-running agents: https://zylos.ai/research/2026-03-31-context-window-management-session-lifecycle-long-running-agents/
- Rate limiting AI agents: https://www.truefoundry.com/blog/rate-limiting-ai-agents-preventing-llm-api-exhaustion
- Auto-research-in-sleep repo: https://github.com/wanshuiyin/auto-claude-code-research-in-sleep
