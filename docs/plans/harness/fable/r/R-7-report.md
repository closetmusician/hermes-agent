// ABOUTME: Delivery report for Phase R task R-7 — scheduled-skill preflight contract.
// ABOUTME: Documents REQ-01..04 evidence, files produced/modified, test results, and pre-existing failures.
// ABOUTME: Written 2026-07-06. Verifier: cd /Users/yklin/Code/pm_os && node --test test/scheduled-preflight.test.js

# R-7 Delivery Report

**Task:** R-7 — Mandatory preflight contract for every scheduled skill (pm-morning, pm-weekly, pm-jira).  
A missing prereq aborts with a specific reason BEFORE any side-effecting work executes.

**Date:** 2026-07-06  
**Branch:** fable (hermes); diffs on pm_os (separate repo — not committed)  
**Status:** DELIVERED — all REQ-01..04 met; 16 preflight unit tests green; zero new regressions.

---

## Files Produced / Modified

| File | Change | Purpose |
|---|---|---|
| `/Users/yklin/Code/pm_os/lib/scheduled-preflight.js` | NEW | Reusable preflight module (REQ-01) |
| `/Users/yklin/Code/pm_os/test/scheduled-preflight.test.js` | NEW | 16 unit tests (RED-first then GREEN) |
| `/Users/yklin/Code/pm_os/bin/run-morning.js` | MODIFIED | Import + preflight wired before `const date =` (REQ-02) |
| `/Users/yklin/Code/pm_os/bin/run-weekly.js` | MODIFIED | Import + preflight wired AFTER dry-run exit (REQ-02) |
| `/Users/yklin/Code/pm_os/bin/run-jira.js` | MODIFIED | Import + preflight wired at top of `main()` (REQ-02) |
| `docs/plans/harness/fable/r/R-7-report.md` | NEW | This report |

---

## REQ-01: Reusable preflight helper

**Evidence:** `/Users/yklin/Code/pm_os/lib/scheduled-preflight.js`

`checkPreflight(config, _deps)` → `{ ok: boolean, reason: string|null }`.

Checks in order (first failure returns immediately):
1. Entry point file exists (`fs.existsSync(entryPoint)`)
2. Required env vars present and non-empty
3. Required input files exist
4. Token health — delegates entirely to `check-token-health.js` via `execFile` subprocess; exit 0=ok, 1=expired/missing, 2=expiring soon; both 1 and 2 are failures
5. Broker/network reachability — TCP connect probe via `net.Socket` (3 s timeout)

Never throws — all errors surface as `{ ok: false, reason: '...' }`.

DI via `__testable(overrides)` factory (same shape as `lib/llm-preflight.js`).  
Overrideable: `runTokenHealth`, `checkTcp`, `getEnv`.

---

## REQ-02: Wiring into all three skills

### run-morning.js
Preflight fires in `main()` after `--help` exit, before `const date = opts.date || todayDate()`.  
Config: `requiredEnv: ['ANTHROPIC_API_KEY'], checkTokenHealth: true`.  
Variable name `scheduledPreflight` (avoids collision with existing `preflightResult` used by `checkLlmCredential` at Phase 1b).

### run-weekly.js
Preflight fires AFTER the `if (opts.dryRun) { ... process.exit(0); }` block, before Step 1.  
Rationale: dry-run has no side effects; it must remain usable without tokens.  
Config: `requiredEnv: ['ANTHROPIC_API_KEY'], checkTokenHealth: true`.

### run-jira.js
Preflight fires at the very start of `main()`, before any Jira API calls.  
Config: `requiredEnv: ['ATLASSIAN_BASE_URL', 'ATLASSIAN_USER', 'ATLASSIAN_API_TOKEN'], checkTokenHealth: false`.  
(Token health not checked — Jira uses ATLASSIAN basic-auth, not the FOCI token managed by check-token-health.js.)

---

## REQ-03: Abort-before-work contract

**Evidence:** If `scheduledPreflight.ok === false`, each skill writes to stderr:
```
PREFLIGHT FAILED: [skill] preflight failed — <specific reason>
Aborting — no pipeline steps were executed.
```
Then calls `process.exit(1)`. No API calls, no file writes, no subprocess spawns occur before this check.

Test `abort-before-work contract` (in `scheduled-preflight.test.js`) verifies that a skill with a missing entry point aborts before any side-effecting work is called.

---

## REQ-04: Tests

**File:** `/Users/yklin/Code/pm_os/test/scheduled-preflight.test.js`

16 tests in 8 describe blocks (node:test runner, zero npm deps):

| Describe block | Tests | What is covered |
|---|---|---|
| entry point check | 2 | missing/present entry point |
| required env vars | 3 | missing single, missing one-of-many, all present |
| required files | 2 | missing file, present file |
| token health — exit 1 | 1 | expired/missing tokens → fail |
| token health — exit 2 | 1 | expiring soon → fail |
| token health — exit 0 | 1 | healthy → pass |
| broker reachability | 3 | unreachable broker, reachable broker, label in reason |
| full pass + abort contract | 3 | all checks pass, abort-before-work, result shape/never-throws |

Tests written RED-first (verified failing before implementation), then GREEN after.

Run:
```bash
cd /Users/yklin/Code/pm_os && node --test test/scheduled-preflight.test.js
# Expected: 16 pass, 0 fail
```

---

## Test results

```
scheduled-preflight.test.js:  16 pass, 0 fail
run-morning-dedup.test.js:    18 pass, 0 fail
run-weekly-integration.test.js: 25 pass, 0 fail
run-jira.test.js:             27 pass, 0 fail
Full suite (test/*.test.js):  1370 pass, 6 fail
```

The 6 remaining failures are all pre-existing (confirmed via `git stash` baseline — same failures existed before R-7 changes).  
My changes introduce zero new failures.

---

## Scope boundary

This task covers pm_os scheduled scripts only. Hermes's `cron/scheduler.py` uses a different architecture (Python, LLM-based, runs jobs in-process via `run_job()`) and was explicitly out of scope for this backlog item.

---

## pm_os repo diff note

Per task constraints, pm_os changes are NOT committed (pm_os is a separate repo). The orchestrator/owner decides whether to promote the diff. Files to stage explicitly:

```
git add lib/scheduled-preflight.js test/scheduled-preflight.test.js bin/run-morning.js bin/run-weekly.js bin/run-jira.js
```
