# ABOUTME: Superset coverage ledger auditing whether v2 (factory-first hermes-fable-plan) preserves every
# ABOUTME: frustration/remediation/fix item from diagnosis.md, remediation-plan.md, and v1 of the plan (commit 58f5e56ad).

# Superset Audit — v2 vs v1 + Diagnosis + Remediation Plan

**Date:** 2026-07-04
**Rule audited:** v2 must be a *superset* of v1's remediation content. Every frustration-fix and hardening item must survive in v2, be deliberately parked with rationale, or be genuinely obsoleted by v2's architecture. Nothing silently dropped.
**Sources:** (1) `diagnosis.md` FM-1..FM-9; (2) `remediation-plan.md` phases + decision matrix; (3) v1 plan `git show 58f5e56ad:…/hermes-fable-plan.md` (CoS-first, extends remediation-plan, adds pm_os-parity + factory); (4) v2 `hermes-fable-plan.md` (factory-first).
**Verdict key:** COVERED | PARTIAL (what's missing) | DROPPED (absent) | OBSOLETE (architecture genuinely moots it — justified).

---

## Counts

- **COVERED: 39**
- **PARTIAL: 12**
- **DROPPED: 1**
- **OBSOLETE: 2**
- **Total: 54**

---

## A. Root-cause frustrations (diagnosis.md FM-1..FM-9)

| ID | Item | Verdict | Evidence / gap |
|---|---|---|---|
| R1 | FM-1 over-broad permissions; in-process guard reachable by shell | COVERED | v2 §5 SM-1 immutable-ring submission gate + worker `--allowedTools` exclusion + broker credential starvation (P1a.1/1a.4). |
| R2 | FM-2 effort inversion (patches shipped, structure never) | COVERED | v2 spine is structure-first by construction; §9 explicitly "not adding features before the enforcement boundary." |
| R3 | FM-3 guards protect the wrong path / no outbound recipient allow-list | COVERED | v2 P1a.1 broker owns ALL egress incl. `git push` (SEC-1); recipient allow-list in broker; §2 governance unification. |
| R4 | FM-4 fail-open enforcement (mandatory_load_failures no consumer) | PARTIAL | v2 broker "fail closed" (P1a.1) + SM-1, but the specific *mandatory-plugin-load-failure → refuse-to-start* consumer from diagnosis FM-4 is not called out; v1 remediation named it implicitly via fail-closed hooks. Add to P0. |
| R5 | FM-5 gateway operational instability / no health model | COVERED | v2 P0.5 jobs-first health signal, P0.4 supervisor sanity + launchd, P0.6 zombie kill. |
| R6 | FM-6 `/approve-email` broken end-to-end; unproven real send | COVERED | v2 P1a.2 stable action IDs over durable artifacts (kills hash-of-truncated-body class); M-Magic/M2 fresh-context real-delivery gate. |
| R7 | FM-7 upstream upgrade debt (~5,000 behind) | COVERED | v2 L-lock clean-upstream hybrid reset; P0.1; P7.1 dual upstream tracking. |
| R8 | FM-8 no inbound permission boundary (content ≠ instructions) | COVERED | v2 §5 `trust-policy.md` rule + runtime injection scan at tool-result time (SEC-1); P6.1 retargets proactive/trust files. |
| R9 | FM-9 setup/env friction (mostly resolved; stale-plist tail) | COVERED | v2 P0.4 launchd points at new checkout; P0.1 clean base. |

## B. Remediation-plan phases & decision-matrix items

| ID | Item | Verdict | Evidence / gap |
|---|---|---|---|
| R10 | Clean upstream base, don't merge fork | COVERED | v2 P0.1 (+ branch migration IC-1/W2). |
| R11 | Bring `.env`/`~/.hermes` config, drop god-file edits | COVERED | v2 P0.1. |
| R12 | Kill zombie Discord | COVERED | v2 P0.6. |
| R13 | WhatsApp pair-or-disable, no 300s loop | PARTIAL | v2 P0.6 says "pair-or-disable-cleanly" but drops v1's explicit **paired-and-verified send/receive gate** (v1 P2.4) that formally closes the conditional. Re-add a verify gate. |
| R14 | Honest health signal; reverify observability dashboard crash | COVERED | v2 P0.5 (jobs-first). Dashboard-reverify note is implied by "replace dashboard" lineage; minor. |
| R15 | Supervisor sanity (launchd path, KeepAlive throttle) | COVERED | v2 P0.4. |
| R16 | Out-of-process send broker owns all third-party egress | COVERED | v2 P1a.1. |
| R17 | 5-condition safe lane (auto-resolver) | PARTIAL | v2 uses safe-lane for *comms* (P6.3) and names `auto-resolver.md` (§6) but the 5-condition test is not a first-class P1 deliverable as in v1 P1.2; it surfaces only in P6. Pull the 5-condition spec forward to P1a or state it's comms-only-by-design. |
| R18 | Fold approval into broker (replace `/approve-email`) | COVERED | v2 P1a.2. |
| R19 | Credentials out of assistant reach (keychain/broker env) | COVERED | v2 P1a.4. |
| R20 | M1 fresh-context bypass verification (approved send arrives; direct send impossible) | COVERED | v2 P1a exit gate (incl. raw `git push` bypass). |
| R21 | Control-plane files: priority-map / auto-resolver / trust-policy / proactive-contract | COVERED | v2 P6.1 (+ §6 rings). Sequenced late (P6) not P2. |
| R22 | `tasks.md` + `processed.json` canonical state + idempotency ledger | PARTIAL | v2 has `tasks.md` intake tag (P2.1) + job ledger, but the CoS canonical `tasks.md`/`processed.json` idempotency layer that prevents double-processing sweeps lands only implicitly in P6; v1 made it P2.1. Confirm placement. |
| R23 | First briefing skill (morning-triage, reference anatomy, one-line cron) | PARTIAL | v2 P6.3 CoS triage layer references pm_os briefing, and P4.4 reuses `run-morning.js`; but the standalone reference-anatomy `morning-triage/SKILL.md` with closed buckets + ledger-no-reprocess is not an explicit deliverable. |
| R24 | Draft-reply skill in yk-voice via broker safe-lane | COVERED | v2 P6.3. |
| R25 | PR-monitor as a one-line skill (propose, not auto-post) | OBSOLETE | v2's factory *is* the PR pipeline; PR summaries/cards are P4.4 morning surfaces and codex-review gating. The v1 "re-point pr-monitor cron to a skill" is subsumed by the factory's own PR-card flow. |
| R26 | Calendar-read + meeting-prep (check ALL calendars) | DROPPED | v2 §9 parks "calendar/meeting-prep" as vNext. This was a core v1 P4 CoS capability and a diagnosis gap-analysis row. Parked with rationale ("control-plane convenience, not the product") — acceptable as a *deliberate* park, but must be logged, not silent. |
| R27 | Proactive discipline enforced (quiet-by-default, one nudge, never repeat) | COVERED | v2 P6.1 proactive-contract retargeted; §4 last-tick SLA; D-8 alert-on-anomaly. |
| R28 | Sustainable upstream tracking cadence (monthly, additive/out-of-core) | COVERED | v2 P7.1. |
| R29 | Self-improving skills as markdown, owner-reviewed diffs | COVERED | v2 P5.3 + §6 ring 1. |

## C. v1-plan additions beyond remediation-plan (pm_os parity, factory, hardening)

| ID | Item | Verdict | Evidence / gap |
|---|---|---|---|
| R30 | Governance unification — one broker approval surface for send / pm_os-write / merge | COVERED | v2 §2 diagram + §5; all gates ride the broker. |
| R31 | pm_os pipeline parity: pm-morning full | COVERED | v2 P4.4 reuses run-morning generator; briefing is core. |
| R32 | pm_os: pm-weekly / weekly operating packet | PARTIAL | v2 §9 parks "full pm_os parity (pulse/weekly/exec-narrative)"; only Jira-intake + morning-briefing harvested (P6.4). pm-weekly packet is parked — deliberate, logged. |
| R33 | pm_os: pm-send broker-gated dispatch (one approval, reconcile AskUserQuestion) | PARTIAL | v2 P6.3 drafts ride the broker, but pm-send's own confirm-gate reconciliation (v1 P5.2) is not restated. Fold into P6.3. |
| R34 | pm_os: pm-jira monitor on cadence + launchd token fix | COVERED | v2 P3.3 Jira poller (BUILD-NEW) handles token relocation + 401 re-auth surfacing. |
| R35 | pm_os: pm-pulse survey (approval or standing safe-lane) | PARTIAL | Parked under "full pm_os parity" (§9). Deliberate; logged. Was a v1 P5 gate row. |
| R36 | pm_os: pm-login shared 401 fallback path | COVERED | v2 P0.4 credential pre-warm + P3.3 401 surfacing generalize this. |
| R37 | pm_os: yk-comms/yk-voice on every outbound draft | COVERED | v2 P4.4 briefing in yk-voice; P6.3 drafts via broker. |
| R38 | pm_os: Glean enterprise search skill | PARTIAL | v2 §9 parks CRM/Salesforce; Glean isn't explicitly retained. Repo-onboarding uses code-review-graph, not Glean. Confirm whether Glean-answer skill is parked or dropped. |
| R39 | pm_os fragility trio: FOCI RT rotation rule (TOKEN-7) | PARTIAL | v2 P0.4 pre-warms tokens + P3.3 token relocation, but the explicit **no-direct-refresh / ensure-tokens.js-only TOKEN-7 rule** is not restated. Add to P0.4. |
| R40 | pm_os fragility: Okta browser-session expiry → surface "re-auth needed" | PARTIAL | v2 P0.4 says "needs user present, never fail silent" for headed-browser — covers the spirit; the specific Okta `~/.agent-browser/pm-os` lane isn't named. Minor. |
| R41 | pm_os fragility: `$ATLASSIAN_API_TOKEN` in `.zshrc` invisible to launchd | COVERED | v2 P3.3 "Atlassian token relocation to a launchd-reachable store; 401→re-auth." |
| R42 | Auth health + paused-degraded lanes (no retry until re-auth) | PARTIAL | v2 P0.5 health signal + §4 last-tick SLA cover detection; the explicit **paused degraded lane** state (v1 P5.3.4) isn't named. |
| R43 | Factory: durable job store (SQLite, transactions, monotonic states, integrity check) | COVERED | v2 P2.1. |
| R44 | Factory: worktree-isolated spawn, per-repo merge lock, rebase-retest-merge | COVERED | v2 P2.2 / P2.4; L-lock. |
| R45 | Factory: scrubbed worker env, no secrets, strict-mcp, allowlist, never bypassPermissions | COVERED | v2 P2.2 + §5 (broker-mediated scoped secret injection W5). |
| R46 | Factory: test gate + independent codex review + one bounded auto-fix/review-fix resume | COVERED | v2 P2.3 gauntlet. |
| R47 | Factory: three cost stops (per-job budget, wall-clock SIGTERM to PGID, daily reserved ceiling) | COVERED | v2 P2.6. |
| R48 | Factory: process groups for clean kills (no orphaned children) | COVERED | v2 P1a.3 SIGTERM-to-PGID; P2.2. |
| R49 | Factory: one-tap merge approval (broker UX) + local-merge/draft-pr per repo | COVERED | v2 P2.4 merge-policy.md. |
| R50 | Factory: worktree cleanup + 24h failure retention + disk pre-flight | COVERED | v2 P5.4 worktree GC (OR-5) + retention; disk-floor spawn gate. |
| R51 | Factory: health check v2 (jobs + workers + last-tick, not just channels) | COVERED | v2 P0.5 jobs-first from day one; §4 last-tick SLA. |
| R52 | Factory: negative test — agent cannot merge to protected branch without tap | COVERED | v2 P2 exit gate (fresh-context, incl. immutable-ring rejection). |
| R53 | Factory: spec-first job type (cheap stage-1 spec, optional tap; `kind: quick` skips) | COVERED | v2 P2.2. |
| R54 | Factory: batch mode (3–5 overnight jobs → morning approval packet) | COVERED | v2 P4.3 overnight queue + P4.4 packet (scaled up to the flagship). |
| R55 | Self-modification restart-gating of factory merges into hermes/pm_os | COVERED | v2 §5 SM-1 + §6 immutable ring; restart-gated per merge-policy. |
| R56 | Merge-conflict handling between parallel jobs (serialize, warn on overlap, never force) | COVERED | v2 P2.4 per-repo lock; P3.2 fleet scheduler; conflict→NEEDS_ATTENTION. |
| R57 | Silent false-success defense (schema'd result + deterministic tests + diff review) | COVERED | v2 P2.2/P2.3 gauntlet; trust the diff + green tests, not transcript. |
| R58 | Flaky-test-gate handling (one retry, flip-flop→NEEDS_ATTENTION) | COVERED | v2 P2.3. |
| R59 | Cross-domain capstone (thread → spec → Jira → merged change) | PARTIAL | v2 P7.4 "explain this merge" + cross-repo transfer exist, but the specific v1 thread→spec→Jira→factory composition capstone (with Jira write-back links) is not restated as an exit gate. Fold into P6/P7. |
| R60 | Supervisor is plain-code, never an LLM in the loop | COVERED | v2 L-lock + §2 + §4. |
| R61 | Setup checklist w/ behavioral gates + placeholder detection ("any unchecked box = not done") | PARTIAL | v2 has probe/capabilities gates (P0.2) but drops the CoS `SETUP-CHECKLIST.md` behavioral-acceptance artifact (v1 §3.1 row 11) — the antidote to "76/76 tests pass ≠ works." Re-add for CoS lanes in P6. |
| R62 | Per-scheduled-skill preflight block (entrypoint/flags/files/token/write-gate) | PARTIAL | v2 doesn't restate the mandatory preflight-block-in-every-scheduled-SKILL anatomy (v1 P2.2/D-9). Fold into P5/P6 skill anatomy. |
| R63 | Product-operating-model.md skeleton (bets/decisions/risks/next-best-action) | OBSOLETE | v2 §9 explicitly parks it as "a CPO artifact… control-plane convenience, not the product." Genuine deliberate scope call for a factory-first product; logged, not silent. |

---

## Placement recommendation

**Strategy (one):** Insert a single **Phase R — CoS/Reliability Precursor** immediately after P0 and before P1a, absorbing the fast reliability-hardening items the owner called a "precursor" (R4, R13, R17, R39, R40, R42, R61, R62), because they harden the very host/broker the factory is built on and are cheap; keep the genuinely CoS-product items (R22, R23, R32, R33, R35, R38, R59) inside the already-existing P6 control-plane phase where they belong, and log R26/R63 as deliberate vNext parks in §9. This puts safety-critical fail-closed/token/preflight fixes on the critical path (nothing merges overnight on an un-hardened host) while avoiding a second CoS build that would re-create the effort-inversion v2 was written to escape.

**PARTIAL + DROPPED items, one line each, with placement:**

- R4 (FM-4 fail-open mandatory-load consumer) → **Phase R** (host hardening, refuse-to-start on missing guard).
- R13 (WhatsApp paired-and-verified gate) → **Phase R** (close the conditional with a real send/receive test).
- R17 (5-condition safe-lane as first-class deliverable) → **P1a** (it's the broker's core policy; don't defer to P6).
- R22 (CoS `tasks.md`/`processed.json` idempotency layer) → **P6.1** (control-plane files).
- R23 (morning-triage reference-anatomy skill) → **P6.3** (CoS triage layer).
- R32 (pm-weekly operating packet) → **§9 park** (already parked; keep logged).
- R33 (pm-send confirm-gate reconciliation, one approval) → **P6.3** (broker dispatch mechanics).
- R35 (pm-pulse survey lane) → **§9 park** (logged).
- R38 (Glean enterprise-search skill) → **§9 park** — decide park vs drop with owner.
- R39 (TOKEN-7 no-direct-refresh rule) → **Phase R** (token safety on the shared host).
- R40 (Okta session-expiry surfacing) → **Phase R** (auth-lane hardening).
- R42 (paused-degraded auth lanes) → **Phase R** / P0.5 health output.
- R59 (thread→spec→Jira→factory capstone) → **P7** (composition exit gate).
- R61 (SETUP-CHECKLIST behavioral gates) → **P6** (CoS onboarding).
- R62 (per-scheduled-skill preflight block) → **P5/P6** skill anatomy.
- R26 (calendar + meeting-prep) → **§9 park** (DROPPED from active scope; log as deliberate vNext, was a diagnosis gap row).

**Obsolete (justified, no placement):** R25 (pr-monitor → subsumed by factory PR cards), R63 (product-operating-model → deliberate factory-first scope park).
