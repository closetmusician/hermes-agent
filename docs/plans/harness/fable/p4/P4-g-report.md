# ABOUTME: P4-g completion report — voice-note intake wiring: Telegram audio →
# ABOUTME: transcribe → injection-scan → task_splitter → spec'd VoiceTaskCard
# ABOUTME: delivered via broker. Files: factory/voice_intake.py + tests.
# ABOUTME: Design source: P4-design.md §8 (REQ-04c, P4-7) + §10 P4-g test list.

# P4-g Report — Voice-Note Intake

**Date:** 2026-07-07
**Branch:** factory
**Status:** COMPLETE — 6/6 tests green, 0 regressions

---

## Files Produced

| File | Role |
|---|---|
| `factory/voice_intake.py` | New — Telegram audio→card wiring (P4-g scope) |
| `tests/factory/test_voice_intake.py` | New — 6 RED-first tests, all green |

---

## REQ Evidence

### REQ-01 — audio → injection-scan → task_splitter → spec'd task card → broker
- `process_voice_note()` calls `ingest_voice_note` (P3-f) for the full
  transcribe→injection_scan→split pipeline; NO duplication.
- Formats `VoiceTaskCard` (is_parked=False for crisp transcript) with card_text
  summarising derived tasks (count, titles, acceptance criteria count, ambiguity).
- Delivers via `broker.enqueue_action(type="task_card", ...)` and stores the
  returned `action_id` on the card.
- ParkedSpec path: is_parked=True, broker delivers `type="task_card_parked"`.
- **Test:** `test_audio_transcript_reaches_splitter` ✓, `test_clear_voice_note_becomes_task_card` ✓,
  `test_vague_voice_note_parks_as_question` ✓

### REQ-02 — zero manual steps; idempotent per audio-note id
- `process_voice_note()` is fully automatic (audio → card with no human gate in the loop).
- `VoiceNoteProcessor.process()` maintains an in-memory seen-set; a duplicate `note_id`
  returns `None` without calling `ingest_voice_note` or the broker.
- **Test:** `test_duplicate_note_id_is_skipped` ✓ — broker called once, not twice.

### REQ-03 — RED-first; mock transcription+telegram+broker; injection-scan mandatory
- All 6 tests were confirmed RED (ModuleNotFoundError) before implementation.
- `test_injection_scan_on_transcript_is_mandatory`: patches only `_call_ai_worker` (not
  `ingest_voice_note`), letting real injection_scan run; asserts fenced marker present if
  injection payload reaches the splitter. If voice_intake bypassed ingest_voice_note the
  transcript would reach the splitter unfenced — test would fail.
- `test_work_voice_note_residency_gated`: patches `ingest_voice_note` to raise
  `ResidencyViolation`; asserts `process_voice_note` propagates (does not swallow it). ✓

---

## Test Count
6 tests, 0 failures, 0 skips.

```
tests/factory/test_voice_intake.py::TestAudioTranscriptReachesSplitter::test_audio_transcript_reaches_splitter ✓
tests/factory/test_voice_intake.py::TestVagueVoiceNoteParksAsQuestion::test_vague_voice_note_parks_as_question ✓
tests/factory/test_voice_intake.py::TestClearVoiceNoteBecomeTaskCard::test_clear_voice_note_becomes_task_card ✓
tests/factory/test_voice_intake.py::TestClearVoiceNoteBecomeTaskCard::test_injection_scan_on_transcript_is_mandatory ✓
tests/factory/test_voice_intake.py::TestWorkVoiceNoteResidencyGated::test_work_voice_note_residency_gated ✓
tests/factory/test_voice_intake.py::TestDuplicateNoteIdIsSkipped::test_duplicate_note_id_is_skipped ✓
```

---

## Pre-existing Failures (not caused by P4-g)

19 pre-existing failures in `test_supervisor.py` (9), `test_overnight_queue.py` (5),
`test_worker_runner.py` (5) existed on the branch before this task; confirmed by
stashing P4-g changes and re-running. P4-g introduced 0 regressions.

---

## Architecture Notes

- `VoiceNoteProcessor` owns idempotency (in-memory seen-set); P6 should persist
  it across restarts.
- `process_voice_note()` calls `transcribe(audio_bytes)` twice: once inside
  `ingest_voice_note` (P3-f) and once to capture the transcript for the card.
  For mocks this is a no-op. A single-call caching wrapper is noted as P6 polish.
- `ResidencyViolation` propagates from `ingest_voice_note` — gateway handler must
  catch it and park the note with an appropriate error message.
