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
| Draft-based approval lookup (replaces hash-based) | merged | `plugins/email-send-guard/__init__.py` |
| Full body in followup message (not truncated) | merged | `plugins/email-send-guard/__init__.py` |
| Approval consumed after send (one-time use) | merged | `plugins/email-send-guard/__init__.py` |
| Recipient required in `email_load_draft` schema | merged | `plugins/email-send-guard/__init__.py` |
| `_extract_recipient` handles angle brackets + bare platform | merged | `plugins/email-send-guard/__init__.py` |
| Approval followup, body-hash enforcement, session-scoped tool cache, and post-send approval consumption | `d97dfe04d` | `plugins/email-send-guard/__init__.py`, `model_tools.py`, `tools/send_message_tool.py`, `tools/registry.py`, `gateway/run.py`, `agent/tool_executor.py`, `agent/conversation_loop.py`, `hermes_cli/commands.py` |

## Remaining work

### Security hardening (from Codex review)

- [x] **HMAC-sign approval records** — Replaced hash-based approval matching
  with draft-based lookup. Approvals are now consumed after send (one-time use),
  eliminating replay risk. Cryptographic signing deferred as defense-in-depth.

- [x] **Guard the gateway reply path** — SMTP fallback removed entirely
  (`3f4784b7b`). All email sends now route through the JS sender via
  `send_message` tool, which passes through `pre_tool_call` guard hooks.

### Test coverage

- [ ] **Integration test for halt_turn loop-break** — Verify that a
  `send_message` call blocked with `halt_turn: True` actually stops the
  conversation loop (not just unit-tests the flag). Requires mocking the
  conversation loop.

- [ ] **E2E test for full Telegram email flow** — Draft → preview → approve →
  send on a messaging platform where `email_send_guard` toolset is now included.

---

## Bug: approval acknowledged but email never sent (2026-05-29)

### Symptom

Telegram `/approve-email` returned an approval confirmation with a 15-minute
validity window, but no email arrived in Yahoo. The approval state existed, but
the followup agent turn either could not see `send_message`, could not recreate
the exact approved body, or burned the one-time approval on a failed send.

### Root Cause

Four independent failures compounded:

1. **Approval hash mismatch** — `target="email"` produced an empty recipient
   during approval matching, so the generated hash never matched the stored
   approval for the intended recipient.

2. **Truncated followup body** — The approval handler sent only the first 200
   characters of the draft body back to the LLM. The LLM could not reconstruct
   the exact original body, so body-hash validation failed even when the user had
   approved the correct draft.

3. **Tool schema cache missing session context** — `_tool_defs_cache` in
   `model_tools.py` cached tool definitions without the session platform in the
   key. A stale non-messaging cache entry could win on followup turns, causing
   `send_message` to disappear from the model's tool schema after approval.

4. **Approval consumed before confirmed send** — The guard consumed one-time
   approval inside the pre-send gate. If the Graph API call failed, retrying
   required the user to approve again even though no email had been delivered.

### Fix Summary

Commit `d97dfe04d` changed the approval flow to draft-based approval with strict
body-hash enforcement, full-body followup messages, session-platform-aware tool
definition caching, and approval consumption only from `post_tool_call` after a
successful `send_message(email)` result. Graph API failures preserve approval so
the same approved draft can be retried during the TTL.

The supporting trace points remain intentionally noisy until Telegram/Yahoo live
delivery has been verified repeatedly.

### Verification Status

- [x] Unit and plugin integration coverage added for approval matching, full
  body followup, body-hash binding, recipient normalization, Graph failure retry,
  and post-send consumption.
- [x] Targeted email guard tests verified via `scripts/run_tests.sh`: `76/76`
  passed.
- [ ] Live trace confirms a Telegram approval followup reaches
  `send_message(email)`, Graph API send, and `post_tool_call` consumption.
- [ ] Live Telegram `/approve-email` test against the running gateway.
- [ ] Yahoo inbox delivery confirmation after the live Telegram approval.

### Step-by-step Debug Log Trail

All email traces use the same marker:

```bash
grep '\[EMAIL-TRACE\]' ~/.hermes/logs/gateway.log ~/.hermes/logs/gateway.error.log | tail -200
```

Follow the trail in this order when debugging a live Telegram send:

1. **Telegram slash dispatch**
   - `gateway/run.py` logs plugin command dispatch, raw handler result, and
     whether `followup_agent_message` rewrote the event text.
   - `hermes_cli/commands.py` logs `should_bypass_active_session()` decisions,
     including underscore-to-hyphen normalization for `/approve_email` versus
     `/approve-email`.

2. **Approval handler and draft binding**
   - `plugins/email-send-guard/__init__.py` logs `_handle_approve()` raw args,
     draft fallback to `state["current"]`, recipient, approval TTL, user-facing
     response, and full `followup_agent_message`.
   - The same plugin logs Gate 1/2/3 decisions, body hash comparisons, recipient
     comparisons, approval lookup, expiry, and final allow/block result.

3. **Followup agent turn**
   - `gateway/run.py` logs `_run_agent` entry, messages that look like approval
     followups, pending queue replay, and `_clear_session_env`.
   - `model_tools.py` logs `_tool_defs_cache` behavior with session platform in
     the cache key, which confirms that `send_message` should stay visible for
     the Telegram followup turn.

4. **Tool availability and guardrail execution**
   - `tools/registry.py` logs `_check_fn_cached` hit/miss behavior for
     `_check_send_message`.
   - `tools/send_message_tool.py` logs email target detection, recipient parsing,
     `_send_to_platform` routing, Graph API subprocess invocation, success, and
     failure stderr snippets.
   - `agent/tool_executor.py` logs concurrent and sequential `pre_tool_call`
     block/allow decisions for `send_message(email)` and `halt_turn` handling.
   - `agent/conversation_loop.py` logs guardrail halt reset at the start of a
     new turn and halt exit decisions.

5. **Approval consumption**
   - `plugins/email-send-guard/__init__.py` logs `post_tool_call` consumption
     after successful sends and preservation after failed sends. If Yahoo never
     receives the message, this is the first place to check whether Hermes thinks
     Graph succeeded.

If the trace stops after `_run_agent: message looks like email approval
followup`, the slash command was dispatched correctly but the followup agent turn
did not reach `send_message(email)`. Check normal gateway/model errors next,
then confirm the `model_tools.py` cache-key trace shows the Telegram session
platform and that `send_message` is present in the tool schema for the followup
turn.

Useful focused commands:

```bash
grep '\[EMAIL-TRACE\]' ~/.hermes/logs/gateway.log ~/.hermes/logs/gateway.error.log | tail -50
grep '\[EMAIL-TRACE\].*Graph API' ~/.hermes/logs/gateway.log ~/.hermes/logs/gateway.error.log
grep '\[EMAIL-TRACE\].*post_tool_call' ~/.hermes/logs/gateway.log ~/.hermes/logs/gateway.error.log
grep '\[EMAIL-TRACE\].*_tool_defs_cache' ~/.hermes/logs/gateway.log ~/.hermes/logs/gateway.error.log
```

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
