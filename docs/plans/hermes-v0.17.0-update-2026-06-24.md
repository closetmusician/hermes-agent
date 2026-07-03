# Hermes v0.17.0 Upstream Merge Plan

**Date:** 2026-06-24 (revised 2026-06-25)
**Upstream release:** v0.17.0 (v2026.6.19) — "The Reach Release"
**Local branch:** `feat/governance-plugins`
**Strategy chosen:** Option 3 — Cherry-pick onto fresh branch

## Upstream Summary

- ~1,475 commits, 1,693 files changed, 235K insertions, 50K deletions
- 245 contributors, 300+ issues closed
- Major: iMessage/Photon, Raft agent network, WhatsApp Business Cloud API,
  background subagents, image editing, automation blueprints,
  `gateway/run.py` god-file refactor (19157 -> 15870 lines),
  `memory` tool batch ops, security hardening

## Local State

- 47 commits ahead, 50 commits behind `origin/main`
- Work: plugin system (control-room, email-send-guard, tool-registry-guard,
  gbrain memory), mandatory plugin loading, registry dispatch enforcement,
  email workflow fixes
- 32 files overlap between local and upstream changes

## Critical Upstream Breaking Changes

### 1. `gateway/run.py` mixin extraction

Upstream extracted the monolithic GatewayRunner into mixins:

| Mixin file | Class | What moved |
|---|---|---|
| `gateway/slash_commands.py` | `GatewaySlashCommandsMixin` | 46 slash-command handlers (~4,077 LOC) |
| `gateway/authz_mixin.py` | `GatewayAuthorizationMixin` | DM/group policy, per-adapter access (~250 LOC) |
| `gateway/kanban_watchers.py` | `GatewayKanbanWatchersMixin` | Kanban notifier/dispatcher loops (~1,000 LOC) |

New class declaration at `gateway/run.py:2475`:
```python
class GatewayRunner(
    GatewayAuthorizationMixin,
    GatewayKanbanWatchersMixin,
    GatewaySlashCommandsMixin,
):
```

Slash commands dispatched via explicit if/elif chain in `_handle_message()`
(~line 7958). Plugin commands fall through after built-in dispatch (~line 8373).

**Impact on local work:**
- `/approve-email` and `/preview-email` handlers must be placed in the
  if/elif chain BEFORE the plugin fallthrough at ~line 8365
- Plugin context dispatch enhancement (`_plugin_command_context`,
  `_invoke_plugin_command_handler`) wraps the call at ~line 8373
- Both can live in a new `gateway/email_commands_mixin.py` or inline

### 2. `send_message` tool: agent-callable removed, internal dispatch changed

Upstream commit `c6c8abbad` intentionally removed agent-callable
registration. The tool is now **internal-only infrastructure** used by
cron, `hermes send` CLI, kanban notifier, and MCP server.

Key upstream comment (`send_message_tool.py:1681-1691`):
```
NOTE: send_message is intentionally NOT registered as an agent-callable
model tool. The agent should not decide on its own to fire off
cross-platform messages or reactions.
```

**Email dispatch changed to plugin registry:**
- Upstream line 932: `_registry_standalone_send("email", pconfig, ...)`
- This calls `platform_registry.get("email").standalone_sender_fn()`
- Email sending now routed through `plugins/platforms/email/adapter.py`

**Local vs upstream portability analysis:**

| Local change | Portable? | Why |
|---|---|---|
| `_extract_email_subject_and_body()` | NO | Upstream email uses plugin registry, not inline parsing |
| Email pconfig synthesis (lines 335-345) | NO | Incompatible with plugin-based dispatch |
| `_send_email()` Graph API wrapper | NO | Upstream expects plugin `standalone_sender_fn`, not subprocess |
| Registry re-registration as agent tool | NO | Upstream intentionally removed this |
| `_send_telegram_media_with_retry()` | YES | Safe retry enhancement, upstream lacks this |
| Email logging traces | DROP | Debug scaffolding |

**Decision required:** The Graph API email routing via `outlook-send-mail.js`
needs to be implemented inside the upstream email plugin's
`standalone_sender_fn` at `plugins/platforms/email/adapter.py`, not in
`send_message_tool.py`. This is a rewrite, not a port.

### 3. Email adapter migrated to bundled plugin

`gateway/platforms/email.py` deleted upstream. Email adapter now at
`plugins/platforms/email/adapter.py`. IMAP IDLE and backoff changes
must target the new plugin location.

### 4. `gateway/session.py` — significant upstream additions

New fields and methods upstream:
- `role_authorized: bool` — adapter-granted role access
- `profile: Optional[str]` — multiplexing profile namespace
- `_session_key_namespace(profile)` — profile-isolated session keys
- `lookup_by_session_id()`, `rewind_session()` — new session methods
- `timestamp` field in DB

Local changes to session.py are small (session context for plugin
dispatch). Should merge cleanly after rebasing.

### 5. `gateway/stream_consumer.py` — significant upstream additions

New upstream features:
- `on_before_finalize()` callback — pauses typing before finalization
- `_preview_message_ids` tracking — streaming preview cleanup
- `_last_edit_overflowed` / `_fallback_preserve_partial_messages`
- `_metadata_for_send()` — per-send metadata, Mattermost-aware
- `message_id` property

Local changes are small. Should merge cleanly.

### 6. `model_tools.py` refactored

Upstream added observability hooks, middleware intercepts, progressive
tool disclosure. Local `pre_tool_call` enforcement must be placed
relative to new hook points.

### 7. `hermes_cli/plugins.py` expanded

12 upstream commits adding: kanban lifecycle hooks, dashboard auth
provider, TTS/STT provider registration, Slack action handler,
auxiliary tasks, adaptive middleware. Local mandatory-plugin loading
must integrate with expanded discovery and registration flow.

## Cherry-Pick Plan (Option 3)

### Pre-flight

```bash
git fetch origin --tags
git checkout -b feat/governance-plugins-v0.17.0 origin/main
```

Old `feat/governance-plugins` branch preserved as backup.

### Unit 1: Plugin Infrastructure (registry + dispatch)

**Commits:**
- `06ba54224` test(registry): failing tests for plugin kwargs dispatch bug
- `bb4d3dfd4` test(security): RED tests for pre_tool_call in registry.dispatch()
- `67e6234b2` fix(registry): unpack args dict as kwargs for plugin handler dispatch
- `73e478b32` fix(security): move pre_tool_call enforcement into registry.dispatch()

**Files:** `tools/registry.py`, `model_tools.py`, `tests/tools/test_registry.py`,
`tests/test_model_tools.py`

**Conflict risk:** LOW for `tools/registry.py` (zero upstream changes).
MEDIUM for `model_tools.py` (upstream added observability hooks —
place pre_tool_call after `tool-hook emit` gating but before execution).

### Unit 2: Governance Plugins (new files — clean apply)

**Commits:**
- `ddced646d` feat(plugins): email-send-guard plugin
- `faaa4e5ab` feat(plugins): email-send-guard plugin (revised)
- `f393467f1` feat(plugins): tool-registry-guard plugin
- `f51b53b30` feat(plugins): control-room plugin
- `e5cae0e90` feat(plugins): control-room plugin (revised)
- `61e6b8405` feat(plugins): gbrain-memory plugin
- `c8b1efcfc` feat(control-room): regex operator + email-blocking policies
- `ea496fd8b` feat(control-room): token read-deny policies
- `759ccf801` fix(control-room): default policy_dir + outlook tool block
- `3fdbb721f` fix(control-room): broaden outlook block + FOCI docs
- `63a05a3da` fix(control-room): revert code-rewrite attack + layered defense
- `24dfe3f4d` fix(security): close codex-identified email guard bypasses
- `4a961e45b` fix(security): update email block messages
- `75db63ca5` fix(email): actionable guard messages, session-scoped drafts

**Files:** `plugins/control-room/`, `plugins/email-send-guard/`,
`plugins/tool-registry-guard/`, `plugins/memory/gbrain/`

**Conflict risk:** NONE — all new directories, purely additive.

**Verify:** `pre_tool_call` hook still exists in `PluginContext` with
same signature (upstream added many new hooks but shouldn't have
changed existing ones).

### Unit 3: Mandatory Plugin Loading

**Commits:**
- `de319f603` test(security): failing tests for mandatory plugin loading
- `9602c2ecf` fix(security): make control-room and email-send-guard mandatory

**Files:** `hermes_cli/plugins.py`, plugin.yaml files,
`tests/hermes_cli/test_mandatory_plugins.py`

**Conflict risk:** MEDIUM — 12 upstream commits expanded plugin API.
Find insertion point after discovery, before registration.

### Unit 4: Gateway Integration (refined)

This unit is broken into 4 sub-units ordered by risk.

#### Unit 4A: Plugin Slash Command Context (LOW risk)

**Commits:**
- `fd5949be0` fix(gateway): dispatch plugin slash commands during active sessions

**What it does:** Adds `_plugin_command_context(source)` and
`_invoke_plugin_command_handler(handler, raw_args, source)` to pass
session context (platform, chat_id, user_id, etc.) to plugin command
handlers via introspection.

**Where it goes upstream:** Wraps the plugin handler call at
`gateway/run.py:~8373` where upstream does `plugin_handler(user_args)`.

**Adaptation:**
- Read upstream `_handle_message()` dispatch to find exact line
- Wrap `plugin_handler(user_args)` with context-aware invocation
- Can be done inline in run.py (small change) or as a mixin method

#### Unit 4B: Email Slash Commands (MEDIUM risk)

**Commits:**
- `d97dfe04d` fix(email): draft-based approval, tool-cache scoping
- `a5e430aca` fix(email): send approved drafts without LLM followup
- `ced55a0cc` fix(email): break infinite loop

**What it does:** Adds `/approve-email` and `/preview-email` slash
commands for the email draft approval workflow.

**Where it goes upstream:** In the if/elif dispatch chain in
`_handle_message()`, BEFORE the plugin fallthrough at ~line 8365:
```python
if canonical == "approve-email":
    return await self._handle_approve_email_command(event)
if canonical == "preview-email":
    return await self._handle_preview_email_command(event)
```

**Adaptation:**
- Extract handler methods from local gateway/run.py
- Place in `GatewaySlashCommandsMixin` (extend it) or a new
  `gateway/email_commands_mixin.py` added to class declaration
- These handlers reference email-send-guard plugin state — verify
  the plugin is loaded before dispatch

#### Unit 4C: Email Send via Graph API (HIGH risk — rewrite)

**Commits to DROP (obsoleted by upstream architecture):**
- `cbc4cc580` feat(send-message): Graph API via outlook-send-mail.js
- `3f4784b7b` refactor(email): HTML formatting to JS sender, kill SMTP

**Why:** Upstream moved email sending to `plugins/platforms/email/`
plugin with `standalone_sender_fn`. Local changes that add Graph API
routing inside `send_message_tool.py` are architecturally incompatible.

**Replacement approach:** Implement Graph API routing inside the
upstream email plugin:
1. Read `plugins/platforms/email/adapter.py` on `origin/main`
2. Add `outlook-send-mail.js` subprocess call as the send backend
   inside the plugin's `standalone_sender_fn`
3. Keep FOCI token routing from local `_send_email()` logic
4. This is **new code**, not a cherry-pick

**Portable commit:**
- `8df995d00` test(send-message): tests for Graph API email backend
  — tests are still valid, may need import path updates

#### Unit 4D: IMAP IDLE + Backoff (HIGH risk — rewrite)

**Commits (cannot cherry-pick — target file deleted):**
- `abf044fb0` feat(email): IMAP IDLE push notifications
- `298ac4c7f` fix(email): 3-layer backoff
- `321361037` fix(email): close 3 backoff gaps
- `8ae60290b` fix(gateway): remaining backoff gaps + cron guard
- `28b1bc5e6` fix(gateway): robust IMAP IDLE backoff

**Replacement approach:**
1. Read upstream `plugins/platforms/email/adapter.py` to understand
   new email plugin structure
2. Port IMAP IDLE push notification logic into the plugin adapter
3. Port 3-layer backoff into plugin adapter's connection handling
4. The `cron/scheduler.py` git-context guard change from `8ae60290b`
   may cherry-pick cleanly (verify)

#### Unit 4E: Agent-Side Email Workflow (MEDIUM risk)

**Commits:**
- `56cd50eee` fix(email): complete email workflow — touches 20+ files

**This mega-commit touches:**
- `agent/agent_runtime_helpers.py` — email credential path
- `agent/file_safety.py` — email attachment safety
- `agent/prompt_builder.py` — email context in system prompt
- `agent/tool_executor.py` — email tool execution hooks
- `model_tools.py` — email tool registration
- `hermes_cli/plugins.py` — email plugin config
- `pyproject.toml` — dependency changes

**Adaptation:** Cannot cherry-pick as one commit. Must be decomposed:
1. Extract agent-side changes (prompt_builder, file_safety) — likely
   portable with minor conflict resolution
2. Extract tool_executor changes — must merge with upstream's memory
   batch ops, background subagent, and tool-progress changes
3. Extract model_tools changes — must merge with upstream's
   observability hooks
4. pyproject.toml — manual merge of dependency lists

### Unit 5: Tests + Docs (apply last)

**Commits:** (same as before — 10 test/doc commits)

**Conflict risk:** LOW — most test files are new.

**Verify:** `conftest.py` fixtures still exist with same signatures.
Upstream added `session_id` forwarding to dispatch — tests that mock
registry.dispatch() may need updated signatures.

## Execution Order

```
Phase 1: Clean applies (confidence building)
  1. Create branch from origin/main
  2. Unit 2: Governance plugins (clean, no conflicts)
  3. Unit 5 partial: Plugin tests + docs (clean)
  4. ---- CHECKPOINT: plugin tests pass ----

Phase 2: Infrastructure (medium risk)
  5. Unit 1: Registry + dispatch (tools/registry.py, model_tools.py)
  6. Unit 3: Mandatory plugin loading (hermes_cli/plugins.py)
  7. Unit 5 partial: Registry + mandatory loading tests
  8. ---- CHECKPOINT: all Unit 1-3 tests pass ----

Phase 3: Gateway integration (high risk)
  9.  Unit 4A: Plugin slash command context dispatch
  10. Unit 4B: Email slash commands (/approve-email, /preview-email)
  11. ---- CHECKPOINT: gateway starts, slash commands work ----

Phase 4: Email rewrite (highest risk)
  12. Unit 4D: IMAP IDLE + backoff → rewrite into email plugin adapter
  13. Unit 4C: Graph API send → rewrite into email plugin adapter
  14. Unit 4E: Agent-side email workflow (decomposed mega-commit)
  15. Unit 5 remainder: Email-specific tests
  16. ---- FINAL: full test suite ----
```

## Commits to Skip

- 12 merge commits (`Merge branch 'worktree-agent-*'`)
- `7f0c23526` Merge remote-tracking branch 'fork/main'
- `e91ffb785` docs(plans): deterministic enforcement plan
- `e05ae3c7d` docs(plans): Phase 2a plan
- `baaa39a2c` docs(plans): Phase 4 plan
- `972302172` docs(email): approval debug trail

## Commits to DROP (obsoleted by upstream)

- `cbc4cc580` feat(send-message): Graph API via outlook-send-mail.js
  — must be reimplemented inside email plugin's `standalone_sender_fn`
- `3f4784b7b` refactor(email): HTML formatting to JS sender, kill SMTP
  — upstream already killed SMTP; HTML formatting must go in plugin

## Risk Summary

| Unit | Risk | Method | Effort |
|------|------|--------|--------|
| 1. Registry/dispatch | Med | Cherry-pick + edit | 1-2 hrs |
| 2. Plugin dirs | None | Clean cherry-pick | 30 min |
| 3. Mandatory loading | Med | Cherry-pick + edit | 1-2 hrs |
| 4A. Plugin cmd context | Low | Cherry-pick + reseat | 1 hr |
| 4B. Email slash cmds | Med | Cherry-pick + reseat into mixin | 2 hrs |
| 4C. Graph API send | High | Rewrite into email plugin | 3-4 hrs |
| 4D. IMAP IDLE/backoff | High | Rewrite into email plugin | 3-4 hrs |
| 4E. Agent-side email | Med | Decompose + merge | 2-3 hrs |
| 5. Tests + docs | Low | Mostly clean | 1 hr |

**Total estimated effort:** 2-3 days

- Phase 1-2 (clean + infrastructure): half day
- Phase 3 (gateway integration): half day
- Phase 4 (email rewrite): 1-2 days

## Open Questions

1. **Re-register send_message as agent tool?** Upstream intentionally
   removed it. Do we want to re-add it (diverge from upstream) or
   adopt upstream's position (agent cannot send messages autonomously)?
   If we re-add, we maintain a permanent fork point.

2. **Email plugin architecture:** Should Graph API routing live in
   the upstream email plugin's `standalone_sender_fn`, or should we
   create a separate `plugins/platforms/email-graph/` plugin that
   overrides the default? The latter is cleaner for maintenance.

3. **Mixin strategy:** Add email commands to existing
   `GatewaySlashCommandsMixin` (simpler) or create a new
   `GatewayEmailCommandsMixin` (cleaner separation)?

## Rollback

- `feat/governance-plugins` preserved intact as backup
- Can fall back to Option 4 (stay diverged) at any checkpoint

## Decision

- [x] Strategy chosen: Option 3 (cherry-pick)
- [ ] Open questions resolved
- [ ] Phase 1-2 complete (plugins + infrastructure)
- [ ] Phase 3 complete (gateway integration)
- [ ] Phase 4 complete (email rewrite)
- [ ] Full test suite passing
- [ ] Old branch archived or deleted
