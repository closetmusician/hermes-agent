# R-Verification — Independent Re-Run of Phase R

# ABOUTME: Independent verifier re-ran every Phase R deliverable against the plan's R exit gate.
# ABOUTME: Verdicts are VERIFIED / UNVERIFIED / CONTRADICTED with re-derived evidence. Fixes nothing.

**Verifier role:** GOVERNANCE_EXEMPT — re-runs work, produces this file only, fixes nothing.
**Date:** 2026-07-06
**Repos:** hermes @ `/Users/yklin/Code/hermes` (branch `factory`); pm_os @ `~/Code/pm_os` (branch `main`, R-3/4/7 diffs UNCOMMITTED but on disk).
**Method:** Every test suite RE-RUN by the verifier; every abort/gate path opened in source; grep for token owners done independently; SSO→degraded chain demonstrated live end-to-end.

---

## Overall R-gate verdict: **PASS-WITH-STAGED (one P1 finding on R-3)**

Every R exit-gate bullet is demonstrated working. One finding partially contradicts the R-3
report's central claim ("standalone-callable REFRESH count: 1") but does **not** break the R gate's
own wording in a way that blocks overnight safety — see BLOCKING/FINDINGS below. WhatsApp real-phone
flip and OpenRouter/launchd items remain STAGED (expected, owner-gated).

---

## Claim table

| REQ | Deliverable | Test re-run (verifier) | Code path opened | Verdict |
|---|---|---|---|---|
| REQ-01 | R-1 fail-closed mandatory plugin load | `test_mandatory_plugins.py` 17✓ + `test_plugins.py` 100✓ = **117/117** | abort real, not cosmetic | **VERIFIED** |
| REQ-02 | R-2 WhatsApp pairing + verify-before-active | `test_whatsapp_pairing_gate.py` 16✓ + `test_whatsapp_connect.py` 29✓ = **45/45** | reconnect-skip + probe-gate real; config off | **VERIFIED** (minor report inaccuracy) |
| REQ-03 | R-3 single token-refresh owner | `single-refresh-owner.test.js` **12/12** | my own grep found an UNGUARDED writer the report missed | **CONTRADICTED** (report claim), gate P1 |
| REQ-04 | R-4+R-5 SSO→degraded chain | `ensure-tokens-sso-expiry.test.js` **23/23** + `test_factory_health.py` **27/27**; live degraded→clear | end-to-end lit live | **VERIFIED** |
| REQ-05 | R-6 stub-detect + R-7 preflight abort | `test_setup_check.py` **33/33** + `scheduled-preflight.test.js` **16/16**; live stub + live abort | stub→NOT DONE, preflight→abort, dry-run bypass | **VERIFIED** |
| REQ-06 | Lint honesty (P2 cleanup) | n/a | flagged names present in test files | **VERIFIED (recorded)** |

**Test totals re-run by verifier:** hermes 117+45+27+33 = **222 GREEN**; pm_os 12+23+16 = **51 GREEN**. 0 failures in every targeted suite.

---

## REQ-01 — R-1 fail-closed mandatory plugin load — **VERIFIED**

- **Re-run:** `./scripts/run_tests.sh tests/hermes_cli/test_mandatory_plugins.py tests/hermes_cli/test_plugins.py` → **117 passed, 0 failed** (17 + 100).
- **Abort is real (not a swallowed warning):**
  - `hermes_cli/plugins.py:1484-1490` — `_discover_and_load_inner` raises `MandatoryPluginLoadError(f"Gateway startup aborted: mandatory security plugin(s) failed to load: {failed_names}...")` when `self.mandatory_load_failures` is non-empty.
  - `hermes_cli/plugins.py:1865-1874` — `_load_mandatory_plugin()` records the failure in `mandatory_load_failures` and logs CRITICAL when a mandatory plugin loads `enabled=False`.
  - `gateway/run.py:6795-6802` — `GatewayRunner.start()` wraps `discover_and_load()` in try/except, catches `MandatoryPluginLoadError`, logs CRITICAL, `return False`.
  - `gateway/run.py:20250-20252` — `success = await runner.start(); if not success: return False` — the gateway never enters its serving loop. Abort propagates to the process.
- **Live proof:** wrote a broken mandatory `control-room` plugin into a tmp `HERMES_BUNDLED_PLUGINS` dir; `discover_and_load(force=True)` raised `MandatoryPluginLoadError` naming `control-room`. No fail-open.
- **SAFE_MODE does not falsely trigger:** `hermes_cli/plugins.py:1281-1284` early-returns before any discovery when `HERMES_SAFE_MODE=1`. Live test with the same broken plugin + `HERMES_SAFE_MODE=1` → no raise, registry size 0.

## REQ-02 — R-2 WhatsApp pairing + verify-before-active — **VERIFIED** (with a minor report inaccuracy)

- **Re-run:** `test_whatsapp_pairing_gate.py` 16✓ + `test_whatsapp_connect.py` 29✓ = **45/45**.
- **Unpaired → zero reconnect (retryable=False):** `plugins/platforms/whatsapp/adapter.py:515-528` — `connect()` calls `_read_creds_registered(creds_path)`; if `None`, `_set_fatal_error("whatsapp_not_registered", ..., retryable=False)` then `return False` **before** the bridge process is spawned. `gateway/run.py:7772-7792` — the reconnect watcher drops non-retryable adapters from `_failed_platforms` (no 300s backoff loop).
- **Paired-but-probe-fail → not ACTIVE:** `adapter.py:759-766` — on probe failure the code closes `_http_session`, sets it `None`, `return False` **without** calling `_mark_connected()`. Same gate at the reuse path (~line 618).
- **Config off:** `~/.hermes/config.yaml:355` → `whatsapp: enabled: false` (authoritative; the gateway skips the platform at run.py:6867). Satisfies "off-and-quiet."
- **Minor report inaccuracy (non-blocking):** R-2 report states the off state rests on `~/.hermes/.env`; in fact `.env` has `WHATSAPP_ENABLED=true`, and it is `config.yaml:355 enabled: false` that keeps WhatsApp off. The gate ("off-and-quiet OR paired-and-verified") holds either way; only the report's attribution is loose.

## REQ-03 — R-3 single token-refresh owner — report claim **CONTRADICTED**; gate risk **P1**

- **Re-run:** `single-refresh-owner.test.js` → **12/12 pass**. The 3 guarded scripts DO exit 1 standalone (live-confirmed): `extract-all-tokens.js`, `extract-teams-chat-token.js`, `extract-teams-token.js` each print `direct invocation refused` and exit 1 when `PM_OS_ENSURE_TOKENS_SPAWNED` is unset.
- **My independent grep (the plan's fresh-context "exactly 1 owner" gate):** searched all non-test `bin/**`,`lib/**` for the token-store write primitive `writeToken(` (the sole writer in `lib/token-store.js:57`). Result — writers in **6 files**:

  | File | writeToken site | Guarded? | In R-3 inventory? |
  |---|---|---|---|
  | `lib/token-store.js` | :57 (definition), :104 | n/a (primitive) | yes |
  | `bin/foci-device-login.js` | :178,:404,:605 | NO — **documented** intentional exception | yes |
  | `bin/extract-all-tokens.js` | :85 | YES | yes |
  | `bin/extract-teams-chat-token.js` | :283 | YES | yes |
  | `bin/extract-teams-token.js` | :203 | YES | yes |
  | **`bin/sp-checkout.js`** | **:134** | **NO** | **NO — completely omitted** |

- **CONTRADICTION:** `bin/sp-checkout.js:128-138` reads the FOCI token, sets a rotated `refresh_token`, and calls `writeToken(TOKEN_NAME, existing)` — i.e. it **persists (refreshes) a token** on the Microsoft-rotates-on-use path. It has **no** `PM_OS_ENSURE_TOKENS_SPAWNED` guard and does **not** refuse direct invocation. R-3's own inventory table omits it entirely, and R-3's headline claim "*Post-fix standalone-callable REFRESH count: 1*" is therefore false — it is at minimum 2 (sp-checkout + the documented foci-device-login exception).
- **Severity assessment (why P1, not P0-blocking):** sp-checkout.js is an **operator-invoked CLI** (SharePoint checkout/checkin). No scheduler, cron, or `ensure-tokens.js` path invokes it — its only in-repo references are its own usage/audit strings. The concrete race the gate guards (an *automated* path refreshing concurrently with ensure-tokens at 3am) is not opened by sp-checkout, because nothing automated calls it. But it is a genuine unguarded refresh writer outside the single owner, so the literal gate ("a fresh-context review finds no direct-refresh call outside the single owner") is **not** met. Recommend: add the same ownership guard to `sp-checkout.js` (its RT rotation) or explicitly document it as an operator-only exception like foci-device-login.
- **"Refreshed exactly once by owner":** the concurrency suite (Suite 3, 2 tests) passed — lock excludes the second concurrent `ensure-tokens` instance; fresh tokens trigger no refresh.

## REQ-04 — R-4+R-5 SSO→degraded chain — **VERIFIED** (live end-to-end)

- **Re-run:** `ensure-tokens-sso-expiry.test.js` **23/23**; `tests/test_factory_health.py` **27/27**.
- **R-4 marker write is real:** `bin/ensure-tokens.js:218-238` — `scheduledAbort(reason,{lane,expiryType})` calls `writeReauthNeeded(lane,expiryType,reason)` (guarded by `if (lane && expiryType)`), notifies, then `process.exit(SCHEDULED_EXIT_CODE)` — no retry loop. `clearReauthNeeded()` called at 6 success exit points (lines 727,829,868,905,969,1093).
- **Live end-to-end (the requested demonstration):**
  - Baseline (marker `{}`): `factory_health.py` `[5] Lane State → all lanes: ok`, lane section healthy.
  - Wrote fake `~/Code/pm_os/state/reauth-needed.json` = `{lane:"teams-chat", expiry_type:"sso-session", reason:"VERIFIER simulated SSO expiry", ...}`; re-ran `scripts/factory_health.py`:
    ```
    [5] Lane State
      teams-chat: DEGRADED [sso-session] — re-auth needed: teams-chat — run /pm-login
        reason: VERIFIER simulated SSO expiry
    ```
    **exit code = 1** (degraded reflected in exit).
  - Restored marker to `{}`; re-ran → `all lanes: ok`, **exit code = 0**.
  - `factory_health.py:compute_exit_code()` sets `has_network_error=True` (exit 1, not 2 — attributable) when `sections["lanes"]["status"]=="degraded"`.
- **Marker restored:** `~/Code/pm_os/state/reauth-needed.json` is back to `{}`; `git -C ~/Code/pm_os status` shows it unchanged.

## REQ-05 — R-6 stub-detect + R-7 preflight abort — **VERIFIED** (live)

- **Re-run:** `test_setup_check.py` **33/33**; `scheduled-preflight.test.js` **16/16**.
- **R-6 live stub-detection:** `scripts/setup_check.py:classify_factory_health_section` — provider claiming `reachable` with `latency_ms=None` → **NOT DONE**; `latency_ms=0` → **NOT DONE**; `latency_ms=143` → DONE. `classify_jira_lane_phase()` and `classify_calendar_lane_phase()` → **NOT DONE** even though pm_os code exists — the "stubbed lane flagged as not done" gate holds. Empty-file stub also caught (`classify_file_exists`, plugins.py:144-145).
- **R-7 preflight abort — live:** `bin/run-weekly.js:442-452` runs `checkPreflight(...)` after the `--dry-run` exit (line ~91) and aborts `PREFLIGHT FAILED: ... / Aborting — no pipeline steps were executed / process.exit(1)`. Live test:
  - `env -u ANTHROPIC_API_KEY node bin/run-weekly.js` → `PREFLIGHT FAILED: [run-weekly] preflight failed — required env var missing: ANTHROPIC_API_KEY`, **exit 1**, no pipeline steps.
  - `env -u ANTHROPIC_API_KEY node bin/run-weekly.js --dry-run` → `--- DRY RUN ---`, **exit 0**, preflight bypassed (as designed — dry-run has no side effects).
  - All three scheduled skills (`run-morning`, `run-weekly`, `run-jira`) import `scheduled-preflight` and carry the abort block.

## REQ-06 — Lint honesty (P2 cleanup — recorded, non-blocking)

Unused-var lint hits in the new pm_os test files (to be cleaned up; not blocking):
- `test/single-refresh-owner.test.js` — `execFileSync`
- `test/ensure-tokens-sso-expiry.test.js` — `makeEmptyTokenDir`, `makeFakeHome`, `runScheduled`
- `test/scheduled-preflight.test.js` — `beforeEach`

(All names confirmed present by the verifier. `makeFakeHome` etc. are declared helpers not referenced in every path.)

---

## BLOCKING list

**None P0.** No R exit-gate bullet is broken end-to-end. One **P1** finding:

- **P1 — R-3 unguarded refresh writer `bin/sp-checkout.js:134`.** A real token-refresh (`writeToken` of a rotated FOCI refresh_token) exists outside the single owner and is unguarded; the R-3 report's inventory omits it and its "count: 1" claim is false. Mitigant: sp-checkout is operator-invoked only (no automation path calls it), so the 3am concurrent-refresh race the gate targets is not actually opened. **Recommended before P1a builds on R-3:** either add the `PM_OS_ENSURE_TOKENS_SPAWNED` ownership guard to sp-checkout's RT-rotation write, or document it as an operator-only exception (as foci-device-login already is).

## STAGED (expected, owner-gated — not verifier-blockable)

- **SG-R-2** — WhatsApp real-phone round-trip (`registered=true` + `WHATSAPP_ENABLED` + gateway restart). Config currently off-and-quiet, which satisfies the gate's "OR off-and-quiet" branch.
- OpenRouter live key (SG-P0-2) and launchd caffeinate bootstrap (SG-P0-1) surface as MANUAL in SETUP-CHECKLIST.md — P0 staged items, not Phase R.

## Notes for orchestrator

- Branch mismatch: several R reports say branch `fable`; the working tree is on `factory`. Cosmetic — the code is present and passes on `factory`.
- pm_os R-3/R-4/R-7 diffs are UNCOMMITTED on `main` (on disk, tests pass). Promotion is the owner's call.
- The reauth marker and pm_os working tree were left exactly as found (marker = `{}`, git clean).
