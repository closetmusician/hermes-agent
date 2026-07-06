# pm_os changes pending owner commit (separate repo)

Phases R and later land some code in `~/Code/pm_os` (a SEPARATE git repo from hermes).
Per the run constraint, the orchestrator did NOT commit these — you review + commit them in pm_os.
All were verified green by the independent Phase-R verifier (61 pm_os tests across the four R suites).

## Files changed / added in ~/Code/pm_os

**R-3 + R-3-gap (single token-refresh owner + shared lock):**
- `bin/ensure-tokens.js` (M) — sole refresh authority; delegates locking to new lib
- `lib/refresh-lock.js` (new) — extracted exclusive lock helper (atomic O_CREAT|O_EXCL, 20-min stale eviction)
- `bin/sp-checkout.js` (M) — now acquires the shared refresh lock before its FOCI rotate (closes the 2nd-writer gap)
- `bin/extract-all-tokens.js`, `bin/extract-teams-chat-token.js`, `bin/extract-teams-token.js` (M) — exit 1 unless spawned by ensure-tokens
- `test/single-refresh-owner.test.js` (new), `test/r3-gap-sp-checkout-lock.test.js` (new)

**R-4 (SSO expiry → re-auth signal):**
- `bin/ensure-tokens.js` (M, same file) — writeReauthNeeded/clearReauthNeeded; classifies SSO/FOCI expiry
- `lib/status.js` (M) — exposes `reauthNeeded` field; writes `state/reauth-needed.json`
- `bin/pm-status.js` (M) — shows "re-auth needed: <lane>" row
- `test/ensure-tokens-sso-expiry.test.js` (new)

**R-7 (scheduled-skill preflight):**
- `lib/scheduled-preflight.js` (new) — checkPreflight(); entry/env/files/token/broker checks
- `bin/run-jira.js`, `bin/run-weekly.js`, `bin/run-morning.js` (M) — preflight front-block (run-weekly: after --dry-run bypass)
- `test/scheduled-preflight.test.js` (new)

## To commit (in ~/Code/pm_os)
```
cd ~/Code/pm_os
git add bin/ensure-tokens.js lib/refresh-lock.js bin/sp-checkout.js \
  bin/extract-all-tokens.js bin/extract-teams-chat-token.js bin/extract-teams-token.js \
  lib/status.js bin/pm-status.js lib/scheduled-preflight.js \
  bin/run-jira.js bin/run-weekly.js bin/run-morning.js \
  test/single-refresh-owner.test.js test/r3-gap-sp-checkout-lock.test.js \
  test/ensure-tokens-sso-expiry.test.js test/scheduled-preflight.test.js
node --test test/*.test.js   # confirm green
git commit -m "hermes-factory R-phase: single refresh owner + SSO re-auth signal + scheduled preflight"
```

## Known minor cleanups (non-blocking, P2)
- `ensure-tokens.js`: `LOCK_PATH` / `LOCK_STALE_MS` now dead after extraction to refresh-lock.js — remove.
- New test files have a few unused vars (beforeEach, makeEmptyTokenDir, makeFakeHome, runScheduled, execFileSync, spawn) — lint cleanup.
- pm_os had ~6 pre-existing test failures before this work (unrelated to R) — not introduced here.
