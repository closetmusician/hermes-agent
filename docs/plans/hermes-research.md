# Hermes Agent Research — Chief of Staff Viability (June 2026)

## 1. Hermes Agent — Actively Maintained, Massively Popular

- **192K GitHub stars**, 33K forks, 170+ contributors on latest release
- **Latest:** v0.16.0 "The Surface Release" (2026-06-05) — native desktop app, web admin panel, security hardening
- **Your local fork** (`~/Code/hermes/`) is at v0.14.0, two releases behind. Uncommitted email integration work in progress.
- Release cadence is biweekly. 50 commits in May alone.

## 2. Architecture — Single Agent with Rich Infrastructure

- Works with **any OpenAI-compatible model** including Anthropic native (`anthropic==0.86.0`)
- 22+ messaging platforms via gateway (Telegram, Discord, Slack, WhatsApp, Signal, Email, Matrix, etc.)
- Built-in cron scheduler, 40+ tools, persistent SQLite memory, approval system with LLM-assisted auto-approve
- **NOT a multi-agent orchestrator** — it delegates to isolated ephemeral subagents but is fundamentally single-agent

## 3. Self-Evolution (GEPA) — Real Science, Early Implementation

- GEPA paper: ICLR 2026 Oral. +13% over MIPROv2, +20% over GRPO, 35x fewer rollouts. Reads execution traces to diagnose WHY things fail.
- **Phase 1 (skill evolution) is implemented** in `~/Code/hermes-evolution/`. DSPy 3.2.1 and GEPA are installed. Pipeline is functional.
- Phases 2-5 (tool descriptions, system prompt, code, continuous loop) are planned only.
- **Local repo has uncommitted config.py fix** needed to find your renamed `hermes` directory (was `hermes-agent`). Should be committed.
- Cost: ~$2-10 per optimization run. No GPU needed.

## 4. Chief-of-Staff Viability

### Hermes CAN Do Natively
- Slack/Telegram/Discord/WhatsApp/Signal monitoring
- Email (IMAP)
- Cron scheduling
- Approval gates
- Persistent memory
- Subagent delegation

### Hermes CANNOT Do Natively (Addressable via MCP/Plugins)
- Teams chat monitoring (only meeting pipeline plugin)
- Outlook/Exchange
- JIRA management
- Calendar triage

**Gap fill**: Composio provides structured API for JIRA, Teams, and 1000+ apps via MCP integrations.

**Verdict:** Hermes is the strongest open-source foundation for a chief-of-staff agent. The hardest pieces (always-on gateway, approval system, memory, multi-platform) are built. Enterprise Microsoft stack integration is the gap, fillable with MCP plugins.

## 5. Alternatives Comparison

| Framework | Strengths | Weaknesses | Verdict |
|-----------|-----------|------------|---------|
| **LangGraph** | Production standard (Klarna, Uber, JPMorgan), stateful workflows | Build everything yourself, 10x more engineering | Workflow engine, not assistant |
| **CrewAI** | Fastest multi-agent prototyping | No always-on mode | Not for persistent assistants |
| **Claude Agent SDK** | Good for embedding in products | "Chyros" daemon unshipped, no messaging gateway | Missing key pieces |
| **Alyna** | Closest turnkey SaaS chief-of-staff (WhatsApp, Slack, Teams, Signal) | Closed-source startup | Risky dependency |
| **AutoGPT** | Name recognition | Declining, not recommended | Skip |

## 6. Hermes + Fable — Fully Compatible

- Set `provider: anthropic`, `model: claude-fable-5` in config. Done.
- Prompt caching works. Adaptive thinking routing works. 1M context window handled natively.
- **Cost warning:** Fable 5 is $10/$50 per MTok — 2x Opus. For always-on monitoring, route by task difficulty (Fable for complex reasoning, Sonnet/Haiku for triage).

## 7. Known Issues in Your Local Setup

- `~/Code/hermes/` is at v0.14.0 — two releases behind (v0.16.0 current)
- Uncommitted config.py fix for renamed directory
- `~/Code/hermes-evolution/.env` contains exposed API keys — consider rotating if repo was ever exposed
- `.gitignore` excludes `.env` but keys may be in git history
