# ABOUTME: Adversarial review of P2-design.md (one worker, end to end).
# ABOUTME: Verdict + reuse-claim verification + attack tables on merge gate,
# ABOUTME: immutable ring, injection scan, cost stops, worker scrub, and RED tests.
# ABOUTME: Every CONFIRMED carries the file:line opened; UNVERIFIED = not opened.
# ABOUTME: Read-only pass on branch `factory`; findings artifact only, no code.

# P2 Design Review — Adversarial (investigate-audit)

**Reviewer stance:** paranoid. This worker autonomously edits and merges code; the gates are the wall between "helper" and "foot-gun." Everything below was opened against real code on branch `factory` (verified `git rev-parse --abbrev-ref HEAD` == `factory`).

## Verdict: **SHIP-WITH-FIXES**

The design's reuse claims are almost entirely accurate — the broker approval machinery, worker ABC, and cost primitives exist as described, and the merge genuinely rides the existing `enqueue_action → held → nonce-approve → execute` path (no side merge path found). But three fixes are load-bearing before implementation, and one is a **P0 that the design does not close**: the worker inherits the supervisor's full `os.environ`, so the credential scrub is only as strong as the (flag-gated, off-by-default) P1a starving — a worker can inherit egress creds today.

**Counts:** P0 = 2 · P1 = 4 · P2 = 3

**Top 3 (each with the exact required fix):**
1. **[P0-A] Worker inherits egress creds via `{**os.environ}` merge.** `worker/local_subprocess.py:93` builds `merged_env = {**os.environ, **spec.env}`; `spec.env` is an *overlay*, not a replacement. The P1a starving that removes `GITHUB_TOKEN`/`TELEGRAM_BOT_TOKEN`/etc. from `os.environ` is gated behind `HERMES_BROKER_EGRESS_STARVE=1` + `HERMES_BROKER_ROUTE=1` (`tests/broker/test_bypass_impossibility_gate.py:24-25`) and is not on by default. **Fix:** the design's `worker_env.scrub_env` must produce a *replacement* env (whitelist-only: `PATH`, temp `HOME`, `--model` keys the worker legitimately needs), and `worker_runner` must launch with a mechanism that does not fold in the parent `os.environ` — either pass a full replacement dict *and* have `LocalSubprocessWorker` support env-replace (it currently only overlays), or assert at launch that no egress key is present in the constructed env. Add a RED test that seeds `GITHUB_TOKEN` in the supervisor env and asserts the worker argv/env contains none.
2. **[P0-B] Injection scan's *pre-model* enforcement point is UNVERIFIED; only the post-hoc belt scan is guaranteed.** The design itself flags this (§6) but still lists REQ-03a's done-when as "flagged *before* the model sees it." The worker-side result filter for `claude -p` has no confirmed interception seam. **Fix:** either (a) demote REQ-03a's "before the model sees it" to "the belt post-run diff scan catches it before TEST and before the ring gate" and make that the falsifiable contract, or (b) prove the `claude -p` hook empirically in P2-d before claiming pre-model neutralization. Do not ship a done-when that asserts a property (pre-model) the design cannot yet enforce.
3. **[P1-C] The merge executor injection seam the design assumes does not exist in `BrokerServer`.** `broker/server.py` holds a *single* `message_executor` (`:63,:69`) and both `_rpc_enqueue_action` (`:237`) and `_rpc_approve` (`:255`) call `self._executor(row)` **with no dispatch on `row["type"]`**. The design (§0, §4.2) says "P2 adds a second executor (merge) alongside the message executor via the same injection seam" — but there is no by-type routing seam today; adding one *modifies `broker/server.py`*, which is inside the immutable ring (`broker/**`). **Fix:** specify the type→executor registry explicitly (a `Dict[str,Executor]` on the server, keyed by action type), acknowledge that wiring it is a broker-source change that must land in P1a/P2-g under review (not via a worker diff), and confirm the ring gate exempts sanctioned maintainer commits (it only inspects *worker* diffs — make that boundary explicit).

---

## REQ-01 — Reuse-claim verification

Every cited file:line opened. Result table:

| Claim | Loc | Status | Note |
|---|---|---|---|
| `BrokerClient.enqueue_action(...)` → `{action_id, disposition}`; no `send()`/`git_push()`/`merge()` verb | `broker_client.py:93-119`, funnel `:4-6` | **CONFIRMED** | Exactly one egress verb; `_call` dispatches; no merge/send/push method exists. |
| `BrokerClient.approve/reject`; assistant has no nonce | `broker_client.py:124-134`; wall `broker/approval.py:78-101` | **CONFIRMED** | `approve` requires nonce; `_validate` rejects None/forged; assistant path has no nonce source. |
| `HeldStore` durable SQLite, commit-before-ack, one-way machine, untruncated payload, WAL+`synchronous=FULL` | `broker/held_store.py:21-27,68-74,76-115` | **CONFIRMED** | `_ALLOWED` map one-way; `enqueue` commits before returning aid (`:114`); `get` returns full payload; PRAGMAs present (`:71-72`). |
| `ApprovalAuthority` HMAC nonce, constant-time validate, burn-on-use, idempotent-by-action_id | `broker/approval.py:27-118` | **CONFIRMED** | `mint_nonce` HMAC(secret, action_id∥salt) `:56-59`; `compare_digest` `:76`; burn before execute `:101`; idempotent cached result `:105-106`. |
| `BrokerServer` unix-socket JSON-RPC, `_CLIENT_METHODS`, `_default_safe_lane`, injected `message_executor` | `broker/server.py:28,31-44,63` | **CONFIRMED (with caveat)** | All present. **Caveat:** single executor, NO by-type dispatch — see P1-C. `merge` NOT wired anywhere yet. |
| `merge` is a `_KNOWN_TYPES` member | `broker/safe_lane.py:15` | **CONFIRMED** | `_KNOWN_TYPES = {"message","git_push","git_push_force","pm_os_write","merge"}`. |
| `SafeLane.evaluate` 5 conditions; C4 fails on empty `allowed_origins`; merge holds by default | `broker/safe_lane.py:63-109`; default `broker/server.py:31-44` | **CONFIRMED** | `c4 = origin in self._allowed_origins` `:98`; `_default_safe_lane` passes `allowed_origins=set()` → every merge holds. |
| `build_git_push_executor` over `{repo,remote,ref,force}`; fail-closed w/o token; `--force` only if row sets it | `broker/executors/git_push_executor.py:13-87` | **CONFIRMED** | No token → refuse `:60-65`; `--force` only if `spec.get("force")` `:57,68`; explicit argv, no shell. |
| `Worker` ABC `launch/check/kill/collect_result`; specs/handles/results; no pgid leak into supervisor | `worker/base.py:44-102,105-172` | **CONFIRMED** | Four abstract methods; dataclasses match; docstring enforces "no pgid in supervisor code" `:12-15`. |
| `LocalSubprocessWorker` setsid PG leader, killpg SIGTERM→SIGKILL, mtime liveness, documented setsid-grandchild residual | `worker/local_subprocess.py:78-152,206-271,154-204` | **CONFIRMED (with caveat)** | `os.setsid` `:105`; killpg SIGTERM `:232` → SIGKILL `:257`; mtime `:198-204`. **Caveat:** grandchild setsid-escape residual `:215-221` — relevant to REQ-04, see below. |
| `RemoteWorker` stub, `pgid=0`, same interface | `worker/base.py:77-78` (documented); `worker/remote_stub.py` | **UNVERIFIED (file not opened)** | ABC documents `pgid=0` for remote; stub file not read this pass. Low risk — interface is in base.py. |
| Hook seam: `post_tool_call` audit-only (return ignored); `pre_tool_call` returns block-dict | `plugins/tool-registry-guard/__init__.py:356-395,468-521,546-548` | **CONFIRMED** | `_post_tool_call` returns `None`, only writes JSONL `:507-518`; `_pre_tool_call` returns block-dict `:388`. Design's escalation (scan can't ride post_tool_call) is sound. |
| `agent/account_usage.py` per-account snapshot, NOT per-job dollars | `agent/account_usage.py:25-46,104-113` | **CONFIRMED** | `AccountUsageWindow`/`Snapshot` carry `used_percent`/`reset_at` per provider window; no per-job dollar field. Design's "meter is genuinely new" holds. |
| `kanban_db` CAS transitions, 15-min claim TTL, `VALID_STATUSES`, separate DB | `hermes_cli/kanban_db.py:102,171 (DEFAULT_CLAIM_TTL),515 (kanban_db_path)` | **CONFIRMED** | `VALID_STATUSES` `:102`; `DEFAULT_CLAIM_TTL_SECONDS = 15*60` `:171`; CAS per-board `:61-68`; separate DB path `:515`. Design's "separate DB, don't fold into VALID_STATUSES" is correct. |
| P1a starving relocates EGRESS creds; `GITHUB_TOKEN` broker-only | `P1a-design.md:40,198,228-236`; gate `tests/broker/test_bypass_impossibility_gate.py` | **CONFIRMED (with the P0-A caveat)** | Starving is real BUT flag-gated (`HERMES_BROKER_EGRESS_STARVE=1`), not default-on. See P0-A. |

**Merge-routes-through-broker-approval:** **CONFIRMED.** `_rpc_approve` (`server.py:246-256`) is the only approve path, it is nonce-gated via `ApprovalAuthority.approve` (`approval.py:78`), and the client stub exposes no merge/push verb. A `type="merge"` action would enqueue → hold (C4 empty) → require a Telegram-delivered nonce to execute. **No side merge path found** in `gateway/` or `broker_client.py`. The one gap is *executor wiring* (P1-C), not the approval route.

---

## REQ-02 — Attack the merge gate (no ungated merge to a protected branch)

| # | Attack | Result | Severity |
|---|---|---|---|
| M1 | Worker pushes directly from its worktree | **BLOCKED.** Two independent walls: (a) `Bash(git push)` absent from `--allowedTools` (§2.3, tool layer); (b) cred-absence — no `GITHUB_TOKEN` in worker env *iff* starving is active. **BUT** wall (b) is the P0-A hole: if the supervisor env is not starved, the worker inherits the token via `{**os.environ}` (`local_subprocess.py:93`) and could `curl`/`git push` if it also has a shell tool. The allowlist wall (a) still holds *if* the allowlist truly excludes shell-general `Bash(...)`. | **P1** (P0-A downgrades to P1 here *because* the allowlist is a second wall; the token leak is scored P0 under REQ-04) |
| M2 | Supervisor calls `git` directly instead of the broker | Design says "plain code supervises; broker pushes." The supervisor is new code (`factory/supervisor.py`) — **nothing structurally prevents it from shelling out to `git push`** other than discipline. The design does not add a guard that the *supervisor process itself* is cred-starved. If the supervisor runs with broker env (to build merge cards) it holds the token. **Fix:** the supervisor must never hold the push token; the merge executor (broker process) is the only holder, and the supervisor reaches it only via `enqueue_action`. State this as an invariant + test (supervisor env has no `GITHUB_TOKEN`). | **P1** |
| M3 | Rebase→retest→merge window: a second job's changes sneak in | The design serializes per-repo via a broker-side lock (§4.2) and re-tests *after* rebase in a throwaway worktree. This closes the classic TOCTOU: the merge happens under the lock, post-rebase, post-retest. **Adequate** — provided the lock is held across rebase+retest+merge+push as one critical section (design says so). Verify the lock is not released between retest and push. | **P2** (design correct; add a test that the lock spans the whole section) |
| M4 | Conflict path forces a push | **BLOCKED.** Conflict → release lock → NEEDS_ATTENTION, never force (§4.2). Backed by real code: `git_push_executor` only forces if `spec["force"]` is set (`:57,68`), and the merge executor never sets it. Design's "never force" holds *as long as the merge executor never writes `force:true`* — add a test asserting the merge card payload never contains `force`. | **P2** |
| M5 | Ungated merge to protected branch via disposition confusion | **BLOCKED.** `_default_safe_lane` has empty `allowed_origins` → C4 always False → every `merge` holds (`safe_lane.py:98`, `server.py:44`). The only auto_sent path (`server.py:232-240`) requires `all(conditions)` True, impossible with empty origins. P2 has no trust tiers. **Solid.** | — |
| M6 | `protected_branches` in `merge-policy.md` is advisory, not enforced | The design lists `protected_branches: [main, master]` but the *enforcement* of "ungated merge to these is impossible" comes from the held+ring gates, not from the policy field. The field itself is decorative in P2. That's acceptable *if* the ff-only/rebase flow targets the base branch and every merge holds — which it does. Note it's belt, not the wall. | **P2** |

**Escalation check:** No ungated merge/push path found *at the gate level*. The one real ungated-push risk is M1/M2 via the P0-A env-inheritance hole — scored as P0 under REQ-04 (secret inheritance) rather than double-counted here.

---

## REQ-03 — Attack the immutable ring + injection scan

### (a) Immutable ring bypass table

| # | Attack | Result | Severity |
|---|---|---|---|
| R1 | Worker diff edits `broker/approval.py` and merges | **BLOCKED (Layer 2).** `immutable_ring.check_diff` scans changed-path list at submission; `broker/**` matches → reject → NEEDS_ATTENTION. Plain code, pre-human. Backed by the design's stated path-list. | — |
| R2 | Rename a ring file, then edit (path no longer matches literal) | **UNADDRESSED.** The design's `RING_PATHS` is a path *prefix/glob* denylist over the diff's changed-path list. A rename shows as delete-old + add-new; if the check only matches the *new* path against `RING_PATHS` it may miss a diff that *deletes* `broker/server.py` (the old path matches, so a delete is caught) but a *move* to a non-ring path that later gets imported is subtler. **Fix:** the gate must reject any diff whose *old-path OR new-path* touches a ring path, and must treat renames-out-of-ring as ring-touching. Add a rename RED test. | **P1** |
| R3 | Symlink / `../` traversal: worker writes through a symlink or `..` to reach a broker file | **UNADDRESSED.** Layer 1 (FS scope exclude) is described as "allowlist + a worktree symlink/ownership guard" but the guard is not specified. A worker that can create a symlink inside its worktree pointing at `../../broker/server.py` and write through it defeats a naive path-prefix check. Layer 2 (diff scan) *would* still catch the resulting diff — **so R3 is contained by Layer 2**, but only if Layer 2 resolves symlinks/`..` to real paths before matching. **Fix:** Layer 2 must canonicalize (realpath) each changed path before matching `RING_PATHS`. Add a traversal RED test. | **P1** |
| R4 | Edit a file that *imports* the ring (e.g. a shim that re-exports `broker.approval`) | **UNADDRESSED but low.** Editing an app file that imports a broker module does not modify the broker module; the import target is still ring-protected. The risk is a worker adding a *new* module that monkeypatches broker internals at runtime. That new file is app-code (passes the ring) but could patch `ApprovalAuthority`. **Fix:** the ring should also forbid diffs that add code importing-and-reassigning broker internals — hard to detect statically; at minimum note it as a residual and rely on the broker running in a *separate process* (a worker-added monkeypatch in the worktree cannot reach the broker process). Actually **the separate-process boundary contains this** — confirmed `broker/server.py:275` runs as its own PID. Low. | **P2** |
| R5 | Edit the gate's own path-list (`factory/immutable_ring.py`) to remove entries | **BLOCKED (self-protecting).** Design includes `factory/immutable_ring.py`, `factory/cost_stops.py`, `factory/injection_scan.py`, `factory/worker_env.py` in `RING_PATHS` (§3.2). A diff weakening the gate touches a ring path → rejected. **Good** — this is the critical self-reference and the design got it right. Verify the path-list literally contains itself (RED test). | — |
| R6 | FS-scope-exclude (Layer 1) is advisory, not real | **PARTIALLY TRUE.** For subprocess workers, Layer 1 = `--allowedTools` allowlist + an unspecified worktree guard. The design honestly labels Layer 1 as best-effort and makes **Layer 2 the wall** ("runs even if Layer 1 were somehow bypassed"). This is the right posture — the wall is plain-code diff rejection at submission, not the AI's tool scope. **Adequate** as long as Layer 2 is airtight (see R2/R3 fixes). | **P2** |

**Ring verdict:** the *wall* (Layer 2 submission-gate diff rejection) is architecturally sound and self-protecting, but **evadable at the margins** (renames R2, traversal R3) until the gate canonicalizes paths and checks both old+new path. Not a clean pass — P1 fixes required.

### (b) Injection scan bypass table

| # | Attack | Result | Severity |
|---|---|---|---|
| I1 | Hostile fetched content reaches the model because the scan runs AFTER exposure | **THE CORE RISK — design admits UNVERIFIED (§6).** Two enforcement points: (1) worker-side result filter at tool-result return — **interception seam for `claude -p` NOT confirmed**; (2) belt post-run diff scan — guaranteed but runs *after* the worker has already reasoned over the content. So in the current design, the *guaranteed* scan (belt) runs after model exposure; the *pre-model* scan (worker-side filter) is unproven. **This means REQ-03a's done-when ("before the model sees it") is not yet satisfiable.** | **P0-B** |
| I2 | Encoding evasion (base64, hex, unicode homoglyphs) slips past regex | Design lists base64-adjacent-to-eval and zero-width/bidi patterns, but a regex denylist is inherently incomplete against novel encodings. **Acceptable as belt** (the design calls it plain regex, not a wall), but do not oversell it. The real containment for injected "push to main" is that the worker *has no push tool and no token* (defense in depth) — so even a successful injection cannot push. **Fix:** frame the scanner as a detector/flagger, and lean on tool-absence as the actual wall. | **P1** |
| I3 | Content the scanner doesn't pattern-match (novel jailbreak phrasing) | Same as I2 — regex misses are inevitable. Contained by tool-absence + the ring + the held merge. **P2** (inherent limitation, honestly scoped). | **P2** |
| I4 | Injected content triggers a *ring/cost* edit rather than a push | Even if the model obeys "disable the safe-lane," the worker cannot edit `broker/**` (ring Layer 2) or the cost-stop files (ring), and cannot push (tool/cred absence). **Contained by the ring**, assuming R2/R3 fixes land. | **P2** |

**Injection verdict:** the *guaranteed* scan runs after model exposure; the pre-model scan is unverified. **P0-B.** The saving grace is that injection cannot *act* (no push tool, no token, ring-protected guards) — so the blast radius of a missed injection is bounded to "worker wastes budget / writes a bad diff that the gauntlet + ring + held-merge catch." That bounding is real and worth stating, but it does not let the design claim pre-model neutralization it hasn't proven.

---

## REQ-04 — Attack cost stops + worker scrub

### Cost stops

| # | Attack | Result | Severity |
|---|---|---|---|
| C1 | Worker forks a grandchild that calls `os.setsid()`, then budget/timeout fires | **ESCAPES.** `worker/local_subprocess.py:215-221` documents exactly this residual: a grandchild that calls `os.setsid()` leaves the process group and **survives `killpg`**. All three cost stops target `handle.pgid` via `kill(handle)` → `os.killpg`. So a setsid-escaping grandchild outlives every cost stop and keeps spending (if it holds a model key — which via P0-A it may). The design (§4.3) claims each stop "kills the worker *and every child* in one shot" — **overstated.** The base.py mitigation note (`:219`) says "P2: supervisor scans process tree after kill" but the P2 design does not include that scan. **Fix:** add the psutil/process-tree sweep the P1a residual defers to P2, or state the residual explicitly in §4.3 and accept that `claude -p`/`codex exec` don't self-daemonize (which is the real reason it's low-probability). | **P1** (low probability for the actual worker CLIs, but the design claims completeness it doesn't have) |
| C2 | Per-job meter misses subprocess spend (token counts parsed from `--json`) | The meter re-derives `cost_so_far_usd` from token counts in the worker's own `--json`/stream output. **A grandchild's independent API calls would not appear in the parent's `--json` stream** — so subprocess spend is invisible to the per-job meter. Contained only by the wall-clock timeout and daily ceiling. **Acceptable** for `claude -p` (single process, reports its own tokens) but note the meter is not a true cgroup-style accounting. | **P2** |
| C3 | Daily ceiling not atomic under concurrency (P3 runs parallel) | Design persists `spent_today`/`daily_budget` to the job-store DB and checks `reserved + spent ≤ ceiling` before launch. **In P2 (single worker) this is fine.** For P3 parallelism the check-then-launch is a read-modify-write race: two ticks could each see room and both launch. Design punts concurrency to P3 but the ledger table lands here. **Fix:** make the reserve step a single atomic `UPDATE ... WHERE spent+reserved+cost <= ceiling` (CAS, mirroring kanban) *now*, so P3 inherits atomicity for free. | **P1** |
| C4 | Budget cap relies only on `--max-budget-usd` CLI flag | Design uses both the CLI-native cap AND the tick meter (belt). If `--max-budget-usd` is unsupported/ignored by a CLI version, the tick meter still fires. **Adequate** (probed present in P0.2 per §2.2). Verify the probe actually asserted it. | **P2** |

### Worker scrub

| # | Attack | Result | Severity |
|---|---|---|---|
| S1 | Worker inherits egress creds from the supervisor's `os.environ` | **P0-A — the headline finding.** `local_subprocess.py:93`: `merged_env = {**os.environ, **spec.env}`. `spec.env` overlays; it cannot *remove* an inherited var. The design's `scrub_env → dict` (§2.3) sets temp HOME + adds keys but never strips inherited egress creds. Those creds are absent from `os.environ` **only when P1a starving is active**, which is flag-gated (`HERMES_BROKER_EGRESS_STARVE=1`, `tests/broker/test_bypass_impossibility_gate.py:24`) and **off by default**. So on a default host, the worker inherits `GITHUB_TOKEN`, `TELEGRAM_BOT_TOKEN`, MS Graph secret, etc. **Fix (mandatory):** `scrub_env` must build a *replacement* env (whitelist), and the runner must launch with that replacement — not an overlay. Since `LocalSubprocessWorker` only overlays, either extend it to support env-replace or have `worker_runner` assert the constructed env contains zero egress keys before launch. RED test: seed `GITHUB_TOKEN` in supervisor env → worker env has none. | **P0** |
| S2 | Temp HOME doesn't cover keychain / broker socket reach | Temp HOME blocks `~/.hermes/.env`, `~/.ssh`, dotfiles, and the broker socket at `~/.hermes/broker/broker.sock` (§2.3). **Good** — but macOS keychain items unlockable by the login session are not gated by HOME. The design inherits P1a's UNVERIFIED keychain note (§6). Acceptable to defer, but the worker running in the *user's login session* could reach keychain-backed creds regardless of HOME. **Fix:** note this residual explicitly; ideally run the worker under a launchd context without keychain unlock. | **P1** |
| S3 | `--dangerously-skip-permissions` sneaks in | **BLOCKED (bypass-tested).** §2.3 forbids the flag categorically and asserts its absence via a test that greps the constructed argv. This is a real, falsifiable invariant. **Good.** | — |
| S4 | Repo `.env`/secrets read from the worktree | Design strips `.env`/`.env.*`/known secret files from the worktree before launch (or denylists them in the scan). **Adequate** but "known secret files" is a denylist — a repo with an unusual secret filename (`credentials.json`, `.npmrc`, `.pypirc`) slips through. **Fix:** combine with the temp-HOME + cred-starve so a leaked repo secret has no egress path anyway; note the denylist is best-effort. | **P2** |
| S5 | `--strict-mcp-config` absent → ambient MCP servers loaded | Design passes `--strict-mcp-config` (§2.3) so only explicit MCP config loads, blocking Gmail/Drive/arbitrary tools. **Good** — asserted-present in the P2-c RED test. | — |

**REQ-04 verdict:** the timeout/budget/ceiling fire against the pgid as designed, **but** (a) a setsid-escaping grandchild escapes all three (C1, design overstates completeness), (b) the daily ceiling isn't atomic for P3 (C3), and (c) **the worker inherits egress creds by default (S1/P0-A)** — the single most serious finding in this review.

---

## REQ-05 — RED test plan: real / theater / missing

Existing broker+worker tests (`tests/broker/*`, `tests/worker/test_worker.py`, and the real `test_bypass_impossibility_gate.py`) prove the patterns are falsifiable and the harness supports real (non-mock) gate tests. Per-gate verdict for the P2 plan (§5):

| Gate | Test(s) | Verdict | Note |
|---|---|---|---|
| State machine one-way | P2-a #2 ★ (illegal `MERGING→RUNNING` raises) | **REAL** | Mirrors `test_held_store.py` pattern; asserts the bad transition FAILS. Falsifiable against real SQL. |
| Supervisor sole-writer / lock | P2-a #5, P2-b #3 ★ | **REAL** | "No other module holds the write cursor" is checkable by import inspection; lock-steal-after-TTL is falsifiable. |
| Per-tick integrity (dead pgid parked) | P2-a #6 | **REAL** | Falsifiable: kill pgid, tick, assert NEEDS_ATTENTION. |
| Worker push-free + no-skip-permissions | P2-c #2 ★ | **REAL — and the P0-A gap must be added here.** The current test asserts argv lacks `Bash(git push)`/`--dangerously-skip-permissions` and "a push fails (no token, no tool)." **Missing:** an assertion that the *constructed env* contains no egress key when the supervisor env does. Add it or the P0-A hole ships untested. | **MISSING sub-assertion (P0-A)** |
| Injection pre-model neutralization | P2-d #1 ★ | **THEATER-RISK.** Asserts content "is flagged on tool-result return, before the worker model context." Against real `claude -p` the interception seam is unproven (§6) — this test would pass only against a *stub* worker harness, not a real `claude -p`. If written against a stub it validates mocked behavior (banned per code-style). **Fix:** either make the belt post-run scan the falsifiable contract (real, testable) or prove the seam empirically first. | **THEATER unless seam proven** |
| Immutable ring Layer 2 reject | P2-h #1 ★, #3 | **REAL** | `check_diff` on a hand-crafted diff touching `broker/approval.py` → reject. Falsifiable, plain code. **Add:** rename (R2) and traversal (R3) RED tests — currently MISSING. | **REAL but incomplete** |
| Immutable ring Layer 1 FS-exclude | P2-h #2 ★ | **WEAK/THEATER-RISK.** "A worker cannot `sed -i` a broker file (FS scope excludes it)." For a subprocess worker this depends on the unspecified worktree guard; the test may only prove the allowlist string, not a real FS denial. Layer 2 is the real wall — lean the gate assertion there. | **THIN** |
| Cost stop — budget | P2-i #1 ★ | **REAL** | `kill(handle)` on pgid asserted; mirrors `test_worker.py`. Falsifiable. |
| Cost stop — timeout to group | P2-i #2 ★ | **REAL — best test in the set.** "A worker *child* also dies" proves SIGTERM reached the *group*, not just the parent. **But** it will NOT catch the C1 setsid-grandchild escape — add a grandchild-that-setsids case to document the residual honestly. | **REAL; add residual case** |
| Cost stop — daily ceiling | P2-i #3 ★, #4 | **REAL** | Would-breach launch blocked; mid-flight breach kills; ledger survives restart. Falsifiable. Add the atomic-CAS assertion (C3) so P3 concurrency is covered. | **REAL; strengthen for C3** |
| Merge held / no ungated merge | P2-g #1 ★ | **REAL** | "No nonce → `approve` is `ApprovalRejected`" is the exact self-approve wall, backed by `approval.py:98`. Falsifiable against real code. |
| Merge never-force | P2-g #3 | **REAL** | Inject conflict → NEEDS_ATTENTION, assert `git push --force` never invoked. Backed by `git_push_executor.py:57,68`. |
| P2 exit-gate composition | `test_p2_first_magic.py` (opus verifier) | **REAL intent** | End-to-end intake→merge with the ★ negatives. Sound *if* it runs against the real broker + real worktree (no internal mocks) per R18. Ensure it does not stub the broker. |

**Gates with NO falsifiable test (or theater-risk):**
- **Injection pre-model (P2-d #1)** — theater unless the `claude -p` seam is proven; belt scan is the real falsifiable contract. **[P0-B]**
- **Worker env cred-absence** — no test asserts the worker env is starved of egress keys given a non-starved supervisor. **[P0-A, MISSING]**
- **Ring rename/traversal** — no RED test for R2/R3. **[P1, MISSING]**
- **Layer 1 FS-exclude** — thin; real wall is Layer 2. **[P1]**

---

## Summary of required fixes

| ID | Sev | Fix |
|---|---|---|
| P0-A | P0 | `scrub_env` must produce a *replacement* env (whitelist), not an overlay; runner must not fold in parent `os.environ`, or must assert zero egress keys in the constructed env before launch. Add RED test: seeded `GITHUB_TOKEN` in supervisor → absent in worker env. |
| P0-B | P0 | Do not claim pre-model injection neutralization until the `claude -p` result-filter seam is empirically proven. Make the belt post-run scan the falsifiable REQ-03a contract; prove the worker-side seam in P2-d or drop the "before the model sees it" done-when. |
| P1-C | P1 | Specify the type→executor registry on `BrokerServer` (no such seam exists today; both `_rpc_*` call a single `self._executor`). Acknowledge wiring it edits `broker/server.py` (ring-protected) and must land as a reviewed maintainer commit, and make explicit that the ring gate inspects *worker* diffs only. |
| P1-R2 | P1 | Ring gate must reject a diff whose old-path OR new-path touches a ring path (renames); add rename RED test. |
| P1-R3 | P1 | Ring gate must canonicalize (realpath) changed paths before matching, defeating symlink/`..` traversal; add traversal RED test. |
| P1-C1 | P1 | Add the process-tree sweep after kill (the P1a residual deferred "to P2"), or state the setsid-grandchild escape explicitly in §4.3 instead of claiming "every child in one shot." |
| P1-C3 | P1 | Make the daily-ceiling reserve an atomic CAS `UPDATE` now so P3 parallelism inherits atomicity. |
| P1-M2 | P1 | Invariant + test: the supervisor process never holds the push token; only the broker merge executor does. |
| P1-S2 | P1 | Note the macOS keychain reach residual (worker in login session); ideally launchd context without keychain unlock. |
| P2-* | P2 | Meter subprocess-spend blindness (C2), `protected_branches` is belt not wall (M6), repo secret-file denylist is best-effort (S4), regex scanner is a detector not a wall (I2/I3) — all honestly scope these as belts, don't oversell. |

**What the design got right (credit where due):** the merge genuinely rides the existing broker approval surface with no side path; the ring is self-protecting (it lists its own files + the cost/scrub/scan code); every merge holds by default via empty `allowed_origins`; `--dangerously-skip-permissions` absence and `--strict-mcp-config` presence are bypass-tested invariants; the reuse table is accurate to the file:line; and the design is honest about its two biggest UNVERIFIEDs (injection seam, keychain). The fixes above harden a fundamentally sound architecture — they do not rearchitect it.
