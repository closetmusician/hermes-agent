# P3-f: Multi-Intake Sources — Implementation Report

**Task:** P3-f  
**Branch:** factory  
**Status:** COMPLETE — 14/14 tests GREEN, 0 regressions  
**Files touched:** `factory/intake_sources.py` (NEW), `tests/factory/test_intake_sources.py` (NEW)  

---

## REQ-01: Spec-doc + Greenfield intake

**Evidence:**

- `ingest_spec_doc(text, *, repo, base_branch)` — injection-scans the document via
  `_fence_if_flagged`, then calls `task_splitter.split()`.  Returns `TaskGraph | ParkedSpec`.
- `ingest_greenfield(idea, *, repo, base_branch)` — returns a `ScaffoldPlan(kind="scaffold-plan",
  stages=[SCAFFOLD, SPEC, IMPLEMENT])` with a canonical `spec_dict` in the `intake.py` shape
  (repo, spec, kind="scaffold-plan", intake_source="greenfield", intake_source_hash).
- `ScaffoldStage` enum added (`scaffold`, `spec`, `implement`) — composable with `two_stage.Stage`.

**Tests (GREEN):**
- `TestSpecDocIntake::test_spec_doc_reaches_splitter` — spec doc → split() called → TaskGraph returned.
- `TestSpecDocIntake::test_spec_doc_returns_parked_when_splitter_parks` — ParkedSpec propagates.
- `TestGreenfieldIntake::test_greenfield_scaffold_plan` — ScaffoldPlan, kind="scaffold-plan",
  SCAFFOLD before SPEC in stages.
- `TestGreenfieldIntake::test_greenfield_scaffold_spec_dict` — spec_dict has correct shape.

---

## REQ-02: Voice-note intake

**Evidence:**

- `ingest_voice_note(audio_bytes, *, transcribe, repo, base_branch)` — calls `transcribe(audio_bytes)`
  (the sole external boundary; mocked in tests), injection-scans the transcript, then calls
  `task_splitter.split()` on the (possibly fenced) transcript.  Returns `TaskGraph | ParkedSpec`.
- `transcribe` is a `Callable[[bytes], str]` parameter — the mock boundary.

**Tests (GREEN):**
- `TestVoiceNoteIntake::test_voice_note_transcribed_to_graph` — mock transcribe called, split called, TaskGraph returned.
- `TestVoiceNoteIntake::test_voice_note_can_return_parked` — vague transcript → ParkedSpec propagates.

---

## REQ-03: Injection scan on every source + idempotency

**Evidence:**

- `_fence_if_flagged(text)` — shared helper: calls `injection_scan.scan()` then `fence()` if findings.
  Called unconditionally by `ingest_spec_doc` and `ingest_voice_note` BEFORE `split()`.
- `ingest_greenfield` calls `scan()` directly (no splitter call — the idea goes into `spec_dict`).
- All three sources produce a stable `intake_source_hash` (SHA-256 over canonical key).

**Tests (GREEN):**
- `TestInjectionScanningAllSources::test_spec_doc_scans_injection` — injection payload fenced, `[EXTERNAL CONTENT` marker in split() arg.
- `TestInjectionScanningAllSources::test_voice_note_transcript_scanned` — transcript injection fenced before split().
- `TestInjectionScanningAllSources::test_greenfield_scan_called_on_idea` — spy confirms `scan` called with idea.
- `TestInjectionScanningAllSources::test_all_three_sources_fence_injection` — master falsifiable test: all three sources fence injection BEFORE split().
- `TestInjectionScanningAllSources::test_injection_property_fails_without_scan` — documentation test (passes trivially, confirms framework).
- `TestIdempotency::test_spec_doc_idempotent_hash` — same text → same spec_hash.
- `TestIdempotency::test_greenfield_idempotent_hash` — same idea → same intake_source_hash.
- `TestIdempotency::test_voice_note_idempotent_hash` — same transcript → same spec_hash.

---

## REQ-04 (TDD): RED-first evidence

RED run confirmed before implementation:
```
13 failed, 1 passed (0.33s)
AttributeError: module 'factory' has no attribute 'intake_sources'
```

GREEN after implementation:
```
14 passed, 0 failed (0.4s)
```

Full related suite (intake + injection_scan + task_splitter): **71/71 GREEN**, 0 regressions.  
Pre-existing failures in test_supervisor, test_job_store, test_gauntlet, test_worker_runner
are unrelated (verified by stash test: same failures without P3-f changes).

---

## Files

- `/Users/yklin/Code/hermes/factory/intake_sources.py` — 195 lines, 5-line ABOUTME, 3+ line comments on each source
- `/Users/yklin/Code/hermes/tests/factory/test_intake_sources.py` — 14 tests

## Design conformance notes

- `intake_sources.py` does NOT touch `intake.py`, `task_splitter.py`, `injection_scan.py`, or any
  P2 shared file.  It calls them; all changes are purely additive (new module).
- `ScaffoldPlan.spec_dict` matches the `intake.py:24` spec-dict shape exactly so the scheduler's
  enqueue path is unchanged (kind="scaffold-plan" is the routing signal).
- `ingest_greenfield` does NOT call `split()` — it returns a ScaffoldPlan for the scheduler to
  drive SCAFFOLD→SPEC→IMPLEMENT.  This matches design §8 ("greenfield → scaffold-plan job kind").
- AI boundaries: only `split()` (patched) and `transcribe` (parameter).  No other mocks.
