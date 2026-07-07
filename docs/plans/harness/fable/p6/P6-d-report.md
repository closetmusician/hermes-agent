# P6-d Report — pm_os wiring (surface only)

**Task:** P6-d — Wire Jira intake + morning briefing into the mission-control panel.
**Branch:** `factory` (verified)
**Status:** GREEN — all 5 tests pass; RED-first cycle verified.

---

## REQ-01 — Jira intake + briefing surfaced

**Evidence:**
- `factory/panel_wiring.py` — `JiraPanelView.live_jira_jobs()` returns all jobs whose
  `intake_source == "jira"` via `store.list_jobs()` (read-only).
- `BriefingPanelView.standup_packet()` delegates directly to `morning_packet.build()`,
  returning a `MorningPacket` with confidence-ranked cards for the panel standup view.
- `test_jira_intake_surfaced` (line 68): enqueues a Jira-sourced job, asserts it appears
  in `live_jira_jobs()`.
- `test_briefing_generator_wired` (line 111): drives a job to AWAITING_APPROVAL, asserts
  `standup_packet()` returns a `MorningPacket` with the card present.

## REQ-02 — Parked pipelines NOT added (negative-inclusive)

**Evidence:**
- `panel_wiring.py` imports ONLY `factory.job_store.JobStore` and
  `factory.morning_packet.{MorningPacket, build}` — no pulse, weekly, or exec_narrative.
- `test_no_pulse_pipeline_added` (line 152): AST import scan — FAILS if pulse is wired.
- `test_no_weekly_pipeline_added` (line 174): AST import scan — FAILS if weekly is wired.
- `test_no_exec_narrative_pipeline_added` (line 198): AST import scan — FAILS if
  exec_narrative is wired.

## REQ-03 — Wired-pipeline mutations ride the broker

**Evidence:**
- `JiraPanelView.trigger_jira_writeback()` calls `broker_client.enqueue_action(type=
  "jira_writeback", ...)` — no direct HTTP or JobStore mutation.
- `BriefingPanelView` is read-only (no mutation path).
- `test_jira_intake_surfaced` asserts `mock_broker.enqueue_action` is called once with
  `type="jira_writeback"` and the action_id is returned.

## REQ-04 — RED-first; parked-absent tests falsifiable

**Evidence:**
- Tests ran RED before `panel_wiring.py` existed (5/5 failed: `FileNotFoundError` on
  source read + import error for `JiraPanelView`/`BriefingPanelView`).
- Negative tests (3-5) are falsifiable: adding `import factory.pulse` to `panel_wiring.py`
  makes `test_no_pulse_pipeline_added` fail.

---

## Test count: 5 tests, all GREEN

```
tests/factory/test_pm_os_wiring_scope.py::test_jira_intake_surfaced         PASS
tests/factory/test_pm_os_wiring_scope.py::test_briefing_generator_wired     PASS
tests/factory/test_pm_os_wiring_scope.py::test_no_pulse_pipeline_added      PASS
tests/factory/test_pm_os_wiring_scope.py::test_no_weekly_pipeline_added     PASS
tests/factory/test_pm_os_wiring_scope.py::test_no_exec_narrative_pipeline_added  PASS
```

## Files (hermes repo — `/Users/yklin/Code/hermes`)

| File | Change | Notes |
|---|---|---|
| `factory/panel_wiring.py` | NEW | Surface glue: JiraPanelView + BriefingPanelView |
| `tests/factory/test_pm_os_wiring_scope.py` | NEW | 5 RED-first tests |
| `factory/job_store.py` | ADDITIVE MIGRATION | `intake_source TEXT` column + migration guard |

**Schema note:** `job_store.py` required a minimal additive migration to store `intake_source`
(the field existed in spec dicts but was silently dropped). The migration adds column
`intake_source TEXT` via `ALTER TABLE … ADD COLUMN` (idempotent), matching the existing
`budget_settled`/`resume_count` migration pattern. No existing tests were broken (44/44 pass).

## pm_os repo changes: none (panel_wiring.py calls the factory-side jira_poller + morning_packet only; run-morning.js is not called directly from the panel wiring layer).
