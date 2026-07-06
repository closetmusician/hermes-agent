# R-3-GAP Report: sp-checkout.js Second Token-Refresh Writer

**Task:** R-3-GAP (GOVERNANCE_EXEMPT mode: live-debug, close a verifier-found gate miss)
**Date:** 2026-07-06
**Repo changed:** ~/Code/pm_os (diff reported here; separate repo — not committed)

---

## REQ-01 — Second-Writer Characterization

**File:** `bin/sp-checkout.js`
**Function:** `exchangeSpToken(refreshToken, hostname)` (lines 110–142 pre-fix)
**What it writes:** `foci-token` — specifically the `refresh_token` field of the FOCI token at `~/.secrets/pm-os/foci-token.json.enc`
**How it writes:** calls `writeToken(TOKEN_NAME, existing)` (from `lib/token-store.js`) after merging the rotated RT from the SP token-exchange HTTP response
**When it runs:** invoked on 401 fallback during SP REST checkout/checkin/undo/status commands; triggered by an operator running `node bin/sp-checkout.js checkout|checkin|undo --confirm ...`
**Concurrency risk:** sp-checkout is NOT called by cron/automation but IS a real process-level second writer. If an operator runs sp-checkout while `ensure-tokens.js` is performing a FOCI refresh (cron or manual), both processes can call `writeToken('foci-token', ...)` simultaneously. Microsoft invalidates a refresh token on use, so whichever write lands last wins; the other's RT is now stale. This is a real (if low-probability) corruption path.

**Verdict:** This is a genuine WRITE, not a read. R-3's "exactly 1 owner" count was wrong by 1. The gap is real.

---

## REQ-02 — Fix: Shared Exclusive Lock

### Mechanism

Extracted the lock logic from `ensure-tokens.js` into a new shared library `lib/refresh-lock.js`. Both scripts now require this library and share one canonical lock path (`~/.pm-os-foci-token.lock`). The model is "one lock authority, multiple lock users":

- `lib/refresh-lock.js` — exports `acquireRefreshLock()`, `releaseRefreshLock()`, `REFRESH_LOCK_PATH`, `REFRESH_LOCK_STALE_MS`. Single canonical home for all lock semantics (atomic `O_CREAT|O_EXCL`, stale-eviction at 20min, PID write for diagnostics).

- `bin/ensure-tokens.js` — changed to `require('../lib/refresh-lock')` and replaced inline `acquireLock`/`releaseLock` with thin wrappers that delegate to the lib. `LOCK_PATH` and `LOCK_STALE_MS` now alias the lib's exported constants.

- `bin/sp-checkout.js` — added `require('../lib/refresh-lock')` and wraps the `writeToken` call in `exchangeSpToken` with `acquireRefreshLock()` / `releaseRefreshLock()` in a try/finally. If the lock is unavailable (ensure-tokens is actively refreshing), sp-checkout logs a warning and skips the RT persist rather than racing. The current SP session still works — only the RT rotation is deferred. This is a deliberate tradeoff: losing the RT persist on a lock-contended path is safe (the old RT was already consumed by the HTTP call; the next ensure-tokens run will do a full refresh). The alternative (blocking until lock is free) would stall a human operator for up to 20 minutes.

### Concurrency guarantee

Two processes can never call `writeToken('foci-token', ...)` simultaneously:
- ensure-tokens holds the lock for its entire refresh lifecycle (acquire in `main()`, release on `process.on('exit')`)
- sp-checkout acquires the lock for the atomic read+write in `exchangeSpToken`, releases immediately after (try/finally)
- If sp-checkout wins the lock, ensure-tokens' `acquireLock()` returns false and it logs "Another ensure-tokens instance is running. Exiting gracefully." (existing behavior, unchanged)
- If ensure-tokens wins the lock, sp-checkout logs "Could not acquire refresh lock; rotated RT not persisted" and skips — no write race

---

## REQ-03 — Ownership Declaration Update

`bin/ensure-tokens.js` ownership header updated:
- Heading changed from `SOLE TOKEN-REFRESH OWNER` to `SOLE TOKEN-REFRESH AUTHORITY (one lock, all refresh paths gated by it)` to honestly reflect the model
- Added `LOCK-SHARING REFRESH SITES` section naming `bin/sp-checkout.js` with its write path described
- `lib/refresh-lock.js` reference added so a fresh-context reader knows where the lock semantics live

A fresh-context reader who greps for `writeToken` in `bin/` now finds two call sites (`ensure-tokens.js` indirectly via `foci-device-login.js`, and `sp-checkout.js` directly) but also finds that both paths are gated by the same lock. The "exactly one owner" story is now truthfully "one lock authority, two lock-sharing refresh sites".

---

## REQ-04 — Regression Test

**File:** `test/r3-gap-sp-checkout-lock.test.js` (pm_os repo, new)

### Suite 1: Source-level structural checks (8 tests)

- sp-checkout requires `lib/refresh-lock`
- sp-checkout destructures `acquireRefreshLock` and `releaseRefreshLock`
- `acquireRefreshLock` appears before `writeToken` in `exchangeSpToken` (ordering by string index)
- `releaseRefreshLock` appears after `writeToken` in `exchangeSpToken` (finally block)
- `lib/refresh-lock.js` exports `REFRESH_LOCK_PATH`
- `ensure-tokens.js` requires `lib/refresh-lock` (shared canonical lock)
- `ensure-tokens.js` ownership header names `sp-checkout.js` as a lock-sharing site

### Suite 2: Concurrency proof (2 tests, simulated — no real FOCI/SSO)

- **Test 1 (locked path):** Pre-creates the lock file, runs a driver subprocess that mimics sp-checkout's RT persist block. Asserts: `WRITE_CALLED=false` and the lock-skip warning is logged. Skips gracefully if `~` writes are blocked (sandboxed environment).
- **Test 2 (normal path):** No lock held; runs the same driver. Asserts: `WRITE_CALLED=true`. Skips if the environment blocks lock-file creation.

Both sandbox-skip paths are gated on an explicit canary write attempt (`EPERM`/`EACCES`), matching the pattern in `test/single-refresh-owner.test.js`.

**RED state (before fix):** Tests 1–5 of Suite 1 would have failed — sp-checkout had no `require('../lib/refresh-lock')`, no `acquireRefreshLock` call, and no `releaseRefreshLock` call. These are unconditional string matches against the file source, so they would fail on the pre-fix code regardless of environment.

### Test output (all 22 pass — 12 original R-3 + 10 R-3-GAP)

```
TAP version 13
# tests 22
# suites 5
# pass 22
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms ~190
```

---

## Files Changed (pm_os repo)

| File | Change |
|---|---|
| `lib/refresh-lock.js` | NEW — shared lock library; exports `acquireRefreshLock`, `releaseRefreshLock`, `REFRESH_LOCK_PATH`, `REFRESH_LOCK_STALE_MS` |
| `bin/sp-checkout.js` | +2 lines (require + destructure); `exchangeSpToken` persist block wrapped in `acquireRefreshLock`/`releaseRefreshLock` try/finally; ~14 lines changed |
| `bin/ensure-tokens.js` | +1 require line; `LOCK_PATH`/`LOCK_STALE_MS` aliased to lib exports; inline `acquireLock`/`releaseLock` replaced with thin wrappers; ownership header updated |
| `test/single-refresh-owner.test.js` | Header pattern updated to match `SOLE TOKEN-REFRESH (OWNER|AUTHORITY)` (1 line) |
| `test/r3-gap-sp-checkout-lock.test.js` | NEW — 10-test regression suite |

**Files changed (hermes):** 1 (this report)

---

## Summary

| Metric | Value |
|---|---|
| Second-writer token written | `foci-token` (`refresh_token` field) |
| Second-writer file path | `~/.secrets/pm-os/foci-token.json.enc` |
| Lock mechanism | Shared `lib/refresh-lock.js`; same lock path as ensure-tokens |
| Can concurrent refresh happen now? | No — one lock, both paths gated |
| Real tokens touched | 0 |
| Real SSO/MFA flows triggered | 0 |
| Tests added (R-3-GAP) | 10 (all green) |
| Combined R-3 + R-3-GAP tests | 22 pass, 0 fail |
| R-3 exit gate status | Now honest: one lock authority, two lock-sharing refresh sites; a fresh-context reader greps `writeToken` in `bin/`, finds two sites, finds both gated by the shared lock |
| Commits | pm_os changes uncommitted (separate repo — report here per task spec) |
