# P1b-e Report — Broker-side auto-merge gate (CODER)

<!-- ABOUTME: Build report for P1b-e — the BROKER-SIDE auto-merge gate, the load-bearing fix -->
<!-- ABOUTME: for the crown-jewel auto-merge risk (review P0-1 server-side re-check, P0-2 self-mint). -->
<!-- ABOUTME: On any auto (non-human) merge the broker re-runs ring + never-graduates + tier over -->
<!-- ABOUTME: the diff and FAILS CLOSED. The broker mints/burns the nonce internally; auto_merge is -->
<!-- ABOUTME: a socket RPC carrying NO nonce. RED+GREEN, server-side-catch, no-mint-RPC, 0-regression. -->

**Role:** CODER, P1b-e (backlog, GOVERNANCE_EXEMPT). **Branch:** `factory` (verified). **Zero commits/pushes.**
**Scope touched (only):** `broker/merge_gate.py` (new), `broker/server.py`, `broker_client.py`, `broker/approval.py`, `tests/broker/test_auto_merge_gate.py` (new). No `factory/` file edited; broker imports nothing from `factory`.

---

## Architecture decision (layering)

The broker must NOT import `factory.*` (verified: `grep 'from factory' broker/*.py` = 0 hits; layering is factory→broker, broker is the credential-holding trust root). Yet `evaluate_auto_merge` needs a tier recompute + ledger read that live factory-side (P1b-a/b, not yet built).

**Resolution (design §V2.1 compliant):** `broker/merge_gate.py` OWNS its own ring path-list (`BROKER_RING_PATHS`, a verbatim copy) + a ported `check_ring` + a never-graduates reader, and takes `git_diff` / `compute_tier` / `never_graduates` as **injected callables**. The factory-side `compute_tier` + ledger reader are wired in by the supervisor (P1b-d) when it constructs the broker's `MergeGate` — the broker module itself imports nothing from factory. This keeps the gate fully testable now (P1b-a/b/c not built yet) and preserves the trust-root layering. A parity test asserts `BROKER_RING_PATHS ⊇ factory.immutable_ring.RING_PATHS` so the broker can never check *less*.

The production `main()` deliberately leaves `merge_gate=None` → the `auto_merge` RPC **fails closed** (every request held) until P1b-d injects the real gate. A standalone broker never auto-merges.

---

## REQ-01 — `broker/merge_gate.py`: server-side gate (design V2.1, review P0-1)

`MergeGate.evaluate(card, now_ms)` and module fn `evaluate_auto_merge(...)`, in order, fail-closed:
1. **Ring re-check** over `git_diff(base, branch, worktree)` via broker-owned `check_ring` → `RingViolation` ⇒ `("held",0,"ring")`.
2. **Never-graduates** re-check on the **trusted** `capability` from the card ⇒ `("held",0,"never")`.
3. **Tier recompute** via injected `compute_tier` (never trusts the card's `requested_tier`); tier<1 ⇒ `("held",0,"tier")`.
4. All pass ⇒ `("auto", tier, "ok")`.
Any exception / unreadable diff / empty diff / unparseable header ⇒ `("held",0,"error"|...)` — the broker never auto-merges on an unrun check.

**Evidence (unit, all RED-first then GREEN):**
- `test_ring_touching_diff_returns_held`, `test_policy_doc_diff_returns_held` — ring hit → held (server-side catch).
- `test_never_graduates_capability_returns_held` — prod-deploy capability → held.
- `test_clean_tier1_card_returns_auto_ok` — clean tier-1 → auto.
- `test_tier_zero_recompute_returns_held` — broker recompute tier-0 → held (ignores card tier).
- `test_any_error_fails_closed_to_held`, `test_unparseable_diff_fails_closed`, `test_module_level_evaluate_auto_merge_helper` — fail-closed on error/garbage.
- `test_broker_ring_list_is_superset_of_factory` (AT-RING-2), `test_broker_does_not_import_factory` (AST-checked).

`escalate_if` (diff needs a worktree that may be gone): resolved by fail-closed design — the diff is computed via the injected `git_diff` over the card's worktree; any failure (worktree gone, git error) → held. Never AUTO on a diff it could not read.

## REQ-02 — `_rpc_auto_merge` + `auto_merge` client verb (design V2.2, review P0-2)

- `"auto_merge"` added to `broker/server.py:_CLIENT_METHODS`; `_rpc_auto_merge(params)` takes a held `action_id` and **NO nonce**.
- Requires `row.type == "merge"` and `row.state == "held"` (else `ApprovalRejected`, fail-closed).
- Runs `self._merge_gate.evaluate(...)`. On **auto**: broker `mint_nonce(aid)` **internally** then `approve(aid, nonce, executor=..., decided_by="trust:auto")` — the identical nonce+executor path as human approval; the mint never crosses the socket. On **held**: card stays held for a human tap.
- `BrokerClient.auto_merge(action_id)` added — a request verb with **no nonce, no mint**.
- `broker/approval.py:approve` gained a keyword-only `decided_by="telegram"` (default preserves the human path); the auto path labels rows `"trust:auto"`. Backward-compatible — the supervisor's existing `approve(...)` call is unaffected.

**Evidence (over the real socketpair, not a mock):**
- `test_auto_merge_clean_tier1_executes_over_socket` — executes; row `state=executed`, `decided_by="trust:auto"`; broker minted internally (caller sent no nonce).
- `test_auto_merge_ring_diff_leaves_held` (AT-BROKER-GATE-1, **load-bearing**) — ring diff over the socket → held, merge executor NOT called, row still `held`.
- `test_auto_merge_never_graduates_leaves_held` (AT-NEVER-1) — trusted forbidden capability → held.
- `test_auto_merge_tier0_recompute_leaves_held` (AT-BROKER-GATE-2) — broker recompute tier-0 → held.
- `test_auto_merge_non_merge_action_rejected`, `test_auto_merge_unknown_action_rejected` — fail-closed.
- `test_auto_merge_is_a_client_method`, `test_broker_client_has_auto_merge_no_mint`, `test_auto_merge_signature_carries_no_nonce` (AT-NONCE-1) — no mint verb reachable over the socket or on the client.

**No-mint-RPC proof (runtime):** `_CLIENT_METHODS = {approve, auto_merge, enqueue_action, health, list_pending, reject, resolve_model_key}` — no `mint*`. `dir(BrokerClient)` has `auto_merge`, no `mint*`. The only `mint_nonce` calls are `_rpc_auto_merge` (broker-internal) and `mint_approval_nonce` (server.py, NOT an RPC — unchanged).

**§V2.4:** `"merge"` added to `_default_safe_lane().irreversible_types` (was `{"git_push_force"}`) so a merge can never auto-send via the SafeLane path — held-by-default; only the tier-gated `auto_merge` RPC releases it.

## REQ-03 — no regression / preserve nonce wall

- `tests/broker/` : **114 passed, 1 skipped** (the skip is the env-gated AF_UNIX filesystem-bind test, pre-existing). 0 failures.
- The self-approval test (`test_assistant_cannot_self_approve_over_socket`) and the meta-guard (`test_removing_nonce_check_would_break_self_approve_guard`) still pass.
- `test_human_approve_still_requires_nonce` (new) — approve without/with a forged nonce still rejected; auto_merge is the ONLY no-nonce path and it is gated.
- `tests/factory/test_supervisor.py`: 9 passed (approval.py signature change is backward-compatible).

## REQ-04 — anti-theater (demonstrated)

Empirically neutered `MergeGate.evaluate` to `return ("auto",1,"ok")` (skip ring/never/tier). Result: **11 of 21 tests FAILED**, including `test_auto_merge_ring_diff_leaves_held`, `test_auto_merge_never_graduates_leaves_held`, `test_auto_merge_tier0_recompute_leaves_held`, and the fail-closed tests. Restored → 21/21 GREEN. This proves the gate is load-bearing: a neutered gate cannot pass the suite. Additionally `test_ring_hit_diff_executes_when_gate_neutered` asserts in-test that a gate with the ring check skipped returns `auto` on the same ring diff the real gate holds — the ring check is what holds it.

## REQ-05 — TDD

RED-first confirmed: initial run failed at import (`cannot import name 'merge_gate'`), then per-behavior RED before each impl slice. Final: `test_auto_merge_gate.py` **21 passed**. Real broker objects over `socket.socketpair` (the production `serve_connection` path); the ONLY stub is the injected `git_diff`/`compute_tier`/`never_graduates` boundary + the recording merge executor (subprocess/git-network boundary). No mocked-internal-behavior tests.

---

## Files
- `broker/merge_gate.py` (new, ~300 LOC) — `MergeGate`, `evaluate_auto_merge`, `BROKER_RING_PATHS`, ported `check_ring`, never-graduates reader.
- `broker/server.py` — `auto_merge` in `_CLIENT_METHODS`, `_rpc_auto_merge`, `merge_gate` ctor param (None ⇒ fail-closed), `merge` ∈ irreversible_types.
- `broker_client.py` — `BrokerClient.auto_merge(action_id)` (no nonce).
- `broker/approval.py` — `approve(..., decided_by="telegram")` keyword default (backward-compatible).
- `tests/broker/test_auto_merge_gate.py` (new) — 21 tests.

## UNVERIFIED / handoff to P1b-d
- Production wiring of a real `MergeGate` (factory `compute_tier` + ledger reader injected) is P1b-d — this task deliberately leaves `main()` gate-less (fail-closed).
- `compute_tier`, `factory/trust_ledger.py`, `factory/trust_policy.py`, `merge_disposition` are P1b-a/b/c and did not exist at build time; the gate is validated with injected fakes, which is the correct decoupling.
