# Hermes Email Send — Root Cause Analysis & Remaining Work

## What happened

Hermes got stuck in an infinite `/approve-email` loop on Telegram. The user
approved multiple times but email never sent. Four independent bugs compounded:

1. **Invisible tools** — `email_load_draft` and `email_show_preview` were not
   included in the `messaging` toolset, so the LLM on Telegram couldn't see them.
   The guard's block messages told the agent "call email_load_draft" — a tool it
   didn't have. Guaranteed loop.

2. **No turn-halt on user-dependent blocks** — When the guard blocked
   `send_message` with "wait for /approve-email," the block returned as a
   synthetic `{"error": "..."}` tool result. The LLM treated it as a transient
   failure and retried immediately. No mechanism existed to make the agent stop
   and wait for user action.

3. **Agent could read/write its own guard state** — `~/.hermes/email-send-guard/`
   was not in the file_safety denylist. The agent read `state.json`, tried to
   write fake approvals, and inspected draft files — all bypassing the guard.

4. **SMTP fallback path** — The himalaya skill declared `fallback_for_tools:
   [send_message]`, causing the agent to load it as a fallback when `send_message`
   was blocked. The agent then grep'd for SMTP credentials and tried direct
   Yahoo SMTP, completely bypassing the email broker.

## What was fixed

| Fix | Commit | Files |
|-----|--------|-------|
| Add `email_send_guard` to `messaging` toolset includes | merged | `toolsets.py` |
| `halt_turn: True` on "not approved" and "expired" blocks | merged | `plugins/email-send-guard/__init__.py`, `hermes_cli/plugins.py`, `agent/tool_executor.py`, `agent/agent_runtime_helpers.py`, `model_tools.py`, `tools/registry.py` |
| `email-send-guard/` added to read/write denylist | merged | `agent/file_safety.py` |
| Remove `fallback_for_tools: [send_message]` from himalaya | merged | `skills/email/himalaya/SKILL.md` |
| Skill-vs-tool priority in prompt builder | merged | `agent/prompt_builder.py` |
| Draft files stored individually (migration from inline state) | merged | `plugins/email-send-guard/__init__.py` |
| Removed redundant `email-send.yaml` control-room policy that blocked sends even after guard approval | `ced55a0cc` | `control-room/email-send.yaml` |
| `email_show_preview` falls back to current draft when `draft_id` is empty/corrupt | `ced55a0cc` | `plugins/email-send-guard/__init__.py` |
| `/approve-email` ignores non-hash freetext args and falls back to current draft | `ced55a0cc` | `plugins/email-send-guard/__init__.py` |
| Complete email workflow — schema, routing, HTML formatting | `56cd50eee` | `tools/send_message_tool.py`, `SOUL.md`, `agent/prompt_builder.py` |
| Kill SMTP fallback, move HTML formatting to JS sender | `3f4784b7b` | `tools/send_message_tool.py`, `gateway/platforms/email.py` |
| Make control-room and email-send-guard mandatory plugins | `9602c2ecf` | `hermes_cli/plugins.py` |
| Move pre_tool_call enforcement into registry.dispatch() | `73e478b32` | `tools/registry.py` |

## Remaining work

### Security hardening (from Codex review)

- [ ] **HMAC-sign approval records** — Approval state is plain JSON with no
  cryptographic binding. File-safety denylist is defense-in-depth only (terminal
  tool can still `cat state.json`). Sign `(recipient, body_hash, expires_at)`
  with a per-session in-memory secret so forged approvals are rejected.

- [ ] **Guard the gateway reply path** — `gateway/platforms/email.py:753-794`
  sends via raw SMTP with zero guard coverage. It's a direct method call, not a
  tool invocation, so `pre_tool_call` hooks never fire. Route through the same
  guarded chokepoint per the `EmailSendBroker` design in `hermes-fail.md` Part 9.

### Test coverage

- [ ] **Integration test for halt_turn loop-break** — Verify that a
  `send_message` call blocked with `halt_turn: True` actually stops the
  conversation loop (not just unit-tests the flag). Requires mocking the
  conversation loop.

- [ ] **E2E test for full Telegram email flow** — Draft → preview → approve →
  send on a messaging platform where `email_send_guard` toolset is now included.

---

## Bug: /approve-email not dispatched on Telegram (2026-05-28)

### Symptom

User sends `/approve-email` as a standalone Telegram message. System responds
"Email draft approved..." but the email is never sent. System then claims the
message wasn't standalone and asks user to resend. Multiple fix attempts failed.

### Root Cause (3 converging bugs)

Three independent Codex investigations converged on the same bug chain:

**Bug A — Plugin commands don't bypass active-session guard**

`should_bypass_active_session()` in `hermes_cli/commands.py:353` only checks
the built-in `COMMAND_REGISTRY`. Plugin-registered commands like `/approve-email`
(registered via `ctx.register_command()` in `plugins/email-send-guard/__init__.py:452`)
return `False`. When the user sends `/approve-email` while an agent session is
active, it hits the busy guard at `gateway/platforms/base.py:3005` and gets
enqueued to `_pending_messages` instead of being dispatched immediately.

**Bug B — Pending-queue replay bypasses plugin dispatch**

When the active session finishes, `_run_agent()` dequeues pending messages at
`gateway/run.py:17418`. The replay path at `run.py:17437` only filters built-in
commands via `resolve_command` — it doesn't recognize plugin commands. The
`/approve-email` text is passed directly to `_run_agent()` as plain text at
`run.py:17563`, completely bypassing the plugin command dispatch block at
`run.py:7484`. The LLM receives `/approve-email` as free text and hallucinates
that it was part of a prior message.

**Bug C — Approval doesn't trigger send**

Even when `/approve-email` IS dispatched correctly (e.g., no active session),
`_handle_approve()` at `email-send-guard/__init__.py:388` only writes an approval
record with a 15-minute TTL and returns a confirmation string. It does NOT trigger
`send_message`. The slash command output goes directly to the user via
`run.py:7491-7497` and never enters the model's conversation context. The LLM
has no idea approval happened and doesn't know to call `send_message`.

### Fix Plan

Fixes must be applied in order — A/B without C still won't send; C without A/B
still swallows the command.

1. **Fix A** — In `should_bypass_active_session()` (`hermes_cli/commands.py:353`),
   also check `get_plugin_command_handler(command_name)` so plugin commands
   bypass the active-session guard.

2. **Fix B** — In the pending-queue replay path (`gateway/run.py:17437`), check
   if a dequeued message is a plugin command. If so, route it back through
   `_handle_message()` / plugin dispatch instead of forwarding as plain text to
   `_run_agent()`.

3. **Fix C** — In `_handle_approve()` (`plugins/email-send-guard/__init__.py:388`),
   after saving the approval record, either:
   - (a) Directly invoke the pending email send using the gateway context, OR
   - (b) Inject the approval confirmation into the model's conversation history
     (not just the user-facing chat) so the LLM knows to call `send_message`.

### Key Files

- `gateway/platforms/base.py` — active-session guard (lines 3005-3035)
- `hermes_cli/commands.py` — `should_bypass_active_session()` (line 353)
- `gateway/run.py` — plugin dispatch (7484), pending replay (17418-17563)
- `plugins/email-send-guard/__init__.py` — approve handler (353-394), registration (452)

### Other Affected Commands

Any plugin-registered slash command suffers from Bug A/B when sent during an
active agent session. Audit all `ctx.register_command()` calls across plugins.
