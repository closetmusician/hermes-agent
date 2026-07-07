# P1b Design Review — Adversarial (Trust Ledger + Graduation / Auto-Merge)

<!-- ABOUTME: Adversarial review of P1b-design.md — the earned-trust auto-merge boundary. -->
<!-- ABOUTME: P1b lets the factory auto-merge WITHOUT a human; this is the crown-jewel risk. -->
<!-- ABOUTME: Read-only audit of the design against the real `factory` branch code seams. -->
<!-- ABOUTME: Verdict + severity-scored attack tables for REQ-01..04. No code changed. -->
<!-- ABOUTME: Source: P1b-design.md + hermes-fable-plan.md §P1b; branch `factory`. -->

**Reviewer role:** ADVERSARIAL (investigate-audit, GOVERNANCE_EXEMPT, read-only).
**Branch:** `factory` (verified `git rev-parse --abbrev-ref HEAD` = `factory`).
**Design under review:** `docs/plans/harness/fable/p1b/P1b-design.md`.
**Verdict:** **SHIP-WITH-FIXES** — the design is sound in shape and its reuse claims are almost all CONFIRMED, but its single highest-risk claim (§4.4: "the broker re-checks the ring / never-graduates gates **server-side** at auto-approval") describes a server-side re-check **that does not exist in the code today and is not specified as new work**. That is a P0 that must be closed before P1b-d builds, because it is the exact hole the whole project exists to close.

- **P0:** 2 · **P1:** 3 · **P2:** 2

**Top-3 findings (exact fix):**
1. **P0 — no server-side ring / never-graduates re-check on the auto-merge path.** The broker's `enqueue_action` auto_sent branch (`broker/server.py:277-286`) executes the merge executor with **zero ring or never-graduates check** — `check_diff` lives only in `factory/gauntlet.py`, worker-side. Design §4.4(b)(c) asserts a server-side re-check that is absent. **Fix:** P1b-d must add, inside the broker (`server.py` auto_sent branch, or `merge_executor`), a `check_diff` + never-graduates + capability re-verification that fail-closes; make it a named RED test (see REQ-04). Do not rely on the worker-side gauntlet gate — that is not the broker.
2. **P0 — the P2 `Supervisor` holds a live in-process `ApprovalAuthority` (with the real `broker_secret`), so it can self-mint + self-approve.** `test_supervisor.py:144,162` construct `ApprovalAuthority(held, broker_secret=...)` and pass it straight into `Supervisor`; `supervisor.py:446` calls `self._authority.approve(...)` in-process. Any code holding `self._authority` can also call `.mint_nonce()`. The self-approval wall only holds when the supervisor talks to the broker **over the socket** (`broker_client.approve`, no mint verb). **Fix:** P1b-d's auto-merge path must route through `BrokerClient`/the socket (which exposes no mint — `_CLIENT_METHODS`, `server.py:28-36`), NOT through an in-process authority; and the design must state that the supervisor process must never be constructed with a real `ApprovalAuthority`. This wiring risk is un-addressed in the design.
3. **P1 — auto-merge as a `held→auto_sent` disposition can slip past the 5-condition SafeLane's own guards.** The design widens `allowed_origins` to pass C4, but a `merge` action still runs C2 (recipient allow-list) and C3 (strategic markers / 2000-char cap) — none of which are the trust gate. Either the merge auto-sends for the wrong reason, or these conditions block a legitimately-graduated merge. **Fix:** the design must specify exactly which SafeLane conditions a `merge` action is evaluated against and prove the tier gate — not C2/C3 — is the binding one.

**Escalations to owner:** REQ-02 fails as written (server-side re-check absent + in-process self-mint capability). Both are P0. The design is buildable **only if** P1b-d is scoped to (a) route auto-merge through the broker socket and (b) add the server-side ring/never-graduates re-check inside the broker. As written, §4.4 is aspirational, not grounded in the cited seams.

---

## REQ-01 — Reuse-claim verification (claim | loc | verdict)

Opened every cited seam. Line numbers in the design are mostly off by 1–5 (files evolved) but the **structures** are real.

| Claim | Design loc | Actual | Verdict |
|---|---|---|---|
| `RING_PATHS` already protects `trust-policy.md` | immutable_ring.py:46-53 / :45-59 | `RING_PATHS` at **:45-60**; contains `"trust-policy.md"`, `"docs/factory/trust-policy.md"`, `"docs/factory/never-graduates.md"` | **CONFIRMED** — trust-policy.md **IS** in the ring (both variants) + never-graduates.md. Escalation trigger ("trust-policy.md NOT in ring") does **not** fire. |
| `_MERGE_SAFE_LANE` constant, empty `allowed_origins` ⇒ every merge held | supervisor.py:55-57, used :418 | Const at **:57**; `json.dumps({"disposition":"held","allowed_origins":[]})`; used in `_enqueue_merge_card` `self._held.enqueue(... safe_lane_json=_MERGE_SAFE_LANE ...)` at **:413** | **CONFIRMED** — the retrofit seam is real and is a genuine constant→function swap. |
| `_enqueue_merge_card` is the merge-gate seam | supervisor.py:383-423 / :418 | `_enqueue_merge_card` at **:383**; enqueue call at **:413** | **CONFIRMED** (line drift only). |
| Nonce mint/burn in `ApprovalAuthority` | approval.py:78, :100-118 | `approve` at **:78**; validate-first + burn-before-execute at **:97-101**; mint at **:48-62** (HMAC over action_id∥salt with broker_secret) | **CONFIRMED** — nonce wall is real, constant-time compare, burn-before-execute, idempotent by action_id. |
| `SafeLane` C4 = origin authority; widening `allowed_origins` is the lever | safe_lane.py:37-109, :96-100 | C4 at **:96-100** (`origin in self._allowed_origins`); docstring "Widening is a P1b policy change" at **:43-45** | **CONFIRMED**. |
| `jobs` table + `trust_tier_at_spawn` reserved | job_store.py:95-138, :115 | schema at **:93-125**; `trust_tier_at_spawn INTEGER -- NULLABLE; reserved for P1b` at **:115** | **CONFIRMED**. |
| `transition(..., extra=)` co-transactional hook | job_store.py:234-280 / :265-268 | `transition` at **:234**; `extra` dict appended as `SET col=?` in the same UPDATE+commit at **:265-282** | **CONFIRMED** — a co-located `trust_ledger` insert on the same connection is feasible (though the design's `record_terminal_outcome` is a *new* method, not shown). |
| `daily_budget` co-lives in jobs DB (precedent for a 2nd table) | job_store.py:133-137 | `daily_budget` table in `_SCHEMA` at **:127-132** | **CONFIRMED**. |
| `HeldStore.enqueue(safe_lane_json=...)` unchanged | held_store.py:531-566 | `enqueue` at **:76-115** (design line nums wrong; file is 7.4K, no :531) | **CONFIRMED (structure)**; the **:531-566** citation is WRONG (file is far shorter) — cosmetic. |
| `check_diff` / `fs_scope_excludes` ring enforcement | immutable_ring.py:184-256, :259-271 | `check_diff` + fs-scope present in immutable_ring.py; **only referenced from `factory/gauntlet.py`** (worker-side submission gate), never from `broker/` | **CONFIRMED it exists** — but see REQ-02 P0: it is **not** on the broker auto-merge path. |
| `never-graduates.md` exists, ring member, lists "Self-approving a merge" | file exists | Exists (26 lines); line 24: "Self-approving a merge. The assistant / supervisor holds no approval nonce" | **CONFIRMED**. |
| `merge-policy.md` + `MergePolicy` loader as repo-class home | merge_policy.py:61-177 | file present (7.3K); doc `docs/factory/merge-policy.md` present | **UNVERIFIED (not opened in full)** — existence confirmed; the `trust:` block is new work, not a reuse claim. |

**REQ-01 verdict:** reuse claims are **CONFIRMED** with only line-number drift and one wrong citation (held_store :531). The positive escalation-blocker — "is trust-policy.md already in the ring?" — resolves **YES**. No REQ-01 escalation fires.

---

## REQ-02 — Auto-merge authority attack (THE crown-jewel risk)

The design's own flagged claim (§4.4): tier-1 auto-merge must be a **broker** auto-approval that (a) the supervisor cannot forge, (b) re-checks the immutable ring **server-side**, (c) re-verifies never-graduates **server-side**. I attacked each.

| # | Attack | Finding (grounded in code) | Severity |
|---|---|---|---|
| A1 | **Supervisor self-mints a nonce and self-approves.** | In P2's real wiring the `Supervisor` is handed a **live in-process `ApprovalAuthority`** built with the real `broker_secret` (`test_supervisor.py:144,162`); `supervisor.py:446` calls `self._authority.approve(...)` in-process. That same object exposes `mint_nonce()` (`approval.py:48`). **Any holder of `self._authority` can mint + approve.** The self-approval wall is only real across the **socket** boundary (`broker_client` exposes no mint; `_CLIENT_METHODS` = health/enqueue/list/approve/reject/resolve_key, `server.py:28-36`; `mint_approval_nonce` is explicitly NOT an RPC, `server.py:341-350`). The P1b design assumes a broker-side auto-approver but the P2 code it inherits gives the supervisor an in-process authority. **If P1b-d reuses that in-process object for auto-merge, the supervisor can self-approve — reopening the exact hole.** | **P0** |
| A2 | **Server-side ring re-check is absent on the auto-merge path.** | `check_diff` is only ever called from `factory/gauntlet.py` (worker submission gate). The broker's `auto_sent` execution branch (`server.py:277-286`) runs `executor(row)` with **no ring check, no never-graduates check, no capability check**. Design §4.4(b)(c) claims the broker "re-checks the ring/never-graduates gates server-side" — **that code does not exist and the design does not schedule building it.** A merge that somehow reaches `auto_sent` (e.g. a mislabeled capability, a widened origin) would execute against `main` with no server-side ring wall. | **P0** |
| A3 | **Tier stored where a worker/supervisor can write it.** | `trust_tier_at_spawn` lives on the `jobs` row, written only by the supervisor (sole writer, `enqueue_from_intake`). A **worker cannot write it** (worker returns only `{status,branch,summary,test_result}` — worker_runner). Good. BUT the *tier is computed by the supervisor* and the supervisor also drives the merge; there is no independent broker-side recomputation of tier at approval time. The broker trusts the disposition/origin it is handed. Combined with A2, a supervisor bug or compromise can hand the broker an `auto_sent` merge the broker will not second-guess. | **P1** |
| A4 | **SafeLane evaluates a `merge` on the wrong conditions.** | To auto-send, `merge` must pass all 5 SafeLane conditions, not just C4. C2 (recipient on allow-list) and C3 (no strategic marker, payload ≤ 2000 chars) are unrelated to trust; a large merge card payload (>2000 chars of findings/diffstat) fails C3 and holds *for the wrong reason*, and `merge` is **not** in `irreversible_types` (only `git_push_force` is, `server.py:57`) so C5 passes for a merge-to-main — i.e. an irreversible protected-branch merge is treated as "recoverable." The design never maps `merge` onto the 5 conditions; it only says "widen allowed_origins." | **P1** |
| A5 | **Mark a job tier-1 to skip the human via mislabel.** | Tier is computed by `compute_tier` with hard-gate early returns (work→0, never-graduates→0, unknown-type→0). The design's gates are sound *as pure logic*. But because the broker does not recompute (A2/A3), the hard gates only bind if `compute_tier` is actually consulted before `_tier_safe_lane_json`. A supervisor code path that emits `auto_sent` without calling `compute_tier` is not caught anywhere server-side. | **P1 (subsumed by A2/A3)** |

**REQ-02 verdict: FAILS as written.** Escalation fires: "ring/never-graduates isn't re-checked server-side at auto-merge" (A2, P0) and "the supervisor could self-mint the nonce" (A1, P0). The design's §4.4 is the right *intent* but is not grounded in the cited seams — the server-side re-check is imaginary and the inherited wiring hands the supervisor a mint-capable authority. **These must be closed in P1b-d scope before build.**

**Exact fixes:**
- Route the auto-merge through `BrokerClient` over the socket (no mint verb reachable); forbid constructing the production Supervisor with a real in-process `ApprovalAuthority`.
- Add a broker-side gate on the `merge` auto_sent branch (`server.py`): re-run `immutable_ring.check_diff` against the merge diff, re-check the capability against `never-graduates.md`, and recompute/validate the tier server-side; fail-closed to `held` on any miss.
- Add `merge` (to a protected branch) to `irreversible_types` OR give `merge` a bespoke evaluation that does not lean on C2/C3/C5 semantics meant for messages.

---

## REQ-03 — Graduation / demotion integrity attack

| # | Attack | Finding | Severity |
|---|---|---|---|
| B1 | **Worker poisons its own outcome record.** | Outcome is recorded by the **supervisor** at the terminal transition (`approve_merge` merged→DONE, or `_park`→rejected), from trusted supervisor-side state — NOT from worker self-report. The worker's JSON is only `{status,branch,summary,test_result}`; `merged_clean` vs `merged_with_fix` is decided at approval time by the supervisor. **A worker cannot write the ledger.** Good — but note `merged_with_fix` detection ("did a human edit the diff") is not shown to exist anywhere in the current code; the design assumes a fact source that is UNVERIFIED. | **P1** (detection source unproven) |
| B2 | **Game the 10-count with 10 trivial no-op jobs.** | `compute_tier` counts any `merged_clean` regardless of substance. Ten no-op/whitespace merges to a personal repo over 30 days **would** graduate that repo×task_type. The design has no "non-trivial change" floor. The 30-day wall-clock is the only friction; task-type is owner-labeled so 10 trivial `docs` merges graduate `docs`. Real but low-blast (personal repos only, per hard gate). | **P2** |
| B3 | **Demotion doesn't fire.** | Demotion is modeled as `compute_tier` returning 0 because the trailing streak broke (append-only ledger, any non-clean row resets streak). This is sound **if** `compute_tier` is consulted on every merge. Given A2/A3 (no server-side recompute), demotion only binds on the supervisor side — a supervisor that caches or skips recompute would keep auto-merging. Demotion is only as real as "compute_tier is called every time," which the broker does not enforce. | **P1** |
| B4 | **`reverted` has no live producer — is the column safe meanwhile?** | Correctly flagged in design §7: no P1b producer; P5.4 sentinel is the future writer. The column + revocation logic exist; AT-DEMOTE-1 injects a `reverted` row directly. Safe: an absent producer means `reverted` never fires spuriously; it just cannot fire *at all* until P5. Honest and acceptable. | **P2 (accepted)** |
| B5 | **Bypass never-graduates / work-tier-0 via task-type mislabel.** | `_is_diligent_repo(repo)` + `repo_class=="work"` are AND-gated hard returns; `repo_class` defaults to `work` (fail-safe). Never-graduates is keyed on **capability**, not task_type, so mislabeling task_type (e.g. calling a prod-deploy "docs") does NOT bypass never-graduates **provided** `capability` is derived from something trustworthy. **The design never specifies where `capability` comes from** — if it is derived from the same owner spec text as task_type, a mislabel could dodge the never-graduates gate. This is UNVERIFIED and a real hole in the design. | **P1** |

**REQ-03 verdict:** ledger is **not worker-poisonable** (supervisor is sole writer, outcome from trusted state) — the primary escalation trigger does NOT fire. BUT: (a) demotion integrity depends on `compute_tier` being called every merge, which the broker does not enforce (ties back to REQ-02 P0); (b) the `capability` provenance for the never-graduates gate is unspecified (B5, P1) — if it rides owner/worker-influenceable text, never-graduates IS bypassable-by-mislabel, which WOULD fire the escalation. **Design must pin where `capability` is derived and prove it is not the mislabelable spec text.**

---

## REQ-04 — RED test-plan judgment (per-test real/theater)

The design's §5.2 test table, judged against real code with injected time.

| Test | Falsifiable vs real code? | Verdict |
|---|---|---|
| **AT-FEED-1** (outcome row on DONE, same txn, rolls back together) | Real: exercises `record_terminal_outcome` co-transactional insert on the real `JobStore`; the roll-back assertion is genuinely falsifiable. | **REAL** |
| **AT-GRAD-1** (10 clean → auto, 9 → held) | Real: `compute_tier` is pure; injected `now`. Falsifiable. | **REAL** |
| **AT-GRAD-2** (29 days → held) | Real: injected time, pure fn. | **REAL** |
| **AT-DEMOTE-1** (inject rejected/reverted → tier 0) | Real **as a unit test of `compute_tier`**. BUT it proves the *pure function* demotes, NOT that the live merge path recomputes on every merge (REQ-02 A2/A3). Passes against the function while the system could still auto-merge. **Partial theater at the system level.** | **REAL (unit) / GAP (system)** |
| **AT-NEVER-1** (graduated + forbidden capability → held) | Real only if `capability` is a real input wired to the gate. Given B5 (capability provenance unspecified), this test could pass against a hand-fed `capability` arg while the real path derives capability differently. **Risk of theater** unless the test drives the real capability-derivation. | **AT-RISK** |
| **AT-NEVER-2** (worker diff editing trust-policy.md → RingViolation) | Real: reuses live `check_diff`; trust-policy.md is genuinely in `RING_PATHS`. Falsifiable. | **REAL** |
| **AT-WORK-1** (work repo, 20 clean → tier 0) | Real: pure-fn hard gate. | **REAL** |
| **AT-OTHER-1** (task_type other → tier 0) | Real: pure-fn hard gate. | **REAL** |
| **AT-PROFILE-1** (ceiling caps, never lifts) | Real: pure `min(computed, ceiling)`. | **REAL** |
| **AT-PATH-1** (one enqueue path for tier-0/1) | Real: asserts a single `HeldStore.enqueue` call. Falsifiable. | **REAL** |
| **AT-NONCE-1** (tier-1 auto runs same executor under broker-minted nonce; supervisor with no nonce → ApprovalRejected) | **THE critical test — and as written it is at risk of theater.** If it constructs an in-process `ApprovalAuthority` (as `test_supervisor.py` does) it can mint a nonce and the test "passes." The test must prove the supervisor **cannot obtain a nonce over the socket** (BrokerClient has no mint verb). As specified ("the supervisor, given no nonce, cannot approve") it tests the wrong thing — of course it can't approve *without* a nonce; the real question is whether it can *get* one. **Must be rewritten to assert no mint path exists across the broker boundary, and to assert the server-side ring re-check fires.** | **THEATER-RISK — rewrite required** |
| **AT-RING-1** (policy files ⊆ RING_PATHS) | Real: trivially checkable, and true today. | **REAL** |

**REQ-04 verdict:** 9 of 12 tests are genuinely real and falsifiable. The **safety-critical** ones — AT-NONCE-1 (self-approval wall) and AT-NEVER-1 (never-graduates at auto-merge) — are **at risk of passing against a stub/in-process authority** and do not test the server-side re-check (which doesn't exist). AT-DEMOTE-1 proves the pure function but not the live path. **Escalation "a safety gate has no real test" fires for the server-side ring/never-graduates re-check: there is no test because there is no code.** These tests must be rewritten to drive the broker socket boundary and a real server-side gate.

---

## Summary

- **REQ-01:** reuse claims CONFIRMED (line drift only); trust-policy.md **is** in the ring — the positive blocker resolves safely.
- **REQ-02:** **FAILS** — §4.4's server-side ring/never-graduates re-check is **absent** (P0), and the inherited P2 wiring hands the supervisor a **mint-capable in-process authority** (P0). Right intent, not grounded in code.
- **REQ-03:** ledger is not worker-poisonable (good); but demotion binds only if `compute_tier` runs every merge (broker doesn't enforce — ties to REQ-02), and `capability` provenance for never-graduates is unspecified (P1).
- **REQ-04:** most tests real; the two crown-jewel tests (AT-NONCE-1, AT-NEVER-1) risk theater and must drive the socket boundary + a real server-side gate; there is no test for the missing server-side re-check.

**Ship gate:** BLOCK on the two P0s until P1b-d scope explicitly (1) routes auto-merge through the broker socket with no reachable mint, and (2) adds a broker-side ring/never-graduates/tier re-check on the `merge` auto_sent branch, each with a falsifiable RED test that fails against the current (gate-absent) code. With those two fixes scoped and tested, the rest of the design is **SHIP-WITH-FIXES**.
