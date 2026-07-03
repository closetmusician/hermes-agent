# ABOUTME: Panel-2 ambition reconciliation for the 15 Codex-B "[AMBITION]" concerns against
# ABOUTME: hermes-fable-plan.md. Each idea is FOLDed into a phase, PROMOTEd to a milestone,
# ABOUTME: PARKed as vNext with rationale, or REJECTed. Guard rail applied throughout:
# ABOUTME: bigger only if it stays out-of-core + declarative and rides the primitives the plan
# ABOUTME: already builds (broker, factory, skill layer, ledgers). Adjudicated inline (Fable).

## Summary table

| id | title (short) | verdict | where it lands |
|---|---|---|---|
| B-1 | Nightly feature batches, not one feature | FOLD | P6 — new M6.5 batch-mode milestone |
| B-2 | Thread-to-shipped-change cross-domain workflow | FOLD | P7 capstone work item + §7 criterion |
| B-3 | PM artifact factory (PRDs/decks/sheets) | FOLD (thin) + PARK (rest) | P5 single-artifact item; full list vNext |
| B-4 | Glean → product-intelligence radar | FOLD | P7 weekly radar skill |
| B-5 | Customer/revenue signal closure (CRM) | PARK | vNext (Glean-first note recorded) |
| B-6 | Spec gate before implementation | FOLD | P6.3/P6.4 spec-first job type |
| B-7 | Bounded review/CI fix loop | FOLD | P6.4 one review-fix resume |
| B-8 | Draft-PR workflow as default | FOLD | P6.5/merge-policy.md per-repo option |
| B-9 | Continuous eval/backtest loops | FOLD | P7.2 scheduled calibration jobs |
| B-10 | Meeting orchestration (agenda→decisions→follow-through) | FOLD (post-meeting) + PARK (pre-meeting packet) | P4.1 extension; agenda/objections vNext |
| B-11 | Weekly operating packet | FOLD | P5 pm-weekly slice v2 |
| B-12 | Factory improves hermes/pm_os themselves | FOLD | P7.2 friction→factory lane |
| B-13 | Exec narrative factory (video reframe) | FOLD | P5.4 reframed scope |
| B-14 | Product operating model memory | FOLD | P4 skeleton + P5 refresh |
| B-15 | Leverage metrics, not just parity | FOLD | §7 leverage scorecard (measured, not gated) |

Verdicts: 13 FOLD (2 of them split FOLD+PARK), 1 PARK, 0 PROMOTE-to-new-phase, 0 REJECT.
Rationale for zero new phases: every accepted idea rides primitives P1–P7 already build; adding phases would
re-create the effort-inversion risk. B-1's batch milestone extends P6's exit rather than adding a phase.

## Per-idea reconciliation

**B-1 — FOLD (P6).** "One real feature" proves the mechanism, not the multiplier. Add after the M6 single-feature gate: "**M6.5 — Batch mode:** 3–5 scoped jobs queued in the evening run overnight (subject to the sleep policy) and produce a **morning approval packet**: one Telegram message per completed job (summary + diff stat + test + review) + a one-line batch digest. Weekly throughput (jobs merged/declined/NEEDS_ATTENTION) is recorded in the ledger." Rides: existing queue, budgets, approval UX. Out-of-core: yes.

**B-2 — FOLD (P7 capstone + §7).** The three pillars must compose or they're three assistants. Add P7 work item: "**Cross-domain capstone:** one real Teams/Outlook thread is turned — via existing skills — into: a drafted spec (PM artifact path), a Jira epic/story (jira-update path), and a factory job whose branch/draft-PR links back to the Jira item; every consequential step broker-gated. This is a composition test of existing primitives — no new infrastructure permitted; if it needs new machinery, that's a finding, not a build ticket." Add to §7 acceptance: "≥1 cross-domain workflow (thread → spec → Jira → merged change) completed end-to-end."

**B-3 — FOLD thin slice (P5) + PARK rest.** pm_os has the Office/SharePoint tools; generating artifacts is skills-on-CLIs. But "generate/update PRDs, launch briefs, roadmap sheets, status decks, decision logs" wholesale is a scope bomb. FOLD: P5 work item "**PM artifact skill — one type first:** pick ONE artifact type with Yu-Kuan (e.g., status deck or PRD draft to SharePoint), ship `pm-artifact/SKILL.md` producing it through registry tools, broker-gated on the SharePoint write; the SKILL.md doubles as the template for further artifact types." PARK (vNext): the full artifact-factory list, one line each, "expand only after the first type earns weekly use."

**B-4 — FOLD (P7).** Radar = new cron + skill on the existing glean wrapper + proactive contract; declarative. Add P7 work item: "**Product-intelligence radar:** weekly skill over Glean/Jira surfacing (a) decisions gone stale, (b) duplicate/conflicting work, (c) customer/account mentions needing attention — each with citations and a proposed action, delivered inside the weekly packet (B-11), never as extra pings. Acceptance: one radar item per month leads to a real action taken." Quiet-contract compliant by riding the weekly packet.

**B-5 — PARK (vNext).** Correct gap, wrong cost: a CRM/Salesforce connector is new load-bearing integration surface (auth, API, schema) — fails the out-of-core/cheap test today, and Track A confirms no existing integration to orchestrate. vNext entry: "Customer-signal layer — start Glean-mediated (Salesforce content Glean already indexes) when a concrete weekly question is named; direct CRM connector only if Glean-mediated proves insufficient. Out of scope now: no existing pm_os surface, and the effort-inversion guard says don't build a new integration while the factory and pm_os lanes are still landing."

**B-6 — FOLD (P6.3/P6.4).** Cheap, prompt-level, high quality-yield. Add: "**Spec-first job type (default for feature-class jobs):** stage 1 — worker drafts spec: requirements, acceptance criteria, test list, risk notes, affected surfaces (schema'd output, ~$0.2–0.5); owner may approve/redirect cheaply at spec stage (optional tap). Stage 2 — implementation worker receives the approved spec. Rote/small jobs skip stage 1 (`kind: quick` at intake)."

**B-7 — FOLD (P6.4).** Symmetric with the existing one-test-fix resume. Add: "After REVIEW, **one bounded review-fix resume**: worker addresses P1-severity codex findings within remaining budget, then re-test + re-review once; still-open P1 findings ride the approval packet as known issues. Never more than one loop — flip-flopping marks NEEDS_ATTENTION."

**B-8 — FOLD (P6.5 / merge-policy.md).** Draft PRs give CI/review history where remotes exist; local merge stays right for tiny personal repos. Add to merge-policy.md schema: "per-repo `integration: local-merge | draft-pr`. Default **draft-pr** for repos with a GitHub remote (supervisor runs `gh pr create --draft`, approval tap runs `gh pr merge`); local-merge for remote-less/tiny repos. Approval UX identical; the artifact differs."

**B-9 — FOLD (P7.2).** pm_os already ships `backtest-*` and `synthesize-feedback.js` — the evidence shows the loop half-built. Add P7.2 work item: "**Calibration loop:** scheduled monthly (with the maintenance budget): triage-classification backtest on the last N weeks (pm_os backtest tools), safe-lane audit mining false-holds/false-sends, pm-send feedback synthesis — each run ends in a **proposed policy diff** (priority-map/auto-resolver edits) through the normal review path. No silent policy drift: proposals only."

**B-10 — FOLD post-meeting half (P4.1) + PARK pre-meeting packet.** Decisions-and-follow-through rides existing primitives (meeting-notes ingestion cat 7, tasks.md, broker drafts). Extend P4.1: "Post-meeting path: ingested meeting notes → decisions appended to a decision log → follow-up tasks created in `tasks.md` same-turn → follow-up drafts proposed through the broker. Acceptance: one real meeting produces logged decisions + at least one follow-up task and draft." PARK: agenda drafting, pre-read packet, likely-objections brief — "vNext: pre-meeting orchestration; add after post-meeting loop proves useful (avoids proactive-contract pressure)."

**B-11 — FOLD (P5 pm-weekly slice).** The evidence JSON already exists; the packet is prompt/skill-level. Amend P5.1 pm-weekly item: "v2 of the weekly skill produces the **weekly operating packet**: status email draft + linked evidence + risks/asks + Jira delta (from pm-jira data) + follow-up tasks written to `tasks.md`. Optional deck rides the B-3 artifact skill once it exists (not a v2 requirement)."

**B-12 — FOLD (P7.2).** The plan already lets the factory propose skills; close the loop from telemetry. Add: "**Friction→factory lane:** preflight failures, degraded lanes, and calibration findings accumulate in a friction log; a monthly pass turns the top items into factory job proposals (skills/wrappers/tests — markdown and out-of-core code only). Hermes/pm_os-core changes stay human-initiated; factory merges into the orchestrator's own repos remain restart-gated (merge-policy rule, unchanged)."

**B-13 — FOLD (P5.4 reframe).** Right lens: the CPO need is async narrative, video is one output. Reframe P5.4: "**Exec-narrative/media capability (discovery-and-build):** the spike evaluates the narrative need — launch deck, demo script, narrated video — against available providers (`video_gen` plugins [UNVERIFIED] / Office tooling via B-3) and builds the minimal skill for ONE named use case; renders/decks hold-by-default in the broker. Park (owner-approved, per C-6) remains acceptable." Matrix video row wording follows suit.

**B-14 — FOLD (P4 skeleton + P5 refresh).** This is canonical-state extension — exactly the declarative pattern the plan celebrates, and the highest-leverage single artifact for a CPO. Add: P4 artifact "`product-operating-model.md` — bets/initiatives, owner, status, key decisions (with dates), open risks, next-best-action; single-writer; seeded manually with Yu-Kuan." P5 amendment: "the weekly packet run refreshes the operating-model file (status/deltas) same-turn; acceptance: a fresh-context reader answers 'what are the current bets and their status?' from the file alone."

**B-15 — FOLD (§7, measured not gated).** Parity gates prove replication; leverage must be *visible* — but hard 10x gates would invite gaming and re-litigate scope. Add to §7: "**Leverage scorecard (measured, reported, not pass/fail):** over two representative weeks at M7 — safe-lane actions auto-resolved/week, factory jobs merged/week, PM artifacts produced/week, cross-domain workflows completed, and a one-line hours-saved estimate. Reported in the exit review; informs vNext, does not block completion."

## Ready-to-paste vNext section (for the reviser)

### vNext — explicitly parked (Appendix A companion)
- **Customer/revenue signal layer (CRM/Salesforce connector)** — start Glean-mediated when a concrete weekly question is named; a direct connector is new load-bearing integration surface and fails the out-of-core test today (B-5).
- **Full PM artifact factory** (launch briefs, roadmap sheets, decision logs, board decks as a family) — expand from the single P5 artifact type only after it earns weekly use (B-3).
- **Pre-meeting orchestration** (agenda drafts, pre-read packets, likely-objections briefs) — add after the post-meeting decision/follow-through loop proves useful; avoids proactive-contract pressure (B-10).
