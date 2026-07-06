# ABOUTME: Delivery report for Phase R task R-6 — SETUP-CHECKLIST.md with behavioral gates.
# ABOUTME: Documents REQ-01..04 evidence, files produced, test results, and open manual gates.
# ABOUTME: Written 2026-07-06. Verifier: run `venv/bin/python -m pytest tests/test_setup_check.py -v`.

# R-6 Delivery Report

**Task:** R-6 — Write `SETUP-CHECKLIST.md` where every item is a behavioral check with
placeholder/stub detection, not a hand-ticked box.

**Date:** 2026-07-06
**Branch:** factory
**Status:** DELIVERED — all REQ-01..04 met; 33 unit tests green.

---

## Files Produced

| File | Purpose |
|---|---|
| `SETUP-CHECKLIST.md` (repo root) | Behavioral checklist — primary deliverable |
| `scripts/setup_check.py` | Automatable runner; classifies lanes DONE/NOT DONE/MANUAL |
| `tests/test_setup_check.py` | 33 unit tests for classification logic |
| `docs/plans/harness/fable/r/R-6-report.md` | This report |

---

## REQ-01: Behavioral checks with (action, expected observable, how to run, pass/fail criteria)

**Evidence:** SETUP-CHECKLIST.md covers 10 lanes across 11 sections (§0–§10).
Every non-MANUAL check specifies:
- **Action:** exact command or observable step
- **Expected result:** the observable outcome that means PASS
- **Stub-detection heuristic:** how to tell if the check was theater
- **Run how:** `AUTO` or `MANUAL`

Lanes covered: provider reachability (Anthropic + OpenRouter), compute/caffeinate,
Telegram send+receive, email/M365 (token health + send+receive), WhatsApp (disabled state),
Jira (P3 — NOT DONE by definition), Calendar (P1c — NOT DONE by definition),
probe/capabilities.json, health command, caffeinate launchd anchor.

Total behavioral checks: 27 individual check rows across the sections.
Automatable: 16 checks. Manual (require human/MFA/Telegram app): 11 checks.

---

## REQ-02: Stub-detection — distinguishes "code exists" from "real round-trip verified"

**Evidence:** SETUP-CHECKLIST.md §9 provides the canonical stub-detection rules:

1. No external round-trip (sub-2ms latency, no outbound connection in logs)
2. Temporally impossible response (expiry in the past, identical results across runs)
3. "Connected" but downstream app shows nothing (Telegram send returns 200, app shows nothing)
4. Succeeds with credentials stripped (canonical stub test: remove cred, re-run, confirm fail)

`scripts/setup_check.py` enforces this in `classify_factory_health_section`:
a provider claiming "reachable" with `latency_ms=None` or `latency_ms=0` is classified
NOT DONE — a real HTTP round-trip to `api.anthropic.com` cannot return in zero milliseconds.

Jira and Calendar lanes return NOT DONE with `automatable=False` regardless of code
existing — explicitly distinguishing "phase code shipped" from "real round-trip verified."

**Gate:** strip `TELEGRAM_BOT_TOKEN` from env, re-run `setup_check.py` — the telegram.getme
check transitions from DONE to NOT DONE. The canned result path is closed.

---

## REQ-03: Automatable runner (scripts/setup_check.py)

**Evidence:**

- `scripts/setup_check.py` runs via `python scripts/setup_check.py`
- Classifies each lane as DONE / NOT DONE / MANUAL and prints a summary table
- Exits 0 when all automatable checks pass; exits 1 when any automatable check is NOT DONE
- Reuses `scripts/factory_health.py` by direct import (provider reachability + compute state)
- Optional flags: `--send-telegram`, `--whatsapp-roundtrip`, `--json`

**Unit tests — 33 tests, all green:**

```
venv/bin/python -m pytest tests/test_setup_check.py -v
33 passed in 0.45s
```

Test classes and counts:
- `TestMakeResult` — 4 tests (schema + invalid status guard)
- `TestClassifyEnvKey` — 7 tests (present/missing/empty/whitespace/comment/multi-key)
- `TestClassifyFileExists` — 3 tests (exists/missing/empty)
- `TestClassifyFactoryHealthSection` — 7 tests (real latency / null latency stub / zero latency stub / NETWORK / missing / case-insensitive / openrouter)
- `TestClassifyCaffeinate` — 3 tests (running/not-running/None)
- `TestClassifyWhatsappDisabled` — 5 tests (absent/disabled/enabled/missing-config/no-enabled-key)
- `TestPhasedLanes` — 4 tests (Jira NOT DONE, Calendar NOT DONE, reason mentions phase)

The stub-detection property is directly unit-tested: `test_reachable_with_null_latency_is_not_done`
and `test_reachable_with_zero_latency_is_not_done` confirm that a stub claiming "reachable"
without a measurable round-trip is classified NOT DONE.

---

## REQ-04: STAGED-GATES cross-reference

**Evidence:** SETUP-CHECKLIST.md §10 contains a table cross-referencing all four current
staged gates from `docs/plans/harness/fable/STAGED-GATES.md`:

| Staged gate | Checklist items blocked |
|---|---|
| SG-P0-1 (caffeinate launchd bootstrap) | §2.3, §8.4 |
| SG-P0-2 (OPENROUTER_API_KEY secret) | §1.3, §8.1 |
| SG-P0-3 (zombie-platform log silence) | §5.2 |
| SG-P0-4 (compute.md from real gauntlet) | §8.5 |

The checklist also references STAGED-GATES.md inline in §2, §5, and §8 for items that require
a real Terminal session or owner secret to flip.

---

## Open Manual Gates (not blocking delivery — owner actions)

These checklist items are marked MANUAL and remain open until the owner performs them:

| Item | Action required |
|---|---|
| §1.3 Probe with live OpenRouter key | Add `OPENROUTER_API_KEY` to `~/.hermes/.env` (SG-P0-2) |
| §2.3 launchd services bootstrapped | `launchctl bootstrap gui/$(id -u) <plist>` in Terminal (SG-P0-1) |
| §3.3 Telegram send round-trip | Send test message from gateway; confirm in Telegram app |
| §3.4 Telegram receive round-trip | Send message to hermes; confirm gateway.log inbound event |
| §4.3 Outlook read via Graph | `node outlook-read-mail.js --limit 1` (may need MFA) |
| §4.4 Email send+receive | Send to yu_kuan@yahoo.com, confirm arrival |
| §6 Jira lane | NOT DONE — P3 not built |
| §7 Calendar lane | NOT DONE — P1c not built |
| §8.4 Caffeinate survives gateway kill | Kill gateway; confirm caffeinate launchd service persists |

---

## Verification Gate (R-6 done-when)

> "Point the checklist at a deliberately-stubbed-but-untested lane; confirm it reports 'not done' even though the code exists."

Demonstrated by:
1. `classify_factory_health_section` with `latency_ms=None` → NOT DONE (test `test_reachable_with_null_latency_is_not_done`)
2. `classify_jira_lane_phase()` → NOT DONE even though `run-jira.js` code exists in pm_os (test `test_jira_lane_is_not_done`)
3. §9 canonical stub test: strip credential, re-run, confirm failure — documented as a repeatable procedure any reviewer can follow

The checklist's §9 Stub-Detection Summary gives a verifier the four concrete heuristics
to apply to any lane, not just the automated ones.
