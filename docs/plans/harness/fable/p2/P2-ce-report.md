# P2-c + P2-e CODER report — factory worker runner (env scrub) + spec-first two-stage

**Task:** P2-ce (backlog, GOVERNANCE_EXEMPT). **Branch:** `factory` (verified `git branch --show-current == factory`).
**Scope:** new files under `factory/` + `tests/factory/`; `worker/base.py` and `worker/local_subprocess.py` NOT modified (extended via composition — a `ReplacementEnvWorker` subclass). No `broker/`, `jobs/`, or gateway touched.

## Files created
| File | Role |
|---|---|
| `factory/worker_env.py` | The replacement-whitelist env scrubber (P0-1 fix). `scrub_env()` builds env from `{}`; `assert_no_egress()` is the falsifiable pre-launch wall. |
| `factory/job_schema.py` | Worker-result JSON schema `{status,branch,summary,test_result}` + `parse_worker_output()` (malformed → `WorkerResultInvalid`, no crash). |
| `factory/worker_runner.py` | Worktree-FIRST run, least-privilege argv (no `git push`, no `--dangerously-skip-permissions`, `--strict-mcp-config`), replacement-env launch, schema-validated collect. |
| `factory/two_stage.py` | Spec-first two-stage (P2-e): `feature` → stage-1 spec (persisted, held) before implement; `quick` skips stage-1. |
| `tests/factory/test_worker_runner.py` | 20 tests (env wall, argv, worktree-first, push-fails, schema). |
| `tests/factory/test_two_stage.py` | 10 tests (plan, budgets, persist/validate). |

## RED → GREEN evidence
**GREEN (final):** `pytest tests/factory/ -q` → **30 passed** (20 worker_runner + 10 two_stage).
Run with `--basetemp` outside the repo tree because this host BLOCKS `.git` creation inside the hermes repo tree ("Operation not permitted" on `.git/config`) — a macOS/sandbox restriction, NOT a code defect. Proof it is environmental: the pre-existing `tests/agent/test_coding_context.py` (git-init based) fails 36 tests under the same runner right now for the identical reason. All 30 pass when basetemp is writable (`/Users/yklin/Code/.factory-bt`).

**RED (P0-1 wall is falsifiable):** temporarily reverting `scrub_env` to the v1 overlay `{**os.environ, ...}` makes exactly the two env tests FAIL:
- `test_worker_env_lacks_seeded_egress_tokens` → FAILED (`EgressKeyLeaked` raised — GITHUB_TOKEN present)
- `test_scrub_env_never_inherits_os_environ` → FAILED (`assert 'SOME_RANDOM_INHERITED' not in {... inherited env ...}`)
Reverting to the replacement design → both PASS. This is the falsifiable proof the scrub cannot be weakened to inherit `os.environ` without a test going red.

## Requirement evidence
- **REQ-01 (replacement-whitelist scrub, P0-1):** `worker_env.scrub_env` builds from `{}` (never reads `os.environ`); whitelist = `PATH`(fixed minimal `/usr/bin:/bin:/usr/sbin:/sbin` + resolved CLI dir), temp `HOME` (mktemp), `TMPDIR`, `LANG`/`LC_ALL=C.UTF-8`, `TERM=dumb`, + ≤1 broker-injected model `*_API_KEY`. `assert_no_egress` fires on any egress-denylist key OR any key ∉ `ALLOWED_KEYS` (whitelist-subset is strictly stronger). Test `test_worker_env_lacks_seeded_egress_tokens` seeds `GITHUB_TOKEN`+`TELEGRAM_BOT_TOKEN` in `os.environ` → constructed worker env lacks both; subset holds; exactly one `*_API_KEY`; HOME ≠ real. **VERIFIED.**
- **REQ-02 (worktree-first, no push):** `WorkerRunner.launch` runs `git worktree add -b factory/<repo>/<id>-<slug>` BEFORE building argv/env/launch (order asserted in `test_worktree_created_before_launch` via the recorded worktree + branch). No `Bash(git push)` in the allowlist (per-repo allowlist also strips a smuggled push tool); `test_worker_push_attempt_fails_no_token_no_tool` runs a real `git push` from the worktree with the scrubbed env → rc != 0 (no remote, no `*_TOKEN`). **VERIFIED.**
- **REQ-03 (schema'd JSON):** `job_schema.parse_worker_output` extracts+validates the JSON (handles log-noise-wrapped output); malformed/missing-field/bad-branch-pattern/branch-mismatch each raise `WorkerResultInvalid`; `WorkerRunner.collect` maps it to a caught error, never a crash. 8 schema tests. **VERIFIED.**
- **REQ-04 (two-stage spec-first, P2-5):** `two_stage.plan_stages("feature")` → `[SPEC, IMPLEMENT]`; `plan_stages("quick")` → `[IMPLEMENT]` (`skips_stage1 True`). `spec_run_spec` uses the small budget tier (`STAGE1_BUDGET_USD=0.50` < implement budget) and a distinct `-spec` slug; `persist_spec_artifact` validates + atomically writes the stage-1 spec (temp + `os.replace`) BEFORE stage-2 — malformed spec → `SpecArtifactInvalid`/`WorkerResultInvalid`, stage-2 not reached. **VERIFIED.**
- **REQ-05 (RED-first; mock only the subprocess boundary):** the only stub is `RecordingWorker` (subclasses `LocalSubprocessWorker`, records the `WorkerSpec` instead of spawning `claude -p`). The scrub, worktree add, argv grep, schema, and two-stage logic all run as real plain code. No real network claude/codex call in any unit test. **VERIFIED.**

## Design fidelity notes
- Env-replace mechanism: used design §2.3(B) option (i)-style **composition** — `ReplacementEnvWorker` overrides only the env-merge of `launch()` so the subprocess env == the scrubbed replacement, WITHOUT editing `worker/local_subprocess.py`. The step-2 `assert_no_egress` belt also ships (runs on the exact dict pre-launch).
- `WorkerRunner` gained an optional `git_env` param (default None → inherits the supervisor's real git env, matching production; hermetic tests inject a pinned env). This is the only concession to testability and does not weaken any wall.

## Escalations / UNVERIFIED
- REQ-01 escalate_if ("worker needs an egress key"): confirmed it does NOT — the single admitted key is a model *_API_KEY (inference-only; cannot push/send). No real need for an egress key found.
- Environmental: this host cannot create `.git` inside the repo tree, so `scripts/run_tests.sh tests/factory/` shows 4 git-dependent failures that are false negatives (same block hits pre-existing suites). GREEN confirmed with writable basetemp. A CI/host without this restriction will pass under the plain runner (the 16 non-git tests already pass everywhere).
