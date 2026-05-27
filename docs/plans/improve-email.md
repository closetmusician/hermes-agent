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

## What was fixed (this branch)

| Fix | Commit | Files |
|-----|--------|-------|
| Add `email_send_guard` to `messaging` toolset includes | uncommitted | `toolsets.py` |
| `halt_turn: True` on "not approved" and "expired" blocks | uncommitted | `plugins/email-send-guard/__init__.py`, `hermes_cli/plugins.py`, `agent/tool_executor.py`, `agent/agent_runtime_helpers.py`, `model_tools.py`, `tools/registry.py` |
| `email-send-guard/` added to read/write denylist | uncommitted | `agent/file_safety.py` |
| Remove `fallback_for_tools: [send_message]` from himalaya | uncommitted | `skills/email/himalaya/SKILL.md` |
| Skill-vs-tool priority in prompt builder | uncommitted | `agent/prompt_builder.py` |
| Draft files stored individually (migration from inline state) | uncommitted | `plugins/email-send-guard/__init__.py` |

Previously fixed in `ced55a0cc`:
- Removed redundant `email-send.yaml` control-room policy that blocked sends even after guard approval
- `email_show_preview` falls back to current draft when `draft_id` is empty/corrupt
- `/approve-email` ignores non-hash freetext args and falls back to current draft

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

### Email architecture (from Agent 3 / hermes-fail.md)

- [ ] **Remove raw SMTP fallback from `_send_email()`** —
  `tools/send_message_tool.py:1304-1334` falls back to `smtplib.SMTP()` using
  `EMAIL_PASSWORD` env var. All sends should route through the planned
  `EmailSendBroker` or at minimum through a guarded transport.

- [ ] **Add `email:` target to `send_message` schema examples** — The schema
  lists signal, matrix, yuanbao examples but omits email. The agent doesn't know
  `email:` is a valid target prefix.

- [ ] **Add email routing guidance to system prompt** — `SOUL.md` and
  `prompt_builder.py` have zero guidance on email routing. The agent is never told
  "use `send_message(target='email:...')` for email." This is what triggers
  improvisation (SMTP, himalaya CLI, etc.) when things go wrong.

### Test coverage

- [ ] **Integration test for halt_turn loop-break** — Verify that a
  `send_message` call blocked with `halt_turn: True` actually stops the
  conversation loop (not just unit-tests the flag). Requires mocking the
  conversation loop.

- [ ] **E2E test for full Telegram email flow** — Draft → preview → approve →
  send on a messaging platform where `email_send_guard` toolset is now included.

---

## E2E Email Workflow Smoke Test Plan

**Goal:** Exercise the full draft → edit → preview → approve → send → verify
receipt workflow via direct `AIAgent` invocation (bypasses Telegram transport,
exercises full agent + plugin + SMTP pipeline).

### Approach

Write and run `tests/e2e_email_smoke.py`. Loop execution until all 5
checkpoints pass.

### Phases

| # | Phase | User message to agent | Checkpoint |
|---|-------|-----------------------|------------|
| A | Draft creation | "Compose a short daily briefing email … Load it as a draft." | Draft file exists in `~/.hermes/email-send-guard/drafts/`, `state.json` `current` set |
| B | Edit + preview | "Edit the draft: add a 4th bullet … reload and preview it." | Agent calls `email_load_draft` (new hash) then `email_show_preview`; `previewed_at` set |
| C | Approve | *(programmatic — call `_handle_approve(draft_id)` directly)* | `state.json` has valid, non-expired approval |
| D | Send | "The email is approved. Send it now to yu_kuan@yahoo.com." | `send_message` returns success via SMTP |
| E | IMAP verify | *(connect to `imap.mail.yahoo.com:993`, search INBOX)* | Email found with correct content |

### Pre-flight

1. Reset `~/.hermes/email-send-guard/state.json` → `{"current": null, "approvals": {}}`
2. Clear stale drafts in `~/.hermes/email-send-guard/drafts/`
3. If `~/.pm-os-foci-token.json` exists, temporarily rename it (forces SMTP path, not Graph API)
4. Test SMTP connectivity independently

### Key files

- `run_agent.py` — `AIAgent`, `run_conversation()`
- `plugins/email-send-guard/__init__.py` — draft/preview/approve + guard hook
- `tools/send_message_tool.py:1271-1334` — `_send_email()` SMTP dispatch
- `toolsets.py:178` — `messaging` toolset (includes `email_send_guard`)
- `gateway/platforms/email.py:565-622` — `_fetch_unseen()` IMAP pattern
- `.env` — Yahoo SMTP/IMAP creds

### Risks

- Agent doesn't call `email_load_draft` → mitigate with explicit instruction
- Plugin not loaded during direct invocation → manually register before run
- Graph API path activates instead of SMTP → check/rename FOCI token
- IMAP timing → retry with 30s timeout
