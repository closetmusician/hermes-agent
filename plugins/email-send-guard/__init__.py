"""
ABOUTME: Email send guard plugin for Hermes.
ABOUTME: Blocks send_message(email) until draft is loaded, previewed, and
ABOUTME: explicitly user-approved. State machine: EMPTY -> LOADED -> PREVIEWED
ABOUTME: -> APPROVED -> ALLOWED. Approvals expire after 15 minutes.
ABOUTME: Uses SHA256 body hashing to bind approval to exact content.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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


def _empty_state() -> dict:
    """Return a blank state structure.

    Structure:
      drafts:    {hash: {body, loaded_at, previewed_at}}
      current:   hash | None
      approvals: {hash: {approved_at, expires_at}}
    """
    return {"drafts": {}, "current": None, "approvals": {}}


def _load_state() -> dict:
    """Load state from disk, returning empty state if missing/corrupt."""
    p = _state_path()
    if not p.exists():
        return _empty_state()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # Ensure all top-level keys present
        for key in ("drafts", "current", "approvals"):
            if key not in data:
                data[key] = _empty_state()[key]
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("email-send-guard: corrupt state file, resetting: %s", exc)
        return _empty_state()


def _save_state(state: dict) -> None:
    """Persist state to disk atomically (write-then-rename)."""
    p = _state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(p)


def _body_hash(body: str) -> str:
    """Compute SHA256 hex digest of a message body."""
    return hashlib.sha256(body.encode()).hexdigest()


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
    if not isinstance(target, str) or not target.startswith("email"):
        return None

    # This is an email send -- enforce the state machine.
    body = args.get("message", "")
    body_hash = _body_hash(body)
    state = _load_state()

    # Gate 1: draft must be loaded
    draft = state["drafts"].get(body_hash)
    if not draft or "loaded_at" not in draft:
        return {
            "action": "block",
            "message": (
                "Email send blocked: no draft loaded for this body. "
                "Load draft first with email_load_draft."
            ),
        }

    # Gate 2: draft must be previewed
    if not draft.get("previewed_at"):
        return {
            "action": "block",
            "message": (
                "Email send blocked: draft not previewed. "
                "Preview draft first with email_show_preview."
            ),
        }

    # Gate 3: user must have approved (and approval must not be expired)
    approval = state["approvals"].get(body_hash)
    if not approval:
        return {
            "action": "block",
            "message": (
                "Email send blocked: user approval required. "
                "Run /approve-email to approve this draft."
            ),
        }
    if approval.get("expires_at", 0) < time.time():
        return {
            "action": "block",
            "message": (
                "Email send blocked: approval expired. "
                "Run /approve-email to re-approve this draft."
            ),
        }

    # All gates passed
    return None


# ---------------------------------------------------------------------------
# Registered tools (agent-callable)
# ---------------------------------------------------------------------------

def _email_load_draft(body: str = "", **_kw: Any) -> str:
    """Store a draft email body for review. Computes and returns the content hash.

    Usage: Agent calls this before sending any email. The hash identifies the
    exact body content for preview and approval.
    Gotchas: Overwrites any prior draft with the same hash. Sets current to
    this draft.
    """
    if not body:
        return json.dumps({"error": "body is required"})

    h = _body_hash(body)
    state = _load_state()
    state["drafts"][h] = {
        "body": body,
        "loaded_at": time.time(),
        "previewed_at": None,
    }
    state["current"] = h
    _save_state(state)

    return json.dumps({
        "status": "draft_loaded",
        "draft_id": h,
        "body_length": len(body),
        "next_step": "Call email_show_preview to preview, then user approves via /approve-email",
    })


def _email_show_preview(draft_id: str = "", **_kw: Any) -> str:
    """Show a formatted preview of a loaded draft and mark it as previewed.

    Usage: Agent calls this after email_load_draft. The draft_id is the hash
    returned by email_load_draft.
    Gotchas: Returns an error if the draft_id doesn't match a loaded draft.
    """
    if not draft_id:
        return json.dumps({"error": "draft_id is required"})

    state = _load_state()
    draft = state["drafts"].get(draft_id)
    if not draft:
        return json.dumps({"error": f"No draft found with id {draft_id}"})

    draft["previewed_at"] = time.time()
    _save_state(state)

    return json.dumps({
        "status": "draft_previewed",
        "draft_id": draft_id,
        "preview": draft["body"],
        "next_step": "User must run /approve-email to approve sending",
    })


# ---------------------------------------------------------------------------
# Slash command (user-only)
# ---------------------------------------------------------------------------

def _handle_approve(raw_args: str) -> str:
    """Approve an email draft for sending. Sets a 15-minute approval window.

    Usage: /approve-email [draft_id]
    If no draft_id provided, approves the current (most recently loaded) draft.
    Gotchas: Only works after the draft has been loaded and previewed.
    """
    draft_id = raw_args.strip() if raw_args else ""
    state = _load_state()

    if not draft_id:
        draft_id = state.get("current", "")
    if not draft_id:
        return "No draft to approve. Load a draft first with email_load_draft."

    draft = state["drafts"].get(draft_id)
    if not draft:
        return f"No draft found with id {draft_id}."
    if not draft.get("previewed_at"):
        return "Draft not previewed yet. Preview with email_show_preview first."

    now = time.time()
    state["approvals"][draft_id] = {
        "approved_at": now,
        "expires_at": now + APPROVAL_TTL_SECONDS,
    }
    _save_state(state)

    return (
        f"Email draft approved (id: {draft_id[:12]}...). "
        f"Approval valid for {APPROVAL_TTL_SECONDS // 60} minutes."
    )


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
    },
    "required": ["body"],
}

_SHOW_PREVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "draft_id": {
            "type": "string",
            "description": "The draft hash ID returned by email_load_draft.",
        },
    },
    "required": ["draft_id"],
}


def register(ctx) -> None:
    """Wire the email send guard into the Hermes plugin system.

    Registers: pre_tool_call hook, two agent-callable tools, and one
    user-only slash command.
    Gotchas: State is per-user (stored under HERMES_HOME), not per-session.
    """
    ctx.register_hook("pre_tool_call", _pre_tool_call)

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
