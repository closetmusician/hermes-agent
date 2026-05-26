# Fix: Email send infinite loop — 3 bugs

## Context

Hermes gets stuck in an infinite loop when trying to send approved emails via Telegram. Three independent bugs compound to make email sending impossible:

1. **control-room blocks even after email-send-guard approves** — the `email-send.yaml` policy checks for `draft.email.loaded` / `draft.email.approved` workflow state keys that **no code ever sets**. Since both plugins register `pre_tool_call` hooks, control-room's broken policy blocks `send_message` even when email-send-guard's 3-gate state machine is fully satisfied.

2. **LLM corrupts the draft_id hash** — `email_show_preview` requires the exact 64-char SHA256 hash as `draft_id`, but the LLM frequently hallucinates/truncates/duplicates portions of it. The tool should fall back to the current draft like `/approve-email` does.

3. **/approve-email parses freetext as draft_id** — when user types `/approve-email to yu_kuan@yahoo.com`, the handler treats `"to yu_kuan@yahoo.com"` as the draft_id lookup key instead of falling back to `state["current"]`.

## Files to modify

- `plugins/email-send-guard/__init__.py` — Bugs 2 & 3
- `plugins/control-room/policies/email-send.yaml` — Bug 1
- `tests/plugins/test_email_send_guard_plugin.py` — New tests for fixes
- `tests/plugins/test_email_send_guard_e2e.py` — New e2e tests for fixes

## Fix 1: Remove broken control-room email-send policy

**File:** `plugins/control-room/policies/email-send.yaml`

**Action:** Delete this file.

**Rationale:** email-send-guard already implements a complete 3-gate state machine (draft loaded → previewed → approved) with proper TTL, content-addressing, and recipient binding. The control-room policy is redundant dead code — nobody ever sets the required workflow state keys. Two independent blocking systems for the same operation that don't coordinate is an anti-pattern. email-send-guard is the domain-specific plugin for this purpose.

## Fix 2: email_show_preview falls back to current draft

**File:** `plugins/email-send-guard/__init__.py`, `_email_show_preview()` (lines 231-254)

**Changes:**
- When `draft_id` is empty, fall back to `state["current"]` (same pattern as `_handle_approve`)
- Update tool schema: remove `draft_id` from `required` list, update description to say "optional — defaults to most recently loaded draft"
- Update tool description to match

## Fix 3: /approve-email ignores non-hash args

**File:** `plugins/email-send-guard/__init__.py`, `_handle_approve()` (lines 261-301)

**Changes:**
- After stripping `raw_args`, validate it looks like a SHA256 hash (64 hex chars) before using it as `draft_id`
- If it doesn't match, fall back to `state["current"]` (existing logic for empty args)
- This handles `/approve-email`, `/approve-email to someone@example.com`, and `/approve-email <actual-hash>` all correctly

## Tests

Add tests to `test_email_send_guard_plugin.py`:
- `test_show_preview_falls_back_to_current_draft` — call email_show_preview with no draft_id, verify it uses current
- `test_approve_ignores_non_hash_args` — call /approve-email with freetext, verify it uses current draft
- `test_approve_accepts_valid_hash` — call /approve-email with a real 64-char hex hash, verify it uses that hash

Add e2e test to `test_email_send_guard_e2e.py`:
- `TestApproveWithFreetext` — full pipeline where /approve-email receives freetext instead of hash

## Verification

1. Run unit tests: `python -m pytest tests/plugins/test_email_send_guard_plugin.py -v`
2. Run e2e tests: `python -m pytest tests/plugins/test_email_send_guard_e2e.py -v`
3. Run control-room tests to confirm no regression: `python -m pytest tests/plugins/test_control_room_plugin.py tests/plugins/test_control_room_e2e.py -v`
4. Verify `email-send.yaml` no longer exists in `plugins/control-room/policies/`
