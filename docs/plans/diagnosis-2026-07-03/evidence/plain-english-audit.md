# ABOUTME: Plain-English audit of hermes-fable-plan.md for a no-context reader.
# ABOUTME: Produced by a simulated 18-year-old intern with zero project knowledge; used to guide a rewrite pass.

---

## Per-Section Comprehensibility Scores (1 = opaque, 10 = clear)

| Section | Score | Primary issue |
|---|---|---|
| §0 Vision | 4 | Jargon cascade in sentence 1; "pm_os", "headless claude -p", "worktrees", "trust ledger", "kanban dispatcher" all undefined. Vision is recoverable if you read slowly, but fails the one-read test. |
| §1 Locked Decisions | 5 | Rationale column cites internal evidence files by path. L2's OpenRouter explanation assumes you know what OpenRouter is. L4's "hybrid reset" unexplained. |
| §2 Architecture | 2 | The ASCII diagram is incomprehensible without prior knowledge. "Broker", "trust ledger", "429 failover ladder", "SkillOpt", "kanban dispatcher", "VIBE", "SM-1", "SEC-2", "G1/G5/G14", "OR-2" are never defined before use. Component notes are dense expert shorthand. |
| §3 Phases | 5 | Phase goals are readable. Deliverables collapse into expert jargon mid-sentence. Binding gates use internal codes (FM-1, FM-3, W4, S8, S9, S10, IC-1, IC-2, OR-1–4, BD1, SM-1, SEC-1, SEC-2, G-substrate §5.1) with zero inline explanation. |
| §4 Reliability | 6 | The best-written section. Problems are named in plain terms (memory, crashes, stalls). Still uses "429" and "PGID" without defining them. |
| §5 Safety & Security | 5 | Prompt injection explanation is actually good. Collapses again with "SEC-1", "SEC-2", "SM-1", "FM-8", "bypassPermissions", "tool-result time". |
| §6 Self-Improvement | 7 | The three-ring table is the clearest structure in the document. Loses a point for "SkillOpt distillation", "auto-tuning within bounds" (tuning what, exactly?), and silent `tuning-log.md` reference. |
| §7 Milestones | 8 | Best section. Each milestone names a concrete thing the owner can use. Minor: M3/M-Magic ordering is confusing (M3 cum-day 22 but M-Magic is day 23–27 — ordering looks wrong). |
| §8 Bootstrap / Open Questions | 7 | Concrete day-one actions are the clearest writing in the plan. BD1 is explained inline, which is the right pattern. |
| §9 Not in Scope | 8 | Clear. No action needed. |
| Appendix A | 7 | Table is readable. "Wow×feasibility", "Eff", SPINE concept undefined inline. |

---

## 15 Worst Offenders

1. **L15, "pm_os"** — Used 8 times before any definition. Fix: add one sentence at first use: "pm_os — a separate personal assistant codebase that handles email, calendar, and Jira; this plan reuses its plumbing."
2. **L15, "headless `claude -p` and `codex exec` workers"** — "headless" and both CLI commands undefined. Fix: "AI worker processes (`claude -p` = Anthropic CLI, `codex exec` = OpenAI CLI) that run in the background without a GUI."
3. **L15, "job DAGs"** — DAG not spelled out. Fix: "dependency-ordered task lists (DAGs = directed acyclic graphs — each task knows which other tasks must finish before it starts)."
4. **L15, "worktrees"** — Used 18 times with zero definition. Fix at first use: "worktrees — isolated copies of a git repo on disk so parallel workers don't overwrite each other's code."
5. **L41, "OpenRouter"** — Introduced as a fact with no explanation. Fix: "OpenRouter — a pay-per-use service that routes API calls to many AI models at ~1/10 the cost of direct subscriptions."
6. **L82, "429 failover ladder"** — A status code and a project pattern smashed together. Fix: "HTTP 429 = 'rate limit exceeded'; the failover ladder means: when one AI provider throttles us, automatically retry on the next cheaper provider."
7. **L94, "trust ledger"** and **"broker"** in the diagram — undefined objects in the middle of an architecture diagram. Fix: add a one-sentence glossary box directly above the diagram.
8. **L105, "SkillOpt distillation"** — Sounds meaningful, is undefined. Fix: "weekly automated pass that reads recent job histories and rewrites the AI prompt templates (called 'skills') to fix recurring failure patterns."
9. **L114, "gateway-embedded kanban dispatcher (`kanban.dispatch_in_gateway`, default on — the standalone `hermes-kanban-dispatcher.service` systemd unit is DEPRECATED per S3)"** — No newcomer knows what a kanban dispatcher, gateway, systemd unit, or S3 (not Amazon) is. Fix: break into two sentences with one-line definitions each.
10. **L129, "fresh-context verified"** — Appears repeatedly as a gate requirement with no explanation. Fix at first use: "(verified by a person or agent with no prior knowledge of the task — preventing the builder from checking their own work)."
11. **L136, "hybrid reset"** and **"~5,000-commit fork"** — The whole clean-base rationale requires knowing what a fork and a "5,000-commit drift" mean for maintainability. Fix: add two sentences: what a fork is, why 5,000 commits of drift makes upgrades painful.
12. **L7 (header block), "P0/P1 findings resolved: worker-dispatch reclassified BUILD-NEW, prompt-injection runtime scan + broker-only pushes, probe-validated model IDs, P1a/P2/P1b resequence"** — The status block is unreadable metadata soup. Fix: move to a collapsible revision-history appendix; replace with a single "What changed from v1: …" sentence.
13. **L165, "PGID / setpgid / SIGTERM-to-PGID"** — Unix process-group internals dropped with no explanation. Fix: "process group kill (a Unix mechanism to kill a child process and all its children at once)."
14. **L8, citation pattern "per `evidence/10x-ambition-critique.md`"** — Every paragraph references an internal evidence file the reader has never seen and cannot act on. Fix: replace each citation with the one-line conclusion the file supports, e.g., "per internal critique, factory-first ordering ships the first owner win ~2 weeks sooner."
15. **L222, "SkillOpt/CODESKILL pattern; +9.69% pass-rate in the literature"** — Sounds like a stat but the reader can't verify it and doesn't know what it measures. Fix: "a technique from recent AI-agent research where prompt templates are automatically improved from run histories; published benchmarks show ~10% improvement in task success rate."

---

## Biggest Structural Change Needed

**§0 does not orient a newcomer.** After reading it, you still don't know: (a) what "hermes" is — is it a library, a server, a chatbot? (b) what was broken before — why was a new plan needed? (c) what will physically exist when this plan is done — a running process, a set of scripts, a web dashboard?

The single highest-leverage fix: add a **4-sentence "What is this?" box at the very top of §0**, before the vision statement, that answers: "Hermes is a personal automation server running on your Mac. Before this plan, it could only do one AI task at a time with a human watching. After this plan, it will autonomously pick up a queue of coding tasks each evening, run them overnight using AI workers, and present only approval decisions to the owner each morning. Everything else in this document is how we build that."

---

## Good Parts (Do Not Churn)

- **§7 Milestones table** — concrete, owner-facing, scannable. The "You can use it for" column is exactly right.
- **§8 Bootstrap actions** — numbered, actionable, stop-after-4 is a good discipline marker.
- **§9 Not in Scope** — tight, unambiguous.
- **§4 Reliability** — the problem names (OOM, silent stalls, watchdog hierarchy) are in plain English and the mitigations are described mechanically, not in buzzwords.
- **§6 Self-Improvement three-ring table** — the ring metaphor works; the "Immutable" row is the clearest statement of the safety contract anywhere in the document.

---

## Overall Score: 4/10 for a no-context reader

The plan is thoroughly researched and internally consistent. It fails plain-English for one systematic reason: **every term is assumed known**. A domain-expert reader scores it 8/10. A smart newcomer scores it 3–4/10. The fix is a 30-line glossary box + one §0 orientation paragraph + stripping internal file cites from the body into footnotes.
