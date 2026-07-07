# P1b Design — Trust Ledger + Graduation

<!-- ABOUTME: Architecture for the earned-trust autonomy boundary: how a (repo × task-type) -->
<!-- ABOUTME: earns auto-merge from real P2 outcomes and how it is revoked on a bad outcome. -->
<!-- ABOUTME: Designed for TDD; graduation/demotion are REAL; the "what never graduates" list -->
<!-- ABOUTME: and work/Diligent tier-0 are unbypassable. trust-policy.md sits in the immutable ring. -->
<!-- ABOUTME: Source plan: docs/plans/diagnosis-2026-07-03/hermes-fable-plan.md §P1b + §5 + §6. -->

**Status:** DESIGN (ARCHITECT, P1b-DESIGN, backlog mode — GOVERNANCE_EXEMPT).
**Branch:** `factory` (verified `git rev-parse --abbrev-ref HEAD` = `factory`).
**Plan authority:** `docs/plans/diagnosis-2026-07-03/hermes-fable-plan.md` §P1b (tasks P1b-1..4), §5 (Safety), §6 (Self-Improvement rings), §1 L1.
**Owner locks:** OQ1 = ask-for-everything launch profile; ONLY personal non-work repos ever auto-merge-eligible; tier-1 = ≥10 consecutive merged-clean + 0 reverts + ≥30 days (wall-clock → STAGED); work/Diligent repos hard-coded tier-0 forever; production deploys/deletions/financial actions NEVER graduate.

---

## 0 — Verdict & shape

The whole of P1b is **policy over data that already exists**, plus one **parameter flip** on an existing gate. Nothing here is a new merge path, a new approval surface, or a new credential boundary — those all shipped in P1a/P2 and P1b consults them.

Three moving parts:

1. **Trust ledger** (REQ-01) — a new SQLite table + markdown mirror, written by the supervisor at the exact moment a job's merge outcome becomes known (DONE / rejected). One row per completed job, keyed by `repo × task_type`.
2. **Trust policy** (REQ-02) — a ring-protected `trust-policy.md` (thresholds) + a **pure function** `compute_tier(repo, task_type, ledger_rows, now) -> int` that reads only ledger data + pinned config and returns an autonomy tier. Work/Diligent and the never-graduates list are hard gates *inside* the function, not toggles.
3. **Profiles + gate retrofit** (REQ-03) — an owner profile switch that sets an effective **tier ceiling**, and a one-line change at the existing merge-card enqueue seam so the held-vs-auto decision is `tier`-parameterized instead of a hardcoded constant.

**The single retrofit seam is real and narrow.** Today `factory/supervisor.py:_enqueue_merge_card` passes a module constant `_MERGE_SAFE_LANE = {"disposition":"held","allowed_origins":[]}` (supervisor.py:55-57, used at :418). Every merge holds because `SafeLane` C4 (`broker/safe_lane.py:96-100`) fails on an empty `allowed_origins`. P1b makes that JSON **tier-computed** rather than constant. That is the entire "one parameterized code path, not a fork" requirement — and the mechanism (widen `allowed_origins` as trust builds) is *exactly* what `safe_lane.py`'s own docstring already anticipates ("Widening is a P1b policy change", safe_lane.py:45-46).

---

## 1 — Verified reused interfaces (file:line)

| Interface | File:line | What P1b reuses it for |
|---|---|---|
| `jobs` table + one-way state machine | `factory/job_store.py:95-138`, `:49-76` | The **outcome source**. A job reaching `DONE` = a merge landed; `NEEDS_ATTENTION` after AWAITING_APPROVAL with a reject-reason = rejected. `trust_tier_at_spawn` column (`:115`) already reserved for P1b. |
| `JobStore.transition(...)` CAS + `extra=` hook | `factory/job_store.py:234-280` | The write point: the outcome-record call fires *transactionally alongside* the terminal transition (no double-write — see §2.3). |
| `Supervisor.approve_merge` (DONE / park) | `factory/supervisor.py:429-457` | The terminal-outcome site: `merged` → DONE (record merged-clean); else `_park` (record rejected/needs-fix). |
| `_enqueue_merge_card` + `_MERGE_SAFE_LANE` | `factory/supervisor.py:383-423`, const `:55-57` | The **retrofit seam** (REQ-03). Replace the constant arg with a tier-computed safe-lane JSON. |
| `SafeLane.evaluate` (5-condition, C4 = origin authority) | `broker/safe_lane.py:37-109` | The auto/hold classifier. Tier≥1 → the merge origin is placed in `allowed_origins` (C4 passes) → auto; tier-0 → not → held. No new decision logic. |
| `HeldStore.enqueue(safe_lane_json=...)` | `broker/held_store.py:531-566` | Unchanged. The card is still enqueued; only its `disposition` differs (`held` vs `auto_sent`). |
| `ApprovalAuthority.approve/reject` (nonce wall) | `broker/approval.py:78-129` | Unchanged. Tier≥1 auto-merge still runs the **same nonce-gated executor** — see §4.4 (auto-approval is broker-minted, not a bypass of the executor). |
| `immutable_ring.RING_PATHS` incl. `trust-policy.md`, `docs/factory/trust-policy.md`, `docs/factory/never-graduates.md` | `factory/immutable_ring.py:45-59` | **Ring protection is already live** (REQ-02). P1b only authors files *at those exact paths*; a worker diff touching them is already rejected at submission (`check_diff`, `:184-256`) and excluded from worker FS scope (`fs_scope_excludes`, `:259-271`). |
| `docs/factory/never-graduates.md` (authored in P2) | file exists, ring member | The "what never graduates" source of truth. `compute_tier` reads its capability list; it is not re-authored. |
| `docs/factory/merge-policy.md` + `MergePolicy` loader | `factory/merge_policy.py:61-177`, doc `docs/factory/merge-policy.md` | Per-repo config home. P1b adds a `trust:` block (repo classification: `personal` \| `work`) to the same file — see §2.2. `allow_openrouter:false` default already flags work repos. |
| `STAGED-GATES.md` convention | `docs/plans/harness/fable/STAGED-GATES.md` | The 30-day wall-clock gate and the live graduation proof append here as `SG-P1b-*` (§6). |

**UNVERIFIED / corrected plan assumptions** (see §7 escalations):
- The plan (P1b-1) says the ledger extends "the policy engine's workflow table." **No such table exists** in `factory/` or `broker/` (grep for `workflow` → only doc/prose hits; `agent/plugin_llm.py:_TrustPolicy` is *plugin* trust, unrelated). The real, live outcome source is the `jobs` table. **Design decision:** a dedicated `trust_ledger` table in a new `factory/trust_ledger.py` store, fed from job outcomes. This is faithful to intent ("SQLite … recording per-(repo × task-type) results") and avoids inventing a table.

---

## 2 — REQ-01: Trust ledger

**Section evidence for the digest: §2.1 (schema), §2.2 (task-type derivation + repo class), §2.3 (job→ledger feed, no double-write), §2.4 (files).**

### 2.1 Schema — `factory/trust_ledger.py`, table `trust_ledger`

One row per **completed job** (append-only; the tier is *computed* over rows, never stored mutably on the ledger — see §3.3 for why append-only matters for revocation honesty).

```sql
CREATE TABLE IF NOT EXISTS trust_ledger (
  job_id        TEXT PRIMARY KEY,     -- FK-in-spirit to jobs.id (ULID); one ledger row per job
  repo          TEXT NOT NULL,        -- jobs.repo (slug)
  task_type     TEXT NOT NULL,        -- derived: 'bugfix'|'feature'|'refactor'|'test'|'docs'|'other' (§2.2)
  outcome       TEXT NOT NULL,        -- 'merged_clean'|'merged_with_fix'|'rejected'|'reverted'
  repo_class    TEXT NOT NULL,        -- 'personal'|'work' snapshot at record time (§2.2) — audit + fast filter
  tier_at_spawn INTEGER,              -- mirror of jobs.trust_tier_at_spawn (what tier the auto/hold ran at)
  confidence    REAL,                 -- jobs.confidence at AWAITING_APPROVAL (NULL until P4.6); consumed by graduation floor once P4 lands
  outcome_ts    INTEGER NOT NULL,     -- ms epoch: when the outcome became known (drives the 30-day window)
  detail        TEXT                  -- fail_reason / reject reason / revert PR id; audit only
);
CREATE INDEX IF NOT EXISTS idx_tl_repo_type ON trust_ledger(repo, task_type, outcome_ts);
```

**Outcome vocabulary** (maps plan's four outcomes):
- `merged_clean` — job reached `DONE` with no human fix and no later revert. **The only outcome that counts toward graduation.**
- `merged_with_fix` — merged, but a human edited the diff before/at approval, OR a `TEST→RUNNING`/`REVIEW→RUNNING` auto-fix retry fired (job_store `_ALLOWED` allows exactly one each, `:69-70`). Breaks the "consecutive clean" streak but is not a revocation.
- `rejected` — owner tapped Reject (`ApprovalAuthority.reject`, approval.py:120-128) → job parked `NEEDS_ATTENTION`. **Auto-revokes to tier-0.**
- `reverted` — a post-merge regression forced a revert. **Auto-revokes to tier-0.** *No live producer today* (see §7 REQ-01 escalation — P5.4 regression sentinel is the future writer; the column + revocation logic exist now so P5 only wires the trigger).

### 2.2 Task-type derivation + repo classification (both are *design decisions*, flagged)

**Task-type:** `jobs` has **no `task_type` column** (verified — grep `task_type` in factory/broker → none). The trust key is `repo × task_type`, so task-type must be *derived when the ledger row is written*. Pure function `derive_task_type(job_row) -> str`:
1. If the spec/intake carried an explicit `task_type` label (P3 task-splitter emits one per node, plan §3.1) → use it.
2. Else classify from the spec text with a **fixed keyword map** (plain code, no AI): `fix|bug|regression`→`bugfix`; `refactor|rename|extract|cleanup`→`refactor`; `test|coverage|spec`→`test`; `docs|readme|comment`→`docs`; default→`feature`. `kind:quick` (job_store `:101`) biases toward `bugfix`/`docs` but does not override an explicit label.
3. Unknown → `other` (a real bucket that **never graduates** — see §3.2 — so a misclassification fails safe toward *more* holding).

**Repo classification** (`personal` vs `work`) is authored in `merge-policy.md` as a new `trust:` block, loaded via the existing `MergePolicy` loader (extend `factory/merge_policy.py`):
```yaml
trust:
  repo_class: work        # 'personal' | 'work'  (DEFAULT: work — fail-safe)
```
**Default `work`** so a repo with no explicit `personal` opt-in is tier-0 forever (matches L1: "ONLY personal non-production repos ever auto-merge-eligible"). This mirrors the `allow_openrouter:false` default fail-safe already in the same file. A hardcoded denylist of known Diligent org prefixes is an additional AND-gate inside `compute_tier` (§3.2) so a mislabeled work repo still cannot graduate.

### 2.3 The job→ledger feed — *without the supervisor writing twice*

The requirement's subtlety: the supervisor is the sole writer of `jobs`; we must not have it write the same outcome to two places in two transactions (a crash between them corrupts the record). **Design:** the ledger row is written **inside the same terminal transition**, via the `transition(..., extra=)` hook that already exists (job_store.py:265-268 appends arbitrary `SET col=?` clauses in the *same* UPDATE). Concretely:

- Add a `JobStore.record_terminal_outcome(job_id, expected, target, *, outcome, ...)` method that (a) performs the CAS transition on `jobs` AND (b) inserts the `trust_ledger` row **on the same connection, same `with self._lock`, same `commit()`**. Because `trust_ledger` lives in the *same SQLite file* as `jobs` (one DB, two tables — like `daily_budget` already co-lives there, job_store.py:133-137), the insert + the state UPDATE are one atomic commit. No second writer, no second DB, no cross-DB 2-phase problem.
- Call sites (supervisor.py): `approve_merge` `merged` branch (`:450-452`) → `record_terminal_outcome(... DONE, outcome=merged_clean_or_fix)`; the reject path (`ApprovalAuthority.reject` → supervisor observes and parks) → `outcome=rejected`.

This keeps job_store the sole writer (the ledger table is owned by the same store module family and only mutated from the supervisor-driven path) and makes "record the outcome" a property of the terminal transition, not a separate bolt-on.

> **Alternative rejected:** a background reconciler that scans `jobs WHERE state=DONE` and back-fills the ledger. Rejected because it re-derives outcome from state alone — it cannot distinguish `merged_clean` from `merged_with_fix` (that fact is only known at approval time, e.g. whether a human edited the diff) and it is a second writer racing the supervisor. Recording at the transition captures the richer fact once, atomically.

### 2.4 Files (REQ-01 deliverable)
- `factory/trust_ledger.py` — the store (schema above, `record_outcome` insert, `read_rows(repo, task_type)` reader). **Reader-open is fine from anywhere; the write path is supervisor-only** (same discipline as job_store).
- `factory/job_store.py` — add `record_terminal_outcome` (co-transactional insert). *This file is NOT in the ring* (only the guard files are) so a maintainer commit here is sanctioned.
- `trust-ledger.md` — human mirror, regenerated each tick alongside `build-jobs.md` (reuse the atomic temp-file+`os.replace` writer pattern, job_store.py:557-572). Read-only from outside; never read back as input.

---

## 3 — REQ-02: trust-policy.md + tier computation

**Section evidence for the digest: §3.1 (policy schema, pinned thresholds), §3.2 (compute_tier pure function + hard gates), §3.3 (ring-protection statement).**

### 3.1 `docs/factory/trust-policy.md` (ring member) — pinned thresholds

A human-browsable YAML-in-markdown (same shape as merge-policy.md), loaded by a `load_trust_policy()` in a new `factory/trust_policy.py`. **The path is already in `RING_PATHS`** (immutable_ring.py:46-53) so a worker can never edit it.

```yaml
# trust-policy.md — pinned graduation thresholds (owner-tunable via a REVIEWED maintainer commit only)
tiers:
  tier1:                         # auto-merge, personal non-production only
    consecutive_merged_clean: 10 # ≥10 consecutive merged_clean, per repo × task_type
    max_reverts: 0               # 0 reverts ever in the window
    min_window_days: 30          # ≥30 days wall-clock (STAGED — see §6)
    min_confidence: 0.0          # AND-condition once P4.6 lands; 0.0 = inert until then
  # tier2 (broader) intentionally ABSENT at launch — L1/OQ1 = ask-for-everything; tier-1 is the ceiling.
revoke:
  on_rejected: true              # first human-rejected outcome → tier-0
  on_reverted: true              # first post-merge regression → tier-0
never_graduates_ref: docs/factory/never-graduates.md   # capability denylist (read, not re-authored)
work_repo_hard_tier0: true       # work/Diligent repos stay tier-0 regardless of record
```

Thresholds are **pinned in the file, not in code** — but the file is ring-protected, so "pinned" = "only a human-reviewed maintainer commit can change them; no worker and no retro diff can" (§6 immutable ring; retro diffs touching `trust-policy.md` are rejected at submission, plan §5 / §P5.3).

### 3.2 `compute_tier(repo, task_type, rows, now, *, policy, repo_class) -> int` — pure code over ledger data

A **pure function** (no I/O, fully unit-testable — the TDD spine). Precedence, **hard gates first** (each is an early `return 0` so no later condition can lift it):

```
def compute_tier(repo, task_type, rows, now, *, policy, repo_class, capability=None):
    # HARD GATE 1 — work/Diligent repos never graduate (L1, plan §5).
    if repo_class == "work" or _is_diligent_repo(repo):   return 0
    # HARD GATE 2 — the capability is on the never-graduates list (prod deploy/deletion/financial).
    if capability in load_never_graduates():              return 0
    # HARD GATE 3 — 'other'/unknown task-type never graduates (fail-safe for misclassification).
    if task_type not in GRADUATABLE_TASK_TYPES:           return 0
    # --- only now may we EARN tier 1 from the record ---
    window = [r for r in rows if r.repo==repo and r.task_type==task_type]
    streak = _consecutive_trailing_merged_clean(window)   # resets on any non-clean
    reverts = sum(1 for r in window if r.outcome == "reverted")
    span_days = (now - _earliest_ts_of_streak(window, streak)) / DAY_MS
    conf_ok = (policy.tier1.min_confidence == 0.0) or _all_streak_conf_ge(window, streak, policy.tier1.min_confidence)
    if (streak >= policy.tier1.consecutive_merged_clean
            and reverts == 0
            and span_days >= policy.tier1.min_window_days
            and conf_ok):
        return 1
    return 0
```

Design properties that make graduation/demotion **real**, not cosmetic:
- **Consecutive** = trailing streak. A single `rejected`/`merged_with_fix`/`reverted` anywhere in the trailing run resets `streak` to 0 → the repo×task-type **drops to tier-0 on the very next computation**. That *is* the auto-revoke: demotion is not a separate code path, it is `compute_tier` returning 0 because the streak broke (plan: "demotion is automatic"). `revoke.on_rejected/on_reverted` are asserted by the fact that those outcomes are non-clean and therefore break the streak.
- **Append-only ledger** (§2.1) is load-bearing here: because rows are never mutated/deleted, an injected bad outcome is *permanently* in the trailing window until 10 fresh clean outcomes push past it — you cannot silently erase a revocation.
- The three hard gates are `return 0` **before** any record is consulted, so no amount of clean history can lift a work repo, a never-graduates capability, or an unknown task-type. This is the unbypassability the exit gate checks.

`GRADUATABLE_TASK_TYPES = {bugfix, feature, refactor, test, docs}` (explicit allowlist; `other` excluded by construction).

### 3.3 Ring-protection statement (REQ-02 done-when)

`trust-policy.md`, its `docs/factory/` twin, and `never-graduates.md` are **already** members of `RING_PATHS` (immutable_ring.py:46-53, verified). Therefore:
1. A **worker** diff that adds/edits/renames-into any of them is rejected by `check_diff` at merge-submission (immutable_ring.py:184-256, fail-closed) → `NEEDS_ATTENTION`, before any human sees a card.
2. Those paths are in `fs_scope_excludes()` (immutable_ring.py:259-271) so the worker's FS tool scope cannot even write them in the worktree (defense-in-depth belt; the submission gate is the wall).
3. A **retro** diff (P5.3) touching them is rejected by the *same* gate (plan §5, §P5.3) — a degenerate retro cannot even propose weakening a trust threshold.

**P1b adds no new enforcement** for this — it *inherits* the P2 ring. The only P1b action is to **author files at those already-guarded paths**. A test asserts each path is in `RING_PATHS` so a future refactor that drops one from the ring fails RED (§5, AT-RING-1).

---

## 4 — REQ-03: Owner profiles + parameterized merge gate

**Section evidence for the digest: §4.1 (profile switch → tier ceiling), §4.2 (the decision function inputs→auto|held), §4.3 (single-path retrofit), §4.4 (auto-merge still nonce-gated, not a bypass).**

### 4.1 Owner profiles — a config switch over an **effective tier ceiling**

`~/.hermes/factory/trust-profile.txt` (or a `profile:` key in a factory config already loaded at startup) holds one of:
- `ask-for-everything` (**default, OQ1**) → `tier_ceiling = 0`. `effective_tier = min(computed_tier, 0) = 0` for every repo. Nothing auto-merges regardless of record. This is the launch posture.
- `auto-merge-personal-non-prod` → `tier_ceiling = 1`. `effective_tier = min(computed_tier, 1)`.

The profile **never lifts** a tier — it only caps. So a work repo (computed tier 0 by hard gate) stays 0 under either profile; the profile only decides whether an *earned* tier-1 is honored or still held. This makes the profile safe to flip: the worst case of `auto-merge-personal-non-prod` is bounded by `compute_tier`'s hard gates.

`effective_tier(repo, task_type) = min(compute_tier(...), profile_ceiling())`.

### 4.2 The gate decision function (inputs → auto | held)

Pure, testable:
```
def merge_disposition(repo, task_type, *, ledger, policy, profile, repo_class, capability) -> ("auto"|"held", tier):
    t = min(compute_tier(repo, task_type, ledger.read_rows(repo, task_type), now_ms(),
                          policy=policy, repo_class=repo_class, capability=capability),
            profile_ceiling(profile))
    return ("auto" if t >= 1 else "held", t)
```
Inputs: `repo`, `task_type`, ledger rows, pinned policy, owner profile, repo class, the job's capability. Output: `auto` or `held`, plus the tier (stored into `jobs.trust_tier_at_spawn` — the column reserved at job_store.py:115 — and mirrored into the ledger row's `tier_at_spawn`).

### 4.3 Single parameterized code path (the retrofit — REQ-03 core, plan P1b-4)

**One line changes shape, no fork.** Today `_enqueue_merge_card` (supervisor.py:413-419) passes the constant `_MERGE_SAFE_LANE`. Retrofit:

```python
# BEFORE (P2): every merge held via a constant.
safe_lane_json=_MERGE_SAFE_LANE,

# AFTER (P1b): tier-computed disposition; SAME enqueue, SAME card, SAME executor.
disposition, tier = merge_disposition(row["repo"], task_type, ledger=..., policy=..., profile=..., ...)
safe_lane_json=_tier_safe_lane_json(disposition, origin=f"factory:{job_id}")
# ... and record tier into jobs.trust_tier_at_spawn on the same enqueue.
```

> **REVISED by v2 §V2.3.** The original plan here — the *supervisor* computes tier, emits `auto_sent` in the `safe_lane_json`, and the broker trusts it — is the P0-2 hole: it lets the supervisor's own classification release the merge. In v2 the supervisor **never emits `auto_sent`**. It always enqueues the merge **held**, but stamps the card with a *requested tier* + trusted `capability`/`task_type` (advisory only); a separate `auto_merge` request over the socket asks the **broker** to decide. The broker recomputes tier and re-checks the ring server-side before it releases anything. See §V2.

Legacy (pre-v2) framing, retained for context: `_tier_safe_lane_json` emitted, for `held`, `{"disposition":"held","allowed_origins":[]}`; for `auto`, `{"disposition":"auto_sent","allowed_origins":["factory:<job_id>"]}`. This is **no longer the auto path** — SafeLane's `allowed_origins`/C4 widening is a message-oriented lever and is not the trust gate for a `merge` (P1 finding A4). The merge card is *always* enqueued **held**; only the broker's `auto_merge` path (§V2.2) can release it.

### 4.4 Auto-merge is **not** a bypass of the nonce wall

> **SUPERSEDED by Revision v2 §V2.** The red-team confirmed the two claims below (broker-side re-check; supervisor cannot self-mint) describe behavior that **does not exist in the P2 code** — the auto-approver, the server-side ring re-check, and the "supervisor holds no in-process authority" property were all aspirational. The **binding design** for the auto-merge authority is now §V2 (REQ-01/REQ-02). The intent stated here is correct; §V2 makes it real and grounds every step in a cited seam. Read §V2 for the authoritative auto-merge path.

Critical safety intent (a "what never graduates" invariant, never-graduates.md:24 "Self-approving a merge"): even a tier-1 auto-merge must **not** let the supervisor mint its own approval — that would recreate the self-approval hole. The *shape* is: the **broker** (which holds the secret, approval.py:40-42) is the party that decides + executes an auto-merge; the supervisor only requests it. §V2 specifies exactly how — over the socket, with a broker-side gate that re-runs the ring/never-graduates/tier checks and fails closed.

---

## 5 — REQ-04: Acceptance-test plan + file-disjoint task breakdown

**Section evidence for the digest: this whole section — falsifiable RED tests per exit-gate clause, disjoint tasks P1b-a..d.**

### 5.1 Task breakdown (file-disjoint so tasks can run in parallel lanes)

| Task | Owns (writes) | Depends on | Deliverable |
|---|---|---|---|
| **P1b-a** (ledger) | `factory/trust_ledger.py` (new); `tests/factory/test_trust_ledger.py` | P2 job store | REQ-01: schema, `record_outcome`, `read_rows`, `trust-ledger.md` mirror writer, `derive_task_type` |
| **P1b-b** (policy+compute) | `docs/factory/trust-policy.md` (new, ring); `factory/trust_policy.py` (new); `tests/factory/test_trust_policy.py` | P1b-a (row shape) | REQ-02: policy loader, `compute_tier` pure fn + 3 hard gates, ring-membership assertion |
| **P1b-c** (profiles+decision) | `factory/trust_profile.py` (new); `tests/factory/test_trust_profile.py`; extend `factory/merge_policy.py` `trust:` block + its test | P1b-b | REQ-03: profile ceiling, `merge_disposition`, `repo_class` load |
| **P1b-d** (feed wiring + capability provenance) | edit `factory/supervisor.py` (`approve_merge`/`_park` outcome hook + record trusted `capability`/`task_type` at job creation, §V2.4); edit `factory/job_store.py` (`record_terminal_outcome`); `tests/factory/test_supervisor_trust.py` | P1b-a,b,c; P2-7 merge gate | REQ-01 feed + REQ-03 trusted-classification provenance (§V2.4) |
| **P1b-e** (broker auto-merge gate) **— BROKER MAINTAINER EDIT** | new `broker/merge_gate.py` (broker-owned ring path-list + `never-graduates` reader + tier recompute, §V2.1); edit `broker/server.py` (new `auto_merge` RPC + fail-closed gate on the `merge` path, §V2.2); edit `broker/executors/merge_executor.py` OR the gate to compute `git diff base...branch` for `check_diff`; `tests/broker/test_auto_merge_gate.py` | P1b-b (policy/tier fn), P1b-c (`merge_disposition`), P2 broker | REQ-01 broker-side re-check + REQ-02 auto-merge-over-socket + no-mint-in-supervisor |

Disjointness: a/b/c each create their own new module + test file (no shared edits). **d** edits existing `factory/` files; **e** edits `broker/` files. d and e touch disjoint packages (factory vs broker) so they can run in parallel; both depend on a/b/c. **e is a broker-maintainer change** — it edits the enforcement boundary (`broker/`, a ring path itself) and must be authored by a maintainer, never a factory worker (immutable-ring boundary, server.py:5-7). No two tasks edit the same file concurrently.

**Why the gate lives in the broker, not by importing `factory.immutable_ring`** (REQ-01 `escalate_if`): the broker currently imports **nothing** from `factory/` (verified — `grep 'from factory' broker/` = 0 hits). The dependency runs factory→broker, never the reverse; the broker is the lower, credential-holding trust root. Importing `factory.immutable_ring` into the broker would (a) invert that layering and (b) make the broker's own security depend on worker-guard code that lives *outside* the broker's ring boundary. **Resolution:** the broker owns its own ring path-list in `broker/merge_gate.py` (a small verbatim copy of the ring path tuple + the `never-graduates.md` capability list, both re-read at gate time from the on-disk files the ring already protects). A test asserts `broker/merge_gate.py`'s path list is a **superset** of `factory.immutable_ring.RING_PATHS` (AT-RING-2) so the two can never drift with the broker checking *less*.

### 5.2 Per-gate RED acceptance tests (each falsifiable; map 1:1 to the P1b exit gate)

Exit gate clauses → tests (all committed RED first, TDD):

| Test id | Falsifiable assertion (RED before impl) | Exit-gate clause |
|---|---|---|
| **AT-FEED-1** | Complete a synthetic P2 job to DONE via the supervisor; a `trust_ledger` row appears keyed by `(repo, task_type)` with `outcome=merged_clean`, in the **same** DB, written in **one** transaction (assert no row exists if the transition is rolled back). | REQ-01 feed / plan P1b-1 |
| **AT-GRAD-1** | Seed the ledger with **exactly 10** consecutive `merged_clean` rows for `(repoP, feature)`, `repo_class=personal`, spanning a **mocked** `now` ≥30 days after the first, profile `auto-merge-personal-non-prod` → `merge_disposition` returns `("auto", 1)`. With **9** rows → `("held", 0)`. | graduate held→auto after ≥10 |
| **AT-GRAD-2** | Same 10-clean seed but `now` only 29 days out → still `("held", 0)` (the 30-day wall-clock gate binds; **time is injected**, real gate STAGED). | ≥30-day window |
| **AT-DEMOTE-1** | Graduated `(repoP, feature)` at tier-1; inject **one** `rejected` row (newest) → next `compute_tier` returns **0** (streak reset = automatic demotion). Same with an injected `reverted` row. | demote on injected bad outcome |
| **AT-NEVER-1** | At a *fully graduated* tier-1 state, drive the **real** `auto_merge` broker path (§V2.2) for a job whose **trusted** `capability` (recorded at job creation, §V2.4) ∈ never-graduates → the broker returns **held**, the merge does **not** execute. Must feed the capability through the trusted job-creation field, NOT a hand-passed arg (closes B5 theater risk). | "what never graduates" unbypassable, server-side |
| **AT-NEVER-2** | A worker diff that edits `docs/factory/trust-policy.md` (or `never-graduates.md`) is rejected by `immutable_ring.check_diff` → `RingViolation` (reuses P2 gate; asserts P1b's new file is genuinely ring-covered). | ring-protection of the policy |
| **AT-WORK-1** | `(repoW, feature)` with `repo_class=work` (or a Diligent org prefix) AND 20 `merged_clean` rows over 90 days → `compute_tier` returns **0**. Work repos never graduate regardless of record. | work/Diligent tier-0 regardless |
| **AT-OTHER-1** | `task_type='other'` (or any non-graduatable) with 20 clean rows → tier **0** (misclassification fails safe). | fail-safe hard gate |
| **AT-PROFILE-1** | Graduatable tier-1 record under `ask-for-everything` profile → `("held", 0)` (ceiling caps); flip to `auto-merge-personal-non-prod` → `("auto", 1)`. Profile only caps, never lifts. | profile switch changes ceiling |
| **AT-PATH-1** | `_enqueue_merge_card` enqueues exactly **one** card via the **same** `HeldStore.enqueue` call, always `state="held"`, for both tier-0 and tier-1 inputs (v2: the supervisor no longer emits `auto_sent`; release is a *separate* broker `auto_merge` request, not a forked enqueue). Assert no second/forked enqueue path and no `auto_sent` in the emitted `safe_lane_json`. | single parameterized path (v2: held-only enqueue) |
| **AT-NONCE-1** | Drive the real `BrokerServer` over a **socketpair** (server.py:181-183 — the real dispatch, not a mock). Assert `mint`/`mint_approval_nonce`/`auto_merge`-self-approve verbs are **not in `_CLIENT_METHODS`** (server.py:28-36) so no client — including a compromised supervisor — can obtain a nonce over the socket. A `_dispatch` frame naming any mint verb returns an error frame. Proves the self-approval wall is the **socket boundary**, not an in-process check. | §V2 no-mint-over-socket |
| **AT-NOMINT-1** | Construct the **production** `Supervisor` and assert it is built with **no real `ApprovalAuthority`** (the prod factory passes `authority=None` / a request-only `BrokerClient`, §V2.3); assert `getattr(sup, "_authority", None)` cannot `mint_nonce`. A test may inject a fake authority; production must not. Fails RED against today's wiring (supervisor.py:113/:134/:446 hold a real authority). | REQ-02 no-mint-in-supervisor |
| **AT-BROKER-GATE-1** | **The load-bearing server-side test.** Enqueue a tier-1-eligible `merge` whose **diff touches a ring path** (e.g. edits `broker/x` or `docs/factory/trust-policy.md`); request `auto_merge` over the socket. The broker's `merge_gate` (§V2.1) runs `check_diff` on `git diff base...branch`, finds the ring hit, and returns **held** (falls back to human) — the merge does **not** execute. Fails RED today (no broker-side check exists; the auto path would run the executor blindly). | REQ-01 broker-side re-check, fail-closed |
| **AT-BROKER-GATE-2** | `auto_merge` request where `merge_gate` recomputes tier from the ledger and gets tier-0 (streak broken since the supervisor's stamp) → **held**. Proves the broker recomputes, never trusts the requested tier on the card. Also: gate unavailable (ledger/policy unreadable) → **held** (fail-closed), asserted. | REQ-01/REQ-03 recompute + fail-closed |
| **AT-RING-1** | Unit: assert `{"trust-policy.md","docs/factory/trust-policy.md","docs/factory/never-graduates.md"} ⊆ RING_PATHS`. Guards against a refactor silently dropping a policy file from the ring. | ring coverage regression guard |
| **AT-RING-2** | Unit: assert `broker/merge_gate.py`'s ring path-list ⊇ `factory.immutable_ring.RING_PATHS`. Guards the broker checking *less* than the worker-side gate after a ring-path edit (§V2.1 drift guard). | broker ring-list parity |

All time-dependent tests inject `now`/`outcome_ts` (mockable ms-epoch parameters on `compute_tier` and the ledger writer) — **no test waits real wall-clock**; the real 30-day gate is STAGED (§6).

---

## 6 — Staged (wall-clock) gate

The 30-day requirement is real-world wall-clock and cannot be exercised in-sandbox. Append to `STAGED-GATES.md`:

| ID | Gate | Why staged | Exact action to flip |
|---|---|---|---|
| SG-P1b-1 | A real personal repo × task-type graduates held→auto after a genuine 10-clean / 30-day / 0-revert record | The 30-day window is wall-clock; tests inject `now`. Only a real month of clean merges proves the live gate | After ≥30 days of real factory operation on a personal repo: `sqlite3 ~/.hermes/factory/jobs.db` (trust_ledger table) shows ≥10 consecutive `merged_clean` for one `(repo,task_type)` spanning ≥30 days, 0 reverts; flip profile to `auto-merge-personal-non-prod`; the **next** qualifying merge for that key arrives as an `auto_sent` card (broker-approved, `decided_by=trust:auto`) not a held one, and lands with no human tap. |
| SG-P1b-2 | Live demotion on a real rejection/revert | Needs a real post-graduation bad outcome | After SG-P1b-1: reject one merge (or let the P5.4 sentinel record a `reverted`) for the graduated key; confirm the following merge for that key is **held** again (tier dropped to 0 automatically). |

The synthetic-outcome tests (AT-GRAD/DEMOTE with injected time) satisfy the P1b **design/build** exit gate now; SG-P1b-1/2 are the honest real-world confirmations.

---

## 7 — Escalations & interface findings

- **REQ-01 escalation — no `workflow` table; outcome fields partially missing.**
  1. The plan's "policy engine's workflow table" **does not exist** (verified: no `workflow` table in factory/broker; `agent/plugin_llm.py:_TrustPolicy` is unrelated plugin trust). Resolved by a dedicated `trust_ledger` table co-located in the jobs DB (§2.1) — faithful to intent, avoids inventing a phantom table.
  2. The `jobs` table has **no explicit merge-outcome field** and **no `task_type`**. `merged_clean` vs `merged_with_fix` is derivable at *approval time* only (was the diff human-edited / did an auto-fix retry fire) — hence the outcome must be recorded at the terminal transition (§2.3), not back-derived from `state`. Task-type is derived (§2.2).
  3. **`reverted` has no live producer.** Post-merge regression detection is P5.4 (regression sentinel), which does not exist yet. P1b builds the `reverted` outcome column + the revocation-on-revert logic so it is *correct the day P5.4 wires the trigger*, but no P1b test can produce a real revert — AT-DEMOTE-1 injects a `reverted` row directly. This is expected per the plan's sequencing (P1b before P5) and is called out so the verifier does not expect an end-to-end revert path in P1b.

- **Interface finding (positive) — ring already covers the policy files.** `trust-policy.md`, `docs/factory/trust-policy.md`, and `never-graduates.md` are already in `RING_PATHS` and `never-graduates.md` already exists (authored in P2). P1b's ring-protection requirement is therefore *inherited*, not *built* — the only work is authoring `trust-policy.md` at the guarded path. AT-RING-1 guards against regression.

- **Interface finding — the retrofit is a constant→function swap at one seam.** `_MERGE_SAFE_LANE` (supervisor.py:55-57) is the only thing standing between "always held" and "tier-parameterized." No new merge path is needed; `SafeLane`'s `allowed_origins`/C4 is the exact widening lever its own docstring names.

- **Safety call-out for adversarial review (§4.4).** Tier-1 auto-merge must be a **broker** auto-approval (broker mints+burns the nonce, re-checks ring/never-graduates server-side), never the supervisor minting its own nonce — otherwise P1b reopens the self-approval hole that started the whole project. This is the highest-risk decision in the design and is deliberately isolated in task P1b-d for focused review.

---

## 8 — Digest

**Verdict:** Buildable as policy-over-existing-data + one constant→function swap; no new merge path, approval surface, or credential boundary. All four REQs satisfiable for TDD.

- **REQ-01** (ledger + job→ledger feed, no double-write): §2.1 schema (`trust_ledger`, co-located in jobs DB), §2.2 task-type/repo-class derivation, §2.3 co-transactional `record_terminal_outcome` (one atomic commit, sole writer preserved), §2.4 files.
- **REQ-02** (trust-policy.md + pure tier-compute + ring): §3.1 pinned thresholds YAML, §3.2 `compute_tier` with 3 early-return hard gates (work / never-graduates / unknown-type), §3.3 ring-protection **already live** in P2.
- **REQ-03** (profiles + single parameterized gate): §4.1 profile = tier *ceiling* (caps, never lifts), §4.2 `merge_disposition` inputs→auto|held, §4.3 constant→`_tier_safe_lane_json` swap at supervisor.py:418 (one path), §4.4 auto-merge stays broker-nonce-gated (not self-approval).
- **REQ-04** (AT plan + disjoint tasks): §5.1 tasks P1b-a..d (a/b/c new files, d edits alone), §5.2 twelve falsifiable RED tests mapping 1:1 to the exit gate (graduate/demote/never-graduates/work-tier0, all with injected time).

**Reused interfaces (verified):** `factory/job_store.py` jobs table + `transition(extra=)`; `factory/supervisor.py:_enqueue_merge_card`/`approve_merge` (retrofit seam, `_MERGE_SAFE_LANE` const at :55); `broker/safe_lane.py` C4/`allowed_origins`; `broker/held_store.py:enqueue`; `broker/approval.py` nonce wall; `factory/immutable_ring.py:RING_PATHS` (already lists the policy files) + `check_diff`/`fs_scope_excludes`; `docs/factory/never-graduates.md` (exists, ring); `factory/merge_policy.py` (repo-class home); `STAGED-GATES.md`.

**Escalations:** (1) no `workflow` table — use a dedicated `trust_ledger` in the jobs DB; (2) no `task_type`/outcome column — derive + record-at-transition; (3) `reverted` has no live producer until P5.4 — column+logic built, injected in tests. All in §7.

**Branch verified:** `factory`. **Output:** `docs/plans/harness/fable/p1b/P1b-design.md`. Zero code/commits made.

---

## Revision v2 — red-team P0 closures

**Why:** `P1b-review.md` (SHIP-WITH-FIXES) found two P0s that make tier-1 auto-merge unsafe as v1 wrote it, plus two P1s. This section is the **binding** design for the auto-merge authority; it supersedes §4.4 and revises §4.3, §5.1, §5.2. Every claim below is re-verified against the `factory` branch (file:line cited; `git rev-parse` = `factory`).

**The two holes, restated in one sentence each.** (P0-1) The "broker re-checks the ring server-side" that v1 §4.4 promised **does not exist** — `check_diff` is called only from `factory/gauntlet.py:339` (worker-side); the broker's `auto_sent` branch (`broker/server.py:277-286`) runs `executor(row)` with **no ring / never-graduates / tier check**. (P0-2) The supervisor is handed a **live in-process `ApprovalAuthority`** (constructor param `factory/supervisor.py:113`, stored `:134`, `self._authority.approve(...)` in-process `:446`) built with the real `broker_secret`; that object also exposes `mint_nonce()` (`broker/approval.py:48`) — **any holder can self-mint + self-approve**. The nonce wall only holds across the **socket** (`_CLIENT_METHODS` = `{health, enqueue_action, list_pending, approve, reject, resolve_model_key}`, `server.py:28-36`; `mint_approval_nonce` is explicitly *not* an RPC, `server.py:342-350`).

### V2.1 — REQ-01: the broker-side auto-merge gate (`broker/merge_gate.py`)

A **new broker-owned module** `broker/merge_gate.py`. It is the server-side wall v1 only claimed. It exposes one pure-ish function:

```
def evaluate_auto_merge(row, *, ledger_reader, policy, trusted_capability, trusted_task_type,
                        repo_class, git_runner, now_ms) -> ("auto"|"held", tier, reason):
    # Runs INSIDE the broker process, over the merge card the supervisor enqueued.
    # 1. RING RE-CHECK (the P0-1 fix). Compute the diff the merge would land:
    #       diff = git_runner(["diff", f"{base}...{branch}"], worktree)   # card.base, card.branch, card.worktree
    #    then check_ring(diff, worktree_root=worktree)  # broker-owned copy, §V2.1 note
    #    → on RingViolation OR on ANY error/unavailability → return ("held", 0, "ring") [FAIL CLOSED]
    # 2. NEVER-GRADUATES RE-CHECK. if trusted_capability in read_never_graduates():  return ("held", 0, "never")
    # 3. TIER RECOMPUTE from the ledger (never trust a tier stamped on the card):
    #       t = compute_tier(repo, trusted_task_type, ledger_reader.read_rows(...), now_ms,
    #                        policy=policy, repo_class=repo_class, capability=trusted_capability)
    #    if t < 1: return ("held", 0, "tier")
    # 4. all passed → ("auto", t, "ok")
```

**Fail-closed is total:** any exception, missing file, unreadable ledger, unparseable diff, or `git diff` failure → `("held", 0, ...)`. The broker never auto-merges on a check it could not run.

**Broker owns its own ring list (REQ-01 `escalate_if` resolution).** The broker imports **nothing** from `factory/` today (`grep 'from factory' broker/*.py broker/executors/*.py` = 0 hits); the layering is factory→broker and must stay that way — the broker is the credential-holding trust root and must not depend on worker-guard code outside its own boundary. So `broker/merge_gate.py` carries a **verbatim copy of the ring path tuple** and its own `check_ring(diff, worktree_root=...)` (a small port of `immutable_ring.check_diff`'s path-canonicalize-and-match logic, `immutable_ring.py:184-258`), plus a reader for `docs/factory/never-graduates.md`. The on-disk policy files are the same ones the ring already protects (they cannot be edited by a worker — `RING_PATHS`, `immutable_ring.py:45-60`). **AT-RING-2** asserts the broker's list ⊇ `factory.immutable_ring.RING_PATHS` so the broker can never silently check *less*.

**Hook point (concrete):** `broker/server.py`. Add an `auto_merge` RPC method `_rpc_auto_merge` and add `"auto_merge"` to `_CLIENT_METHODS` (`server.py:28-36`). It is the ONLY new client verb; it takes an `action_id` (the already-enqueued held merge card) and **no nonce** (the client has none). Its body:
1. `row = self._store.get(action_id)`; require `row["type"] == "merge"` and `row["state"] == "held"` (else error).
2. `disp, tier, reason = self._merge_gate.evaluate_auto_merge(row, ...)` — the broker reads the trusted `capability`/`task_type` from the card payload (§V2.4), the ledger (a broker-opened **read-only** connection to the jobs DB path), the pinned policy, and computes the diff via the card's worktree.
3. If `disp == "auto"`: the **broker** mints+burns a nonce internally (`self._approval.mint_nonce(action_id)` then `self._approval.approve(action_id, nonce, executor=self._executor_for(row))`), `decided_by="trust:auto"`. This reuses the *identical* nonce+executor path as human approval (`_rpc_approve`, `server.py:292-308`) — the mint stays broker-internal, never crosses the socket.
4. If `disp == "held"`: leave the card held (a human still approves it via the normal Telegram nonce button); return `{"disposition":"held","reason":reason}`.

This closes P0-1: the ring/never-graduates/tier re-check now runs **in the broker, over the diff**, and fails closed. It is a **broker-maintainer edit** (task P1b-e), never worker-authored (`broker/` is itself a ring path, `immutable_ring.py:47`).

### V2.2 — REQ-02: auto-merge is a socket request; the broker decides + executes

The full flow, with the trust boundary marked:

```
SUPERVISOR (holds NO mint-capable authority in prod)                BROKER (holds broker_secret + tier policy + ring list)
  1. enqueue merge card, state="held", payload stamps            ─►  HeldStore.enqueue(... state="held")   [unchanged seam]
     trusted {capability, task_type, requested_tier(advisory)}
  2. broker_client.auto_merge(action_id)          ── SOCKET ──►   _rpc_auto_merge:
                                                                     evaluate_auto_merge()  ← ring+never+tier RE-CHECK (V2.1)
                                                                       held  → stays held (human approves later)
                                                                       auto  → broker mints+burns nonce INTERNALLY,
                                                                               runs merge_executor, decided_by="trust:auto"
  3. ◄── {disposition, tier, reason} ────────────────────────────   (supervisor never saw a nonce)
```

**The supervisor requests; the broker decides.** The decision (tier + ring + never-graduates) and the execution (nonce mint + executor) both live in the broker, which holds the secret and the policy. The supervisor's only new capability is `BrokerClient.auto_merge(action_id)` — a request verb with **no nonce and no mint**. Add `auto_merge` to `broker_client.py`'s method set (it is a ring path, `immutable_ring.py:46` — maintainer edit) mirroring the existing `approve`/`reject` request stubs.

**Why this is not a bypass:** the executor, the nonce mechanism, and now the ring gate are all on the critical path exactly as for a human approval — the only difference from a human tap is *who* decided (the broker's tier policy vs. the owner's thumb), and that decision is itself gated by the server-side re-check. Auto-merge is "the broker approves it for you after re-verifying," never "the assistant approves itself."

### V2.3 — REQ-02: the supervisor must NOT hold a mint-capable authority in production

Today `Supervisor.__init__` takes `authority: ApprovalAuthority` (`supervisor.py:113`) and calls `self._authority.approve(...)` in-process (`:446`). **This is the self-mint surface** — `approve` and `mint_nonce` sit on the same object. v2 rule:

- **Production wiring:** the factory that constructs the prod `Supervisor` passes **no real `ApprovalAuthority`**. Human approval and auto-merge both route over the socket via `BrokerClient` (`approve` for the human path, `auto_merge` for the trust path). The supervisor's `approve_merge` (`:429`) is rewritten to call `broker_client.approve(action_id, nonce)` — where the *nonce came from the broker's out-of-band Telegram mint*, never from an in-process `mint_nonce`. The supervisor holds no secret and no mint.
- **Test wiring:** tests MAY inject a fake authority (a stub with a no-op `approve` and no `mint_nonce`, or a socketpair-backed real `BrokerServer`). The prod constructor path must accept `authority=None` and refuse to run the in-process approve branch when it is None (fail-closed: no authority ⇒ must go over the socket).
- **Guard test AT-NOMINT-1:** assert the production `Supervisor` is constructed without a mint-capable authority; this fails RED against today's `test_supervisor.py:144,162` wiring (which passes a real `ApprovalAuthority(held, broker_secret=...)`).

This closes P0-2: in production the only holder of `mint_nonce` is the broker process; the supervisor can request but never mint.

### V2.4 — REQ-03: merge-specific auto conditions + trusted capability/task-type provenance

**P1-A4 (merge not mapped to safe-lane conditions).** The message-oriented SafeLane conditions (C2 recipient allow-list, C3 2000-char/strategic-marker cap, C5 `irreversible_types`) are **not** the trust gate for a `merge` and must not decide it. Two changes:
1. The merge card is **always enqueued `held`** (§4.3 revised) — SafeLane never auto-sends a merge. The *only* path that releases a merge without a human is `_rpc_auto_merge` (§V2.2), whose binding condition is the **trust tier** recomputed server-side, not C2/C3/C4/C5.
2. Add `"merge"` to the broker's `irreversible_types` set (`server.py:57`, currently `{"git_push_force"}`) so that even if some future code widened `allowed_origins`, a `merge` can never auto-send through the *SafeLane* path — it is held-by-default and only the tier-gated `auto_merge` RPC can release it. **Merge joins the held-by-default/irreversible set; only explicit tier-1 auto releases it.**

**P1-B5 (capability/task-type provenance).** `never-graduates` keys off **capability**; if capability were derived from mislabelable worker/spec text, a prod-deploy relabeled "docs" would dodge the gate. v2 rule: **capability and task-type are classified by trusted intake/supervisor code at job creation and recorded on the `jobs` row** (and mirrored onto the merge card payload the broker reads in §V2.1) — never taken from worker output. The worker's return contract stays `{status, branch, summary, test_result}` (no capability field). `derive_task_type` (§2.2) runs in the **supervisor** at job-creation/enqueue time over the *intake spec* (trusted), and the classification is frozen at that point (recorded, immutable thereafter). The broker consumes the recorded value from the card; it does not re-derive from free text. A test (folded into AT-NEVER-1) drives the capability through this trusted field, not a hand-passed arg, so it cannot pass against a stubbed capability.

### V2.5 — Revised task + test summary

- **Tasks (§5.1 revised):** P1b-a/b/c unchanged (new modules). **P1b-d** = factory-side feed wiring + trusted capability/task-type recording at job creation. **P1b-e (NEW, broker-maintainer edit)** = `broker/merge_gate.py` + `broker/server.py` `auto_merge` RPC + `broker_client.py` `auto_merge` verb + the `merge`→`irreversible_types` add. d (factory) and e (broker) are package-disjoint → parallelizable; both depend on a/b/c. **Task count: 5** (a, b, c, d, e).
- **New/changed RED tests (§5.2 revised):** `AT-NONCE-1` rewritten to drive the **socketpair** and assert no mint verb in `_CLIENT_METHODS`; **AT-NOMINT-1** (prod supervisor has no mint-capable authority); **AT-BROKER-GATE-1** (tier-1 merge whose diff touches the ring is STILL held by the broker's server-side `check_ring` — the load-bearing catch); **AT-BROKER-GATE-2** (broker recomputes tier-0 → held; gate unavailable → held, fail-closed); **AT-NEVER-1** rewritten to drive the real `auto_merge` path with a **trusted** capability; **AT-RING-2** (broker ring-list ⊇ `RING_PATHS`). AT-WORK-1 (work/Diligent tier-0) unchanged and now also asserted server-side via AT-BROKER-GATE-2's recompute. All crown-jewel tests drive the **socket boundary**, not an in-process stub authority.

### V2.6 — Re-verified seams (v2)

| Claim | File:line | Verdict |
|---|---|---|
| Broker `auto_sent` branch runs `executor(row)` with no ring/tier/never check | `broker/server.py:277-286` | CONFIRMED — this is the P0-1 gap |
| `check_diff` called only worker-side | `factory/gauntlet.py:339` (sole caller; `immutable_ring.py:194` is a docstring) | CONFIRMED |
| Supervisor holds in-process `ApprovalAuthority`, calls `.approve` in-process | `factory/supervisor.py:113` (param), `:134` (store), `:446` (call) | CONFIRMED — P0-2 |
| `ApprovalAuthority.mint_nonce` exists on the same object as `approve` | `broker/approval.py:48` (mint), `:78` (approve) | CONFIRMED |
| Socket exposes no mint; `_CLIENT_METHODS` is the wall | `broker/server.py:28-36`; `mint_approval_nonce` not an RPC `:342-350` | CONFIRMED |
| `irreversible_types` = `{"git_push_force"}` only (merge absent) | `broker/server.py:57` | CONFIRMED — merge must be added (§V2.4) |
| Broker imports nothing from `factory/` (layering to preserve) | `grep 'from factory' broker/` = 0 | CONFIRMED — broker owns its own ring list |
| Merge card carries `{repo, branch, base, worktree, remote}` → broker can `git diff base...branch` for the ring re-check | `broker/executors/merge_executor.py` payload fields; `factory/supervisor.py:407-416` card build | CONFIRMED — diff is computable server-side |
| `HeldStore.enqueue` has no `capability` param → trusted capability rides in the `payload` JSON | `broker/held_store.py:76-115` | CONFIRMED |

**v2 verdict:** the two P0s are closed by moving the auto-merge decision+execution into the broker (`auto_merge` RPC over the socket + `broker/merge_gate.py` server-side re-check, fail-closed), and by forbidding a mint-capable authority in the production supervisor. The two P1s are closed by making the trust tier the sole binding condition for a merge (held-by-default + `merge` ∈ `irreversible_types`) and by sourcing capability/task-type from trusted job-creation code. Task count rises 4→5 (P1b-e is the broker-maintainer edit). Zero code/commits made; design doc only.
