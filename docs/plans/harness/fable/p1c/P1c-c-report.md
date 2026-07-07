# ABOUTME: P1c-c completion report — exactly-one-send ledger (REQ-03).
# ABOUTME: Documents RED-first TDD cycle, the exactly-once proof, and the concurrency
# ABOUTME: fix applied to close the TOCTOU race in the check-and-enqueue path.
# ABOUTME: Report only — no code. Written after all 6 tests passed GREEN.
# ABOUTME: Branch: factory. Task: P1c-c. Author: coder subagent 2026-07-07.

# P1c-c Completion Report — Send-Intent Ledger

**Task:** P1c-c  **Branch:** `factory`  **Date:** 2026-07-07  **Status:** GREEN (6/6)

---

## TDD Cycle

### RED phase
`tests/broker/test_send_intents.py` was written first (no implementation). Running the suite
immediately gave:
```
ModuleNotFoundError: No module named 'hermes_cli.send_intents'
collected 0 items / 1 error
```
RED confirmed — all 6 tests were failing before any implementation existed.

### GREEN phase — first attempt (5/6)
`hermes_cli/send_intents.py` was written with:
- SQLite WAL-mode DB mirroring `held_store.py` durability pattern
- `get()` fast-path SELECT before broker call
- `_insert_intent()` with `INSERT ON CONFLICT DO NOTHING`

Result: 5/6. `test_concurrent_same_key_enqueue_produces_one_broker_action` **failed** with
`expected exactly 1 broker call, got 2`. Root cause: TOCTOU race — two threads both passed
the `get()` check (both saw None) then both called the broker before either committed the
INSERT.

### GREEN phase — fix (6/6)
Collapsed the check-and-enqueue into one critical section under `self._lock`. The lock
serialises the SELECT + broker call + INSERT atomically for the within-process case.
`INSERT ON CONFLICT DO NOTHING` is kept as a cross-process safety net. Result: 6/6 GREEN.

---

## REQ Evidence

| REQ | Test | Assertion | Result |
|-----|------|-----------|--------|
| REQ-01 | `test_second_enqueue_same_key_returns_existing_action_id` | 2 calls, same key → 1 broker call; second returns `deduped:True` + first action_id | PASS |
| REQ-02 | `test_exactly_one_send_on_retry` | approve + retry → 1 broker call, 1 executor call, `deduped:True` on retry | PASS |
| REQ-03 | `test_concurrent_same_key_enqueue_produces_one_broker_action` | 10 threads, same key → 1 broker call; all threads get same action_id | PASS |
| REQ-04 | All tests + `test_dedup_is_load_bearing` | Tests use real SQLite; mocked broker at boundary; removing dedup → broker called twice → `test_second_enqueue_same_key` fails | PASS |

**Additional tests:**
- `test_first_enqueue_creates_broker_action` — baseline happy path
- `test_different_keys_produce_different_broker_actions` — no over-dedup
- `test_dedup_is_load_bearing` — anti-weakening oracle (documented falsification)

---

## Exactly-Once Proof

The P1c design (§4) identifies two halves:

1. **One action per intent** — `SendIntentLedger.enqueue_send()` holds `self._lock` across the
   SELECT + broker call + INSERT, serialising all threads. The second call for the same key
   finds the row in the SELECT and returns `deduped:True` without calling the broker.

2. **One send per action** — `ApprovalAuthority.approve()` (`broker/approval.py:105-106`)
   returns the cached `result_json` if the action is already `executed`, without re-running
   the executor. The broker's nonce is burned on use, so a re-tap is rejected.

Together: retry-before-approval → deduped at the ledger (half 1); retry-after-approval →
same action_id already `executed` → cached result, no re-send (half 2). The `test_exactly_one_
send_on_retry` test covers half 1 end-to-end; the broker's `test_approve_idempotent_on_already_
executed` (existing, `tests/broker/test_approval.py`) covers half 2.

---

## Files Produced

- `hermes_cli/send_intents.py` — the ledger (new)
- `tests/broker/test_send_intents.py` — 6 tests (new)
- `docs/plans/harness/fable/p1c/P1c-c-report.md` — this file

## Files NOT Touched (as required)
- `broker_client.py` — unmodified
- `broker/` — unmodified
- `~/Code/pm_os/` — unmodified

---

## Staged Gate
SG-P1c-3 (Telegram inbox forwarder UNVERIFIED on factory branch — noted in design §1)
remains staged; not in scope for P1c-c.
