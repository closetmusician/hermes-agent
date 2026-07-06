# R-2 Report — WhatsApp Pairing Gate + Verify-Before-Active

**Branch:** fable  
**Task:** R-2 (hermes-fable-plan.md §Phase R)  
**Date:** 2026-07-06

---

## Objective

WhatsApp must be either OFF-and-quiet (no retry loop) or PAIRED-and-verified
(active only after a real send-receive round-trip). P0-9 set `enabled: false`;
R-2 adds the verify-before-active logic and confirms the off path is silent.

---

## REQ-01 — Pairing gate (unpaired WhatsApp stays inactive, zero reconnect)

**Evidence:**

`plugins/platforms/whatsapp/adapter.py` `connect()` now calls `_read_creds_registered(creds_path)` immediately after the `creds_path.exists()` check (before any bridge process is spawned). If it returns `None`, `_set_fatal_error("whatsapp_not_registered", ..., retryable=False)` fires and `connect()` returns `False`.

The `retryable=False` flag is the contract the reconnect watcher in `gateway/run.py` checks at line 7792: non-retryable platforms are dropped from `_failed_platforms` immediately, so the 300-second exponential backoff loop never queues them.

Current `~/.hermes/whatsapp/session/creds.json` has `registered: false`. With `enabled: false`, the platform is skipped at `run.py:6867` (zero reconnects). If it were `enabled: true`, the new gate fires first: bridge is never spawned, no retry noise.

Test evidence (16/16 GREEN, `tests/gateway/test_whatsapp_pairing_gate.py`):
- `TestReadCredsRegistered` — 6 unit tests verify `_read_creds_registered()` returns JID or None correctly across all creds states.
- `TestConnectPairingGate::test_registered_false_sets_fatal_non_retryable` — `creds.json` with `registered=false` → `connect()` returns False, `_fatal_error_code == "whatsapp_not_registered"`, `_fatal_error_retryable is False`, `Popen` NOT called.
- `TestConnectPairingGate::test_creds_absent_still_fatal_non_retryable` — No creds.json → existing `whatsapp_not_paired` gate fires (retryable=False).
- `TestConnectPairingGate::test_registered_false_does_not_enter_failed_platforms` — Non-retryable contract confirmed.

---

## REQ-02 — Verify-before-active (ACTIVE only after round-trip succeeds)

**Evidence:**

`connect()` now calls `await self._run_round_trip_probe(_owner_jid)` immediately after `self._http_session = aiohttp.ClientSession()` is created and before `_mark_connected()`. If the probe returns `False`, `_http_session` is closed, and `connect()` returns `False` without ever calling `_mark_connected()`.

This gate applies to BOTH code paths where the connector becomes active:
- Line ~594: reuse of an existing running bridge (hash-fresh).
- Line ~740: new bridge launched and reached status:connected.

The probe sends `POST /send {chatId: owner_jid, message: "[hermes probe]"}` to the bridge. Success requires HTTP 200 + `{"success": true}`. Any other outcome (503, `success: false`, network exception, `_http_session is None`) returns `False`.

Test evidence (new tests in `TestRunRoundTripProbe` and `TestConnectVerifyBeforeActive`):
- `test_success_returns_true` — HTTP 200 + success:true → True.
- `test_http_non_200_returns_false` — HTTP 503 → False.
- `test_bridge_success_false_returns_false` — HTTP 200 + success:false → False.
- `test_network_exception_returns_false` — aiohttp.ClientError raised → False.
- `test_no_http_session_returns_false` — `_http_session is None` → False.
- `test_paired_probe_ok_marks_connected` — full connect() with paired creds + probe-ok → `_mark_connected` called once, returns True.
- `test_paired_probe_fail_does_not_mark_connected` — full connect() with paired creds + probe-fail (503) → `_mark_connected` NOT called, returns False.

---

## REQ-03 — Tests RED-first, mock only external transport

**Evidence:**

Test file created before implementation in RED state (all 16 tests failed with `AttributeError: property 'name' … no setter` once the name-property issue was resolved, then `AttributeError: '_read_creds_registered'` confirming the methods did not exist). Implementation was written after test file was in place. All external WhatsApp transport (bridge HTTP, aiohttp) is mocked; no real phone, no real bridge process is touched.

Existing `tests/gateway/test_whatsapp_connect.py` (29 tests) also remain GREEN after adding `_read_creds_registered` and `_run_round_trip_probe` stubs to `_connect_patches()`.

---

## REQ-04 — Pairing prerequisites documented; STAGED-GATES.md updated

**Evidence:**

`docs/plans/harness/fable/STAGED-GATES.md` has a new row `SG-R-2` explaining the exact action to flip the gate live (pair the phone, enable WhatsApp, confirm `Round-trip probe passed` in logs). The current machine has `registered=false` and `enabled=false` — the real-phone flip is staged.

Pairing prerequisites:
1. `hermes whatsapp` (or equivalent QR flow) to scan with phone.
2. Confirm `~/.hermes/whatsapp/session/creds.json` has `"registered": true`.
3. Set `WHATSAPP_ENABLED=true` in `~/.hermes/.env`.
4. Restart gateway; watch `hermes logs` for `Round-trip probe passed`.

---

## Files Changed

| File | Change |
|---|---|
| `plugins/platforms/whatsapp/adapter.py` | Added `_read_creds_registered()`, `_run_round_trip_probe()`; modified `connect()` in two call sites |
| `tests/gateway/test_whatsapp_pairing_gate.py` | New test file — 16 tests (REQ-01, REQ-02, REQ-03) |
| `tests/gateway/test_whatsapp_connect.py` | Added `_apply_all()` helper + `_read_creds_registered`/`_run_round_trip_probe` stubs to `_connect_patches()`; fixed two standalone tests |
| `docs/plans/harness/fable/STAGED-GATES.md` | Added SG-R-2 row (REQ-04) |

---

## Test Results

```
tests/gateway/test_whatsapp_pairing_gate.py   16/16 PASS
tests/gateway/test_whatsapp_connect.py        29/29 PASS
Total                                          45/45 PASS
```

---

## STAGED

`SG-R-2` — real-phone round-trip probe requires pairing creds with `registered=true`. See `STAGED-GATES.md` for exact flip procedure.
