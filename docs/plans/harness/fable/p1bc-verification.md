# P1b + P1c Verification (re-run, independent)

<!-- ABOUTME: Independent VERIFIER re-run of Phases P1b (auto-merge/trust) + P1c (assistant essentials). -->
<!-- ABOUTME: Produced by re-running tests + 3 anti-theater neuter/restore demos, not by re-reading reports. -->
<!-- ABOUTME: Crown-jewel focus: P1b auto-merges WITHOUT a human; P1c crux is exactly-one-send. -->
<!-- ABOUTME: Verdicts VERIFIED / UNVERIFIED / CONTRADICTED. Source: branch `factory`, pm_os on disk. -->
<!-- ABOUTME: Verifier is GOVERNANCE_EXEMPT — re-runs work, fixes nothing, does not commit. -->

**Role:** VERIFIER (re-run, read-only except the doc + temporary anti-theater neuter/restore, fully reverted).
**Branch:** `factory` (hermes) · pm_os at `~/Code/pm_os` (P1c inbox code uncommitted on disk).
**Method:** RE-RAN suites myself; recorded MY counts. Three anti-theater demos actually executed (neuter → observe FAIL → restore → tree clean). Did NOT trust producer self-reports.

**Overall:** **P1b = PASS-WITH-STAGED · P1c = PASS-WITH-STAGED.** The two review P0s (server-side ring/never-graduates re-check; supervisor self-mint) are CLOSED in code and each has a load-bearing test I falsified. No non-env failure exists. What remains is the honestly-staged live/wall-clock work.

**Tally:** 6 requirements → **6 VERIFIED (with staged carve-outs noted) · 0 UNVERIFIED · 0 CONTRADICTED.** Anti-theater: **3/3 demos fired** (each neuter made the guarding test FAIL).

---

## REQ-01 — Suite counts (my re-run) + env-failure separation → VERIFIED

Runner: `scripts/run_tests.sh` (per-file isolated subprocesses; venv at `./venv`).

| Suite | Tests | Pass | Fail |
|---|---|---|---|
| tests/broker/test_auto_merge_gate.py | 21 | 21 | 0 |
| tests/broker/test_send_intents.py | 6 | 6 | 0 |
| tests/broker/test_bypass_impossibility_gate.py | 6 | 6 | 0 |
| tests/broker/test_server_client.py | 7 | 7 | 0 |
| tests/factory/test_trust_ledger.py | 19 | 19 | 0 |
| tests/factory/test_trust_policy.py | 14 | 14 | 0 |
| tests/factory/test_trust_profile.py | 16 | 16 | 0 |
| tests/factory/test_broker_launch.py | 10 | 10 | 0 |
| tests/factory/test_supervisor_trust.py | 6 | 6 | 0 |
| pm_os test/inbox-sweep.test.js | 22 | 22 | 0 |

**Env-gated failures (UNVERIFIED-BY-ENV, NOT regressions):** the full `tests/broker/ tests/factory/` sweep shows **20 failures in exactly 3 files** — `test_supervisor.py` (9), `test_merge_executor.py` (7), `test_worker_runner.py` (4). **Every one** fails with the identical signature: a `git init`/`git clone` returning exit 128 because the sandbox blocks writing `.git/hooks/commit-msg.sample` / `.git/config` (`Operation not permitted`). I reproduced the bare cause directly (`mkdir` and `git init` under the repo both return `Operation not permitted`). These tests build a real git remote/worktree, which the sandbox forbids — they are **environment failures, not code failures**. (`test_immutable_ring.py` reports "3 passed" but a non-zero runner exit from a warnings-as-errors/hook artifact, not a test failure.) **No non-env failure exists → escalation trigger does NOT fire.**

---

## REQ-02 — Crown-jewel P0-2: no supervisor self-mint → VERIFIED (anti-theater fired)

- **Prod path gets `authority=None`:** `factory/supervisor.py:616` — `build_production_supervisor(...)` passes `authority=None` explicitly ("NO-MINT: production holds no mint-capable authority"). The supervisor holds no `ApprovalAuthority`, no broker secret, no `mint_nonce`.
- **Auto-merge goes over the socket, not in-process:** `request_auto_merge` (`supervisor.py:484-506`) calls `self._broker_client.auto_merge(action_id)` — a no-nonce request verb. `approve_merge` (`:442-482`) routes human approval via `broker_client.approve(action_id, nonce)`. Both fail-closed (raise) if no `broker_client`.
- **ANTI-THEATER (I ran it):** I reverted the guard — made `build_production_supervisor` pass a mint-capable `_AntiTheaterMintAuthority()` — and re-ran `test_supervisor_trust.py`. Result: **`test_production_supervisor_has_no_mint_capable_authority` FAILED** (`assert authority is None` → the prod supervisor now held a mint-capable object) **and** `test_prod_held_merge_approval_routes_over_socket` FAILED (approval took the in-process path, never hit the socket). Restored `factory/supervisor.py` via `git checkout`; tree clean.

**Escalation ("prod supervisor can hold a mint authority / self-approve"): does NOT fire.**

---

## REQ-03 — Crown-jewel P0-1: server-side gate is real → VERIFIED (anti-theater fired)

- **Server-side catch over the socket:** `broker/merge_gate.py` `MergeGate.evaluate` re-runs, in order, (1) ring re-check over the merge diff (`check_ring`), (2) never-graduates re-check on the trusted capability, (3) tier recompute from the ledger — AUTO only if all pass, HELD on any failure/exception (fail-closed). `broker/server.py:_rpc_auto_merge` (:325-376) is the only no-nonce release path; on non-`auto` it leaves the card held and the executor never runs. I ran the socketpair tests (`test_auto_merge_gate.py`, real `BrokerServer`, not a mock): a ring-touching diff, a never-graduates capability, and a broker-recomputed tier-0 each leave the card **held** and `merge_exec.calls == []`.
- **ANTI-THEATER (I ran it):** I neutered `MergeGate.evaluate` to `return (_AUTO, 1, "ok")` and re-ran `test_auto_merge_gate.py`. Result: **`test_auto_merge_ring_diff_leaves_held` FAILED** (ring diff now merged), plus the never-graduates and tier-0 socket tests and the unit ring tests — **11 failed / 10 passed**. Restored `broker/merge_gate.py`; tree clean.
- **No client-reachable mint verb:** `_CLIENT_METHODS` (`server.py:28-39`) = {health, enqueue_action, list_pending, approve, reject, resolve_model_key, auto_merge}. **No mint verb.** `mint_approval_nonce` (:410) is deliberately NOT in the set, so `_dispatch` (:245) rejects it as method-not-found. `BrokerClient` (`broker_client.py`) exposes no `mint*` method. The only `mint_nonce()` call on the auto path is broker-internal (`server.py:373`), never crossing the socket.
- **Broker owns its own ring list, imports nothing from factory:** `BROKER_RING_PATHS` is a verbatim copy; `test_broker_ring_list_is_superset_of_factory` and `test_broker_does_not_import_factory` (AST-checked) both pass.

**Escalation ("ring/never-graduates diff can auto-merge, or a mint RPC exists"): does NOT fire.**

---

## REQ-04 — Graduation/demotion matrix + hard gates → VERIFIED

`tests/factory/test_trust_policy.py` (14/14, run verbose) — the full matrix observed against `compute_tier` with injected `now`:

| Condition | Result | Test |
|---|---|---|
| 10 clean + ≥30 days | tier-1 | `test_ten_clean_thirty_days_graduates_to_tier1` |
| 9 clean | tier-0 | `test_nine_clean_stays_tier0` |
| 10 clean but 29 days | tier-0 | `test_ten_clean_but_only_29_days_is_tier0` |
| reverted row present | tier-0 (streak reset) | `test_reverted_row_resets_streak_to_tier0` |
| rejected row present | tier-0 | `test_rejected_row_resets_streak_to_tier0` |
| merged_with_fix | breaks streak | `test_merged_with_fix_breaks_streak` |
| work repo, clean record | tier-0 | `test_work_repo_is_tier0_regardless_of_record` |
| diligent-* prefix | tier-0 | `test_diligent_org_prefix_is_tier0` |
| never-graduates capability | tier-0 | `test_never_graduates_capability_is_tier0` |
| unknown/other task_type | tier-0 | `test_other_task_type_is_tier0` |

The three hard gates are early-return `return 0` in `compute_tier` (`trust_policy.py:247,256,262`) **before** any ledger data is read — unbypassable by any accumulation of clean outcomes. Thresholds pinned in `docs/factory/trust-policy.md` (consecutive=10, max_reverts=0, min_window_days=30).

**Ring protection confirmed:** `factory.immutable_ring.RING_PATHS` contains `trust-policy.md`, `docs/factory/trust-policy.md`, AND `docs/factory/never-graduates.md` (verified by importing the tuple). A worker cannot edit its own thresholds; `test_ring_paths_include_trust_policy_files` guards a refactor from dropping them.

**Escalation ("a hard gate is bypassable or trust-policy.md isn't ring-protected"): does NOT fire.**

---

## REQ-05 — P1c exactly-one-send → VERIFIED (anti-theater fired)

- **Exactly-once on retry (before + after approval):** `hermes_cli/send_intents.py` `SendIntentLedger.enqueue_send` dedups on a caller-supplied stable `intent_key` under a write lock (dedup early-return + `INSERT ON CONFLICT DO NOTHING`). `tests/broker/test_send_intents.py` (6/6): `test_exactly_one_send_on_retry` proves a retry returns the same `action_id`, `deduped:True`, broker called exactly once, executor ran once. The broker's own idempotent nonce-gated `approve` (burn-before-execute, `approval.py`) covers the after-approval half.
- **ANTI-THEATER (I ran it):** I disabled the ledger dedup (`if False:`) and re-ran. Result: **`test_exactly_one_send_on_retry` FAILED** (broker called twice → double-send), plus 3 sibling dedup tests — **4 failed / 2 passed**. Restored `hermes_cli/send_intents.py`; tree clean.
- **Inbox-sweep idempotency key is a stable message id, NOT a body-hash:** `~/Code/pm_os/bin/inbox-sweep.js` `buildProcessedKey(msg)` returns `<source>:<provider immutable id>` (`meta.messageId || msg.id`; teams uses `conversationId`). Every branch **throws** (fail-closed) when no stable id is present — no body-hash fallback anywhere. The header comment states "Idempotency key: never a body hash (prevents /approve-email bug class)." The pm_os suite includes the anti-hash tests "SAME body but DIFFERENT ids → distinct keys" and "same id + edited body → same key" and the "second sweep acts on 0 items" critical gate — all pass (22/22).

**Escalation ("a retry can double-send"): does NOT fire.**

---

## REQ-06 — Staged vs verified (honest split)

| Item | Status | Note |
|---|---|---|
| P1b compute_tier graduation matrix (10-clean/9/29-day/revert/reject/hard-gates) | **VERIFIED (unit, injected time)** | pure fn, falsifiable; not a live 30-day record |
| P1b server-side auto-merge gate (ring/never/tier over socket) | **VERIFIED (real BrokerServer over socketpair)** | anti-theater fired |
| P1b no-self-mint (authority=None, socket-only) | **VERIFIED** | anti-theater fired |
| SG-P1b-1 — a real (repo×task-type) with ≥10 clean / 0 reverts / ≥30 days wall-clock | **STAGED** | needs live merges over calendar time; cannot exist yet |
| SG-P1b-3 — a real graduated repo auto-merges with NO human tap on the live host | **STAGED** | needs SG-P1a-2/3 + SG-P2-1 + SG-P1b-1 real first |
| P1c exactly-one-send ledger dedup | **VERIFIED** | anti-theater fired |
| P1c inbox-sweep dedup + triage (fixtures) | **VERIFIED (22/22, in-sandbox fixtures)** | |
| SG-P1c-1 — real inbox sweep processes no item twice (live Outlook/Teams + FOCI) | **STAGED** | test marked STAGED; needs live tokens |
| SG-P1c (design ask) — ≥1 real meeting prep note assembled end-to-end | **STAGED** | needs live calendar/mail; `bin/meeting-prep.js` present, unrun live |
| SG-P1c-3 — Telegram forwarder live | **STAGED** | marked STAGED/UNVERIFIED on factory branch |
| pm_os P1c code (`bin/inbox-sweep.js`, `bin/meeting-prep.js`) | **UNCOMMITTED ON DISK** | present + tested at `~/Code/pm_os`, not committed |

All staged items have exact flip procedures in `docs/plans/harness/fable/STAGED-GATES.md`.

---

## Blocking list

**None.** No non-env test failure; both crown-jewel P0s closed with load-bearing (falsified) tests. The remaining work is honestly staged (live wall-clock graduation, live auto-merge, live inbox/meeting, pm_os commit) and does not block the phase verdicts.

## Tree cleanliness

Source tree confirmed clean after all three anti-theater neuter/restore cycles (`broker/merge_gate.py`, `factory/supervisor.py`, `hermes_cli/send_intents.py` each `git checkout`-restored; no `ANTI-THEATER` marker remains). Only pre-existing untracked `hermes-evolution/` and `.claude/worktrees/` remain (not produced by this verification). No commits made.
