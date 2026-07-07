# P2-SUPERVISOR — Coder Report (backlog mode, GOVERNANCE_EXEMPT)

**Task:** the thin, single-job SUPERVISOR that chains the built P2 pieces into ONE runnable
end-to-end loop — intake → job → scrubbed worker in a worktree → gauntlet → immutable-ring +
injection check → merge card enqueued via broker → (nonce approval) → merge. This closes the
POSITIVE half of P2's first-magic gate the verifier found STAGED (`P2-verification.md §REQ-06`:
"No `Supervisor` class / tick loop exists"). NO AI in the supervisor loop — plain code driving
the pieces; the AI lives inside the worker subprocess only.

**Branch:** `factory` (verified). **Files (mine, disjoint):**
- `factory/supervisor.py` (new — the driver)
- `tests/factory/test_supervisor.py` (new — RED-first)
- this report

**Pieces WIRED (called, NOT modified):** `factory/intake.py`, `factory/job_store.py`,
`factory/two_stage.py`, `factory/worker_runner.py`, `factory/gauntlet.py` (which itself already
runs `factory/immutable_ring.check_diff` + `factory/injection_scan.scan` on the worker diff),
`factory/injection_scan.py` (chokepoint-1 INPUT fence), `broker/held_store.HeldStore`,
`broker/approval.ApprovalAuthority`, `broker/executors/merge_executor.build_merge_executor`.

---

## REQ evidence

| REQ | Verdict | Evidence |
|---|---|---|
| REQ-01 end-to-end one job (sole writer → worker → gauntlet → held merge → merge) | DONE | `test_happy_path_merges_after_single_approval` drives intake→gauntlet→held merge and, after a nonce approval, the merge LANDS on a local bare "remote" (`assert after != before`) and the job → `DONE`. `test_single_writer_idempotent_intake`: both calls converge on one `enqueue_job`; a re-tick of the same source does NOT duplicate (idempotency guard). |
| REQ-02 gates enforced IN the loop | DONE | `test_ring_touching_job_never_reaches_approval`: a worker diff touching `broker/**` → job ends `NEEDS_ATTENTION`, NO merge card enqueued. `test_gauntlet_fail_ends_needs_attention`: a deterministic test failure with no fix worker → `NEEDS_ATTENTION`, no merge card. `test_input_injection_is_fenced_and_flagged`: chokepoint-1 fences an injected spec before the WorkerSpec is built. |
| REQ-03 single-approval invariant | DONE | `test_no_nonce_no_merge`: `approve_merge(..., nonce=None)` raises `ApprovalRejected`; the remote does NOT move and the job stays `AWAITING_APPROVAL`. `test_exactly_one_approval_gate_between_intake_and_merge`: exactly ONE held action total on the happy path (the merge). |
| REQ-04 quick vs feature routing | DONE | `test_quick_skips_spec_stage`: no stage-1 spec artifact for a quick job. `test_feature_produces_stage1_spec_before_implement`: a feature job persists a schema'd stage-1 spec BEFORE the implement stage. |
| REQ-05 STAGED live-run procedure | DONE | Appended SG-P2-1/2/3 to `docs/plans/harness/fable/STAGED-GATES.md` — exact owner commands for one real first-magic job (real `claude -p` worker, real repo, real Telegram approval), a real ring rejection, and a real cost-stop, all gated on the broker being live (SG-P1a-2/3). |
| REQ-06 RED-first, real objects, mock only the worker CLI + net push, anti-weakening | DONE | RED shown (import error). Real `JobStore`/`Gauntlet`/`immutable_ring`/`injection_scan`/`HeldStore`/`ApprovalAuthority`/`merge_executor` over REAL local git + a local bare remote. Two gates proven load-bearing (neuter→fail→restore). |

**Final suite:** `9 passed in 2.75s` (supervisor). Full factory suite `135 passed` (126 prior + 9 new; zero regressions).

```
test_happy_path_merges_after_single_approval PASSED
test_no_nonce_no_merge PASSED
test_exactly_one_approval_gate_between_intake_and_merge PASSED
test_ring_touching_job_never_reaches_approval PASSED
test_gauntlet_fail_ends_needs_attention PASSED
test_input_injection_is_fenced_and_flagged PASSED
test_quick_skips_spec_stage PASSED
test_feature_produces_stage1_spec_before_implement PASSED
test_single_writer_idempotent_intake PASSED
```

---

## RED first (TDD evidence)

Before `factory/supervisor.py` existed:
```
$ python -c "import factory.supervisor"
ModuleNotFoundError: No module named 'factory.supervisor'
```
The test file imports `from factory.supervisor import Supervisor, IntakeItem` — collection
failed until the module existed. GREEN after implementation (output above).

> Test-runner note: the repo venv has a `langsmith` pytest plugin that fails to import
> (`ModuleNotFoundError: xxhash`) and swallows collection with "No tests collected". Run with
> `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` (or `scripts/run_tests.sh`, which sets its own plugin env)
> to get real output. This is a pre-existing repo condition, not introduced here.

---

## The mock boundary (anti-theater — exactly ONE thing stubbed per side)

- **Worker CLI subprocess** (`claude -p`/`codex exec`): stubbed via `WorkerRunner`'s injectable
  `worker_impl` (a `ScriptedWorker` that does the file-edit + LOCAL git commit the real worker
  would, so the downstream gauntlet sees a REAL git diff, then returns the schema'd JSON the real
  CLI emits). Everything downstream of the worker — the diff, the ring gate, the injection scan,
  the merge — is real.
- **Network push:** the merge executor pushes to a **local bare repo** standing in for the remote
  — real `git push` to a `file://`-style path, no network, no token. The M2 no-egress-token
  invariant holds: the worker's scrubbed env has no `GITHUB_TOKEN`, and the supervisor never
  pushes; the executor's push (in the broker) is the only egress.

Everything else is the REAL object: `JobStore` (real SQLite state machine), `Gauntlet` (real
test→review→ring/injection gate), `HeldStore` + `ApprovalAuthority` (the REAL HMAC nonce wall),
`build_merge_executor` (real rebase→re-test→merge→push over real git).

---

## Two gates PROVEN falsifiable (neuter → FAIL → restore)

**1. Single-approval / nonce wall.** Neutered `approve_merge` to mint its own nonce
(`self._authority.approve(action_id, self._authority.mint_nonce(action_id), ...)`):
```
test_no_nonce_no_merge FAILED — Failed: DID NOT RAISE <class 'broker.approval.ApprovalRejected'>
```
Restored (`action_id, nonce`) → `test_no_nonce_no_merge PASSED`. The nonce wall is load-bearing:
without it a merge would land with no approval.

**2. Immutable-ring gate.** Neutered `factory/immutable_ring.is_ring_path` to `return False`
(the ring stops rejecting):
```
test_ring_touching_job_never_reaches_approval FAILED
  - NEEDS_ATTENTION
  + AWAITING_APPROVAL
```
With the ring dead, a `broker/**`-touching worker diff sailed to `AWAITING_APPROVAL` (a merge
card would be enqueued). Restored → `9 passed`, `git diff factory/immutable_ring.py` empty. The
ring gate is load-bearing: it genuinely stops a ring-touching job from ever reaching approval.

---

## INTERFACE MISMATCH FOUND (REQ-01 escalate_if — documented, not edited around by hacking)

**The merge executor cannot `checkout base` in a git LINKED worktree.** `build_merge_executor`
(`broker/executors/merge_executor.py:207`) runs `git checkout base` then `git merge --ff-only
branch` inside the card's `worktree`. But the worker runs in a git *linked worktree* created by
`WorkerRunner` (`git worktree add`), and git REFUSES `checkout main` in a linked worktree when
`main` is checked out in the primary tree:
```
fatal: 'main' is already used by worktree at '.../repo'
```
The executor does NOT check the `checkout` return code (it only checks the `merge` rc), so with
the card pointed at the worker's worktree the merge became a SILENT NO-OP — the executor returned
`status: merged` with `Everything up-to-date`, but the remote never advanced. This surfaced as
the first RED on the happy-path test (`AssertionError: remote main did not advance`,
`before == after`).

**Resolution — supervisor-side, executor UNMODIFIED (per "call, don't edit").** The supervisor
prepares a DEDICATED merge checkout (`Supervisor._prepare_merge_worktree`): it clones the worker's
worktree locally (carrying the branch commits — no push), materializes a LOCAL `base` branch, and
repoints `origin` at the true remote. In that clone `base` IS checkable-out, so the executor's
`checkout base; merge --ff-only` fast-forwards correctly and the push lands. This is legitimate
supervisor responsibility (preparing the merge context); the executor's contract
(`{repo, branch, base, worktree, remote}`) is honored exactly. The finding is worth flagging for
a future maintainer commit: the executor could check the `checkout` rc and fail-loud instead of
silently no-op'ing — a one-line robustness fix inside the ring, out of scope here.

No other interface mismatches. All other pieces composed cleanly through their public APIs.

---

## Constraints honored

- Touched ONLY `factory/supervisor.py`, `tests/factory/test_supervisor.py`, this report, and the
  shared `STAGED-GATES.md` (REQ-05). Did NOT modify any wired piece.
- `broker/server.py` shows modified in `git status` — that is the PRE-EXISTING P2-g-maint commit
  (`45dc10d9`, per `P2-verification.md` §Tree state), NOT this session.
- NO AI in the supervisor loop (grep for anthropic/openai/claude -p/codex exec/model_tools in
  `factory/supervisor.py` → none). The merge goes through the broker nonce-approval. Never
  force-pushes (the executor never sets `force`; the supervisor never pushes at all).
- 5-line ABOUTME on the module; 3+ line Purpose/Usage/Gotchas comments on the tick/driver methods.
- Did NOT commit/push. Temp debug scripts removed.
