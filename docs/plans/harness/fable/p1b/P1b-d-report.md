# P1b-d Report — Tier-1 auto-merge wiring (CODER)

<!-- ABOUTME: Build report for P1b-d — the last-mile wiring of the crown-jewel auto-merge path. -->
<!-- ABOUTME: The composition root injects the REAL factory compute_tier + ledger + profile into -->
<!-- ABOUTME: the broker's MergeGate; the prod supervisor holds NO mint and requests auto-merge -->
<!-- ABOUTME: over the socket. RED+GREEN, no-mint proof, end-to-end auto-merge + server-side-catch -->
<!-- ABOUTME: proofs, 0-regression (20 pre-existing git-sandbox failures only), anti-weakening. -->

**Role:** CODER, P1b-d (backlog, GOVERNANCE_EXEMPT). **Branch:** `factory` (verified `git rev-parse` = factory). **Zero commits/pushes.**
**Scope touched (only):** `factory/broker_launch.py` (new — composition root), `factory/supervisor.py` (no-mint + socket routing + `build_production_supervisor` + `request_auto_merge`), `tests/factory/test_broker_launch.py` (new), `tests/factory/test_supervisor_trust.py` (new), `docs/plans/harness/fable/STAGED-GATES.md` (SG-P1b-3). No `broker/` file edited (P1b-e already built the broker side); the broker CORE still imports nothing from factory.

---

## Layering decision (REQ-01 escalate_if — resolved without inverting layering)

The broker's `MergeGate` (P1b-e) already takes `compute_tier` as an **injected callable** with a deliberately narrow contract: `(repo, task_type, capability, now_ms) -> int`. The factory's real `compute_tier` has a richer signature (`rows, now, *, policy, repo_class, capability`). The clean injection point is therefore a **new composition-root module** `factory/broker_launch.py` that imports BOTH `factory.trust_policy` and `broker.merge_gate`, and builds a closure adapter over the real `TrustLedger` reader + pinned policy + owner-profile ceiling + per-repo class. The broker package imports nothing from factory — verified structurally by an AST test (`test_broker_launch_does_not_import_factory_into_broker_core`), not just grep. This is the least-bad composition root: the only place both layers meet, the trust root (`broker/`) stays the lower layer.

---

## REQ-01 — composition root injects the REAL gate

`factory/broker_launch.build_merge_gate(ledger, repo_class_for, profile, policy?, git_diff?)` returns a `MergeGate` whose `compute_tier` is the closure `_compute_tier_adapter`: it reads real ledger rows for `(repo, task_type)`, runs the real `factory.trust_policy.compute_tier` with the pinned policy + per-repo class, then caps with `profile_ceiling(profile)` (min — a profile only lowers). `git_diff` defaults to a real `git diff base...branch` subprocess runner; `never_graduates` defaults to the gate's own ring-protected doc reader.

**Done-when evidence:**
- `test_composed_gate_returns_auto_for_graduated_clean_tier1_card` — a tier-1 clean card returns `("auto", 1)` using the REAL `compute_tier` over a REAL seeded `TrustLedger` (10 clean rows / ≥30 days / personal / permissive profile).
- `test_broker_launch_does_not_import_factory_into_broker_core` — AST walk over `broker/**/*.py`: zero `from factory` / `import factory`. Broker core has no factory import.

## REQ-02 — prod supervisor holds NO mint authority

`factory/supervisor.py`:
- `Supervisor.__init__` now accepts `authority: Optional[ApprovalAuthority] = None` and a new `broker_client=None`. Production passes `authority=None`.
- `approve_merge` routes: if `authority` present (TEST path) → in-process; elif `broker_client` present → `broker_client.approve(action_id, nonce=nonce)` over the socket (the broker validates the nonce it minted out-of-band via Telegram); else → **raises** (fail-closed, no way to approve).
- New `request_auto_merge(action_id)` → `broker_client.auto_merge(action_id)` — a no-nonce request verb; raises if no broker_client (never falls back to in-process mint).
- New module fn `build_production_supervisor(...)` — the sanctioned prod constructor: passes `authority=None`, requires a request-only `broker_client`. Never accepts an `ApprovalAuthority`.

**Done-when evidence (AT-NOMINT-1):**
- `test_production_supervisor_has_no_mint_capable_authority` — `getattr(sup, "_authority") is None`, no `_broker_secret`, no `mint_nonce`. **Fails RED against the pre-P1b-d wiring** (which passed a real `ApprovalAuthority(held, broker_secret=...)`).
- `test_prod_held_merge_approval_routes_over_socket` — the P2 held path still works: `approve_merge` calls `broker_client.approve(action_id, nonce)` and the job reaches `DONE`. The held human path was rewired over the socket, not broken.
- `test_prod_approve_merge_without_broker_client_fails_closed` — no authority + no client → raises.

## REQ-03 — end-to-end tier-1 auto-merge (over the REAL socket)

Driven over `socket.socketpair()` against the production `BrokerServer.serve_connection` path (not a mock), with the composed gate and a recording merge executor (the only stubbed boundary = git-network):
- `test_end_to_end_graduated_clean_job_auto_merges_over_socket` — graduated clean job → `auto_merge` over the socket → broker recomputes tier-1 from the real ledger, mints+burns a nonce INTERNALLY, executor runs once, row `state=executed`, `decided_by="trust:auto"`, caller sent NO nonce. **Auto-merge with NO human.**
- `test_end_to_end_ring_touching_tier1_job_stays_held_over_socket` — tier-1-eligible job whose diff touches `broker/approval.py` → broker returns `held` (reason `ring`), executor NEVER runs, row still `held`. **Server-side catch.**
- `test_end_to_end_tier0_job_stays_held_over_socket` — ungraduated repo → broker recomputes tier-0 → `held` (reason `tier`), executor never runs.
- Gate-level parallels: `test_composed_gate_holds_tier0_card`, `test_composed_gate_holds_ring_touching_tier1_card`, `test_composed_gate_holds_under_ask_for_everything_profile`, `test_composed_gate_holds_work_repo_regardless_of_record`.

## REQ-04 — preserve everything (0 regressions)

Definitive baseline comparison (canonical `scripts/run_tests.sh`, per-file isolation):
- **BASELINE (all my changes removed — supervisor.py stashed, broker_launch.py + new tests moved aside):** `278 passed, 20 failed`.
- **WITH my changes:** `284 passed, 20 failed` (+6 net in-suite; my 2 new files add 16 passing tests).
- The **20 failures are byte-identical** in both runs and are ALL `assert 128 == 0` — a git subprocess returncode-128 from the sandbox blocking git hook-sample file writes (`Operation not permitted` on `git clone`), in `tests/factory/test_supervisor.py`, `test_worker_runner.py`, `tests/broker/test_merge_executor.py`. **Pre-existing sandbox limitation, not a regression** (proven by the stash run failing identically on the clean tree).
- P1b-e gate + nonce wall specifically: `tests/broker/test_auto_merge_gate.py` **21 passed**; `test_approval.py` green (combined 30 passed). The P1a nonce wall (`_CLIENT_METHODS` has no mint verb) and P1b-e server-side gate both intact.

## REQ-05 — RED-first + anti-weakening

**RED-first:** initial run of both new test files failed at import (`ModuleNotFoundError: No module named 'factory.broker_launch'` / `cannot import build_production_supervisor`), confirmed before any impl. Then GREEN: 16/16.

**Anti-weakening (both directions demonstrated empirically):**
1. Weakened `broker_launch._compute_tier_adapter` to `return 1` (constant tier, skipping the real `compute_tier`): **5 tests FAILED** — `test_composed_gate_holds_tier0_card`, `..._ask_for_everything_profile`, `..._work_repo_regardless_of_record`, `test_end_to_end_tier0_job_stays_held_over_socket`, and the explicit `test_real_compute_tier_is_load_bearing_in_composition`. Restored → green. Proves the REAL ledger-driven tier is load-bearing (a hardcoded tier-1 would auto-merge an ungraduated repo).
2. Weakened `build_production_supervisor` to pass a mint-capable fake authority: **2 tests FAILED** — `test_production_supervisor_has_no_mint_capable_authority` (AT-NOMINT-1) and `test_prod_held_merge_approval_routes_over_socket`. Restored → green. Proves the no-mint fix is load-bearing.

**STAGED row:** `SG-P1b-3` appended to `STAGED-GATES.md` — the live-host auto-merge run (real graduated repo, broker-internal nonce mint, no human tap) with the exact flip procedure + negative checks (ring/tier-0/profile-flip → held). Depends on SG-P1b-1 (real 30-day/10-clean record).

---

## Files
- `factory/broker_launch.py` (new, ~140 LOC) — `build_merge_gate` composition root, `_compute_tier_adapter` closure, `_real_git_diff` subprocess runner.
- `factory/supervisor.py` — `authority` now `Optional` (None in prod) + `broker_client`; `approve_merge` socket-routes; new `request_auto_merge`; new module fn `build_production_supervisor`.
- `tests/factory/test_broker_launch.py` (new) — 10 tests (composition/gate + end-to-end socket + anti-weakening + AST import-guard).
- `tests/factory/test_supervisor_trust.py` (new) — 6 tests (AT-NOMINT-1 + held-path-over-socket + fail-closed + anti-weakening).
- `docs/plans/harness/fable/STAGED-GATES.md` — SG-P1b-3.

## Notes / UNVERIFIED
- The `repo_class_for` callable is injected by the caller (a per-repo `personal`/`work` classifier). P1b-c owns the `merge-policy.md` `trust:` block that backs it in production; this task takes it as an injected boundary so the composition root does not re-implement classification. **UNVERIFIED** whether P1b-c's `merge_policy` `trust:` loader landed — the composition root is agnostic (any `repo->class` callable works); wiring the concrete classifier is a one-line production glue when P1b-c's loader is confirmed.
- Production `broker/server.py:main()` still constructs `merge_gate=None` (fail-closed). Flipping it to `build_merge_gate(...)` is the production launch glue (documented in SG-P1b-3 step 2); left un-flipped here so a standalone broker never auto-merges until the owner opts in with a real ledger + profile.
- The 20 pre-existing git-sandbox test failures are environmental (`Operation not permitted` on git hook writes), independent of this task; flagged for the verifier so they are not attributed to P1b-d.
