# P2-fg Finisher Report — Gauntlet + Held-for-Approval Broker Merge

**Task:** P2-fg-finish (backlog / GOVERNANCE_EXEMPT) — complete + verify the gauntlet
and merge path a prior agent left mid-task (socket death). **Branch:** `factory`.
**Design source:** `docs/plans/harness/fable/p2/P2-design.md` §4.1 (gauntlet) + §4.2
(held merge + merge-policy) + §5 RED tests.

All three P2-fg suites GREEN: **27 passed** (9 gauntlet + 11 merge_policy + 7
merge_executor). The prior agent's on-disk work was correct and complete for the
four existing files; the only real gap was the missing `tests/broker/test_merge_executor.py`
(now written) and this report.

---

## REQ-01 — Merge executor logic reviewed (correct; no force-push)

`broker/executors/merge_executor.py` (244 lines) reviewed line-by-line against
design §4.2. Verdict: **correct as filled, no changes needed.**

- **Runs under the broker on approval:** `build_merge_executor(...)` returns the
  callable that `ApprovalAuthority.approve(..., executor=...)` invokes only after a
  valid nonce (`broker/approval.py:110-111`). The supervisor/assistant has no nonce.
- **Per-repo lock:** `_repo_lock(repo)` returns a process-singleton `threading.Lock`
  (guarded by `_REPO_LOCKS_GUARD` so two threads racing to create one repo's lock get
  the SAME object). The `with lock:` block spans fetch → rebase → re-test → merge →
  push as ONE critical section (review M3) — verified by test #3 below.
- **Order = fetch → rebase → re-test → merge(--ff-only) → push:** matches §4.2 exactly
  (merge_executor.py:171-233).
- **Conflict → NEEDS_ATTENTION, never force:** on rebase rc!=0 it runs `git rebase
  --abort` (leaves the worktree clean) and returns `{"status":"needs_attention",
  "reason":"rebase_conflict","forced":False}` (lines 181-190). Post-rebase re-test
  failure and non-ff merge likewise return needs_attention, never a force.
- **Reuses git_push_executor:** the push half is `build_git_push_executor(egress_cred)`
  (line 130); the merge executor calls it with `"force": False` **always** (line 223).
- **Force-push audit (grep):** the ONLY `--force` in the entire merge path is
  `git_push_executor.py:69`, gated by `if force:` where `force` is read from the row
  payload. The merge executor never sets it True. No unconditional force anywhere in
  `merge_executor.py` / `git_push_executor.py` / `gauntlet.py` (the three `force`
  references in merge_executor are all in comments or `force=False`).

**Fix made to the crashed work:** none required for the executor logic. (The single
in-progress edit found on disk was `broker/server.py:378` wiring
`build_merge_executor(creds.egress_cred)` — correct, left as-is.)

## REQ-02 — tests/broker/test_merge_executor.py (NEW, 350 lines, 7 tests)

Real broker objects throughout (`HeldStore` + `ApprovalAuthority` + the REAL
`build_merge_executor`) over **real local git repositories**. The only mocked seam is
the git-network boundary — and it is made hermetic by pushing to a **local bare** repo
(a real `git push` over file transport, no network), so the no-force guarantee runs for
real. Cases:

1. `test_clean_merge_succeeds_under_broker` — approve-with-nonce → rebase, re-test,
   merge --ff-only, push → `status=merged`; the commit really lands on the remote's
   `main` (asserted via `git log main`).
2. `test_conflicting_merge_needs_attention_never_forces` — README first-line conflict
   between base and feature → `needs_attention` / `reason=rebase_conflict`;
   `monkeypatch` wraps `subprocess.run` to **assert no `-f`/`--force`/`--force-with-lease`
   ever appears in any git argv**; the feature commit is NOT on remote main.
3. `test_post_rebase_test_failure_needs_attention` — clean rebase, failing re-test →
   `needs_attention` / `reason=post_rebase_test_fail`, no merge, `forced=False`.
4. `test_per_repo_lock_serializes_same_repo_merges` — two same-repo merges run on two
   threads; a `_slow_test` seam records concurrency; `max_concurrent == 1` proves the
   per-repo lock serializes the whole critical section.
5. `test_no_ungated_merge_without_nonce` — `approve(aid, None, ...)` raises
   `ApprovalRejected`, state stays `held`, nothing merged to the protected branch.
6. `test_forged_nonce_cannot_merge` — a nonce minted with the wrong secret is rejected;
   state stays `held`.
7. `test_ring_touching_diff_rejected_before_merge` — a `broker/approval.py`-touching
   diff makes `check_diff` raise `RingViolation` (so it can never become a merge card);
   a benign app-only diff does NOT raise (negative control — the gate is specific).

## REQ-03 — All three suites GREEN (real pytest output)

Run via the project venv (`./venv/bin/python`, `-p no:langsmith` to skip an unrelated
`xxhash`-missing plugin in a sibling site-packages):

```
tests/factory/test_gauntlet.py .........                                 [ 33%]
tests/factory/test_merge_policy.py ...........                           [ 74%]
tests/broker/test_merge_executor.py .......                              [100%]
============================== 27 passed in 3.20s ==============================
```

`tests/broker/test_registry_dispatch.py` (the P2-g-maint registry seam) also re-run:
**10 passed** — my changes did not disturb the type→executor dispatch.

**Fixes to the crashed work under REQ-03:** none — the two existing suites (gauntlet,
merge_policy) were already GREEN as found. The only failure encountered was in the NEW
merge_executor test during its own bring-up: the clean-merge case returned
`push_failed` because `git_push_executor` fail-closes without a token. Root cause was
in the TEST fixture (a local bare remote needs no real token but the executor still
demands one) — fixed in the test by injecting a `_dummy_cred` so the real push runs
against the local bare remote. **No source change** — this validated the executor's
fail-closed token gate is genuinely enforced.

## REQ-04 — Anti-weakening proofs (temporary edits, tree restored clean)

**Proof 1 — nonce bypass breaks the no-ungated-merge guarantee.** Temporarily made
`ApprovalAuthority._validate` `return True` unconditionally. Result: both
`test_no_ungated_merge_without_nonce` and `test_forged_nonce_cannot_merge` **FAILED**
("DID NOT RAISE ApprovalRejected") — a nonce-less/forged approve would then execute the
merge. Restored `broker/approval.py`; the nonce wall is real, not theater.

**Proof 2 — removing check_diff breaks the ring rejection.** Temporarily made
`factory.immutable_ring.check_diff` a no-op (`return` before scanning). Result: both
`test_ring_touching_diff_rejected_before_merge` (merge_executor) and
`test_ring_touching_diff_parked_before_approval` (gauntlet) **FAILED** — the ring diff
cleared to `AWAITING_APPROVAL` instead of `NEEDS_ATTENTION`. Restored
`factory/immutable_ring.py`; the ring gate is a real wall.

**Conflict-never-force:** proven live by test #2 (subprocess.run wrapper asserts no
force flag in any git argv during a real conflict) AND by static grep (the sole
`--force` is the payload-gated branch in git_push_executor, which the merge executor
always calls with `force=False`).

**Tree restored:** `git diff --stat broker/approval.py factory/immutable_ring.py` is
empty; `grep -rn 'ANTI-WEAKENING PROBE' broker/ factory/` finds nothing. Post-restore
re-run: **27 passed**.

## REQ-05 — This report

Written to `docs/plans/harness/fable/p2/P2-fg-report.md`.

---

## Files (all under the allowed touch-set)

| File | Lines | Status |
|---|---|---|
| `factory/gauntlet.py` | 389 | verified (unchanged — correct as found) |
| `factory/merge_policy.py` | 177 | verified (unchanged — correct as found) |
| `docs/factory/merge-policy.md` | 44 | verified (unchanged — `repo: hermes`, allow_openrouter present) |
| `broker/executors/merge_executor.py` | 244 | verified (unchanged — no force, reuses git_push) |
| `tests/factory/test_gauntlet.py` | 228 | verified GREEN (9 tests) |
| `tests/factory/test_merge_policy.py` | 100 | verified GREEN (11 tests) |
| `tests/broker/test_merge_executor.py` | 350 | **NEW — 7 tests GREEN** |
| `docs/plans/harness/fable/p2/P2-fg-report.md` | this | **NEW** |

**Not committed** (orchestrator commits). No other modules edited — the tests call the
real broker/factory objects, never modify them.
