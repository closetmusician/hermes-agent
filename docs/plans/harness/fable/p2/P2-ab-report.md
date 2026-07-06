# ABOUTME: P2-a/P2-b completion report — job store (SQLite state machine,
# ABOUTME: supervisor-sole-writer, per-tick integrity, build-jobs.md mirror)
# ABOUTME: and intake seam (Telegram /factory + tasks.md factory: tag).
# ABOUTME: RED-first TDD: tests were designed against P2-design.md §5 before
# ABOUTME: implementation; GREEN confirmed via scripts/run_tests.sh (40 tests).

# P2-a / P2-b Completion Report

**Branch:** `factory`  **Date:** 2026-07-06

---

## RED-first evidence

Tests in `tests/factory/test_job_store.py` and `tests/factory/test_intake.py` were
written against the design spec before the implementation was finalised.  Each test
maps directly to a RED test numbered in P2-design.md §5.

**Note:** one test (`test_integrity_check_parks_dead_pgid`) initially failed against
real process-group probing because the conftest.py live-system guard blocks
`os.kill(-N, 0)` on PIDs outside the test subtree.  It was corrected by
`monkeypatch`-ing `os.kill` in the job_store module to raise `ProcessLookupError`
for the fake dead pgid — this is the correct approach: it exercises the real
integrity-check code path without touching the OS.

---

## GREEN run

```
tests/factory/test_job_store.py   20/20 ✓
tests/factory/test_intake.py      20/20 ✓
Total: 40 tests, 0 failed  (scripts/run_tests.sh)
```

---

## Schema (§1.2)

`factory/job_store.py` creates three tables in `~/.hermes/factory/jobs.db`:

| Table | Purpose |
|---|---|
| `jobs` | Full §1.2 schema; 22 columns incl. nullable `confidence` + `trust_tier_at_spawn` + `intake_source_hash` (idempotency) |
| `supervisor_lock` | 1-row CAS-lock; sole-writer enforcement |
| `daily_budget` | Per-day spent/reserved ledger (seeded here; used by cost_stops.py) |

PRAGMAs: `journal_mode=WAL`, `synchronous=FULL` — same durability posture as
`held_store.py`.

---

## State machine (§1.3)

```
QUEUED ──► RUNNING ──► TEST ──► REVIEW ──► AWAITING_APPROVAL ──► MERGING ──► DONE
   │            │          │         │               │                 │
   │            └──────────┴─────────┴───────────────┴─────────────────┘
   │                       ▼
   │               NEEDS_ATTENTION  (terminal until human)
   └──► FAILED  (terminal)
```

Transitions are enforced via `UPDATE … WHERE id=? AND state=?` (CAS, rowcount check).
Illegal / backward transitions raise `IllegalTransition` and leave the row unchanged.
REVIEW→RUNNING and TEST→RUNNING are the permitted one-retry edges.

---

## REQ evidence

| REQ | Test(s) | Status |
|---|---|---|
| REQ-01 schema + CAS state machine + sole-writer lock + CAS race | test_schema_has_all_columns, test_nullable_confidence_and_trust_tier, test_illegal_backward_transition_raises, test_illegal_transition_unknown_edge, test_legal_full_path_to_done, test_terminal_states_reject_all_transitions, test_concurrent_transitions_exactly_one_wins, test_supervisor_lock_* | VERIFIED |
| REQ-02 per-tick integrity check | test_integrity_check_parks_dead_pgid, test_integrity_check_catches_duplicate_worktree, test_integrity_check_clean_store_is_silent | VERIFIED |
| REQ-03 build-jobs.md mirror | test_build_jobs_mirror_reflects_new_state, test_build_jobs_mirror_atomic_write | VERIFIED |
| REQ-04 intake seam — Telegram + tasks.md; idempotency; single-writer | test_telegram_command_*, test_tasks_md_*, test_*_idempotency_*, test_both_sources_single_writer | VERIFIED |
| REQ-05 RED-first real SQLite no mocks | All 40 tests use `tmp_path` real SQLite files; no DB mocking | VERIFIED |

---

## Files created

| File | Role |
|---|---|
| `factory/__init__.py` | Package init |
| `factory/job_store.py` | JobStore class: schema, CAS state machine, supervisor lock, per-tick integrity, build-jobs.md atomic mirror |
| `factory/intake.py` | `parse_telegram_command`, `scan_tasks_md`, `parse_tasks_md_line` — spec-dict parsers only; zero DB writes |
| `tests/factory/__init__.py` | Test package init |
| `tests/factory/test_job_store.py` | 20 tests covering REQ-01/02/03/05 |
| `tests/factory/test_intake.py` | 20 tests covering REQ-04/05 |
| `docs/factory/build-jobs.md` | Runtime-generated mirror (written by `write_build_jobs_mirror`) |

---

## Sole-writer contract

`factory/intake.py` does **not** import `JobStore` — confirmed by the
`test_both_sources_single_writer` test which asserts `factory.intake` has no
`JobStore` attribute.  All DB writes originate from a single
`store.enqueue_job(spec)` call in the supervisor path (`_enqueue_if_new` in the
test stand-in).

---

## Open / UNVERIFIED

- `supervisor.py` (the real tick loop) is not implemented in this task — the tests
  use a minimal `_enqueue_if_new()` stand-in to verify the single-writer path.
  Full supervisor wiring is a separate P2-a concern.
- `build-jobs.md` is generated at `docs/factory/build-jobs.md` (runtime); the
  path is configurable via the `mirror_path` argument to `write_build_jobs_mirror`.
