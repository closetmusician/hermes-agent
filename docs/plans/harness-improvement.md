# Hermes Chief-of-Staff Diagnosis & Remediation Plan — Fable Prompt

ABOUTME: One-shot Fable 5 prompt to diagnose why the hermes repo has failed to function as a
ABOUTME: world-class AI chief of staff, interview the user about their struggles, and propose
ABOUTME: a concrete remediation plan. Based on the harness-improvement methodology.

---

# ONE-SHOT HERMES DIAGNOSIS — instruction for the top-tier session

<role>
You are a senior AI systems architect specializing in autonomous agent frameworks. This is the
only session I will run with a top-tier model. Your job is to perform a forensic diagnosis of
why my deployment of Hermes (by NousResearch, https://github.com/NousResearch/hermes-agent) has
repeatedly FAILED to function as an AI chief of staff, despite ~2 months of effort (2026-05-18
through today). You will:
  1. Ingest all evidence — repo state, planning docs, conversation transcripts, reference repos
  2. Synthesize a root-cause diagnosis
  3. Interview me (max 8 questions, descending priority) to identify my biggest struggles
  4. Produce a concrete, phased remediation plan

I am an SVP of Product at Diligent, an expert solo developer. Skip basics for me, but the
remediation plan you write must be explicit enough for weaker models (Opus/Sonnet) to execute.

Context: I forked hermes-agent into ~/Code/hermes, added governance plugins (email-send-guard,
control-room, tool-registry-guard) on branch feat/governance-plugins. The fork is 47 commits
ahead and 50+ behind upstream v0.17.0. Every major subsystem has broken at some point: gateway
crash loops, email security bypass, silent channel failures, IMAP death spirals, broken approval
flows, upstream merge conflicts. I've also built two reference "AI chief of staff" repos
(clawchief, tradclaw) using a different architecture (openclaw-based) that I want you to study
for patterns that work.
</role>

<sources>
Hand-curated list of references. Read EVERY item here via subagent delegation.
For URLs, web_fetch each and return one conclusion line per source.
For file paths, read in full. For directories, inventory contents and read key files.
If something 404s or is missing, note it and move on.

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: HERMES REPO — CURRENT STATE
# The repo itself. Understand architecture, local modifications, what's broken.
# ═══════════════════════════════════════════════════════════════════════════════

# Core architecture (read these in full)
- ~/Code/hermes/README.md
- ~/Code/hermes/AGENTS.md
- ~/Code/hermes/CLAUDE.md.proposed
- ~/Code/hermes/pyproject.toml
- ~/Code/hermes/run_agent.py                      # AIAgent class — core conversation loop
- ~/Code/hermes/cli.py                             # HermesCLI — interactive terminal UI
- ~/Code/hermes/hermes_state.py                    # SessionDB — SQLite session store

# Gateway (the messaging backbone — where most failures occurred)
- ~/Code/hermes/gateway/run.py                     # 18K-line god-file
- ~/Code/hermes/gateway/session.py
- ~/Code/hermes/gateway/config.py
- ~/Code/hermes/gateway/delivery.py
- ~/Code/hermes/gateway/hooks.py
- ~/Code/hermes/gateway/platforms/email.py         # Email adapter — security incident source
- ~/Code/hermes/gateway/platforms/telegram.py
- ~/Code/hermes/gateway/platforms/whatsapp.py

# Governance plugins (my local additions — the security hardening layer)
- ~/Code/hermes/plugins/email-send-guard/__init__.py
- ~/Code/hermes/plugins/control-room/__init__.py
- ~/Code/hermes/plugins/tool-registry-guard/__init__.py
- ~/Code/hermes/plugins/teams_pipeline/             # Directory — inventory and read key files

# Agent internals (understand the LLM loop, memory, skills, tool safety)
- ~/Code/hermes/agent/conversation_loop.py
- ~/Code/hermes/agent/skill_commands.py
- ~/Code/hermes/agent/tool_guardrails.py
- ~/Code/hermes/agent/tool_executor.py
- ~/Code/hermes/agent/curator.py                   # Background skill lifecycle
- ~/Code/hermes/agent/auxiliary_client.py           # Per-task LLM routing
- ~/Code/hermes/agent/system_prompt.py
- ~/Code/hermes/agent/prompt_builder.py

# Tools (what capabilities hermes exposes to the LLM)
- ~/Code/hermes/tools/registry.py
- ~/Code/hermes/tools/terminal_tool.py
- ~/Code/hermes/tools/delegate_tool.py
- ~/Code/hermes/tools/send_message_tool.py
- ~/Code/hermes/tools/approval.py
- ~/Code/hermes/tools/mcp_tool.py
- ~/Code/hermes/tools/memory_tool.py
- ~/Code/hermes/tools/skill_manager_tool.py
- ~/Code/hermes/tools/cronjob_tools.py

# Skills (built-in skill categories — inventory all, deep-read productivity/email/github)
- ~/Code/hermes/skills/                            # Directory — full inventory
- ~/Code/hermes/skills/productivity/               # Key for chief-of-staff use case
- ~/Code/hermes/skills/email/
- ~/Code/hermes/skills/github/
- ~/Code/hermes/skills/autonomous-ai-agents/

# Cron system (scheduling — critical for daily briefings, monitoring)
- ~/Code/hermes/cron/jobs.py
- ~/Code/hermes/cron/scheduler.py

# Config
- ~/Code/hermes/.claude/settings.local.json
- ~/Code/hermes/.env                               # Runtime config (redact secrets)
- ~/Code/hermes/cli-config.yaml.example

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: PLANNING DOCS — THE TRAIL OF FAILURES AND FIXES
# Read ALL of these. They document the full arc of what went wrong and what
# was attempted. This is the richest diagnostic evidence.
# ═══════════════════════════════════════════════════════════════════════════════

- ~/Code/hermes/docs/plans/hermes-research.md           # Initial viability research
- ~/Code/hermes/docs/plans/hermes-attack-plan.md        # 8-phase chief-of-staff implementation plan
- ~/Code/hermes/docs/plans/hermes-fail.md               # Security audit after email guard bypass (813 lines)
- ~/Code/hermes/docs/plans/hermes-improvement.md        # 5-phase security hardening plan
- ~/Code/hermes/docs/plans/hermes-improvement-v2.md     # Gap analysis: what works vs what's broken
- ~/Code/hermes/docs/plans/hermes-v0.17.0-update-2026-06-24.md  # Upstream merge strategy
- ~/Code/hermes/docs/plans/improve-email.md             # Root cause of email send guard failure

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: CONVERSATION TRANSCRIPTS — THE STRUGGLE SESSIONS
# These are the actual Claude Code sessions where failures happened in real-time.
# Read ALL non-agent JSONL files. Use grep/jq/head — never cat full files into
# context. Extract: what broke, what the user said, what was attempted, outcomes.
# ═══════════════════════════════════════════════════════════════════════════════

# Primary hermes sessions (all under conversation-archive, sorted by approx date)
# Initial setup & chief-of-staff config (2026-05-18)
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/a4247ccd-5752-4d4b-8f43-e5542d6dd64b.jsonl

# Gateway crash loop fix (2026-05-19)
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes-agent/0125ba35-807c-4902-8156-89978b8b51ac.jsonl

# Email security bypass / attack chain discovery (2026-05-20–23) — THE BIG ONE
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes-agent/03cedd64-234c-4a7b-adac-68e79af3bb2a.jsonl

# Hermes-fail audit report generation (2026-05-23)
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes-agent/187e7ef3-5be3-44df-8c10-641db336b523.jsonl

# Security hardening session (2026-05-23)
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes-agent/2520fd6c-4ac6-4f16-a1ea-61385a60aaeb.jsonl

# IMAP connection death & gateway stale plist (2026-05-24)
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/85308d94-2e58-47d3-8c47-df9a581df81b.jsonl

# Silent channel routing & email guard fixes (2026-05-27–29)
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/9222ef3b-7961-4ad5-83d5-aedc8fd42d83.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/9ee56879-2f19-4fa1-97b4-80e0fadc0ce1.jsonl

# v0.17.0 upstream merge analysis (2026-06-24–25)
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/60c98efe-001a-4fec-b09d-f78512ab7f35.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/80ebb0f8-95e5-4c2f-b815-b9c8e867a60a.jsonl

# Additional hermes sessions (read these too — may contain struggles not captured above)
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/169e3fa7-0032-472c-a27b-8cf85f07bcd5.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/de15df21-13b0-4437-8586-8829f31069f7.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/31fe08a1-06d1-48dd-acad-93555d420ca4.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/ad3a8a88-60c9-47fa-b9e5-1b7425086bf1.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/bb625d47-7c56-40b5-9de5-50066538ceb5.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/38f04b90-9110-4ac2-aafa-12ce32041a56.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/05fb3512-9e4d-4e59-8291-3296b6d8913a.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/bae4172b-2bd0-40d7-90e6-7e6f7a46ae1e.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/ec47dba1-de02-4e05-97d8-9e8b5cf8a069.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/2ed69dda-fbd4-4cd9-a34b-ba277d7ca58c.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/31fd1333-019b-4fd9-ab61-1371d5ba81e8.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/eacb9333-19d7-4926-a1ee-3746b557cacb.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes/07b328b2-7f07-4d1f-b4b0-dbfbe82a7ea7.jsonl

# Hermes-agent sessions (older repo name, same project)
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes-agent/9635e8db-37c5-4d6c-96e2-597eaa822f30.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes-agent/ba33dd34-42e2-4887-b80d-60e4af05cc86.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes-agent/f37ddf7c-ed1e-457c-ab0f-b73f184af515.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes-agent/21fc4acc-89d2-4e70-b324-f51ca31354a1.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes-agent/77223912-88c0-4679-a54a-6ce182b64503.jsonl

# Hermes-evolution sessions
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes-evolution/9b830bb2-1708-4315-bdbb-fb7fe1e79006.jsonl
- ~/.config/superpowers/conversation-archive/-Users-yklin-Code-hermes-evolution/75e01101-a601-4ddf-bb59-55faf10ef300.jsonl

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: PROJECT MEMORY & CONFIG
# Claude Code's persistent memory about this project across sessions.
# ═══════════════════════════════════════════════════════════════════════════════

- ~/.claude/projects/-Users-yklin-Code-hermes/memory/MEMORY.md
- ~/.claude/projects/-Users-yklin-Code-hermes/memory/project_email_guard_fixes.md
- ~/.claude/projects/-Users-yklin-Code-hermes-evolution/memory/MEMORY.md
- ~/.claude/projects/-Users-yklin-Code-hermes-evolution/memory/reference_hermes_setup.md

# Also search episodic memory with queries: "hermes chief of staff", "hermes gateway",
# "hermes email guard", "hermes crash", "hermes security bypass"

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: REFERENCE REPOS — WHAT "WORKING" LOOKS LIKE
# Two AI chief-of-staff repos built on openclaw architecture. Study their
# patterns for what hermes is missing.
# ═══════════════════════════════════════════════════════════════════════════════

# Clawchief (professional/work chief of staff)
- ~/Code/clawchief/README.md
- ~/Code/clawchief/CHANNELS.md
- ~/Code/clawchief/INSTALL-CHECKLIST.md
- ~/Code/clawchief/INSTALL-WITH-OPENCLAW.md
- ~/Code/clawchief/REVIEW-NOTES.md
- ~/Code/clawchief/SETUP-GOG.md
- ~/Code/clawchief/docs/arch-plan.md
- ~/Code/clawchief/docs/research/aichief-research.md
- ~/Code/clawchief/docs/research/clawchief-learnings.md
- ~/Code/clawchief/clawchief/                      # Directory — read all .md files
- ~/Code/clawchief/skills/                          # Directory — read all SKILL.md files
- ~/Code/clawchief/cron/jobs.template.json
- ~/Code/clawchief/workspace/                       # Directory — read all files

# Tradclaw (personal/family chief of staff)
- ~/Code/tradclaw/README.md
- ~/Code/tradclaw/AGENTS.md
- ~/Code/tradclaw/SETUP-CHECKLIST.md
- ~/Code/tradclaw/docs/arch-plan.md
- ~/Code/tradclaw/docs/research/tradclaw-learnings.md
- ~/Code/tradclaw/tradclaw/                         # Directory — read all .md files
- ~/Code/tradclaw/skills/                           # Directory — read all SKILL.md files
- ~/Code/tradclaw/cron/                             # Directory — read all files
- ~/Code/tradclaw/workspace/                        # Directory — read all files

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6: EXTERNAL RESEARCH
# Supplement with live web searches for current best practices.
# ═══════════════════════════════════════════════════════════════════════════════

- https://github.com/NousResearch/hermes-agent      # Upstream — current version, README, issues
# Search for: "hermes agent chief of staff", "hermes agent production deployment",
# "autonomous agent reliability patterns 2026", "AI chief of staff architecture patterns"
</sources>

<inputs>
═══════════════════════════════════════════════════════════════════════════════════════
THE ONLY THINGS YOU EDIT ARE THE <sources> BLOCK ABOVE AND THIS <inputs> BLOCK.
═══════════════════════════════════════════════════════════════════════════════════════

# 1) SETTINGS
HERMES_REPO_PATH     = ~/Code/hermes
CLAWCHIEF_REPO_PATH  = ~/Code/clawchief
TRADCLAW_REPO_PATH   = ~/Code/tradclaw
DATE                 = 2026-07-03

# 2) KNOWN FAILURE CATEGORIES (from prior sessions — seed your investigation)
# a) Gateway instability: IMAP crash loops, stale launchd plist, exit code 78
# b) Security bypass: LLM searched filesystem, extracted FOCI tokens, patched guard source code
# c) Email delivery: /approve-email accepted but never sent (4 compounding bugs)
# d) Silent channel routing: Telegram SSL failure caused silent switch to WhatsApp
# e) Upstream divergence: 47 ahead / 50+ behind, 32 conflicting files, merge stalled
# f) Setup friction: package never pip-installed, yaml ModuleNotFoundError, CLI not on PATH
═══════════════════════════════════════════════════════════════════════════════════════
</inputs>

<the_one_rule_that_governs_everything>
Your audience for every durable artifact is a WEAKER model. Therefore:
- Weak models need explicit instructions; strong models need whitespace.
- Every recommendation MUST include decision criteria plus one positive and one negative
  example. Abstract directives ("improve reliability", "add monitoring") equal writing nothing.
- Everything you output must be executable at the Sonnet tier. Build nothing that depends on
  your own capability to run.
</the_one_rule_that_governs_everything>

<operating_rules>
1. DISCOVER, DON'T REMEMBER. Do not rely on memory for tool names, file paths, module
   structures, or upstream API changes. Read them from the live repo state. Tag anything not
   verifiable as [UNVERIFIED]. Never fabricate a path or module name.

2. TWO OUTPUT CLASSES, TWO SAFETY RULES.
   - NEW files (diagnosis, remediation plan, comparison reports): write to disk THE MOMENT each
     is complete, in value order. This session can be interrupted at any time — whatever is on
     disk is all I get.
   - EDITS to EXISTING files: never modify in place before approval. Write as a NEW file first
     (e.g. `<name>.proposed`).

3. EXACTLY TWO PERMITTED STOPS: (a) once, for the interview in <interview>; (b) once, at the
   <approval_gate> before applying any changes. Everywhere else, proceed autonomously.

4. TOKEN DISCIPLINE / DELEGATE THE FRONT LINE. The orchestrator does not do bulk reading.
   Delegate bulk reads (especially the JSONL conversation transcripts), repo scans, and web
   lookups to subagents. Use grep/jq/head — never `cat` a full JSONL or large file into your
   context. Subagents return only conclusions + `file:line` refs; long artifacts go to disk,
   path only.

5. VERIFICATION IS NEVER SELF-VERIFICATION. Acceptance checks go to a fresh-context agent.

6. Use maximum effort / long turns if the environment allows it.
</operating_rules>

<outputs>
What this session produces:
  1. diagnosis.md — Root-cause analysis of why hermes failed as chief of staff, organized by
     failure category with evidence chains from repo state + conversation transcripts.
  2. comparison.md — Structural comparison: hermes architecture vs clawchief/tradclaw patterns.
     What the reference repos do right that hermes doesn't. Pattern gaps, not feature lists.
  3. remediation-plan.md — Phased, actionable plan to make hermes work as chief of staff.
     Each phase has: goal, specific changes, acceptance criteria, estimated effort, dependencies.
     Must address: should we keep the fork or start fresh? Merge upstream or diverge? Which
     subsystems to fix vs replace vs abandon?
  4. interview-synthesis.md — User's answers to the interview questions + how they reshape
     the diagnosis and remediation plan.
  5. next-steps.md — Concrete "start here Monday" instructions for a weaker model session.
</outputs>

<output_locations>
Write every file to these exact locations. `mkdir -p` all directories before writing.

  THIS RUN's artifacts:
    ~/Code/hermes/docs/plans/diagnosis-{{DATE}}/diagnosis.md
    ~/Code/hermes/docs/plans/diagnosis-{{DATE}}/comparison.md
    ~/Code/hermes/docs/plans/diagnosis-{{DATE}}/remediation-plan.md
    ~/Code/hermes/docs/plans/diagnosis-{{DATE}}/interview-synthesis.md
    ~/Code/hermes/docs/plans/diagnosis-{{DATE}}/next-steps.md
    ~/Code/hermes/docs/plans/diagnosis-{{DATE}}/evidence/         # Subagent findings go here

At the very end, print a file manifest: every path written and its purpose.
</output_locations>

Execute phases in order; do not skip.

<phase_0_repo_state_discovery>
Establish the ground truth of the hermes repo RIGHT NOW. Record from the live repo:
  - Git status: branch, ahead/behind, uncommitted changes, recent commits (20)
  - Python environment: installed packages, hermes version, whether `hermes` CLI works
  - Gateway status: is it running? launchd plist state? recent logs?
  - Plugin state: which governance plugins are loaded? Are they functional?
  - Cron jobs: what's scheduled? Are any running?
  - Config: .env settings (redact secrets), cli-config.yaml, any SOUL.md
  - Upstream delta: how far behind v0.17.0? Key breaking changes?
Output: a state snapshot table. Mark unknowns [UNVERIFIED].
</phase_0_repo_state_discovery>

<phase_1_evidence_ingestion>
Delegate ALL of this to subagents. Do not pollute your orchestrator context with raw content.

1. PLANNING DOCS — Read every doc in <sources> Section 2. For each, extract:
   what was planned, what was executed, what succeeded, what failed, what was abandoned.
   Return a findings table.

2. CONVERSATION TRANSCRIPTS — Read every JSONL in <sources> Section 3. Use grep/jq/head to
   extract: user frustration quotes, error messages, what broke, what the fix was, whether the
   fix held. Focus on PATTERNS across sessions, not individual bugs. Do not sample — read every
   session listed. Return a struggle-pattern table ranked by frequency/severity.

3. REPO ARCHITECTURE — Read the files in <sources> Section 1. Map: how the gateway loop works,
   how tools are dispatched, how plugins intercept, how the approval flow works, how email
   sending actually happens end-to-end. Return an architecture summary focused on failure-prone
   paths.

4. REFERENCE REPOS — Read everything in <sources> Section 5. For each repo, extract:
   architecture pattern, how skills/cron/channels/workspace are structured, what makes it
   simpler or more reliable than hermes. Return a comparison table.

5. EPISODIC MEMORY — Search episodic memory for hermes-related conversations using queries:
   "hermes chief of staff", "hermes gateway", "hermes email guard", "hermes crash",
   "hermes security bypass", "hermes cron", "hermes setup". Return any additional struggle
   patterns not captured in the JSONL transcripts.

Output per subagent: conclusions + file:line refs only. Long findings go to
`~/Code/hermes/docs/plans/diagnosis-{{DATE}}/evidence/`.
</phase_1_evidence_ingestion>

<phase_2_research>
Supplement Phase 1 with external research. Delegate to a subagent:
  - Check upstream hermes-agent repo: current version, recent changes, open issues related to
    gateway stability, email, security, or production deployment
  - Search for production deployment guides or post-mortems from other hermes users
  - Search for "AI chief of staff" architecture patterns (2025-2026)
  - Compare hermes architecture (Python monolith, OpenAI-compat, plugin system) vs alternatives
    (Claude Agent SDK, LangGraph, CrewAI, the openclaw pattern used by clawchief/tradclaw)
Output: max 8 findings, each with source URL and relevance to hermes's failures.
</phase_2_research>

<phase_3_diagnostic_synthesis>
Synthesize Phases 0–2 into the anchor document. Identify:

1. THE TOP FAILURE MODES — ranked by how much they blocked the chief-of-staff use case.
   For each: the evidence chain (which sessions, which docs, which code paths), root cause
   (not symptoms), and whether the root cause is fixable in the current architecture.

2. THE ARCHITECTURAL QUESTION — Is hermes the right foundation? Compare:
   - Hermes strengths: 192K stars, active upstream, 20+ platform adapters, built-in cron,
     self-improving skills, MCP integration
   - Hermes weaknesses: 18K-line god-files, fork maintenance burden, security model assumes
     trusted LLM, gateway instability, massive codebase complexity
   - Clawchief/tradclaw pattern strengths: simpler architecture, purpose-built skills,
     structured workspace, openclaw primitives
   - Verdict: keep hermes? merge upstream? start fresh on openclaw? hybrid?

3. THE GAP ANALYSIS — What does a "world-class AI chief of staff" actually need to do, and
   where does hermes fall short? Map against: daily briefings, email triage, calendar awareness,
   meeting prep, PR monitoring, task management, proactive nudges, multi-channel delivery.

Write to disk as `diagnosis.md` and `comparison.md` immediately.
</phase_3_diagnostic_synthesis>

<interview>
Now — informed by the diagnosis — ask AT MOST 8 questions using AskUserQuestions, in
DESCENDING PRIORITY ORDER (most important first). Only ask where genuine ambiguity remains
that would materially change the remediation plan. State the default answer for each if I
skip it.

Focus areas for questions (pick the most diagnostic ones, max 8):
- Which failure modes hurt you most? (rank from diagnosis)
- What does your ideal daily workflow with the chief of staff look like?
- Are you willing to invest in merging upstream, or would you rather start fresh?
- Which channels (Telegram/WhatsApp/Email/Teams/Slack) actually matter?
- What's your model budget / which providers do you want to use?
- How much do you trust the LLM to act autonomously vs requiring approval?
- Are clawchief/tradclaw working well enough that hermes should learn from them?
- What's the timeline — do you need this working in days, weeks, or months?

Then stop and wait. This is your one question stop.
If my answers conflict with Phase 2 research, my answers win — note the tradeoff in one line.
</interview>

<build>
After the interview, build in this value order. Write each file to disk the moment it's complete.

1. `interview-synthesis.md` — Summarize the user's answers. Note where answers conflict with
   Phase 2 research (user's answers win — note the tradeoff in one line).

2. `remediation-plan.md` — The main deliverable. Phased plan with:
   - PHASE 0: Stabilize (get hermes running reliably TODAY — fix or bypass broken subsystems)
   - PHASE 1: Secure (close the security gaps — tool isolation, approval hardening, token lockdown)
   - PHASE 2: Integrate (connect to the channels/services that matter — informed by interview)
   - PHASE 3: Automate (cron jobs, daily briefings, proactive monitoring — the chief-of-staff behaviors)
   - PHASE 4: Evolve (self-improving skills, upstream merge strategy, long-term maintenance)

   Each phase has:
   - Goal (one sentence)
   - Specific changes (file paths, code changes, config changes)
   - Acceptance criteria (how to verify it works)
   - Estimated effort (hours/days)
   - Dependencies (what must be true before starting)
   - Decision: keep/fix/replace/abandon for each subsystem

   Include a DECISION MATRIX: for each major subsystem (gateway, email, cron, approval,
   plugins, skills, memory), recommend: fix in current fork | merge upstream fix | rewrite |
   replace with alternative | abandon.

3. `next-steps.md` — "Start here Monday" instructions. The first 3 concrete actions a weaker
   model (Opus/Sonnet) can execute in a Claude Code session against this repo. Each action has:
   exact commands to run, files to read, expected outcome, what to do if it fails.

4. Update `comparison.md` with interview insights if the user's answers change the
   hermes-vs-alternatives calculus.
</build>

<approval_gate>
Present the remediation plan summary. Stop and wait for explicit approval before any
code changes. The plan is a RECOMMENDATION — I decide what to execute.
</approval_gate>

<wrap_up>
Mandatory.
1. Spawn a FRESH-CONTEXT subagent to adversarially review every file you produced: conflicting
   recommendations, wrong paths, stale assumptions, recommendations that contradict the user's
   interview answers, and language a weaker model would misread. Fix everything it finds.
2. Read-back verification: confirm every file is on disk and complete.
3. One-page summary: the diagnosis in 3 sentences, the top 3 remediation actions, and what
   "success" looks like in 2 weeks.
4. If context runs low: stop, do steps 1-3 first, dump unfinished items into next-steps.md
   as a handoff.
</wrap_up>

<honesty_clause>
Label the limits of this diagnosis. You are reading code and transcripts, not running the system
or reproducing failures. Some root causes may be wrong — flag confidence levels. The remediation
plan is a hypothesis, not a guarantee. Unsure → mark [UNVERIFIED]. Do not fabricate evidence
or invent failure modes not supported by the sources.
</honesty_clause>

<acceptance_criteria>
- Every failure mode in the diagnosis traces to evidence from conversation transcripts, planning
  docs, or live repo state.
- The comparison with clawchief/tradclaw identifies at least 3 concrete architectural patterns
  hermes is missing.
- The remediation plan has 5 phases, each with specific file paths and acceptance criteria.
- The interview asked ≤8 questions in descending priority, each with a default answer.
- All recommendations are Sonnet-executable with positive+negative examples.
- The "next steps" file has exactly 3 actions, each with exact commands and expected outcomes.
- No existing file modified before approval.
- The final summary fits on one screen.
</acceptance_criteria>

---
Begin by reading the <sources>. After ingestion, proceed through phases autonomously until the
<interview> stop. Do not stop for any other reason.
