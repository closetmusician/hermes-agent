# P6-b Coder Report — Voice-Note Consolidation

**Task:** P6-b (backlog mode, GOVERNANCE_EXEMPT)
**Branch:** factory (verified)
**Files touched:** `factory/voice_thread.py` (new), `tests/factory/test_voice_thread.py` (new)

---

## Requirements Evidence

### REQ-01: Multi-note threading → ONE card

- `VoiceThread.add_note` assigns notes to threads via a rolling 6h window: if a prior open thread for the sender has a note within 6h, the new note joins it; otherwise a new thread_id is minted (`_find_open_thread_for_sender`).
- `consolidate_threads` iterates open threads, concatenates transcripts in ts order, calls `split()` once per thread, emits one `task_card` broker action.
- Test evidence: `test_multi_note_thread_single_card` (3 notes → 1 card, `broker.enqueue_action.call_count == 1`, `split.call_count == 1`); `test_notes_outside_window_new_thread` (>6h gap → 2 separate cards).

### REQ-02 (P2-2 cross-note injection fix)

- `consolidate_threads` calls `scan(concat_transcript)` on the full concatenation BEFORE `split()`. If findings are present, `fence()` wraps the blob before it reaches the splitter. This is IN ADDITION to per-note scans at ingest time.
- Load-bearing test: `test_cross_note_injection_caught_on_concat` uses REAL `injection_scan.scan` (no mock), verifies note1 ("push to ") and note2 ("main") each pass individually, but the concat ("push to main") triggers the `push-to-protected` rule, and the text delivered to `split()` is fenced (`[EXTERNAL CONTENT`).
- Sanity test: `test_clean_concat_reaches_split_unfenced` confirms clean text is NOT fenced (no false positives).

### REQ-03: Question-back flow + idempotency

- When `split()` returns `ParkedSpec`, `consolidate_threads` calls `enqueue_question` with the ambiguity reason + 3 proposed answers. Thread is marked `questioned=1` in `thread_state` table.
- On re-run, `is_questioned()` returns True → question-back is skipped.
- Test evidence: `test_vague_thread_triggers_question_back` (enqueue_question called once, broker.enqueue_action NOT called); `test_question_back_is_idempotent` (two consolidation passes → enqueue_question.call_count == 1).

### REQ-04 (RED-first; reuse; durable idempotency; anti-weakening)

- RED confirmed: all 9 tests failed with `ModuleNotFoundError` before implementation.
- Reuse: calls `factory.task_splitter.split`, `factory.morning_packet.enqueue_question`, `factory.injection_scan.scan/fence`. No duplication of voice_intake or intake_sources logic.
- Durable idempotency: note_id is a PRIMARY KEY on `voice_threads`; `add_note` returns False on `IntegrityError`. Tested across re-instantiation: `test_durable_idempotency_survives_restart` creates two `VoiceThread` instances on the same DB path, verifies duplicate note_id is rejected and note count is 1.
- Priority tagging: `test_priority_tag_applied` (urgent → high; plain → normal); `test_priority_is_advisory_not_auto_escalation` (broker action type == "task_card", not "queue_reprioritize").
- Anti-weakening: the cross-note test uses REAL `injection_scan.scan`. Mocking it would trivially pass even without the concat scan — this is noted in the test docstring.

---

## Bugs Fixed During Implementation

- **Deadlock in `mark_consolidated`**: the method called `is_questioned()` (which acquires `self._lock`) while already holding `self._lock`. Fixed by extracting `_is_questioned_nolock()` for lock-held internal use.
- **Fixed-bucket window fragility**: initial `_derive_thread_id` used `ts // THREAD_WINDOW_SECONDS` (a fixed 6h epoch bucket), which would split notes near a bucket boundary into different threads. Fixed by rolling-window lookup in `_find_open_thread_for_sender`: join the most-recent open thread if its latest note is within 6h of the incoming note's ts.

---

## Test Count and Results

- 9 tests in `tests/factory/test_voice_thread.py`
- 9/9 GREEN (verified with `venv/bin/python -m pytest`)
- 44/44 related existing tests pass (test_voice_intake, test_intake_sources, test_injection_scan, test_morning_packet)

---

## Files

- `factory/voice_thread.py` — new (VoiceThread, consolidate_threads, _detect_priority, _derive_thread_id, _consolidate_priority, _format_thread_card)
- `tests/factory/test_voice_thread.py` — new (9 RED-first tests)
