# R-1 Report — Fail-Closed Mandatory Plugin Loading

**Task:** REQ-01..04 mandatory plugin fail-closed gate  
**Branch:** fable  
**Date:** 2026-07-06  
**Status:** COMPLETE — all 117 tests GREEN (0 failures)

---

## Summary

Made config/plugin loading FAIL-CLOSED for mandatory security plugins (FM-4 fix). If any plugin marked `mandatory: true` in `plugin.yaml` or listed in the hardcoded `MANDATORY_SECURITY_PLUGINS` frozenset fails to load, `PluginManager.discover_and_load()` raises `MandatoryPluginLoadError` naming the plugin, and the gateway's `start()` method catches it and returns `False` (aborts startup).

---

## RED → GREEN Evidence

### RED run (before implementation)

```
FAILED tests/hermes_cli/test_mandatory_plugins.py — ImportError: cannot import name
'MANDATORY_SECURITY_PLUGINS' from 'hermes_cli.plugins'
```

All 17 tests in `test_mandatory_plugins.py` failed with `ImportError` — implementation did not yet exist.

### GREEN run (after implementation)

```
=== Summary: 2 files, 117 tests passed, 0 failed (100% complete) in 4.2s (36 workers) ===
  tests/hermes_cli/test_mandatory_plugins.py   17 ✓
  tests/hermes_cli/test_plugins.py            100 ✓
```

---

## Files Changed

### `hermes_cli/plugins.py` (+91 lines)

| Lines | Change |
|---|---|
| 279–284 | `MANDATORY_SECURITY_PLUGINS` frozenset — hardcoded defense-in-depth set (control-room, email-send-guard, tool-registry-guard) |
| 286–299 | `MandatoryPluginLoadError(RuntimeError)` class — raised by `discover_and_load()` naming the plugin(s) |
| 342–345 | `mandatory: bool = False` field on `PluginManifest` dataclass |
| 1261–1262 | `mandatory_load_failures: Dict[str, str] = {}` on `PluginManager.__init__` |
| 1297 | `self.mandatory_load_failures.clear()` in force-reset path |
| 1384–1386 | Mandatory plugin bypass in `_discover_and_load_inner` winners loop — skips disabled/enabled gates, routes to `_load_mandatory_plugin()` |
| 1480–1489 | Fail-closed gate at end of `_discover_and_load_inner` — raises `MandatoryPluginLoadError` if `mandatory_load_failures` is non-empty |
| 1650–1667 | `_parse_manifest()` — parses `mandatory` from YAML, OR-combines with hardcoded set |
| 1853–1870 | `_load_mandatory_plugin()` method — calls `_load_plugin()`, then checks `loaded.enabled`; on failure records in `mandatory_load_failures` and logs CRITICAL |

### `gateway/run.py` (+13 lines, -2 lines)

| Lines | Change |
|---|---|
| 6789–6803 | `GatewayRunner.start()` — `discover_and_load()` wrapped in try/except; `MandatoryPluginLoadError` caught, logs CRITICAL, returns `False` to abort startup |

### `tests/hermes_cli/test_mandatory_plugins.py` (new file, 17 tests)

Full TDD test suite created before implementation (RED-first). Covers all four requirements.

### `tests/hermes_cli/test_plugins.py` (+15 lines)

5 existing tests fixed for regression caused by mandatory plugins now loading from real bundled dir. Fix: `monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", ...)` added to isolate each test's tmp plugin dir.

---

## Requirement Evidence

### REQ-01 — Mandatory failure raises `MandatoryPluginLoadError` naming the plugin

- `hermes_cli/plugins.py:1853–1870` `_load_mandatory_plugin()` records failure
- `hermes_cli/plugins.py:1484–1489` raises with plugin name(s) in message
- Tests: `TestMandatoryFailClosed.test_broken_mandatory_raises_with_plugin_name` (line 99), `test_broken_mandatory_logs_critical` (line 118), `test_broken_mandatory_records_in_failures_dict` (line 140)

### REQ-02 — Healthy mandatory plugins allow normal startup

- `hermes_cli/plugins.py:1384–1386` mandatory plugins bypass disabled/not-enabled gates, load unconditionally when healthy
- Tests: `TestHealthyMandatoryPluginsLoadNormally` (lines 231–303) — 3 tests covering healthy load, disabled-config bypass, absent-enabled-list bypass

### REQ-03 — Optional failure does NOT abort startup

- `hermes_cli/plugins.py:1384–1386` — non-mandatory plugins skip `_load_mandatory_plugin()` entirely; `mandatory_load_failures` stays empty; no raise
- Tests: `TestOptionalPluginFailDoesNotAbort` (lines 168–225) — 2 tests confirming no raise and clean `mandatory_load_failures`

### REQ-04 — `HERMES_SAFE_MODE=1` bypasses mandatory-fail-closed path

- `hermes_cli/plugins.py` existing early-return at top of `discover_and_load()` — when `HERMES_SAFE_MODE=1` is set, returns before any discovery; mandatory check never runs
- Tests: `TestSafeModeBypass` (lines 381–423) — 2 tests confirming no raise and empty registry

---

## Design Notes

- **Defense-in-depth**: `MANDATORY_SECURITY_PLUGINS` frozenset ensures control-room, email-send-guard, and tool-registry-guard are always mandatory even if someone edits `plugin.yaml`. The `mandatory` flag in YAML is OR-combined with the frozenset.
- **Mandatory bypass config gates**: Mandatory plugins load regardless of `plugins.enabled` / `plugins.disabled` lists — fail-closed security posture overrides operator config. This is intentional (FM-4: operators were silently starting degraded by omitting mandatory plugins from config).
- **SAFE_MODE semantic**: `HERMES_SAFE_MODE=1` means "intentional operator override, disable all plugins" — this is not a load failure, so mandatory-fail-closed does not apply. The existing early-return handles this correctly with no new code.
- **Test isolation regression**: Loading mandatory plugins unconditionally broke 5 tests in `test_plugins.py` that set `HERMES_HOME` to a tmp dir but didn't override `HERMES_BUNDLED_PLUGINS`. The fix is the `HERMES_BUNDLED_PLUGINS` env var already available in the plugin system — tests now point it at the test's own tmp dir so no real bundled plugins load.
