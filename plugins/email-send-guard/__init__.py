"""
ABOUTME: Email send guard plugin for Hermes.
ABOUTME: Blocks send_message(email) until draft is loaded, previewed, and
ABOUTME: explicitly user-approved. Drafts stored as individual JSON files in a
ABOUTME: configurable directory (default: ~/.hermes/email-send-guard/drafts/).
ABOUTME: Approvals and current-pointer live in state.json. Approvals expire after 15 min.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

APPROVAL_TTL_SECONDS = 15 * 60  # 15 minutes


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _state_dir() -> Path:
    """Return ~/.hermes/email-send-guard/, creating it if needed.

    Honours HERMES_HOME env var for test isolation.
    """
    home = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
    d = Path(home) / "email-send-guard"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path() -> Path:
    """Path to the JSON state file."""
    return _state_dir() / "state.json"


def _get_drafts_dir() -> Path:
    """Return the directory for individual draft JSON files, creating it if needed.

    Reads drafts_dir from hermes config (plugins.entries.email-send-guard.drafts_dir).
    Falls back to _state_dir()/drafts/ if config unavailable or unset.
    Gotchas: Lazy-imports hermes_cli.config to avoid circular imports.
    """
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly()
        value = (
            cfg.get("plugins", {})
            .get("entries", {})
            .get("email-send-guard", {})
            .get("drafts_dir")
        )
        if value:
            d = Path(value).expanduser()
            d.mkdir(parents=True, exist_ok=True)
            return d
    except Exception:
        pass
    d = _state_dir() / "drafts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_draft(body_hash: str) -> dict | None:
    """Load a single draft file by body hash.

    Returns the parsed dict, or None if the file is missing.
    Gotchas: Logs a warning and returns None on corrupt JSON.
    """
    p = _get_drafts_dir() / f"draft_{body_hash}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("email-send-guard: corrupt draft file %s: %s", p.name, exc)
        return None


def _save_draft(body_hash: str, draft: dict) -> None:
    """Persist a single draft file atomically (tmp + rename).

    Uses the same write-then-rename pattern as _save_state to avoid
    partial writes on crash.
    Gotchas: Overwrites any existing draft with the same hash.
    """
    p = _get_drafts_dir() / f"draft_{body_hash}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(draft, indent=2), encoding="utf-8")
    tmp.replace(p)


def _migrate_drafts_from_state(state: dict) -> dict:
    """Move legacy inline drafts from state.json into individual files.

    Idempotent: skips drafts that already have a corresponding file.
    After migration, removes the "drafts" key from state and persists.
    Returns the cleaned state dict.
    """
    drafts = state.get("drafts", {})
    migrated = 0
    for body_hash, draft_data in drafts.items():
        target = _get_drafts_dir() / f"draft_{body_hash}.json"
        if target.exists():
            continue
        _save_draft(body_hash, draft_data)
        migrated += 1
    if migrated:
        logger.info("email-send-guard: migrated %d draft(s) to individual files", migrated)
    state.pop("drafts", None)
    _save_state(state)
    return state


def _empty_state() -> dict:
    """Return a blank state structure.

    Structure:
      current:   hash | None
      approvals: {hash: {approved_at, expires_at}}
    Drafts are stored as individual files via _save_draft/_load_draft.
    """
    return {"current": None, "current_by_session": {}, "approvals": {}}


def _load_state() -> dict:
    """Load state from disk, returning empty state if missing/corrupt."""
    p = _state_path()
    if not p.exists():
        return _empty_state()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # Migrate legacy drafts stored inline in state.json
        if "drafts" in data:
            data = _migrate_drafts_from_state(data)
        # Ensure all top-level keys present
        for key in ("current", "current_by_session", "approvals"):
            if key not in data:
                data[key] = _empty_state()[key]
        if not isinstance(data.get("current_by_session"), dict):
            data["current_by_session"] = {}
        # Drop any lingering drafts key
        data.pop("drafts", None)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("email-send-guard: corrupt state file, resetting: %s", exc)
        return _empty_state()


def _save_state(state: dict) -> None:
    """Persist state to disk atomically (write-then-rename)."""
    state.pop("drafts", None)  # Defensive: drafts live in individual files
    p = _state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(p)


def _body_hash(body: str) -> str:
    """Compute SHA256 hex digest of a message body.

    Used for draft identity — drafts are content-addressed by body alone
    since the review step is about inspecting what will be sent.
    """
    return hashlib.sha256(body.encode()).hexdigest()


def _scope_key(session_id: str = "", session_key: str = "") -> str:
    """Return a stable draft-current scope for a conversation, if available."""
    return (session_id or session_key or "").strip()


def _get_current_draft_id(state: dict, session_id: str = "", session_key: str = "") -> str:
    """Return the current draft for this session, falling back only for unscoped callers."""
    scope = _scope_key(session_id, session_key)
    if scope:
        current_by_session = state.get("current_by_session") or {}
        return str(current_by_session.get(scope) or "")
    return str(state.get("current") or "")


def _set_current_draft_id(state: dict, draft_id: str, session_id: str = "", session_key: str = "") -> None:
    """Set the current draft globally for legacy callers and per-session when scoped."""
    state["current"] = draft_id
    scope = _scope_key(session_id, session_key)
    if scope:
        current_by_session = state.setdefault("current_by_session", {})
        if isinstance(current_by_session, dict):
            current_by_session[scope] = draft_id


def _clear_current_draft_id(state: dict, draft_id: str) -> None:
    """Clear current pointers that still point at a successfully sent draft."""
    if state.get("current") == draft_id:
        state["current"] = None
    current_by_session = state.get("current_by_session") or {}
    if isinstance(current_by_session, dict):
        for scope, current in list(current_by_session.items()):
            if current == draft_id:
                del current_by_session[scope]


def _extract_recipient(target: str) -> str:
    """Extract the email recipient from a send_message target string.

    Handles: "email:user@example.com", "email:Display Name <user@example.com>",
    "email" (bare platform, home channel), "email:" (empty).
    Returns lowercased bare email address, or empty string for home channel.
    """
    parts = target.split(":", 1)
    if len(parts) < 2 or not parts[1].strip():
        return ""
    raw = parts[1].strip().lower()
    # Handle "Display Name <user@example.com>" format
    if "<" in raw and ">" in raw:
        match = re.search(r'<([^>]+)>', raw)
        if match:
            return match.group(1).strip()
    return raw


def _approval_hash(recipient: str, body: str) -> str:
    """Compute SHA256 hex digest binding approval to (recipient, body).

    NOTE: No longer used for approval storage/lookup — approvals are now
    keyed by draft_id (body hash) with recipient stored inside the approval
    record. Kept for backward compatibility and potential external use.
    The recipient is lowercased for consistency.
    """
    key = f"{recipient.lower()}\0{body}"
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# pre_tool_call hook -- the enforcement gate
# ---------------------------------------------------------------------------

def _pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    **_kw: Any,
) -> Optional[Dict[str, str]]:
    """Block email sends that haven't passed the full state machine.

    Returns None to allow, or {"action": "block", "message": "..."} to block.
    Only intercepts send_message with an email target.
    """
    if tool_name != "send_message":
        return None
    if not isinstance(args, dict):
        return None
    target = args.get("target", "")
    if not isinstance(target, str) or not target.lower().startswith("email"):
        return None

    # This is an email send -- enforce the state machine.
    body = args.get("message", "")
    bh = _body_hash(body)
    state = _load_state()

    # Gate 1: draft must be loaded (keyed by body hash — content review)
    draft = _load_draft(bh)
    if not draft or "loaded_at" not in draft:
        return {
            "action": "block",
            "message": (
                "Email send blocked: no draft loaded for this email body. Before sending, you must:\n"
                "1. Call email_load_draft(body='your message', recipient='recipient@example.com') to create a draft\n"
                "2. Call email_show_preview to review the draft with the user\n"
                "3. Wait for the user to run /approve-email\n"
                "4. Then call send_message(target='email:recipient@example.com', message='your message') to send"
            ),
        }

    # Gate 2: draft must be previewed
    if not draft.get("previewed_at"):
        return {
            "action": "block",
            "message": (
                "Email send blocked: draft exists but has not been previewed. "
                "Call email_show_preview now to show the draft to the user for review. "
                "After preview, the user will run /approve-email to approve sending."
            ),
        }

    # Gate 3: user must have approved this exact body hash (and approval
    # must not be expired). Do not rely on the mutable "current" pointer here:
    # "current" is only a UI convenience for /approve-email with no draft id.
    current_draft_id = state.get("current")
    logger.warning(
        "[EMAIL-TRACE] Gate 3: body_hash=%s, current_draft_id=%s, matching=%s",
        bh, current_draft_id, bh == current_draft_id,
    )

    approval = state["approvals"].get(bh)
    logger.warning(
        "[EMAIL-TRACE] Gate 3: draft_id=%s, approval_found=%s, approval=%s",
        bh, approval is not None, approval,
    )
    if not approval:
        return {
            "action": "block",
            "halt_turn": True,
            "message": (
                "Email send blocked: draft previewed but not yet approved by the user. "
                "NEXT STEP: Wait for the user to run /approve-email — only they can do this. "
                "Once approved, call send_message with the same body and recipient to send. "
                "The send_message tool is still available; it will succeed after user approval."
            ),
        }

    if approval.get("expires_at", 0) < time.time():
        logger.warning(
            "[EMAIL-TRACE] Gate 3: approval expired — expires_at=%s, now=%s",
            approval.get("expires_at"), time.time(),
        )
        return {
            "action": "block",
            "halt_turn": True,
            "message": (
                "Email send blocked: previous approval has expired (15-minute TTL). "
                "To resend, restart the approval workflow:\n"
                "1. Call email_load_draft(body=..., recipient=...) to reload the draft\n"
                "2. Call email_show_preview() to show it to the user\n"
                "3. Wait for the user to run /approve-email\n"
                "4. Then call send_message to send"
            ),
        }

    # Verify recipient matches (normalized)
    approved_recipient = approval.get("recipient", "")
    send_recipient = _extract_recipient(target)
    logger.warning(
        "[EMAIL-TRACE] Gate 3: approved_recipient=%s, send_recipient=%s",
        approved_recipient, send_recipient,
    )
    if send_recipient and approved_recipient and send_recipient != approved_recipient:
        return {
            "action": "block",
            "halt_turn": True,
            "message": (
                f"Email send blocked: recipient mismatch. Approved for '{approved_recipient}' "
                f"but send target is '{send_recipient}'. "
                f"Call email_load_draft(body=..., recipient='{send_recipient}') to start a new approval for this recipient."
            ),
        }

    # All gates passed — allow the send. Approval is consumed AFTER
    # successful send by _post_tool_call (not here) so a failed send
    # doesn't burn the approval.
    logger.warning(
        "[EMAIL-TRACE] Gate 3: all gates passed for draft_id=%s — send allowed (approval preserved until post_tool_call)",
        bh,
    )
    return None


# ---------------------------------------------------------------------------
# post_tool_call hook -- consume approval after successful send
# ---------------------------------------------------------------------------

def _post_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    **_kw: Any,
) -> None:
    """Consume the one-time approval AFTER a successful send_message.

    Moved out of _pre_tool_call so that a failed send (Graph API timeout,
    missing config, etc.) does not burn the approval. The user only needs
    to re-approve if the send actually succeeded and the approval was used.
    Gotchas: Only fires for send_message with email targets. Checks the
    result for error indicators before consuming.
    """
    if tool_name != "send_message":
        return
    if not isinstance(args, dict):
        return
    target = args.get("target", "")
    if not isinstance(target, str) or not target.lower().startswith("email"):
        return

    # Check if the send failed — don't consume approval on error.
    # Tool results are JSON strings; errors contain "error" keys or
    # are non-JSON exception strings.
    send_failed = False
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("error"):
                send_failed = True
        except (json.JSONDecodeError, ValueError):
            # Non-JSON result (e.g. raw exception message) = failure
            send_failed = True

    body = args.get("message", "")
    bh = _body_hash(body)
    state = _load_state()

    if send_failed:
        logger.warning(
            "[EMAIL-TRACE] post_tool_call: send_message failed — approval preserved for draft_id=%s",
            bh,
        )
        return

    # Send succeeded — consume the one-time approval
    if bh in state.get("approvals", {}):
        del state["approvals"][bh]
        _clear_current_draft_id(state, bh)
        _save_state(state)
        logger.warning(
            "[EMAIL-TRACE] post_tool_call: approval consumed after successful send for draft_id=%s",
            bh,
        )


# ---------------------------------------------------------------------------
# Registered tools (agent-callable)
# ---------------------------------------------------------------------------

def _email_load_draft(
    body: str = "",
    recipient: str = "",
    session_id: str = "",
    session_key: str = "",
    **_kw: Any,
) -> str:
    """Store a draft email body for review. Computes and returns the content hash.

    Usage: Agent calls this before sending any email. The hash identifies the
    exact body content for preview and approval. Recipient is required so
    the approval can be bound to the specific target.
    Gotchas: Overwrites any prior draft with the same hash. Sets current to
    this draft. Recipient is stored and used in the approval hash.
    """
    if not body:
        return json.dumps({"error": "body is required"})

    h = _body_hash(body)
    _save_draft(h, {
        "body": body,
        "recipient": recipient.strip().lower(),
        "loaded_at": time.time(),
        "previewed_at": None,
    })
    state = _load_state()
    _set_current_draft_id(state, h, session_id=session_id, session_key=session_key)
    _save_state(state)

    return json.dumps({
        "status": "draft_loaded",
        "draft_id": h,
        "body_length": len(body),
        "recipient": recipient.strip().lower(),
        "next_step": "Call email_show_preview to preview, then user approves via /approve-email",
    })


def _email_show_preview(
    draft_id: str = "",
    session_id: str = "",
    session_key: str = "",
    **_kw: Any,
) -> str:
    """Show a formatted preview of a loaded draft and mark it as previewed.

    Usage: Agent calls this after email_load_draft. Omit draft_id to preview
    the most recently loaded draft.
    Gotchas: Returns an error if no matching draft exists.
    """
    state = _load_state()

    if not draft_id:
        draft_id = _get_current_draft_id(state, session_id=session_id, session_key=session_key)
    if not draft_id:
        return json.dumps({"error": "No draft to preview. Load a draft first with email_load_draft."})

    draft = _load_draft(draft_id)
    if not draft:
        return json.dumps({"error": f"No draft found with id {draft_id}"})

    draft["previewed_at"] = time.time()
    _save_draft(draft_id, draft)

    return json.dumps({
        "status": "draft_previewed",
        "draft_id": draft_id,
        "preview": draft["body"],
        "next_step": "User must run /approve-email to approve sending",
    })


# ---------------------------------------------------------------------------
# Slash command (user-only)
# ---------------------------------------------------------------------------

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _handle_approve(
    raw_args: str,
    session_id: str = "",
    session_key: str = "",
    **_kw: Any,
) -> str | dict:
    """Approve an email draft for sending. Sets a 15-minute approval window.

    Usage: /approve-email [draft_id]
    If no draft_id provided (or arg is not a valid hash), approves the current
    (most recently loaded) draft. Approval is bound to the (recipient, body)
    tuple stored in the draft so it cannot be replayed to a different recipient.
    Gotchas: Only works after the draft has been loaded and previewed.
    On success returns a dict with ``approved_tool_call`` so the gateway can
    execute send_message directly, plus ``followup_agent_message`` for older
    dispatchers that still route approval through an agent turn.
    """
    logger.warning("[EMAIL-TRACE] _handle_approve called with raw_args=%s", raw_args)
    raw = raw_args.strip() if raw_args else ""
    draft_id = raw if _SHA256_RE.match(raw) else ""
    state = _load_state()

    if not draft_id:
        draft_id = _get_current_draft_id(state, session_id=session_id, session_key=session_key)
        logger.warning(
            "[EMAIL-TRACE] No draft_id in args, using scoped current=%s session_id=%s session_key=%s",
            draft_id, session_id, session_key,
        )
    if not draft_id:
        return (
            "No draft to approve for this session. Load and preview a draft first with "
            "email_load_draft/email_show_preview, or run /approve-email <draft_id>."
        )

    draft = _load_draft(draft_id)
    if not draft:
        return f"No draft found with id {draft_id}."
    if not draft.get("previewed_at"):
        return "Draft not previewed yet. Preview with email_show_preview first."

    # Store approval keyed by draft_id (body hash) so pre_tool_call can
    # look it up directly from the current draft pointer.
    recipient = draft.get("recipient", "")
    logger.warning("[EMAIL-TRACE] _handle_approve: draft_id=%s, recipient=%s", draft_id, recipient)
    if not recipient:
        return "Draft has no recipient. Load a new draft with a required recipient before approving."

    now = time.time()
    state["approvals"][draft_id] = {
        "recipient": recipient.lower(),
        "approved_at": now,
        "expires_at": now + APPROVAL_TTL_SECONDS,
    }
    _save_state(state)
    logger.warning(
        "[EMAIL-TRACE] Stored approval: approvals[%s] = {recipient: %s, approved_at: %s, expires_at: %s}",
        draft_id, recipient, now, now + APPROVAL_TTL_SECONDS,
    )

    recipient_note = f" to {recipient}" if recipient else ""
    user_message = (
        f"Email draft approved{recipient_note} (id: {draft_id[:12]}...). "
        f"Approval valid for {APPROVAL_TTL_SECONDS // 60} minutes."
    )

    # Build the exact send_message call that the gateway can execute
    # deterministically after approval. Keep the full follow-up message as a
    # compatibility fallback for dispatchers that do not yet understand
    # approved_tool_call.
    approved_tool_call = None
    if recipient:
        approved_tool_call = {
            "name": "send_message",
            "args": {
                "target": f"email:{recipient}",
                "message": draft["body"],
            },
        }

    followup = (
        f"The user has approved sending the email to {recipient}. "
        f"Call send_message now with target='email:{recipient}' and the following EXACT body "
        f"(do not modify it):\n\n{draft['body']}"
    )

    logger.warning("[EMAIL-TRACE] user_message=%s", user_message)
    logger.warning("[EMAIL-TRACE] followup_agent_message=%s", followup)
    result = {
        "user_message": user_message,
        "followup_agent_message": followup,
    }
    if approved_tool_call:
        result["approved_tool_call"] = approved_tool_call
    logger.warning("[EMAIL-TRACE] _handle_approve returning dict: %s", result)
    return result


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

_LOAD_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {
            "type": "string",
            "description": "The full email body text to store as a draft.",
        },
        "recipient": {
            "type": "string",
            "description": "The email recipient address (e.g. user@example.com). Used to bind approval to this specific recipient.",
        },
    },
    "required": ["body", "recipient"],
}

_SHOW_PREVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "draft_id": {
            "type": "string",
            "description": "The draft hash ID returned by email_load_draft. Optional — defaults to the most recently loaded draft.",
        },
    },
}


def register(ctx) -> None:
    """Wire the email send guard into the Hermes plugin system.

    Registers: pre_tool_call and post_tool_call hooks, two agent-callable
    tools, and one user-only slash command.
    Gotchas: State is per-user (stored under HERMES_HOME), not per-session.
    """
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)

    ctx.register_tool(
        name="email_load_draft",
        toolset="email_send_guard",
        description="Store an email draft for review before sending.",
        schema=_LOAD_DRAFT_SCHEMA,
        handler=_email_load_draft,
    )

    ctx.register_tool(
        name="email_show_preview",
        toolset="email_send_guard",
        description="Preview a loaded email draft and mark it as reviewed.",
        schema=_SHOW_PREVIEW_SCHEMA,
        handler=_email_show_preview,
    )

    ctx.register_command(
        "approve-email",
        handler=_handle_approve,
        description="Approve an email draft for sending (15-min TTL).",
        args_hint="[draft_id]",
    )
