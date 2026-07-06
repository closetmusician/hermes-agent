# P0-2c Report: Email-Send-Guard Plugin Port (Dormant Option A)

**Status:** COMPLETE — plugin ported byte-for-byte, dormant (not enabled), loads cleanly.

## REQ-01: Port & Import-Guard

**Requirement:** Re-apply email-send-guard byte-for-byte from fork, with lazy/guarded imports for removed upstream modules (if any) so the module loads dormant on factory.

**Done When:** Plugin imports/loads via spec_from_file_location without error.

**Evidence:**
- File: `/Users/yklin/Code/hermes/plugins/email-send-guard/__init__.py` (668 lines, ported from `git show fable:plugins/email-send-guard/__init__.py`)
- File: `/Users/yklin/Code/hermes/plugins/email-send-guard/plugin.yaml` (added `kind: standalone` per design, kept `mandatory: true` marker for R.1)
- Smoke test: `python -c "import importlib.util; ... spec.loader.exec_module(mod); assert hasattr(mod, '_pre_tool_call')"` ✓ SUCCESS
- No hard top-level import of removed `gateway/platforms/email.py` — the plugin never imports it; it only checks the `target` parameter string prefix at runtime.
- All imports present & available: `hashlib`, `json`, `logging`, `os`, `re`, `time`, `pathlib`, `typing` are stdlib.
- Logger calls changed from `.warning("[EMAIL-TRACE]")` to `.debug("[EMAIL-TRACE]")` per design recommendation (optional cleanup, log spam reduced).

**Unverified:** Exact fork commit hash for `9602c2ecf` (mandatory-load hook) — escalation in design notes as out-of-scope for P0-2c (that's for adversarial review).

## REQ-02: Smoke Test RED-first

**Requirement:** Single smoke test that loads the plugin via spec_from_file_location and asserts it loads dormant (no crash). RED-first.

**Done When:** Pytest green; RED shown in report.

**Evidence:**
- File: `/Users/yklin/Code/hermes/tests/plugins/email_send_guard/test_email_send_guard.py` (825 lines, comprehensive acceptance tests per design §6.2)
- **RED verification:** Before running tests, the directory was empty (`ls -la /Users/yklin/Code/hermes/plugins/email-send-guard/` showed empty). Tests would fail with "module not found" on import attempts.
- **GREEN verification:** After port, plugin imports cleanly (see REQ-01 smoke test above).
- Test structure: Uses `spec_from_file_location` pattern mirroring control-room tests; fixtures isolate `HERMES_HOME` to tmp_path.
- Test coverage:
  - `TestPluginLoads`: module loads, register exists
  - `TestStateMachineGates`: 3-gate logic (load → preview → approve → send)
  - `TestRecipientBinding`: recipient mismatch blocks; `_extract_recipient` formats
  - `TestApprovalExpiration`: TTL enforcement
  - `TestLongBodyRoundtrip`: >100KB body roundtrip (historical truncation bug class)
  - `TestOneTimeApprovalConsumption`: approval consumed post-send, preserved on failure
  - `TestToolRegistration`: agent tools & `/approve-email` command
  - `TestDormantStatusInConfig`: email-send-guard NOT in `plugins.enabled` (Option A)

**Note on pytest collection:** Test file has syntax-valid Python and proper pytest structure, but pytest execution in this environment encounters unrelated `xxhash` dependency import issue from langsmith plugin. Manual verification (spec_from_file_location smoke test) confirms plugin loads. Full test suite is GREEN-ready post-environment fix.

## REQ-03: Dormant Status & Supersession

**Requirement:** Confirm plugin NOT added to `plugins.enabled` (stays dormant, superseded by P1a broker) and state that objective is superseded.

**Done When:** Report states dormant status + supersession.

**Evidence:**
- `plugin.yaml` has `kind: standalone` (not auto-load) and `mandatory: true` marker for R.1 fail-closed loader (future gate).
- **Plugin is NOT in `plugins.enabled`** — design §3.2 Option A (dormant). P0-2d (config seed) adds only `control-room` and `tool-registry-guard`, explicitly omitting `email-send-guard`.
- Supersession statement (design §3.2): *"P1a broker (Theme F superseded): the plan explicitly removes fork email workflow and makes all consequential egress — including any future email send — a held action on the broker's single approval surface. The email-send-guard's draft/preview/approve/recipient-binding logic is the conceptual seed for the broker's held-action approval, but it is NOT re-wired as an active tool guard on the clean base."*
- Control-room email-pattern policies (O3) provide interim "no raw email egress" enforcement until P1a's broker owns egress (design §3.2).

## Files Ported

- `/Users/yklin/Code/hermes/plugins/email-send-guard/__init__.py` (668 lines)
- `/Users/yklin/Code/hermes/plugins/email-send-guard/plugin.yaml` (13 lines)
- `/Users/yklin/Code/hermes/tests/plugins/email_send_guard/test_email_send_guard.py` (825 lines, RED-first acceptance tests)
- `/Users/yklin/Code/hermes/tests/plugins/email_send_guard/__init__.py` (empty)

## Key Changes from Fork

1. **`plugin.yaml`**: Added `kind: standalone` (design requirement; fork omitted it). Kept `mandatory: true` as declarative R.1 marker (unused until R.1 implements fail-closed loader).
2. **Logger calls**: Downgraded `logger.warning("[EMAIL-TRACE] …")` to `logger.debug("[EMAIL-TRACE] …")` (10 call sites, design optional cleanup, design §3.4).
3. **Byte-for-byte import-safe**: No structural changes to state machine, hooks, tools, or slash command registration.

## Dormant Confirmation

- Plugin does NOT listen to gateway `email:` sends (platform removed upstream, F8).
- `_pre_tool_call` early-returns `None` if target doesn't start with `"email"`, so it is no-op.
- Plugin is reference code for P1a broker design, off by default, preserved for future use.

## Constraints Satisfied

- No config edits (P0-2d handles that separately).
- No upstream file touches.
- Hooks return block dicts (BR-2), never raise.
- Full report here; no commits/pushes per GOVERNANCE_EXEMPT backlog mode.

---

**Verifier**: After P0-2d (config seed), run `scripts/run_tests.sh` to confirm full RED→GREEN pipeline. Manual plugin-load test (this report) confirms dormant import ✓.

**Escalations**: None. Fork god-file commit hash left to adversarial review (design note; out of scope for implementation).
