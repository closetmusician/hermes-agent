# P2 Verification — Independent Re-Run

**Verifier role:** RE-RAN every suite, PROVED two gates falsifiable (neuter-and-restore),
did not read-and-trust. Fixed nothing. Branch `factory`. Sandbox disabled for git-based tests
(the orchestrator cannot run them; the subagent can). Verifier-touched source is clean at end.

**Overall verdict: PASS-WITH-STAGED.** Every P2 safety gate is real and un-evadable as
demonstrated; the only STAGED item is the *single live end-to-end merge of one real feature*
(the "first-magic moment"), which is proven piece-by-piece at component level but is not driven
by one supervisor process in an integration test (see REQ-06).

---

## Claim Table

| REQ | Verdict | Evidence |
|---|---|---|
| REQ-01 Re-run all suites | **VERIFIED** | factory 126 passed; broker 86 passed / 1 skipped (AF_UNIX bind, env-gated). Zero non-env-gated failures. |
| REQ-02 Worker cred-wall real + anti-theater | **VERIFIED** | `scrub_env` builds env from a literal dict, never reads `os.environ`; neuter → both worker-env tests FAIL (GITHUB_TOKEN leaks, `EgressKeyLeaked` raised); restored clean. |
| REQ-03 No-ungated-merge + immutable-ring + anti-theater | **VERIFIED** | 7/7 merge_executor tests pass over REAL git; neuter of `check_diff` ring-check → ring test FAILS ("DID NOT RAISE RingViolation"); restored clean. |
| REQ-04 Cost stops fire against process groups | **VERIFIED** | 7/7; all 3 stops fire; atomicity is a real 2-thread barrier race (CAS); grandchild residual honestly documented (kill-OR-log, not falsely claimed). |
| REQ-05 Job flow + spec-first + injection | **VERIFIED** | one-way state machine rejects backward + terminal transitions; `kind:quick` skips stage-1, feature-class emits schema'd (Draft7) stage-1 spec; injection flags hidden "push to main". |
| REQ-06 Honest staged/limitation list | **VERIFIED (list produced)** | See staged table below. The live single-approval e2e is STAGED. |

Counts: **5 VERIFIED / 0 UNVERIFIED / 0 CONTRADICTED** (REQ-06 is a list-production task, produced).

---

## REQ-01 — Per-suite observed counts (my re-run, sandbox off)

`venv/bin/python -m pytest <suite> -p no:cacheprovider -o addopts=""`

**tests/factory/ — 126 passed (24.7s aggregate):**

| Suite | Observed |
|---|---|
| test_cost_stops.py | 7 passed |
| test_gauntlet.py | 9 passed |
| test_immutable_ring.py | 13 passed |
| test_injection_scan.py | 16 passed |
| test_intake.py | 20 passed |
| test_job_store.py | 20 passed |
| test_merge_policy.py | 11 passed |
| test_two_stage.py | 10 passed |
| test_worker_runner.py | 20 passed (incl. worker_env + immutable_ring imports) |

**tests/broker/ — 86 passed / 1 skipped (3.7s aggregate):**

| Suite | Observed |
|---|---|
| test_merge_executor.py | 7 passed |
| test_registry_dispatch.py | 10 passed |
| test_action_id / approval / bypass_impossibility_gate / credentials / egress_routing / egress_starving / held_store / jsonrpc / safe_lane / server_client | 69 passed, 1 skipped |

**The 1 skip** = `tests/broker/test_server_client.py:74` — "AF_UNIX bind() blocked in this sandbox".
Env-gated, not a failure. (Under the raw sandbox, 4 worker_runner git-init tests also error with
`git init` exit 128 — a `mkdir: Operation not permitted` sandbox artifact, NOT a code failure;
with sandbox disabled all 4 pass. Same for the merge_executor real-git tests.)

---

## REQ-02 — Worker cred-wall (P0-1) is REAL — anti-theater demonstrated

**Scrub logic cited** (`factory/worker_env.py:226-233`): `env` is constructed as a literal dict
of 6 fixed keys (PATH/HOME/TMPDIR/LANG/LC_ALL/TERM) plus at most one broker-injected model key.
`os.environ` is **never** read or overlaid. HOME is a fresh `mktemp` per job (blocks
`~/.hermes/.env`, `~/.ssh`, gh hosts.yml, broker socket). `assert_no_egress` (lines 261-294) is a
positive whitelist-subset wall strictly stronger than the denylist. The worker carries only a
model *inference* key — never an egress/push token.

**Anti-theater (neuter → FAIL → restore):**
- Weakened `scrub_env` to `env = {**os.environ, ...}` (overlay).
- `test_worker_env_lacks_seeded_egress_tokens` + `test_scrub_env_never_inherits_os_environ` →
  **2 failed**: `EgressKeyLeaked: worker env contains egress-classed key(s):
  ['AWS_*', 'GITHUB_TOKEN', 'TELEGRAM_BOT_TOKEN']` and `AssertionError: 'SOME_RANDOM_INHERITED'
  not in {...}`. The test is genuinely load-bearing.
- `git checkout factory/worker_env.py` → **2 passed**. Tree clean (`git diff` empty).

---

## REQ-03 — No-ungated-merge + immutable-ring — anti-theater demonstrated

Both gates observed passing in `tests/broker/test_merge_executor.py` (7/7, over REAL local git
repos with a real bare "remote" push):
- `test_no_ungated_merge_without_nonce`: `authority.approve(aid, None)` raises `ApprovalRejected`;
  action stays `held`; remote main NOT advanced (executor never ran).
- `test_forged_nonce_cannot_merge`: nonce minted with wrong secret rejected.
- `test_conflicting_merge_needs_attention_never_forces`: rebase conflict → needs_attention,
  `forced=False`, and a monkeypatched `subprocess.run` asserts NO `-f/--force/--force-with-lease`
  ever appears.
- `test_ring_touching_diff_rejected_before_merge`: a `broker/approval.py` diff raises
  `RingViolation` before it can become a merge card; app-only diff passes (negative control).

**Anti-theater (neuter → FAIL → restore):**
- Disabled the ring hit in `check_diff` (`if False and is_ring_path(rel)`).
- `test_ring_touching_diff_rejected_before_merge` → **FAILED**: "DID NOT RAISE
  `RingViolation`". A broker/**-touching diff would leak to merge.
- `git checkout factory/immutable_ring.py` → full suite **7 passed**. Tree clean.

Ring hardening confirmed in source: realpath-canonicalization under worktree (catches `..`
traversal + symlink components), both old+new path of every entry (rename evasion), symlink-target
resolution, and fail-CLOSED on un-parseable entries.

---

## REQ-04 — Three cost stops fire against process groups

`tests/factory/test_cost_stops.py` — 7 passed:
- `test_budget_cap_fires_on_pgid` — per-job budget cap fires against the pgid.
- `test_timeout_sigterm_reaches_group` — wall-clock timeout, SIGTERM to the group.
- `test_ceiling_blocks_would_breach_launch` + `test_ceiling_mid_flight_breach_kills_pgid` —
  atomic daily ceiling blocks a would-breach launch AND kills active workers mid-flight.
- `test_atomic_cas_prevents_double_booking` — **real 2-thread `threading.Barrier` race**: two
  concurrent `try_reserve()` with joint caps exceeding headroom → exactly ONE succeeds; ledger
  shows $0.30 reserved not $0.60 (CAS rowcount guard). Genuine atomicity, no race.
- `test_grandchild_setsid_residual_documented` — spawns a real `os.setsid()` grandchild that
  escapes the group; after `kill_pgid_with_sweep`, asserts the escaped grandchild is dead (psutil
  sweep) **OR** an `orphan-survived` log line exists. **Residual honestly documented, not falsely
  claimed killed.**

---

## REQ-05 — Job flow + spec-first + injection

- **One-way state machine** (`test_job_store.py`): `test_illegal_backward_transition_raises`,
  `test_illegal_transition_unknown_edge`, `test_terminal_states_reject_all_transitions`,
  `test_concurrent_transitions_exactly_one_wins` — backward/illegal/terminal transitions rejected.
- **Two-stage** (`test_two_stage.py`): `test_quick_skips_stage1` (kind:quick skips stage-1),
  `test_feature_plans_spec_before_implement` + `test_persist_stage1_spec_writes_artifact`
  (feature-class emits a persisted spec), validated against a real `jsonschema.Draft7Validator`
  schema (`factory/job_schema.py`, required: status/branch/summary/test_result).
- **Injection** (`test_injection_scan.py`): `test_input_hidden_push_to_main_is_flagged` catches a
  hidden "push to main"; also case/whitespace, zero-width (post-NFKC), bidi, base64-near-eval,
  curl|sh, and output-diff merge-to-protected. Fence-wrap on flag, no-op on clean.

---

## REQ-06 — Staged vs Verified (honesty list)

| Item | Status | Note |
|---|---|---|
| In-worker-loop injection interception | **STAGED (documented residual)** | Scan runs on tool-result return (input) + on output diff. It does NOT intercept mid-model-loop; `RESIDUAL` string states this and `test_residual_documented_not_a_claim` asserts the honesty. |
| setsid-grandchild cost residual | **STAGED (documented residual)** | psutil sweep is best-effort; an escaped grandchild may survive and is LOGGED (`orphan-survived`), not falsely claimed killed. Test asserts kill-OR-log. |
| AF_UNIX socket-bind broker tests | **ENV-GATED** | `test_server_client.py:74` skips under sandbox; passes on a real host. Not a gap in the code. |
| "One real feature merges with a single approval, end to end" (first-magic) | **STAGED — component-verified, not one live integration run** | The pieces are each proven: intake→job row (single-writer), worker launch (scrubbed env, no push), gauntlet (test→review→ring/injection gate→AWAITING_APPROVAL), and merge via the REAL `ApprovalAuthority` nonce over REAL git (`test_clean_merge_succeeds_under_broker` lands "feature work" on the remote's main under one nonce-approval). What is NOT present: a single supervisor *process* that drives intake→gauntlet→merge automatically with a live `claude -p`/`codex exec` worker producing a real feature. No `Supervisor` class / tick loop exists (`grep` finds only a comment). The live "wake to a PR, approve from phone" moment needs a real host + real CLI + real approval — STAGED for a host run, not demonstrated by tests. |

**Note on the ring-gate scope (design §7, honest):** `check_diff` gates WORKER-produced worktree
diffs only. It does NOT gate a human maintainer's reviewed `broker/**` commits (e.g. the pre-existing
`broker/server.py` registry change). That is by design — the sanctioned dev path — not an evasion.

---

## BLOCKING list

**None.** No non-env-gated failure. All four safety gates (cred-wall, no-ungated-merge,
immutable-ring, cost stops) are real and falsifiable as demonstrated.

**Advisory (not blocking the P2 gate as written, which is unit-verifiable for the negative tests):**
the *positive* first-magic assertion ("delivers and merges one real feature with a single
approval, end to end") is STAGED — it requires a live host run with a real worker CLI, which
tests cannot substitute. The negative gate (no ungated merge, ring diff rejected) IS fully
verified. Recommend a single supervised live run on the `hermes-factory` host to close the
positive half before declaring first-magic.

---

## Tree state at end

`factory/worker_env.py` and `factory/immutable_ring.py`: **clean** (neuters fully reverted,
`git diff` empty). Pre-existing at session start and NOT verifier-authored: `broker/server.py`
(1-line, from commit 45dc10d99 P2-g-maint) and untracked `hermes-evolution/`. No commits made.
