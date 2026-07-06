# R-4 Report: SSO/Okta Expiry Detection + Re-Auth Signal

**Task:** When an SSO/Okta token expires, the affected lane surfaces a visible "re-auth needed" signal to the owner instead of failing silently or looping. Detection in `pm_os` distinguishes SSO expiry from other errors, emits a named signal, stops blind retry, and writes a machine-readable R-5-consumable marker.

---

## REQ-01 — Classify SSO expiry distinctly from other errors

**Three distinct failure classes now handled:**

| Class | expiry_type | Lane | Trigger path |
|---|---|---|---|
| SSO/Okta session expired | `sso-session` | `teams-chat` | Scheduled mode + headless extraction fails → gate before Phase 3 |
| FOCI token permanently revoked | `token-revoked` | `foci` | `runFociRefresh()` returns `'revoked'` (exit 78 or `invalid_grant`) |
| Generic / transient failure | _(no marker)_ | — | Network error, timeout, or unclassified subprocess failure |

The key insight: SSO expiry is distinguishable because it manifests as "scheduled mode + headless extraction fails" — at that gate (line ~877 in `ensure-tokens.js`), the browser is at an Okta/sign-in page rather than Teams, and no interactive recovery is possible. This is structurally different from a FOCI revocation (which has its own explicit `invalid_grant` signal) and from transient network failures (which get no lane marker).

**Files changed:**

- `bin/ensure-tokens.js`: `scheduledAbort()` signature extended to `scheduledAbort(reason, { lane, expiryType })`. Added `writeReauthNeeded()` and `clearReauthNeeded()` functions. Two `scheduledAbort` call sites now pass lane + expiry_type:
  - SSO gate: `{ lane: 'teams-chat', expiryType: 'sso-session' }`
  - FOCI revocation: `{ lane: 'foci', expiryType: 'token-revoked' }`

---

## REQ-02 — "re-auth needed: <lane>" visible signal + no blind retry

**Human-visible surfaces:**

1. **stdout log** (every run): `re-auth needed: teams-chat (sso-session) — <reason>`
2. **macOS notify**: `"PM-OS: re-auth needed: teams-chat — run /pm-login (<reason>)"`
3. **`pm-status` text table**: `! re-auth needed: teams-chat [sso-session] — run /pm-login` + `reason: <reason>`
4. **Telegram `/pm-status`**: `re-auth needed: teams-chat (sso-session) — /pm-login`

**No blind retry:** `scheduledAbort()` writes the marker then calls `process.exit(SCHEDULED_EXIT_CODE)` (exit 10). The `run-morning.js` LLM preflight aborts cleanly on exit 10. No retry loop exists for exit 10. The marker persists until `clearReauthNeeded()` is called, which only happens after `allCriticalTokensFresh()` confirms recovery.

`clearReauthNeeded()` is called at all 6 successful exit points in `ensure-tokens.js` so the marker clears automatically when re-auth succeeds.

---

## REQ-03 — Tests (RED-first, no real SSO/MFA)

**Test file:** `/Users/yklin/Code/pm_os/test/ensure-tokens-sso-expiry.test.js`

**23 tests across 5 suites — all pass:**

| Suite | Tests | What it covers |
|---|---|---|
| R-4 source contracts: ensure-tokens.js | 9 | `writeReauthNeeded`, `clearReauthNeeded`, `sso-session` type, `token-revoked` type, `teams-chat` lane, `foci` lane, `clearReauthNeeded` on success, `scheduledAbort` signature |
| R-4 source contracts: lib/status.js | 5 | reads `reauth-needed.json`, `reauthNeeded` field in snapshot, cleared-state as null, R-5 contract docs, Telegram rendering |
| R-4 writeReauthNeeded: output schema | 3 | harness writes `sso-session` schema and validates all R-5 fields; harness writes `token-revoked` schema; `clearReauthNeeded` writes `{}` |
| R-4 buildStatusSnapshot: reauthNeeded field | 5 | null when file absent; null when `{}`; carries lane+expiry_type when populated; foci/token-revoked variant; reauthNeeded ≠ needsHuman |
| R-4 non-expiry error: no false re-auth signal | 1 | `writeReauthNeeded` only called inside `if (lane && expiryType)` guard — not on all `scheduledAbort` calls |

No real SSO/MFA/Okta calls. No token file writes. Uses `writeAtomic()` inline harnesses and `buildStatusSnapshot({ skipTokenCheck: true })` for fixture-safe isolation.

**Pre-existing failures** (2 tests in `ensure-tokens-modes.test.js`) were confirmed present before R-4 changes via `git stash` baseline check — not regressions.

**Status tests** (`test/status.test.js`): 30/30 pass, no regressions.

---

## REQ-04 — R-5-consumable marker: `state/reauth-needed.json`

**Schema (written by `writeReauthNeeded()`):**

```json
{
  "ts": "<ISO 8601 timestamp>",
  "lane": "teams-chat",
  "expiry_type": "sso-session",
  "reason": "tokens stale and headless extraction failed — SSO session expired, interactive re-login required",
  "skill": "ensure-tokens",
  "action_needed": "re-auth needed: teams-chat — run /pm-login"
}
```

**Cleared state** (written by `clearReauthNeeded()` after successful re-auth):
```json
{}
```

**R-5 read contract** (implemented in `lib/status.js`):
- File absent → `reauthNeeded = null` (no degradation)
- File = `{}` → `reauthNeeded = null` (cleared, resolved)
- File with `lane` field → `reauthNeeded = { lane, expiry_type, reason, ts, action_needed }` (lane degraded)

R-5 can use `snap.reauthNeeded` to enter a paused/degraded state for the named lane without re-parsing the file.

**`expiry_type` enum:** `sso-session` | `token-revoked` | `token-expired` (last reserved for plain token age expiry, not yet wired).

---

## Diff digest (≤15 lines per file)

**`bin/ensure-tokens.js`** (+65 lines, +2 lines to 2 existing call sites):
- Added `writeReauthNeeded(lane, expiryType, reason)` — writes structured JSON to `state/reauth-needed.json`
- Added `clearReauthNeeded()` — writes `{}` to same file (cleared state)
- `scheduledAbort(reason, { lane, expiryType })` — added optional destructured param; conditionally calls `writeReauthNeeded`; lane-specific notify message
- 2 `scheduledAbort` call sites: SSO gate → `{ lane: 'teams-chat', expiryType: 'sso-session' }`; FOCI revocation → `{ lane: 'foci', expiryType: 'token-revoked' }`
- `clearReauthNeeded()` added at 6 success exit points

**`lib/status.js`** (+18 lines):
- ABOUTME updated with R-5 contract note
- `buildStatusSnapshot()` reads `reauth-needed.json` via `safeReadJson()`; extracts `reauthNeeded` (null if absent/cleared)
- `snapshot.reauthNeeded` field added with R-5 contract comment
- `getStatusMessage()` renders `re-auth needed: <lane> (<expiry_type>) — /pm-login` when set

**`bin/pm-status.js`** (+7 lines):
- `renderText()`: displays `! re-auth needed: <lane> [<expiry_type>] — run /pm-login` block when `snap.reauthNeeded.lane` is set
