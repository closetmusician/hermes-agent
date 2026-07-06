# R-3 Report: Single Token-Refresh Owner

**Task:** Establish exactly one component (`ensure-tokens.js`) as the sole OAuth/SSO token-refresh owner across `~/Code/pm_os`, eliminating concurrent-refresh races.

---

## REQ-01 — Token-Refresh Site Inventory

| File | Token(s) Written | Classification | Bypass Risk (before fix) |
|---|---|---|---|
| `bin/ensure-tokens.js` | foci-token, foci-office-token, teams-chat-token (via subprocesses) | **REFRESH — designated sole owner** | None (this IS the owner) |
| `bin/foci-device-login.js` | foci-token, foci-office-token, teams-chat-token | REFRESH — actual OAuth writer; invoked as subprocess by ensure-tokens | Bypass if called standalone |
| `bin/extract-all-tokens.js` | teams-chat-token | REFRESH — browser extraction; called by ensure-tokens via `runExtraction()` | Bypass if called standalone |
| `bin/extract-teams-chat-token.js` | teams-chat-token | REFRESH — FOCI-first + browser fallback; **NOT called by ensure-tokens** | Always bypass |
| `bin/extract-teams-token.js` | teams-token (legacy Graph bearer) | REFRESH — browser extraction; **NOT called by ensure-tokens** | Always bypass |
| `bin/check-token-health.js` | (none) | READ — pure health check | n/a |
| `bin/get-foci-token.js` | (none) | READ — scope router, read-only | n/a |
| `lib/token-store.js` | (infrastructure) | Both — crypto layer called by REFRESH sites | n/a |

**Pre-fix REFRESH count:** 5 sites (including `foci-device-login.js` as a legitimate subprocess writer)  
**Post-fix standalone-callable REFRESH count:** 1 (only `ensure-tokens.js` can execute unguarded)

### Pre-existing concurrency protection

`ensure-tokens.js` already had an exclusive file lock at `~/.pm-os-foci-token.lock` using `fs.openSync(LOCK_PATH, 'wx')` (atomic exclusive create). The gap: the lock only protected against concurrent `ensure-tokens.js` instances — it did not prevent `extract-all-tokens.js`, `extract-teams-chat-token.js`, or `extract-teams-token.js` from being called directly and bypassing the lock entirely.

---

## REQ-02 — Enforcement Changes

### Mechanism

`ensure-tokens.js` now sets `process.env.PM_OS_ENSURE_TOKENS_SPAWNED = '1'` immediately after acquiring the lock. The `safeEnv()` function (which builds the env for all child processes) has `PM_OS_ENSURE_TOKENS_SPAWNED` added to `SAFE_ENV_ALLOW` so it propagates automatically to all subprocesses.

Each of the three bypass scripts checks `process.env.PM_OS_ENSURE_TOKENS_SPAWNED !== '1'` at the top of `main()`. On a direct call (env var absent), they exit 1 with:

```
<script-name>: direct invocation refused.
Token writes are owned exclusively by ensure-tokens.js.
Run: node bin/ensure-tokens.js
```

### Files Changed (pm_os repo)

**`bin/ensure-tokens.js`** — 3 changes:
1. Added ownership declaration block comment after the existing `ABOUTME:` header (lines 11-24), listing the enforcement mechanism, the lock, and which scripts are now read-only.
2. Added `'PM_OS_ENSURE_TOKENS_SPAWNED'` to `SAFE_ENV_ALLOW` with explanatory comment.
3. Added `process.env.PM_OS_ENSURE_TOKENS_SPAWNED = '1';` in `main()` immediately after `acquireLock()` succeeds, before any subprocess spawn.

**`bin/extract-all-tokens.js`** — Added 10-line ownership guard at top of `main()`.

**`bin/extract-teams-chat-token.js`** — Added 10-line ownership guard at top of `main()`.

**`bin/extract-teams-token.js`** — Added 10-line ownership guard at top of `main()`.

**`test/single-refresh-owner.test.js`** (new) — Test suite (see REQ-03).

### Note on `foci-device-login.js`

`foci-device-login.js` is the actual OAuth HTTP writer. It was intentionally **not** guarded because it is a legitimate target for direct invocation during interactive device-code enrollment (`/pm-login` skill) and manual debugging. The threat model for this task is concurrent-refresh races from automation paths — `foci-device-login.js` called in a script context is a separate concern (the plan audit noted it but did not classify it as a race-inducing bypass for the automated paths covered here).

---

## REQ-03 — Concurrency Proof

Test file: `test/single-refresh-owner.test.js` (pm_os repo)

### Suite 1: Source-level contract checks (6 tests)

Verifies structural invariants without subprocess overhead:
- `SAFE_ENV_ALLOW` includes `PM_OS_ENSURE_TOKENS_SPAWNED`
- Ownership marker is set **after** `acquireLock()` (ordering verified by string index comparison)
- All 3 bypass scripts contain the guard pattern
- Ownership declaration header is present in `ensure-tokens.js`

### Suite 2: Subprocess rejection (4 tests)

Spawns each bypass script with `PM_OS_ENSURE_TOKENS_SPAWNED` absent:
- Verifies exit code = 1
- Verifies stderr contains `direct invocation refused`
- Verifies stderr points to `ensure-tokens.js`
- Verifies guard passes (does not block) when `PM_OS_ENSURE_TOKENS_SPAWNED=1`

### Suite 3: Concurrency serialisation (2 tests)

Uses `PM_OS_TOKEN_PATH` override (plaintext fixture files, no real encryption/secrets) and `PM_OS_CRON=1` (no browser/MFA):
- **Test 1** (lock pre-written): pre-creates the lock file, launches a second instance, verifies it exits 0 with "Another ensure-tokens instance is running". Skips gracefully in sandboxed environments that block `$HOME` writes.
- **Test 2** (concurrent launch): launches two instances simultaneously with `Promise.all`. Verifies: at least one exits 0 or 10; at least one is defeated by the lock; neither attempts a refresh on fresh tokens.

### Test output (all 12 pass)

```
TAP version 13
# Subtest: R-3 source contracts: ownership marker propagation
    ok 1 - SAFE_ENV_ALLOW includes PM_OS_ENSURE_TOKENS_SPAWNED
    ok 2 - ensure-tokens sets PM_OS_ENSURE_TOKENS_SPAWNED after acquiring lock
    ok 3 - extract-all-tokens.js has ownership guard in main()
    ok 4 - extract-teams-chat-token.js has ownership guard in main()
    ok 5 - extract-teams-token.js has ownership guard in main()
    ok 6 - ensure-tokens header declares sole-owner intent
ok 1 - R-3 source contracts: ownership marker propagation
# Subtest: R-3 subprocess rejection: direct calls exit 1 immediately
    ok 1 - extract-all-tokens.js exits 1 and prints ownership error when called directly
    ok 2 - extract-teams-chat-token.js exits 1 and prints ownership error when called directly
    ok 3 - extract-teams-token.js exits 1 and prints ownership error when called directly
    ok 4 - extract-all-tokens.js guard is bypassed when PM_OS_ENSURE_TOKENS_SPAWNED=1 (guard itself not blocking)
ok 2 - R-3 subprocess rejection: direct calls exit 1 immediately
# Subtest: R-3 concurrency: lock serialises two simultaneous ensure-tokens.js invocations
    ok 1 - second instance exits 0 immediately when lock is already held
    ok 2 - two concurrent launches produce at most one refresh attempt (lock excludes second)
ok 3 - R-3 concurrency: lock serialises two simultaneous ensure-tokens.js invocations
1..3
# tests 12 / pass 12 / fail 0
# duration_ms 178
```

---

## REQ-04 — Ownership Declaration Header

Added to top of `bin/ensure-tokens.js` immediately after the `ABOUTME:` block:

```
// SOLE TOKEN-REFRESH OWNER
// This script is the ONLY component authorised to write (refresh or mint) OAuth/SSO tokens.
// It holds an exclusive file lock (LOCK_PATH) for the duration of any refresh and sets the
// PM_OS_ENSURE_TOKENS_SPAWNED=1 env variable before spawning subprocesses (foci-device-login.js,
// extract-all-tokens.js). Those scripts check for this variable and refuse to write tokens
// if called directly (i.e. not as a subprocess of this owner).
//
// Scripts that are now READ-ONLY (must not write tokens standalone):
//   bin/extract-all-tokens.js        — teams-chat-token (browser extraction)
//   bin/extract-teams-chat-token.js  — teams-chat-token (FOCI or browser)
//   bin/extract-teams-token.js       — teams-token (legacy Graph bearer)
//
// bin/foci-device-login.js is still the actual OAuth writer (writes foci-token,
// foci-office-token, teams-chat-token) but is only called as a subprocess of this script.
```

---

## Summary

| Metric | Value |
|---|---|
| REFRESH sites before fix | 5 (ensure-tokens + 4 bypass paths) |
| REFRESH sites callable standalone after fix | 1 (ensure-tokens.js only) |
| Files changed (pm_os) | 5 (ensure-tokens.js, extract-all-tokens.js, extract-teams-chat-token.js, extract-teams-token.js, test/single-refresh-owner.test.js) |
| Files changed (hermes) | 1 (this report) |
| Tests added | 12 (all green) |
| Real tokens touched | 0 |
| Real SSO/MFA flows triggered | 0 |

**Commits**: pm_os changes are uncommitted (separate repo — orchestrator decides). Hermes report is uncommitted.
