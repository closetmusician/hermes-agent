# Hermes Improvement v2 — Automated Chief of Staff Gap Analysis

## Source Documents

### Chief-of-Staff Plans (Primary — ~/.claude/docs/learnings/)

| Document | Path | Scope |
|---|---|---|
| **Gap Analysis & Viability Research** | `docs/plans/hermes-research.md` (3.7K) | Architecture assessment, self-evolution (GEPA) feasibility, native vs MCP-addressable gaps, comparison with alternatives (LangGraph, CrewAI, Claude Agent SDK). Early June 2026. |
| **8-Phase Attack Plan** | `docs/plans/hermes-attack-plan.md` (29.6K) | Full implementation plan: Phase 0 (v0.16.0 upgrade) → Phase 1 (Teams+Slack) → Phase 2 (Outlook+Calendar via MCP) → Phase 3 (JIRA) → Phase 4 (Morning triage cron) → Phase 5 (PR review+deploy monitoring) → Phase 6 (Model routing) → Phase 7 (Approval gates) → Phase 8 (Self-evolution). Includes decision points, risk assessment, token economics. **Dated 2026-06-12.** |
| **Sprint Plan v2 (Fable)** | `~/.claude/docs/learnings/fable/attack-plans-v2.md` (15.4K) | Sprint-based implementation plan with Fable model routing and execution strategy. |
| **Sprint Plan v1 (Fable)** | `~/.claude/docs/learnings/fable/attack-plans.md` (31.9K) | Detailed attack plan with decision matrix, dependency graph, and execution strategy. |

### Implementation & Security (This Repo)

| Document | Path | Scope |
|---|---|---|
| Research Survey | `hermes-evolution/docs/research-hermes.md` | Ecosystem survey of deterministic orchestration & behavior policing (Statewright, BetterClaw, Shellfirm, etc.). Ends with 5 recommended next moves. Dated 2026-05-22. |
| Improvement Plan v1 | `docs/plans/hermes-improvement.md` | 5-phase implementation roadmap: email-send-guard, control-room (audit+policy), tool-registry-guard, gbrain memory, email loophole closure, silent channel routing fix. Full code specs, ~891 lines. |
| Failure Audit | `docs/plans/hermes-fail.md` | Architectural security audit: 7 email-sending code paths mapped, 3× Codex adversarial review, EmailSendBroker single-chokepoint architecture proposal. ~812 lines. |
| v0.17.0 Merge Strategy | `docs/plans/hermes-v0.17.0-update-2026-06-24.md` | Cherry-pick strategy for merging upstream v0.17.0 "The Reach Release" (~1,475 commits) while preserving 47 local governance-plugin commits. |
| Email Pipeline | `hermes-evolution/docs/email-send.md` | Documents the 9-step morning briefing pipeline (token refresh, Teams/Outlook fetch, classify, prioritize, draft responses). |

## Related Conversations (Episodic Memory)

- **2026-05-18** — Initial Hermes chief-of-staff setup: SOUL.md, memory files, cron automations (morning briefing, weekly status, PR monitor), gateway launchd service, WhatsApp pairing.
- **2026-05-22 to 2026-05-29** — Email send guard development, plugin dispatch bug fixes, control-room policy engine, FOCI token incident response.
- **2026-06-24** — Upstream v0.17.0 merge analysis, dashboard plugin exploration.

## Current State (as of 2026-07-01)

### What Works
- Plugin hook system (16+ lifecycle events, `pre_tool_call` authoritative blocking)
- Email-send-guard plugin (draft → preview → approve state machine)
- Control-room plugin (SQLite audit logging, YAML policy engine with regex)
- Tool-registry-guard plugin (banned-pattern blocking)
- Mandatory security plugin loading
- Registry-level pre_tool_call enforcement (moved inside `registry.dispatch()`)
- Morning briefing pipeline (Teams + Outlook fetch → classify → prioritize → draft)
- Gateway with Telegram, WhatsApp, Email platform adapters

### What's Broken / Missing

#### Email & Messaging (Blocking Chief-of-Staff Use)
- [ ] Gateway email reply path (`EmailAdapter.send()`) bypasses all guards — primary email path has zero enforcement
- [ ] Telegram delivery unreliable (SSL/TLS errors not classified as retryable)
- [ ] Silent channel fallback (Telegram fails → agent switches to WhatsApp without telling user)
- [ ] `"mirrored"` field semantically ambiguous — LLM claims "mirrored to telegram" when it means "recorded in session transcript"
- [ ] Stream consumer bypasses retry logic for transient errors
- [ ] Telegram media sends skip retry entirely
- [ ] Email toolset (`email_send_guard`) not auto-enabled — invisible to LLM unless manually activated
- [ ] `send_message` schema missing email examples (`email:user@example.com`)
- [ ] System prompt (SOUL.md) has zero email workflow guidance

#### Enforcement & Security
- [ ] Hook invocation failures are fail-open (exception caught, execution proceeds)
- [ ] Approval state stored in agent-writable JSON/SQLite (agent can forge)
- [ ] No egress controls for SMTP/Graph API/curl from execute_code/terminal
- [ ] No credential isolation — FOCI tokens readable by agent process
- [ ] EmailSendBroker single-chokepoint architecture not implemented
- [ ] Gateway reply path needs reply-aware policy (reply-to-inbound vs. outbound-to-new-recipient)

#### Orchestration & Autonomy (Gap to "Drive Claude Code for Development")
- [ ] No state machine / DAG workflow DSL (research identified Statewright as model)
- [ ] No approval gate framework (`pre_approval_request` hook is observer-only)
- [ ] No tool-call budget or rate limiting
- [ ] `gateway/builtin_hooks/` empty — no first-party guard plugins shipped
- [ ] No cross-turn policy persistence (policies don't survive session boundaries)
- [ ] No ability to spawn/manage Claude Code sessions programmatically
- [ ] No feedback loop from execution results back to planning
- [ ] No task decomposition or multi-step planning capability
- [ ] No way to monitor/intervene in running agent jobs from Telegram

#### Upstream Merge (Blocking Progress)
- [ ] v0.17.0 upstream merge not completed — 32 files conflict, email adapter relocated to plugin
- [ ] `send_message` removed as agent-callable tool upstream (intentional design decision)
- [ ] Email adapter migrated from `gateway/platforms/email.py` to `plugins/platforms/email/adapter.py`
- [ ] Gateway `run.py` refactored into 3 mixins — slash command dispatch changed

## Delta: Today → Automated Chief of Staff

### Tier 1: Make What Exists Actually Work
1. Fix email/Telegram delivery reliability (retryable errors, retry logic)
2. Auto-enable email toolset + add schema examples + system prompt guidance
3. Complete v0.17.0 upstream merge to unblock further development
4. Fix silent channel routing (prompt guidance + retry classification)

### Tier 2: Security & Trust
5. Implement EmailSendBroker chokepoint (route all email through single gate)
6. Move approval state outside agent-writable filesystem
7. Fail-closed hook invocation for dangerous tools
8. Credential isolation (FOCI tokens, SMTP creds not agent-accessible)

### Tier 3: Autonomous Chief of Staff Capabilities
9. State machine / workflow DSL (phase-based tool allowlists, transition rules)
10. Claude Code session management (spawn, monitor, intervene via Telegram)
11. Task decomposition + multi-step planning with execution feedback loop
12. Cross-session memory integration (gbrain as durable knowledge store)
13. Approval gates framework (pause on sensitive actions, resume after human OK)
14. Real-time agent monitoring dashboard (plugin-based, WebSocket status)

### Tier 4: Self-Improvement Loop
15. Hermes self-evolution pipeline (DSPy + GEPA skill optimization)
16. Execution trace analysis for automated improvement
17. Cross-agent portability (work across Claude Code, Codex, etc.)
