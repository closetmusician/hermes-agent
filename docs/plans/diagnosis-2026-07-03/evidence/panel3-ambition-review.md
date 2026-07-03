# ABOUTME: Panel 3 (Ambition & Completeness) adversarial review of hermes-fable-plan.md v2 factory-first.
# ABOUTME: Cross-examines the plan against 10x-ambition-critique (G1-G15), didnt-know-you-wanted (8 SPINE), factory-sota-2026 (12 findings); judges 10X-ness, gate bindingness, sequencing, delight, self-improvement contract.

# Panel 3 — Ambition & Completeness Review

**Date:** 2026-07-03
**Reviewer:** Ambition & Completeness panel (fresh eyes; trusts only what is read)
**Target:** `docs/plans/diagnosis-2026-07-03/hermes-fable-plan.md` (v2, factory-first, 360 lines)
**Method:** Line-by-line trace of every named gap/SPINE/SOTA finding into the plan; adjudication of the synthesis agent's own confessed tensions; judgment of the vision, the gates, and the delight axis.

---

## A. Gap traceability (10x-ambition-critique G1-G15)

Every one of the 15 named gaps is at least *referenced*. None is silently dropped — a real improvement over v1. But two are addressed in name only.

| Gap | Where | Verdict |
|---|---|---|
| G1 decomposition engine | §2 note, P3.1, appendix | Woven, deliverable-backed |
| G2 multi-provider router | L2, §2 router, P4.2, `model-routing.md` | Woven |
| G3 trust ledger | L1, P1.2, §6 | Woven, keystone |
| G4 worker abstraction | L4, P1.4 | Woven (stub proves seam) |
| G5 fleet DAG scheduler | §2, P3.2 | Woven |
| G6 checkpoint/resume + 429 | P4.1, P4.2 | Woven |
| **G7 eval harness + per-job confidence SCORE** | §2.3 ("confidence's floor"); P4.4 "risk score"; §9 parks worker-eval #13 | **THIN — see F1** |
| G8 outcome learning loop | P5.1, P5.3 | Woven |
| G9 four intake pipelines | P3.3-3.5 | Woven |
| G10 self-supervision | P5.4, §4 | Woven |
| G11 repo onboarding | P3.4 | Woven |
| G12 failure forensics | P5.5, P4.1 | Woven |
| G13 morning replay surface | P4.4 | Woven |
| G14 compute inventory | P0.3, §2 | Woven |
| G15 spec quality gate | P3.1 | Woven |

## B. SPINE traceability (didnt-know-you-wanted, 8 items)

All 8 SPINE items mapped in Appendix A intro (#20→P3, #21→P6, #3→P3, #1→P4, #11→P5, #14→P1, #6+#7→P4). Each lands in a phase with a deliverable. Verified real, not hand-waved — **except #7 (one-tap approval)** which is co-located with #6 in P4.4 but the *card as an interactive Telegram surface* is really built in P1.3 (generic held-action approval) and P2.4 (approval card). That's fine — earlier is better — but see F5 on delight timing.

## C. SOTA load-bearing findings (factory-sota-2026)

429 ladder ✓ (P4.2, §4). Fresh-agent-per-phase ✓ (P4.1, §2). Plain-code supervisor ✓ (L, §2, §9). Memory throttling / GREEN-YELLOW-RED ✓ (P5.4, §4). Watchdog-of-watchdog ✓ (P5.4, §4). SkillOpt ✓ (P5.2). Decision-ready escalation package ✓ (P4.4, §2, cites §4.4 35-45%). Async-first approval ✓ (P1.3 "durable, survives restart"). All load-bearing findings present.

---

## FINDINGS

### F1 — [P1] Per-job confidence SCORE (G7) is asserted but never defined as a deliverable
§2.3 / P4.4 / §9. The critique's G7 was two things: (1) a verification gauntlet — DONE — and (2) a **per-job confidence score** computed from test-coverage delta + review-severity + historical repo pass-rate, feeding the trust ledger and the ranked morning digest. The plan surfaces a "risk score" (P4.4, from code-review-graph impact analysis) and a "ranked-by-confidence digest" (P4.3, #10) but **never defines what the confidence number is or where it is computed**. The ranked digest cannot rank without it; the trust ledger's graduation logic (P1.2 "N clean") has no confidence input. This is a silent downgrade of a P1 gap to a UI label.
**Fix:** Add a deliverable in P4 (e.g. 4.6) "Per-job confidence score": define the inputs (test pass/coverage-delta, review-finding severity count, repo historical merge-clean rate from the scorecard, blast-radius from the living map), the formula's shape, and where it is persisted (job ledger column). Make the P4.3 ranked digest and the P4 exit gate reference *it* explicitly ("digest ranked by the computed confidence score"), not a vibes ordering.

### F2 — [P1] P1 exit gate proves graduation "after a clean track record" but never states the threshold N
P1.2 / P1 gate (L156) / OQ2. The gate says a repo×task-type "demonstrably graduates from held→auto after a clean track record." But the *rule* — how many clean merges, over what window, at what confidence floor — is undefined, and there is no OQ asking the owner for it. "After a clean track record" is exactly the kind of unfalsifiable phrasing the panel is told to flag: an implementer cannot build the graduation check without inventing the threshold, and inventing autonomy thresholds is a safety-relevant guess.
**Fix:** Either pin a concrete default in P1.2 (e.g. "≥N=10 merged-clean, 0 reverts, over ≥14 days, confidence ≥ threshold → propose tier-1") OR add OQ8 forcing the owner to set N, window, and the confidence floor before P1 build. The gate then reads "graduates after the *configured* threshold is met," which is testable.

### F3 — [P1] Time-to-first-magic is ~21-29 days away; no earlier owner-visible win is designed
Sequencing (P0 3-4d + P1 8-11d + P2 10-14d = 21-29d before "one real feature merged with a single approval," M3). The owner's stated dopamine is *waking up to a built PR*. Under this plan the first PR the owner approves from their phone is a month out, because P1 (broker+trust+worker-abstraction, 8-11d) sits entirely before the first worker runs. The synthesis agent flagged this tension (3b) but did not resolve it. P1's full trust-ledger graduation machinery is NOT required to merge the *first* human-approved PR — only the broker + a hold-for-approval surface are. Graduation/demotion can follow P2.
**Fix:** Split P1: land 1.1 (broker) + 1.3 (held-action approval) + 1.4 (worker abstraction) as "P1a" (~5-6d), then run P2's one-worker end-to-end (first magic ~day 14-18), then land 1.2 (trust ledger + graduation) as "P1b" folded into P2's tail — trust graduation *needs* P2's merge outcomes to have anything to graduate on anyway (this also resolves the agent's confessed chicken-egg tension 3c). Re-order M2/M3 accordingly. State the target explicitly: "first owner-approved overnight-built PR by ~day 16."

### F4 — [P1] The P4 flagship gate ("3 consecutive nights, ≥5 tasks, zero human before 7am") has no defined starting queue or repo set, making it non-reproducible
P4 gate (L198) / OQ5. The single most important gate in the plan is unfalsifiable as written: "≥5 tasks go intake→PR-ready" — from *what* queue, in *which* repos, of *what* difficulty class? A cherry-picked five trivial docstring jobs would pass; five real features would not. OQ5 asks which repos are the proving ground but nothing binds the gate to a *representative* task mix. Demo-ware passes exactly this kind of gate (factory-sota §7: "single-task demos with cherry-picked specs").
**Fix:** Bind the gate to a named, committed seed queue: "≥5 tasks drawn from a committed `p4-gate-queue.md` containing ≥2 feature-class and ≥1 refactor-class jobs across ≥2 repos, none authored to be trivially green." Add the confidence-score tolerance and the cost ceiling number (currently "under the nightly ceiling" — the ceiling itself is OQ3, so the gate is doubly undefined until OQ3 answers).

### F5 — [P2] Delight is correctly front-loaded on approval but back-loads the two highest-wow moments; verify this is intentional
P4.4 (briefing + one-tap cards land at ~day 45-60), P6.2 (voice-note, wow 10), P7.2 (demo reel, wow 10). The morning experience (briefing, one-tap, question queue) lands at P4 — reasonable, it's gated on there being overnight work to report. But the two capabilities the ideation doc scored **wow 10** — voice-note→spec (#21) and demo-reel (#8) — are in P6 and P7, i.e. the very end. The owner "feels the vision" earliest through delight, and the single most magical-feeling item (#21, per the source doc) is ~60+ days out.
**Fix:** Not a blocker — voice-note genuinely depends on the decomposition engine (P3) and demo-reel on a runnable-surface pipeline. But pull voice-note→spec forward to a P4 stretch deliverable (its only hard dep, decomposition, ships in P3) so one wow-10 moment lands with the first overnight batch, not two phases later. Note it explicitly as a "delight pull-forward" so it isn't lost.

### F6 — [P2] Self-improvement contract (§6) is real, but the "auto-tune within bounds, no diff" ring lacks an audit trail requirement
§6 middle ring. The contract is genuinely good — three concentric rings, immutable core, proposal→diff→owner-approval for skills. Not ceremony. One hole: the middle ring ("may auto-tune within bounds, no diff") lets routing weights and backpressure thresholds change silently with no owner-visible record. That is the ring most likely to drift unnoticed and the hardest to debug when a night goes wrong ("why did it route everything to DeepSeek at 3am?").
**Fix:** Add to the middle-ring cell: "every auto-tune writes an entry to a `tuning-log.md` surfaced in the weekly retro (§P7.3)." Cheap, closes the observability gap the arxiv paper (§2 finding #1) names as the top open problem, and makes the ring falsifiable.

---

## Judgments requested

**1. Actually 10X or v1 relabeled?** Genuinely 10X. §0 states the product as the factory, demotes CoS to control plane, and the inverted spine (factory as P0-P4, CoS as P6) is a real structural rebuild, not a rename. A skeptical owner reading §0 + the north-star metric table + the four locks would feel the vision. This is the plan for the stated product.

**2. Are the gates binding?** Mostly yes — fresh-context verification is applied throughout and most gates are concrete (24h uptime, bypass-impossible, cost stops fire, fan-out≥2/serialize≥1). Three are vibes as written: P1 graduation threshold (F2), P4 queue composition (F4), and the confidence-score-dependent ranked digest (F1). Fix those three and every gate is falsifiable.

**3. Confessed tensions:** (a) 360 lines — most phases are implementer-ready; the thinnest is **P4.4 confidence/risk scoring** (F1) and **P3.1's ambiguity interceptor grading rubric** (says "grades spec for R7/R17 failure classes" but not the pass/park threshold — a minor version of F2). (b) Effort realism — sequencing is sound EXCEPT time-to-first-magic (F3); the fix is the P1a/P1b split. (c) Chicken-egg P1↔P2 — real, and the F3 split resolves it: trust graduation cannot be built before P2 produces merge outcomes, so it belongs in P1b after P2.

**4. Delight axis:** Lands adequately (approval at P4) but backloads both wow-10 items (F5).

**5. Self-improvement contract:** Real, not ceremony — specific artifacts, specific approval flow, immutable core. One audit-trail hole (F6).

---

## VERDICT

**REVISE (6 findings: 4×P1, 2×P2).** No P0 blockers — the architecture is committed to the right product and every locked decision is honored. The P1 findings are precision failures on otherwise-sound structure: two undefined thresholds (F2, F4) and one silently-downgraded deliverable (F1) turn three flagship gates from falsifiable to vibes, and the sequencing (F3) delays first-magic by ~2 weeks with no compensating early win. All four are fixable with added text/deliverables, not rearchitecture. Fix F1-F4 to make the gates binding and the first overnight win land by ~day 16; F5-F6 are polish.
