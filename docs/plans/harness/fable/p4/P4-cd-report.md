# ABOUTME: Completion report for P4-c (overnight queue) + P4-d (morning packet + PR
# ABOUTME: card + question queue). RED-first TDD; 13 tests, all green; no regressions.
# ABOUTME: Scope: factory/overnight_queue.py, factory/morning_packet.py, factory/pr_card.py
# ABOUTME: + tests. Pre-existing failures in test_supervisor/test_worker_runner unchanged.
# ABOUTME: Batch-approve nonce anti-weakening test confirmed: bypass = FAIL.

# P4-cd completion report

**Date:** 2026-07-07
**Task:** P4-c (overnight queue) + P4-d (morning packet + PR card + question queue)
**Branch:** factory
**Status:** GREEN (13/13 tests pass; 0 new failures introduced)

---

## REQ evidence

| REQ | Evidence |
|-----|---------|
| REQ-01 | `test_night_window_stops_new_spawns_outside_window`: NightWindow(2,2) (zero-width) → 0 spawns. `test_inside_window_jobs_are_spawned`: NightWindow(0,24) → 1 spawn. Window guard in `run_night` gating tick calls. |
| REQ-02 | `test_packet_ranked_by_confidence_desc`: confs [0.5, 0.9, 0.2] → card order [0.9, 0.5, 0.2]. `test_batch_approve_still_uses_broker_nonce`: mock.transition called exactly twice with individual action_ids — no bulk bypass. `test_irreversible_card_never_batch_auto`: irreversible card → held_for_review, not batchable. |
| REQ-03 | `test_pr_card_has_all_fields`: all 6 fields present (summary, reasoning, alternatives, reversibility, diff_stat, confidence, risk). `test_long_payload_not_truncated`: 5000-char reasoning survives json.dumps(card.to_dict()) verbatim. |
| REQ-04 | `test_question_queue_tap_answers_resume_job`: enqueue_question → held-action in broker; answer_question → state='approved', result['answer']='v2'. `test_question_payload_not_truncated`: 3000-char question survives broker payload intact. |
| REQ-05 | RED confirmed (ModuleNotFoundError on both files before implementation). Anti-weakening: `test_batch_approve_still_uses_broker_nonce` asserts `mock.transition.call_count == 2` — if batch_approve merges directly without individual transitions, call_count != 2 → FAIL. |

---

## Files written

| File | Lines | Purpose |
|------|-------|---------|
| `factory/overnight_queue.py` | ~185 | NightWindow predicate + run_night loop + _admit_waiting_capacity |
| `factory/morning_packet.py` | ~215 | build() + batch_approve() + partition_for_batch() + enqueue_question() + answer_question() |
| `factory/pr_card.py` | ~140 | PRCard dataclass + build_card() + card_from_job() |
| `tests/factory/test_overnight_queue.py` | ~180 | 5 tests: REQ-01 (window), REQ-02 (cap), REQ-03 (terminal states), REQ-04 (WAITING_CAPACITY) |
| `tests/factory/test_morning_packet.py` | ~210 | 8 tests: REQ-02 (ranking + nonce), REQ-03 (fields + no-truncate), REQ-04 (question), REQ-05 (anti-weakening) |

---

## Test count

- **13 new tests**, all green
- Pre-existing failures: 14 (test_supervisor.py: 9, test_worker_runner.py: 5) — confirmed pre-existing via git stash

---

## Key design decisions

1. **NightWindow** with `start_hour == end_hour` → always inactive (zero-width = test sentinel for expired window).
2. **WAITING_CAPACITY re-admission** via `_admit_waiting_capacity()` — runs before the window check so parked jobs re-admit regardless of window state. The scheduler's `reserve_resume_slot` handles the CAS.
3. **batch_approve** calls `held_store.transition` per action — no bulk path. The anti-weakening test asserts `call_count == len(action_ids)`.
4. **PRCard.to_dict()** is verbatim — no truncation. Long reasoning/alternatives ride the broker's unbounded TEXT column (action-id → full-payload pattern).
5. **confidence=0.5 neutral** for NULL jobs (P4-f not run yet); NEEDS_ATTENTION cards get 0.0 to sort last.
6. **Question queue** uses broker type='question'; `answer_question` transitions 'held'→'approved' + writes result_json — the resume path reads result_json.

---

## Unverified (UNVERIFIED)

- UNVERIFIED: `run-morning.js` integration (design §5.2 calls for a new digest section). The card build and ranking are ready; the JS renderer hookup is deferred (no breaking dependency — morning_packet.build() returns a structured MorningPacket, the renderer is additive).
- UNVERIFIED: code-review-graph blast-radius injection. `risk.source='placeholder'` is documented in pr_card.py per REQ-03.
