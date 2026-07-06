# ABOUTME: P0-2d completion report — governance plugin config reconciliation.
# ABOUTME: Documents the before/after of plugins.enabled, memory/gbrain dangling
# ABOUTME: entry decision, smoke test output, and REQ-01..04 evidence.
# ABOUTME: Config is ~/.hermes/config.yaml (outside repo); example-config note
# ABOUTME: added to cli-config.yaml.example in repo.
# ABOUTME: Written 2026-07-06.

# P0-2d — Config Reconciliation Report

**Date:** 2026-07-06  
**Branch:** factory  
**Task:** Reconcile `plugins.enabled` in `~/.hermes/config.yaml` against what exists on factory.

---

## §1 — Before/After: plugins.enabled

### Before (ORIGINAL — backed up to `~/.hermes/config.yaml.bak-p02d-20260706`)

```yaml
plugins:
  enabled:
  - email-send-guard    # WRONG: Option A = dormant (not enabled)
  - control-room        # correct
  - tool-registry-guard # correct
  - memory/gbrain       # WRONG: dangling — plugins/memory/gbrain/ is empty on factory
  entries:
    email-send-guard:
      drafts_dir: ~/pm_os/reports/drafts
```

### After (LIVE — `~/.hermes/config.yaml` lines 486–506)

```yaml
plugins:
  # Governance plugins (P0-2 reconciliation 2026-07-06):
  #   control-room:       ENABLED  — audit + policy enforcement spine (governance)
  #   tool-registry-guard: ENABLED — bans DIY Office/SharePoint libs, injects registry context
  #   email-send-guard:   DORMANT (Option A) — plugin ported to disk but NOT enabled;
  #                       email workflow superseded by P1a broker; control-room email-pattern
  #                       policies provide interim "no raw email egress" enforcement.
  #   memory/gbrain:      REMOVED — dangling entry; plugins/memory/gbrain/ dir is empty
  #                       on factory branch (commit 61e6b8405 lives on fable only, not ported
  #                       in P0-2 scope). Would cause a load error that fails open (F5).
  # NOTE: HERMES_SAFE_MODE=1 disables ALL plugin discovery — operator-only troubleshooting
  #       switch, never set in the launchd service env. Until R.1 (fail-closed loader) lands,
  #       a load failure for an enabled plugin fails OPEN (F5 / bypass B7).
  enabled:
  - control-room
  - tool-registry-guard
  entries:
    email-send-guard:
      drafts_dir: ~/pm_os/reports/drafts
```

**Diff summary:**
- `email-send-guard` removed from enabled list (Option A: dormant; entries block preserved)
- `memory/gbrain` removed from enabled list (dangling — see §2 below)
- `control-room` and `tool-registry-guard` retained (intended governance plugins)

---

## §2 — memory/gbrain Decision (REQ-01)

**Verdict: DANGLING ENTRY — removed from `plugins.enabled`.**

Evidence:
- `plugins/memory/gbrain/` directory exists on disk (created as empty dir by git)
- `git ls-tree -r --name-only HEAD -- plugins/memory/gbrain/` → empty (no files in factory HEAD)
- `git log --all -- plugins/memory/gbrain/` → commit `61e6b8405` (feat: add gbrain-memory plugin) exists only on `fable` branch, NOT ported in P0-2 scope
- The plugin has no `plugin.yaml` or `__init__.py` on factory; the loader would attempt import and fail open (F5)
- Escalate caveat: removing memory/gbrain may affect a feature the owner relies on. However, `gbrain` is a CLI binary (`~/.bun/bin/gbrain`) that integrates directly with the owner's global harness — it is NOT dependent on this plugin being in `plugins.enabled`. The plugin was a P3 governance feature that simply hasn't landed on factory yet. Documented here per REQ-01 escalation clause: owner should re-add `memory/gbrain` to `plugins.enabled` after the plugin is ported to factory.

---

## §3 — REQ-02: entries blocks intact

The `entries.email-send-guard` config block was **preserved** in `~/.hermes/config.yaml`. Only the `enabled` list was modified. No plugin config blocks were deleted.

---

## §4 — REQ-03: Plugin Load Smoke Test

**Command:**
```python
from hermes_cli.plugins import PluginManager, _get_enabled_plugins
enabled = _get_enabled_plugins()
pm = PluginManager()
pm.discover_and_load(force=True)
```

**Output (captured 2026-07-06):**
```
[CONFIG] plugins.enabled = ['control-room', 'tool-registry-guard']
[LOADED]  enabled plugins: [..., 'control-room', ..., 'tool-registry-guard', ...]
[SKIPPED] discovered-but-not-enabled: [..., 'email-send-guard', ...]
[HOOKS] pre_tool_call=True, pre_llm_call=True, post_tool_call=True
SMOKE TEST: PASS — control-room + tool-registry-guard loaded, email-send-guard absent, all hooks registered
```

**Assertions verified:**
- `control-room` in loaded plugins ✓
- `tool-registry-guard` in loaded plugins ✓
- `email-send-guard` in skipped/not-enabled list (NOT in loaded) ✓
- `memory/gbrain` absent from both loaded and skipped (entry removed, empty dir not scanned as valid plugin) ✓
- `pre_tool_call` hook registered ✓
- `pre_llm_call` hook registered (tool-registry-guard T2) ✓
- `post_tool_call` hook registered ✓
- `HERMES_SAFE_MODE` not set in environment ✓

**Verdict: PASS.** No zombie errors. No load failures. Both governance plugins active.

---

## §5 — REQ-04: Governance-plugins note in repo example config

Added to `cli-config.yaml.example` (end of file, after line 1382):

```yaml
# ── Governance Plugins (P0-2 factory baseline) ────────────────────────────────
#
# Standalone plugins that enforce governance policies are loaded ONLY if their
# key appears in plugins.enabled. Two plugins are enabled by default in the
# factory host; one is ported dormant (Option A) as the P1a design reference.
#
# Enabled plugins fire at EVERY tool call via the native pre_tool_call hook
# (hermes_cli/plugins.py — first-block-wins). A plugin that fails to load
# currently fails OPEN (bypass B7, interim until R.1 fail-closed loader lands).
# HERMES_SAFE_MODE=1 disables ALL plugin discovery — operator-only
# troubleshooting switch; never set it in the launchd service env.
#
# plugins:
#   enabled:
#   - control-room          # ENABLED: audit + declarative policy enforcement spine.
#   - tool-registry-guard   # ENABLED: bans DIY Office/SharePoint libs; injects
#                           #   pm_os tool-registry table into every LLM turn.
#   # email-send-guard NOT in plugins.enabled (Option A — dormant).
#   entries:
#     email-send-guard:
#       drafts_dir: ~/pm_os/reports/drafts   # kept even when dormant
```

**File:** `cli-config.yaml.example` (repo root, referenced by CONTRIBUTING.md §"Installation")

---

## §6 — Accepted Interim Gaps

| Gap | Status |
|---|---|
| R.1 fail-closed loader (B7): a load failure for an enabled plugin fails OPEN | ACCEPTED interim; declarative `mandatory: true` markers in plugin.yaml are the R.1 contract. Bounded: smoke test confirms clean load today. |
| HERMES_SAFE_MODE (B4): disables all guards if set | ACCEPTED; operator-only switch, confirmed NOT set in running environment. Verify it's absent from the launchd service plist (out of scope for P0-2d). |
| memory/gbrain missing from factory (not ported in P0-2) | DOCUMENTED; owner should re-add to `plugins.enabled` after gbrain plugin is ported. |

---

## §7 — File Paths

| Artifact | Path | In repo? |
|---|---|---|
| Live config (edited) | `~/.hermes/config.yaml` | No (operator config) |
| Config backup | `~/.hermes/config.yaml.bak-p02d-20260706` | No |
| Example config note | `/Users/yklin/Code/hermes/cli-config.yaml.example` | Yes |
| This report | `/Users/yklin/Code/hermes/docs/plans/harness/fable/p0/P0-2d-report.md` | Yes |
