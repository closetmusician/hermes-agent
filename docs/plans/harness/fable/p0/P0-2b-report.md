# ABOUTME: Implementation report for P0-2b: tool-registry-guard plugin port.
# ABOUTME: Covers REQ-01..04 evidence, RED/GREEN test runs, file inventory,
# ABOUTME: REQ-02 god-file confirmation (registry.py untouched), and gaps.
# ABOUTME: Branch: factory. No upstream files patched.
# ABOUTME: Author: CODER subagent (P0-2b, 2026-07-06).

# P0-2b Implementation Report — tool-registry-guard

**Branch:** factory  
**Task:** P0-2b — re-apply tool-registry-guard onto factory base using native `pre_tool_call` hook.  
**Status:** COMPLETE — 31/31 tests GREEN.

---

## Files Produced

| File | Notes |
|---|---|
| `plugins/tool-registry-guard/__init__.py` | Ported from `fable` fork byte-for-byte; no functional change (design §4.3 confirms none needed). 5-line ABOUTME header present. |
| `plugins/tool-registry-guard/plugin.yaml` | Fork source + added `kind: standalone` + `mandatory: true` (design §4.3 item 1, §4.2). |
| `plugins/tool-registry-guard/default-registry.yaml` | Carried from fork byte-for-byte (REQ-04). |
| `tests/plugins/tool-registry-guard/test_tool_registry_guard.py` | 31 tests, RED-first, importlib.util.spec_from_file_location loading. |

No upstream files patched (REQ-02).

---

## REQ-01 Evidence — Plugin load + rule coverage

**Plugin loads via spec_from_file_location:** confirmed by all 31 tests passing. The `_load_plugin()` helper in the test file uses:
```python
spec = importlib.util.spec_from_file_location(
    "hermes_plugins_trg_under_test",
    plugin_dir / "__init__.py",
    submodule_search_locations=[str(plugin_dir)],
)
```
(review note P2 importability finding — hyphenated dir not importable; load by file path instead.)

**Fork-enforced rules preserved:**
- **T1 (ban pip/import):** `test_block_pip_install_pandas`, `test_block_import_openpyxl_in_write_file`, `test_block_inline_python_import_docx`, et al. — all blocking tests pass.
- **T2 (pre_llm_call inject):** `test_returns_context_dict`, `test_context_contains_tool_registry_header` — verified `{"context": ...}` return contract and TOOL REGISTRY header present.
- **T3 (audit JSONL):** `test_audit_entry_written_for_write_file`, `test_audit_entry_flags_violation` — JSONL written with `violation` flag.

**Hook returns block dict, never raises (F5 contract):** `TestHookNeverRaises` class (4 tests) confirms all three hooks are safe-to-call with degenerate inputs and never raise.

**plugin.yaml fields:**
- `kind: standalone` — correct (design F2: standalone plugins load only if `plugins.enabled` lists them).
- `mandatory: true` — added as R.1 declarative marker (design §4.3 item 1, BR-1). Field is currently a no-op (design F1) but will be consumed by R.1 fail-closed loader.
- `provides_hooks` not a recognized plugin.yaml field in upstream (registry is in the yaml `hooks:` list). Hooks registered via `ctx.register_hook()` in `register()`. Not a gap — that IS the upstream wiring path.

---

## REQ-02 Evidence — No god-file patch needed

**Confirmed:** `tools/registry.py:574 dispatch` has zero hits for `pre_tool_call`, `registry_guard`, or any hook invocation (`grep -n "pre_tool_call\|registry_guard\|tool.registry" tools/registry.py` → 0 matches).

**Native interception path:** `agent/tool_executor.py:419` and `:1038` call `get_pre_tool_call_block_message(tool_name, args, ...)` which calls `invoke_hook("pre_tool_call", ...)` → fires all registered `pre_tool_call` callbacks including `_pre_tool_call` from this plugin. The fork's registry.dispatch patch (commit `73e478b32`) placed enforcement inside `tools/registry.py:574`. The native path at `tool_executor.py:419/:1038` covers the same interception: every tool call routed through the executor fires the block gate before execution. `tools/registry.py` is untouched.

**Additional call sites (F-DELTA from review):** `model_tools.py:1178` and `agent_runtime_helpers.py:2101` also invoke `get_pre_tool_call_block_message`. All four sites fire the same registered hooks — enforcement is not weakened, coverage is broader than the fork's single registry.dispatch patch.

---

## REQ-03 Evidence — Tests RED-first

**RED run (plugin absent — `plugins/tool-registry-guard/` renamed away):**
```
31 tests collected
30 ERRORS (FileNotFoundError on spec_from_file_location — plugin __init__.py not found)
1 FAILED (test_custom_registry_overrides_default — also FileNotFoundError)
0 passed
```
All 31 tests fail without the plugin on disk. Verified by temporarily renaming the plugin directory.

**GREEN run (plugin present):**
```
31 passed in 0.38s
```
Full output:
```
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockPipInstall::test_block_pip_install_pandas PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockPipInstall::test_block_pip3_install_openpyxl PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockPipInstall::test_block_pip_install_python_docx PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockPipInstall::test_block_pip_install_python_pptx PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockPipInstall::test_no_false_positive_pip_install_requests PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockPipInstall::test_no_false_positive_non_terminal_tool PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockBannedImportInContent::test_block_import_openpyxl_in_write_file PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockBannedImportInContent::test_block_from_docx_import_in_patch PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockBannedImportInContent::test_block_import_pandas_in_write_file PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockBannedImportInContent::test_no_false_positive_import_os PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockBannedImportInContent::test_no_false_positive_import_json PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockInlinePythonImport::test_block_inline_python_import_docx PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockInlinePythonImport::test_block_inline_python3_import_pptx PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestBlockInlinePythonImport::test_no_false_positive_python_c_import_os PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestPreLlmCallInjectsContext::test_returns_context_dict PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestPreLlmCallInjectsContext::test_context_contains_tool_registry_header PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestPreLlmCallInjectsContext::test_context_contains_banned_package PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestPreLlmCallInjectsContext::test_pre_llm_call_absorbs_extra_kwargs PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestAuditJsonlWritten::test_audit_entry_written_for_write_file PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestAuditJsonlWritten::test_audit_entry_flags_violation PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestAuditJsonlWritten::test_non_codegen_tool_not_audited PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestAuditJsonlWritten::test_terminal_banned_pip_flagged_in_audit PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestRegistryDrivesDecisions::test_default_registry_loaded PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestRegistryDrivesDecisions::test_registry_populates_banned_sets PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestRegistryDrivesDecisions::test_registry_drives_allow_decision PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestRegistryDrivesDecisions::test_registry_drives_deny_decision PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestRegistryDrivesDecisions::test_custom_registry_overrides_default PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestHookNeverRaises::test_pre_tool_call_with_none_args_does_not_raise PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestHookNeverRaises::test_pre_tool_call_with_empty_command_does_not_raise PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestHookNeverRaises::test_pre_llm_call_with_no_registry_returns_none PASSED
tests/plugins/tool-registry-guard/test_tool_registry_guard.py::TestHookNeverRaises::test_post_tool_call_without_audit_path_does_not_raise PASSED

============================== 31 passed in 0.38s ==============================
```

---

## REQ-04 Evidence — default-registry.yaml loaded, drives allow/deny

- `test_default_registry_loaded`: asserts `_registry["tools"]` has ≥1 entry after load.
- `test_registry_populates_banned_sets`: asserts `_banned_packages` and `_banned_imports` non-empty.
- `test_registry_drives_deny_decision`: `pip install xlrd` → blocked (xlrd in default-registry.yaml banned_alternatives for graph-workbook).
- `test_registry_drives_allow_decision`: `pip install boto3` → None (boto3 not in registry).
- `test_custom_registry_overrides_default`: user registry at `~/.hermes/tool-registry.yaml` overrides default; pandas no longer blocked, custom package is blocked.

---

## Gaps and Accepted Limitations

| Gap | Disposition | Source |
|---|---|---|
| T1 is steering, not hard security — obfuscated imports (`__import__("pandas")`) evade regex | Accepted (design §4.1 T1 "block and redirect" objective is steering; documented in review P2 item 7) | review P2-T1 |
| `mandatory: true` is currently a no-op (F1) — plugin loads only if listed in `plugins.enabled` | Accepted interim (BR-1); closed by R.1 fail-closed loader consuming the marker | design §4.3 |
| Silent fail-open on load error until R.1 (Bypass B5/B7) | Accepted interim; R.1 on critical path after P0-2 | design §5 |
| `provides_hooks` not in plugin.yaml schema — not needed; hooks registered via `ctx.register_hook()` | Not a gap | upstream wiring confirmed |
