# P5 Adversarial Review — Learning Loops + Self-Supervision (the self-modification crown)

**Reviewer role:** ADVERSARIAL (read-only, GOVERNANCE_EXEMPT). **Branch:** `factory` (confirmed).
**Target:** `docs/plans/harness/fable/p5/P5-design.md`.
**Plan gate:** `docs/plans/diagnosis-2026-07-03/hermes-fable-plan.md` §P5 (rows P5-1..5 :422-426; M6 exit :534).
**Scope note:** P5 is **design-only** — no `retro*.py`, `scorecard.py`, `routing_view.py`,
`skill_improve.py`, `pattern_bank.py`, `forensics_store.py`, or `factory/watchdogs/` exist on
disk yet (verified `ls`). This review therefore judges (a) the REUSE claims against real code, and
(b) the DESIGN's stated invariants + RED-test plan for the crown self-modification risk. Attacks on
"running code" are attacks on the design's contract.

---

## Verdict: **SHIP-WITH-FIXES**

The crown reuse substrate is real and hardened — `immutable_ring.check_diff` canonicalizes and
lists every claimed protected path, INCLUDING its own source. The design's decision to route every
retro diff through that identical gate is sound. But the design leaves **one structural hole at the
crown seam** (the owner-approved retro diff's *apply* path is not shown to re-check the ring — only
the PROPOSE-time factory gate is), and **two path-list gaps** (`retro_ring_gate.py` and the
`RING_PATHS` tuple's home are relied on but the design never states they are themselves protected /
covered). None are "the ring is already breakable" — they are "the design must name the apply-path
wall and the gate-source protection before build." Fix them in the P5-b spec and the crown holds.

**Counts:** P0 = 1 · P1 = 3 · P2 = 2.

**Top 3 findings (with exact fix):**
1. **[P0] The owner-approve apply path for a `retro_diff` has no shown ring re-check.** The broker's
   server-side `check_ring` fires ONLY on `auto_merge` for `type=="merge"` cards; the human-tap
   `_rpc_approve` path runs the executor with NO ring re-check. The design's retro diff is applied
   after an owner tap via a NEW (unspecified) executor. **Fix:** the `retro_diff` executor MUST call
   `merge_gate.check_ring` (or `immutable_ring.check_diff`) over the diff immediately before applying
   it — a second wall symmetric to the merge card — and RT-3 must exercise the executor, not only the
   PROPOSE gate.
2. **[P1] `retro_ring_gate.py` is not itself an immutable-ring member.** The crown wall lives in a NEW
   file that a *future retro* (editing skills/prompts, which it is allowed to do) could target if the
   gate logic ever drifts into a "skill." **Fix:** add `factory/retro_ring_gate.py` to `RING_PATHS`
   (one-line, mirrors how `immutable_ring.py` protects its own source at :55) and add a parity RED test.
3. **[P1] Design does not state the gate runs on the diff *the owner will apply*, only the diff
   *proposed*.** If PROPOSE and APPLY can diverge (owner edits, re-render, stale worktree), the gate
   guarantee is TOCTOU-broken. **Fix:** pin the exact diff bytes gate→hold→apply (hash the diff; the
   executor re-checks the same bytes) so the checked artifact and the applied artifact are identical.

**Escalated (crown):** REQ-02 vector V1 (apply-path bypass) is a P0 — a retro can reach a ring path
IF the `retro_diff` executor omits the ring re-check, because the design's only shown wall is at
propose-time and the owner tap does not re-gate. This is fixable in spec, not a fundamental break.

---

## REQ-01 — Reuse verification (each claim: loc | verdict)

| Claim | Location | Verdict |
|---|---|---|
| `immutable_ring.check_diff(diff, *, worktree_root)` exists, raises `RingViolation` | `immutable_ring.py:184` | **CONFIRMED** |
| `RING_PATHS` includes `broker/` | `immutable_ring.py:47` | **CONFIRMED** |
| `RING_PATHS` includes `broker_client.py` | `immutable_ring.py:48` | **CONFIRMED** |
| `RING_PATHS` includes `trust-policy.md` + `docs/factory/trust-policy.md` | `immutable_ring.py:51-52` | **CONFIRMED** |
| `RING_PATHS` includes `docs/factory/never-graduates.md` | `immutable_ring.py:53` | **CONFIRMED** |
| `RING_PATHS` includes `cost_stops.py` | `immutable_ring.py:57` | **CONFIRMED** |
| `RING_PATHS` includes `factory/immutable_ring.py` ITSELF (gate protects own source) | `immutable_ring.py:55` | **CONFIRMED** — the path-LIST's own home is a ring member |
| `check_diff` **canonicalizes** (realpath) so `..`/symlink/rename cannot evade | `immutable_ring.py:91-97` (`_canonical_relative` realpaths both worktree root AND path), `:169-174` (rename from/to), `:229-256` (symlink target, both interpretations) | **CONFIRMED** — hardened; checks old+new, symlink target relative to link-dir AND worktree-root, fails closed on escape |
| `check_diff` fails CLOSED on un-parseable entry | `immutable_ring.py:212-215` | **CONFIRMED** |
| `broker/` dir + policy docs actually exist | `ls broker/` → executors/, server.py, merge_gate.py, approval.py…; `docs/factory/never-graduates.md` (1.8K), `trust-policy.md` (3.4K) present | **CONFIRMED** |
| `never-graduates.md` enumerates the enforcement boundary (broker, guard code, trust policy, self-approve) | `never-graduates.md:11-25` | **CONFIRMED** — explicitly names `broker/**`, `factory/immutable_ring.py`, `injection_scan.py`, `cost_stops.py`, `worker_env.py`, "editing the enforcement boundary," "self-approving a merge" |
| `model_router.admissible_ladder(repo, policy, catalog)` composes `_FIRST_PARTY_LADDER` + probe-confirmed OpenRouter, residency-filtered | `model_router.py:147-174`; orderings at `:50-63`; residency filter `_rung_admissible` `:129-144` (fail-closed on None policy) | **CONFIRMED** — the design's "order within admissible set becomes data-driven, residency wall untouched" is accurate; `select_tier` task_type is informational today (`:190-192`) |
| `forensics.capture_pre_kill` / `capture_post_mortem`; capture-BEFORE-kill ordering; post-mortem never raises | `forensics.py:86` (pre_kill), `:110` (post_mortem, every write try/except-swallowed :126-146), ordering `:9-13` | **CONFIRMED** |
| `scheduler` `throttled_fn` seam admits nothing new when True | `scheduler.py:114` (ctor param), `:234-237` (tick early-returns `[]` when `throttled_fn()` True) | **CONFIRMED** — memory-zone/disk watchdogs compose by passing this callable; no scheduler edit needed |
| `trust_ledger` reserves `reverted` outcome, no live producer until P5.4 | `trust_ledger.py:22-23` (docstring), `:57` `VALID_OUTCOMES` includes `reverted` | **CONFIRMED** |
| `broker_client.enqueue_action` → `{action_id, disposition}` ('held'); `approve(nonce)` | `broker_client.py:93-119` (enqueue), `:106` ('held' doc), `:124-131` (approve+nonce) | **CONFIRMED** — client holds no send/push verb, `auto_merge` carries no nonce |

**REQ-01 result:** every reuse claim CONFIRMED. The crown substrate is real. The design's central
premise — "the retro gate is not new policy, it is routing retro output through an existing hardened
wall" — is TRUE. Nothing UNVERIFIED in the reuse table (I opened each cited line).

**Bonus discovery (strengthens the design, not claimed):** the broker owns an INDEPENDENT ring
re-check `merge_gate.check_ring` with `BROKER_RING_PATHS` (`merge_gate.py:35,152`), asserted by a
parity test to be a SUPERSET of the factory ring (`:14`, "AT-RING-2 asserts BROKER_RING_PATHS ⊇
factory.immutable_ring.RING_PATHS"). This is a genuine second wall — see REQ-02 for why the design
fails to leverage it for retro diffs.

---

## REQ-02 — Self-modification attack table (THE crown)

| # | Vector | Reaches a ring path? | Severity |
|---|---|---|---|
| V1 | **Retro's owner-approved diff is APPLIED without a ring re-check.** Design §4.1 step 4 says the diff "is applied via a human-reviewed apply path" after an owner Telegram tap. But the broker's human-tap `_rpc_approve` (`server.py:322`) runs the executor with **no** `check_ring` — the server-side ring re-check exists ONLY on `_rpc_auto_merge` for `type=="merge"` cards (`server.py:348`, `merge_gate.evaluate:269-275`). The retro is a NEW action type (`retro_diff`) with no executor today. If that executor omits the ring check, the ONLY wall is the factory-side propose-time `check_retro_diff`. | **YES, conditionally** — if the `retro_diff` executor omits the ring re-check, the propose-time gate is the sole wall; a TOCTOU (V4) or a gate bug then reaches a ring path with no backstop. | **P0** |
| V2 | Retro edits the gate SOURCE `factory/immutable_ring.py` (weaken `check_diff`, shrink `RING_PATHS`). | **NO** — `immutable_ring.py` is itself in `RING_PATHS` (`:55`); a diff touching it is rejected by the very gate it tries to weaken. The path-LIST's home is protected. CONFIRMED safe. | — (safe) |
| V3 | Retro edits the retro GATE `factory/retro_ring_gate.py` (the NEW thin wrapper). | **YES potentially** — `retro_ring_gate.py` is NOT in `RING_PATHS` (it does not exist yet; the design never adds it). A retro is *permitted* to edit skill/prompt files; if the gate wrapper ever lives near that surface, a retro diff could target it and the propose-time gate would NOT reject it (it is not a ring path). | **P1** (fix: add to RING_PATHS) |
| V4 | **PROPOSE/APPLY divergence (TOCTOU).** The gate runs on the *proposed* diff (§4.2). The design does not pin that the *applied* bytes are identical. An owner edit, a re-render, or a stale-worktree apply could land a different diff than the one gated. | **YES potentially** — a diff that passes the gate then mutates before apply defeats the guarantee. | **P1** |
| V5 | Rename into `broker/`, symlink onto `trust-policy.md`, `../../broker/x`. | **NO** — inherited from `check_diff` canonicalization (`immutable_ring.py:91-97,169-174,229-256`); the design's RT-4 asserts this. CONFIRMED safe *at the propose gate* (but see V1 — worthless if the apply path is unchecked). | — (safe at gate) |
| V6 | A "skill improvement" or "pattern" file that, once applied, redefines a guard the workers READ (behavior-defining file outside the ring). | **PARTIAL** — skills/prompts are deliberately editable (that IS the retro's job) and are NOT ring paths. A skill that redefines, say, an injection-scan heuristic the workers consult is behavior-changing but not ring-touching. The mitigant is the human owner tap (every skill diff is owner-approved, never silent) — but the design leans entirely on the human catching a subtle guard-redefinition in a skill diff. | **P2** (accepted risk; strengthen owner-card to flag guard-adjacent skill edits) |
| V7 | Gate fail-OPEN on error. | **NO** — `check_diff` fails CLOSED (`:212-215`); the design §4.2 says an un-parseable/empty diff is rejected (RT-5) and a `RingViolation` produces zero held actions. Design intent is fail-closed. CONFIRMED (design-level). | — (safe) |
| V8 | Partial-apply evasion (strip the ring hunk, enqueue the rest). | **NO** — design §4.2 explicitly rejects the WHOLE diff if any hunk is ring-touching ("does not strip the bad hunk and enqueue the rest"). Good. | — (safe) |
| V9 | Trust-graduation via policy-doc rewrite. | **NO** — design §4.2: a graduation rides as a data-driven tier bump referencing `compute_tier` inputs; the retro CANNOT edit `trust-policy.md`/`never-graduates.md` (ring). `compute_tier`'s three hard gates remain the arbiter. CONFIRMED safe. | — (safe) |

**REQ-02 escalation (crown):** **V1 is P0.** The design's single load-bearing sentence ("every
retro diff passes `check_retro_diff` BEFORE it can become a held action") protects the *propose*
step but says nothing binding about the *apply* step. Because the broker only re-checks the ring for
merge cards on the auto-merge RPC — not on human-tap approval, and not for a `retro_diff` type — the
retro's apply path has no shown second wall. A single gate (propose-time) guarding self-modification
is exactly the "one wall" the whole P5 design philosophy warns against everywhere else (belt+wall,
two-layer). **The fix is cheap and must be in the P5-b spec:** the `retro_diff` (and `skill_diff`)
executor re-checks the ring over the exact-bytes diff immediately before applying, mirroring
`merge_gate.evaluate` step 1. With that, V1, V3, and V4 all collapse to "rejected at apply."

---

## REQ-03 — Watchdog + scorecard attack table

| # | Vector | Enforcement or theater? | Severity |
|---|---|---|---|
| W1 | Drift watchdog only LOGS, doesn't pause. | **Design = enforcement.** §5.1: transitions the job to `PAUSED_DRIFT` AND SIGTERMs the worker pgid after `capture_pre_kill`. WD-1 asserts the job lands in `PAUSED_DRIFT` + `DRIFT` alert + pre-kill ran. This is real pause, not advisory — CONTINGENT on build matching design. | P2 (verify at build) |
| W2 | Regression sentinel is advisory (doesn't open a revert / pause branched tasks). | **Design = enforcement.** §5.2: (a) enqueues a revert HELD action via broker, (b) pauses every QUEUED/ADMITTED job whose base resolves to the suspect main sha, (c) writes a `reverted` trust outcome (auto-revokes tier via `compute_tier` streak). WD-3 asserts all three. Real — but note the revert itself is a HELD owner-approved card (correct: `never-graduates.md:11` forbids auto-push to protected branch), so the *revert lands* only on owner tap. The PAUSE of branched tasks is immediate (plain code) — that bounds the blast window without waiting for the human. Acceptable. | P2 (verify at build) |
| W3 | Who watches the last watchdog? | **Design addresses it, with a fail-safe floor.** §5.4: watchdog-supervisor respawns dead watchdogs and writes its own heartbeat; if IT dies, the `job_store` supervisor-lock stale-steal (15-min TTL, `job_store.py:538` per design) recovers the tick loop; and if the supervisor cannot confirm drift/sentinel alive it **throttles new spawns** (fail-safe, not fail-open). WD-5 asserts throttle-on-unconfirmed. Reasonable — the regress is bounded (a dead top supervisor stops the fleet, it does not run it blind). | P2 |
| W4 | **Scorecard poisoning — a worker influences its own scorecard sample to get itself routed more / promote a cheaper-worse model.** | **Structurally hard, one gap.** The sole writer is the SUPERVISOR (design §3.2 "the SUPERVISOR is sole writer", mirrors `trust_ledger.record_outcome_on_conn` co-transactional pattern). A worker cannot write its own scorecard row — it only produces a diff + test result, which the supervisor scores. `task_type` is derived by `trust_ledger.derive_task_type` (plain keyword map), not worker free-text. `outcome`/`cost`/`findings_count` come from the gauntlet + cost meter, not the worker. **Gap:** `findings_count` is "parsed from `review_findings_path`" — if a worker can influence the review artifact (it authored the code the reviewer reviews, not the review), that is indirect at most. No direct poison path. | P1 (pin: scorecard inputs must ALL derive from supervisor-observed facts, never worker-emitted metadata; add a RED test that a worker-authored `test_result`/`findings` string cannot move its own sample) |
| W5 | **Routing view ADDS a residency-violating model (phantom / OpenRouter entry bypass).** | **Blocked by design + inherited P4 guard.** §3.3 anti-regression: `routing_view` NEVER adds a model absent from `catalog`; it ORDERS the catalog-confirmed set, never EXTENDS it. SC-4 asserts a scorecard row for an absent model does not enter any ladder. The residency filter (`_rung_admissible`, `model_router.py:129-144`) is UNCHANGED — a `allow_openrouter:false` repo gets zero OR rungs after reordering (SC-5). CONFIRMED the design preserves the P4 crown residency wall; only *order within the admissible set* is data-driven. | — (safe by design) |
| W6 | Cold-start scorecard flip-flop / crash. | §3.3 + SC-3: `n < MIN_SAMPLES` falls back to the hand-written order (the default IS the prior); deterministic, no crash. Reasonable. | P2 |
| W7 | Memory-zone throttle doesn't actually stop admission. | §5.3 + WD-4: `throttled()` True wires into the real `scheduler.throttled_fn` seam (CONFIRMED `:234-237` returns `[]`). Real enforcement — `tick()` admits nothing. | — (safe) |

**REQ-03 escalation:** none rise to P0. W4 (scorecard poison) is P1 — the sole-writer design blocks
the direct path, but the design should explicitly forbid ANY worker-emitted value from entering a
scorecard sample and test it. W5 (the residency-bypass-via-routing-view, the most dangerous) is
CONFIRMED blocked: the view orders, never extends, and the residency filter is untouched.

---

## REQ-04 — RED test plan verdict (real enforcement vs theater)

| Test | What it must prove | Real or theater? |
|---|---|---|
| **RT-3 (CROWN ring-rejection)** | A retro diff touching `cost_stops.py` / `broker/x.py` / `trust-policy.md` / `never-graduates.md` is REJECTED by `check_retro_diff` and produces ZERO held actions; deleting the `immutable_ring.check_diff` call makes it leak. | **REAL at the propose gate** — `check_diff` is real hardened code (verified), so a test that calls the true `check_retro_diff` against a ring-touching diff will genuinely reject, and removing the call genuinely leaks. **BUT INCOMPLETE (see P0/V1):** RT-3 as written tests the PROPOSE gate only. It does NOT test that the APPLY path (the `retro_diff` executor) re-checks. **A crown test must also assert: an owner-approved retro diff whose bytes touch a ring path is rejected at APPLY, not merged.** As written, RT-3 is real-but-half — it proves the front door is locked while the design leaves the back door (apply path) unspecified. Upgrade RT-3 to parametrize {propose-gate reject, apply-executor reject}. |
| **RT-1 (retro never silent)** | Retro emits diff+rationale → `enqueue_action` held action; NEVER writes the diff to disk directly (spy the apply path — zero direct writes). | **REAL / falsifiable** — a spy on the disk-write path with an asserted count of 0 is a genuine falsifiable check; if the retro ever `open(...).write`s the diff, the spy catches it. Good. Strengthen: also assert `enqueue_action` was called exactly once with `type='retro_diff'` (ties silent-editing to the held-action contract). |
| **RT-4 (evasion inheritance)** | rename-into-`broker/`, symlink-onto-`trust-policy.md`, `../../broker/x` rejected. | **REAL** — delegates to `check_diff`'s verified canonicalization (`:229-256` symlink both-interpretations, `:169-174` rename, `:91-97` realpath). These are the exact evasions `immutable_ring.py`'s own tests already cover; RT-4 re-asserts through the retro wrapper. Genuine. |
| **RT-5 (fail-closed)** | un-parseable/empty proposed diff → rejected, no held action. | **REAL** — inherited from `check_diff:212-215` fail-closed. Falsifiable. |
| **RT-6 (gate BEFORE enqueue)** | a `RingViolation` means `enqueue_action` was never called (ordering spy). | **REAL** — a spy asserting `enqueue_action.call_count == 0` on a ring diff is genuine ordering enforcement; mirrors forensics' capture-before-kill ordering test. Good. |
| **WD-1 (drift pause)** | synthetically-churning worker → `PAUSED_DRIFT` + `DRIFT` alert; `capture_pre_kill` ran before SIGTERM. | **REAL enforcement (design-level)** — asserts a state transition + alert + ordering, not a log line. Injectable clock/store make it fixture-runnable. Genuine, contingent on build. |
| **WD-3 (sentinel revert)** | injected post-merge break → revert held-action enqueued AND branched tasks paused AND `reverted` trust outcome written. | **REAL enforcement** — three concrete assertions (enqueue happened, jobs transitioned, ledger row written), not "logged a warning." The `reverted` outcome producer is exactly what `trust_ledger.py:22-23` reserved. Genuine. |
| **SC-2 (routing changes from data)** | seed scorecard so `bugfix` favors a cheaper confirmed model; assert `task_type_table()['bugfix'][0]` differs from hand-written `_FIRST_PARTY_LADDER[0]` AND `select_tier` returns it while clearing residency. | **REAL** — asserts a behavioral difference (order changed) tied to a residency invariant (false-policy repo still zero OR rungs). Falsifiable and it exercises the actual `admissible_ladder` seam. Good. |
| **SC-4 (view orders, never extends)** | scorecard row for a catalog-absent model does NOT enter any ladder; removing the catalog-intersection leaks. | **REAL** — the F12 phantom-guard anti-regression, falsifiable by construction. Critical and genuine. |

**REQ-04 escalation:** the crown test **RT-3 is real but INCOMPLETE, not theater** — it genuinely
exercises the hardened `check_diff` at propose-time (deleting the call leaks), which is the bar for
"not theater." It does NOT rise to a BLOCK. But it must be UPGRADED to also cover the apply-path
(P0/V1): a crown that tests only the front door while the design leaves the apply door unspecified is
a real gap in coverage even though every assertion it makes is real.

---

## Findings summary (severity-ordered)

- **P0-1 [V1] — Retro apply path has no shown ring re-check.** The broker re-checks the ring only for
  `type=="merge"` auto-merge (`server.py:348`, `merge_gate.evaluate:269-275`); human-tap approve
  (`_rpc_approve:322`) and the (not-yet-existing) `retro_diff` executor do not. **Fix:** P5-b spec must
  require the `retro_diff`/`skill_diff` executor to call `check_ring`/`check_diff` over the exact-bytes
  diff immediately before applying; RT-3 must exercise the executor, not only the propose gate.
- **P1-1 [V3] — `retro_ring_gate.py` not in `RING_PATHS`.** The new crown-wall file is unprotected.
  **Fix:** add `factory/retro_ring_gate.py` to `RING_PATHS` (one line, mirrors `:55`), add a parity RED
  test. (Optional belt: add it to `BROKER_RING_PATHS` too, keeping AT-RING-2's superset invariant.)
- **P1-2 [V4] — PROPOSE/APPLY diff divergence (TOCTOU) unaddressed.** **Fix:** hash the gated diff
  bytes; the executor re-checks the identical bytes; reject on mismatch.
- **P1-3 [W4] — Scorecard inputs not explicitly worker-isolated.** Sole-writer=supervisor blocks the
  direct path, but the design should state that NO worker-emitted value (test_result string,
  findings text) enters a sample, and test it (a worker cannot move its own sample).
- **P2-1 [V6] — Guard-redefining skill edits rely solely on the human tap.** A skill/prompt edit that
  subtly redefines a worker-read heuristic is owner-approved but not ring-blocked. **Fix (nice-to-have):**
  the owner card flags skill diffs that touch guard-adjacent skills for extra scrutiny.
- **P2-2 [W1/W3/W6] — Watchdog enforcement is design-correct but build-contingent.** Verify at build
  that drift actually transitions `PAUSED_DRIFT` (not logs), the watchdog-supervisor throttles on
  unconfirmed (fail-safe), and cold-start falls back deterministically.

## What the design got RIGHT (crown-relevant, so it is on record)

- Reuses the identical hardened `check_diff` rather than re-implementing — the single best decision.
- The gate protects its OWN source (`immutable_ring.py:55` in `RING_PATHS`), so the path-list cannot be
  shrunk by a worker OR (per V2) a retro.
- Whole-diff rejection (no partial-apply strip) closes the obvious evasion (§4.2 / V8).
- Routing view ORDERS, never EXTENDS — the P4 residency crown survives verbatim (SC-4/SC-5 / W5).
- Watchdog fail-safe is fail-CLOSED (throttle on unconfirmed), not fail-open.
- Revert-to-protected-branch is a HELD owner card, never auto-push (honors `never-graduates.md:11`).
- STAGED entry for the two-week cost-trend gate is honest — the mechanism is gated at build, the
  outcome is deferred to real fleet data (§7.3), not faked.
