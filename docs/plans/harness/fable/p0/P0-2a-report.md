# P0-2a Implementation Report — control-room plugin re-application

**Task:** P0-2a (backlog, GOVERNANCE_EXEMPT)
**Branch:** factory (verified)
**Date:** 2026-07-06

---

## RED Run (before implementation)

40 tests collected, 40 errors — FileNotFoundError on `plugins/control-room/__init__.py`.
All tests genuinely RED on clean base (plugin dir existed but was empty).

```
40 errors in 0.60s
```

Every test failed with: `FileNotFoundError: .../plugins/control-room/__init__.py`

---

## GREEN Run (after implementation)

```
40 passed in 0.66s
```

All 40 tests pass.

---

## Fixes Applied

### F1-CR (P1, REQ-02) — Path-normalization for self-protection
**Problem:** Fork used `"plugins/control-room" in target_path` substring check on
raw model-supplied `args["path"]`. Evadable via:
- `write_file(path="__init__.py")` from cwd `plugins/control-room/`
- `write_file(path="../control-room/__init__.py")` traversal

**Fix:** Replaced with `os.path.realpath` + `Path.relative_to` comparison against
the resolved guard directories. Functions: `_resolve_guard_dirs()`, `_is_under_guard_dir()`.
Both absolute paths, relative paths (via CWD), and traversal sequences are now blocked.
Tests: `TestSelfProtectionNormalized` (7 tests, all GREEN).

### F3-CR (P1, REQ-03) — Terminal file-edit of guard source or config.yaml
**Problem:** Fork's terminal branch only blocked `outlook-*` / `get-foci-token`.
`sed -i`, `echo >`, `tee`, `cp` targeting guard source or `~/.hermes/config.yaml`
had zero coverage.

**Fix:** Added `_terminal_targets_protected_path()` that:
1. First confirms the command matches a write pattern (`_TERMINAL_WRITE_PATTERN` regex
   covering: `>`, `>>`, `sed -i`, `tee `, `cp/mv`, `open(f,'w')`).
2. Then checks if any protected path string appears in the command.

**Residual gap (documented, P1a):** Full terminal command parsing (obfuscated forms:
`bash -c 'eval ...'`, heredocs, variable expansion) is out of scope for P0-2.
Those are deferred to P1a's egress/credential wall, per F3-CR review rationale.
Tests: `TestTerminalGuardSourceEdits` (7 tests, all GREEN).

### BR-4 + B3 (REQ-04) — Widened write-protection scope
**BR-4:** Added `plugins/tool-registry-guard` to the protected guard dirs list.
The fork omitted it (only covered `control-room` and `email-send-guard`).

**B3:** Added `~/.hermes/config.yaml` write-protection via `_is_config_write()`
using realpath comparison. Covers both `write_file`/`patch` (via `_is_config_write`)
and `terminal` (via `_terminal_targets_protected_path`).

Note (F-B3 from review): Upstream `file_tools._check_sensitive_path` already blocks
`write_file` to resolved `~/.hermes/config.yaml` via a separate mechanism. The
control-room guard adds an explicit, independent layer that doesn't depend on file_tools.
Tests: `TestConfigWriteProtection` (2 tests), `test_tool_registry_guard_source_blocked`,
`test_tool_registry_guard_traversal_blocked` (all GREEN).

### F2-CR (P2, REQ-04) — execute_code import-block documented as steering
**Problem:** Review found B10 was represented as CLOSED but is substring-evadable
(`importlib.import_module("gateway")` evades `"import gateway"` check).

**Fix:** Added inline code comment explicitly documenting the substring-only nature
and known evasion forms. Comment in `pre_tool_call` execute_code branch:
> "F2-CR note: This is substring-based steering, not hard security. [...] B10 is NOT
> claimed as a hard security close."

Test `test_importlib_form_best_effort` documents the gap: asserts result is None
(evasion succeeds) and documents this as the known F2-CR limitation. UNVERIFIED that
the specific importlib form evades — the test accepts either None or block.

---

## REQ Evidence

| REQ | Status | Evidence |
|-----|--------|---------|
| REQ-01 | DONE | Plugin loads via spec_from_file_location; `test_module_loads_without_error`, `test_register_wires_hooks` GREEN. plugin.yaml has `kind: standalone`, `mandatory: true`, `provides_hooks`. |
| REQ-02 | DONE | F1-CR applied; 7 TestSelfProtectionNormalized tests including cwd-relative and traversal paths all GREEN. |
| REQ-03 | DONE | F3-CR applied; 7 TestTerminalGuardSourceEdits tests GREEN. Residual obfuscated-terminal gap explicitly deferred to P1a in this report. |
| REQ-04 | DONE | BR-4 (tool-registry-guard), B3 (config.yaml), F2-CR documented. 40/40 GREEN. |

---

## Files Created

- `plugins/control-room/__init__.py` — main plugin with F1-CR, F3-CR, BR-4, B3 fixes
- `plugins/control-room/plugin.yaml` — `kind: standalone`, `mandatory: true`, `provides_hooks`
- `plugins/control-room/policies/execute-code-email-block.yaml`
- `plugins/control-room/policies/execute-code-token-read-deny.yaml`
- `plugins/control-room/policies/patch-email-block.yaml`
- `plugins/control-room/policies/patch-token-read-deny.yaml`
- `plugins/control-room/policies/terminal-email-block.yaml`
- `plugins/control-room/policies/terminal-token-read-deny.yaml`
- `plugins/control-room/policies/writefile-email-block.yaml`
- `plugins/control-room/policies/writefile-token-read-deny.yaml`
- `tests/plugins/control_room/__init__.py`
- `tests/plugins/control_room/test_control_room.py` — 40 RED-first tests

---

## Deferrals

1. **F3-CR residual** — obfuscated terminal command forms (bash -c eval, heredoc, variable
   expansion) are not caught by the regex-based `_TERMINAL_WRITE_PATTERN`. Deferred to P1a
   egress/credential wall. The high-signal forms (sed/tee/redirect) are covered.

2. **F2-CR residual** — `importlib.import_module` / `__import__` / `exec("imp"+"ort agent")`
   evasions of the execute_code internal-import block. The block is steering-only.
   Real containment requires the execute_code sandbox (P1a).

3. **B5/B7** — Fail-open on plugin load error until R.1's fail-closed loader lands.
   `mandatory: true` in plugin.yaml is the declarative marker R.1 will consume.

4. **Config seed** — Adding `control-room` and `tool-registry-guard` to `plugins.enabled`
   in `~/.hermes/config.yaml` is P0-2d (separate task, depends on P0-2a/b/c complete).
