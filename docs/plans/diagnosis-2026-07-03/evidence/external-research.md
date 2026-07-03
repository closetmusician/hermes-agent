# External Research — hermes-agent Fork Diagnosis (2026-07-03)

ABOUTME: External evidence for the keep-fork vs merge-upstream vs start-fresh decision.
ABOUTME: 8 findings max, each with claim, source, and decision relevance.
ABOUTME: Researched via upstream GitHub (README, releases, issues), official security
ABOUTME: docs, and 2026 deployment/framework comparisons.
ABOUTME: Part of the diagnosis-2026-07-03 evidence bundle.

## F1. Upstream has moved so far that "merge" is really a re-port

Upstream is at v0.18.0 ("The Judgment Release", tagged 2026.7.1 under the new date-based scheme, released 2026-07-01) with 14,291 commits on main. Since the fork's v0.14.0 base (2026-05-16), upstream shipped four major releases (v0.15 "Velocity", v0.16 "Surface", v0.17 "Reach", v0.18 "Judgment") that rearchitected the gateway (scale-to-zero dormancy, drain coordination for clean restarts), added a native Electron desktop app, a full admin dashboard replacing SSH+YAML config, background/parallel subagents, and completion-contract self-verification. Skills bundling changed and dashboard auth tightened.

- Source: https://github.com/NousResearch/hermes-agent and https://github.com/NousResearch/hermes-agent/releases
- Decision relevance: 3,405 commits behind across 4 major releases means merge-upstream ≈ re-implementing the 61 local commits on a new codebase; its cost should be compared against start-fresh, not against keep-fork.

## F2. The fork's gateway-crash class is a known, since-fixed upstream defect family

The v0.14/v0.15-era gateway had a documented cluster of lifecycle bugs matching classic "chief of staff went dark" symptoms: agent tool-loop freeze destroying the ThreadPoolExecutor with no recovery so the gateway main loop keeps running but every asyncio call fails (#10849); stale `gateway.pid` causing restart loops after SIGKILL (#13655); and `hermes gateway restart` failing exactly when the gateway is crashed/unresponsive because the SIGUSR1 handler can't run (#12438). v0.18.0 explicitly closed all P0/P1 issues (~700 items).

- Source: https://github.com/NousResearch/hermes-agent/issues/10849, https://github.com/NousResearch/hermes-agent/issues/13655, https://github.com/NousResearch/hermes-agent/issues/12438
- Decision relevance: local gateway instability is likely inherited upstream debt already paid off in v0.18 — keeping the fork means re-fixing bugs upstream already fixed; check local crash logs against these signatures before blaming fork-local code.

## F3. Staying on v0.14.0 means missing shipped security fixes, including a CVE pin

v0.16.0 shipped concrete security hardening the fork lacks: the CVE-2026-48710 Starlette pin, SSRF off-loop hardening, and subprocess credential stripping. v0.18.0's mass P0/P1 closure included further stability/security items. An independent audit of the default configuration (#7826, filed against v0.8.0) found 4 critical / 9 high issues — unrestricted local shell execution, unrestricted filesystem reads (SSH keys, credentials), approval checks skipped entirely on container backends, and persistent agent-created skills in `~/.hermes/skills/` — several of which were only worked through in later releases.

- Source: https://github.com/NousResearch/hermes-agent/releases (v0.16.0 notes), https://github.com/NousResearch/hermes-agent/issues/7826
- Decision relevance: keep-fork carries an open-ended obligation to backport security fixes; the longer the divergence, the worse this gets — a strong standing argument against keep-fork for an agent holding email and calendar credentials.

## F4. Even current upstream has NO safe first-party email path — merging won't fix the email platform

Upstream's supported email integration is the himalaya CLI skill (IMAP/SMTP driven through the terminal tool), which means the agent must hold shell access while reading untrusted inbox content — precisely the content-as-instruction shape where a confused email steers the agent into arbitrary shell commands. A shell-free native mail tool is an open feature request (#42307), not a shipped feature, and the issue itself concedes the dangerous-command gate is "pattern-based and evadable."

- Source: https://github.com/NousResearch/hermes-agent/issues/42307
- Decision relevance: the fork's custom email plugin work is not obsoleted by upstream — email must be solved locally under ALL three options, so email-platform effort is a sunk-cost-neutral factor; what matters is which base makes a capability-scoped (no-terminal) mail tool easiest.

## F5. Upstream's philosophy rejects harness-level defense against content being mistaken for instructions; the fork's risk model exceeds what hermes-agent guarantees

Issue #18981 ("No harness-level defense against prompt injection in tool outputs") was closed **not planned**: untrusted tool output (web, files, email) enters model context unvalidated, and the sanctioned defense is a behavioral skill (BridgeWard) that depends on model compliance. The official security docs confirm the model: seven-layer defense covers command approval (manual/smart/off), a hardline blocklist, context-file scanning, and the tirith pre-exec command scanner — but nothing for untrusted content in tool results, and no email-specific guidance at all.

- Source: https://github.com/NousResearch/hermes-agent/issues/18981, https://hermes-agent.nousresearch.com/docs/user-guide/security
- Decision relevance: an inbox-reading chief of staff needs architectural (capability-scoped) defenses that upstream has declared out of scope — this is the strongest single argument for start-fresh on a framework with permission/hook primitives, or for accepting permanent fork-local security architecture.

## F6. Working personal chief-of-staff deployments converge on patterns that reduce, not increase, LLM exposure

Documented 2026 single-operator builds that run reliably share an architecture: two-tier processing (pure-rule scheduled scans every 30 min; LLM only for triage/drafting at set times), graduated trust (autonomous mode for predictable tasks, approval-gated mode for higher-stakes actions), dual-model cost routing (small model classifies, large model drafts, <$3/day), and memory with decay (~2,000 active observations). Every message flows through one intelligence+safety pipeline regardless of entry channel.

- Source: https://doneyli.substack.com/p/i-built-an-ai-chief-of-staff-that (also https://medium.com/data-science-collective/i-built-a-100-open-source-local-multi-agent-ai-chief-of-staff-on-my-laptop-heres-the-blueprint-f6301bb48488)
- Decision relevance: if the failed deployment ran LLM-in-the-loop on every cron tick and inbox poll, the fix is architectural (rules-first tiers + approval gates) and portable to any of the three options — this reframes part of the failure as design debt, not framework choice.

## F7. Independent comparison rates hermes the reliability leader among personal-agent platforms; alternatives are not a safer harbor

Peter Yang's May 2026 hands-on comparison found hermes more reliable than openclaw (which imposed ~10% ongoing maintenance overhead, memory bugs, and breakage after updates) with notably better cron-job stability; Claude-based setups showed only ~98% uptime with silent feature failures; Codex lacked mobile; Gemini was fragmented. His conclusion: "reliability trumps features" for 24/7 personal agents.

- Source: https://creatoreconomy.so/p/the-race-to-build-a-personal-ai-agent-openclaw-hermes-claude-codex-gemini
- Decision relevance: abandoning the hermes ecosystem for the openclaw pattern trades known problems for worse ones — current upstream hermes-agent is competitively the most reliable base, favoring merge-upstream over start-fresh-on-another-platform.

## F8. Framework fit for a single-user macOS chief-of-staff: the real trade is hermes' built-in channels/cron vs Claude Agent SDK's security primitives

2026 framework benchmarks agree on the shapes: LangGraph offers deterministic graphs, persistent checkpointers, and native human-in-the-loop (chosen where auditability is binding) but ships zero channel delivery or scheduling; CrewAI is multi-agent-role-shaped and burns ~2-3x tokens per run ($0.78 vs $0.35 LangGraph / $0.42 Claude SDK on comparable workflows) — wrong shape for one user; Claude Agent SDK provides the autonomous loop with model-level safety, permission callbacks and hooks (the capability-scoping F5 asks for) but, like LangGraph, requires building channel adapters, cron, and approval UX yourself — exactly the parts hermes-agent ships batteries-included (20+ channels, one gateway, built-in scheduler, approval system).

- Source: https://qubittool.com/blog/ai-agent-framework-comparison-2026, https://www.requesty.ai/blog/best-ai-agent-sdks-compared-2026-langchain-crewai-openai-anthropic-google
- Decision relevance: start-fresh is only rational if correctness for untrusted-content handling (F5) is the binding constraint worth rebuilding delivery/cron/approvals for; otherwise merge-upstream keeps the infrastructure hermes already does best.
