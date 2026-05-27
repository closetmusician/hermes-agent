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
    return {"current": None, "approvals": {}}


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
        for key in ("current", "approvals"):
            if key not in data:
                data[key] = _empty_state()[key]
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


def _extract_recipient(target: str) -> str:
    """Extract the email recipient from a send_message target string.

    Target format is "email:user@example.com" (case-insensitive prefix).
    Returns the lowercased recipient, or empty string if unparseable.
    """
    parts = target.split(":", 1)
    if len(parts) < 2:
        return ""
    return parts[1].strip().lower()


def _approval_hash(recipient: str, body: str) -> str:
    """Compute SHA256 hex digest binding approval to (recipient, body).

    Approvals must be scoped to a specific recipient so that an approval
    for safe@company.com cannot be replayed to evil@attacker.com with the
    same body. The recipient is lowercased for consistency.
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
    recipient = _extract_recipient(target)
    ah = _approval_hash(recipient, body)
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

    # Gate 3: user must have approved (and approval must not be expired).
    # Approval is keyed by hash(recipient, body) so an approval for one
    # recipient cannot be replayed to send the same body to another.
    approval = state["approvals"].get(ah)
    if not approval:
        return {
            "action": "block",
            "halt_turn": True,
            "message": (
                "Email send blocked: draft previewed but not yet approved. "
                "The user must run /approve-email in Telegram — you cannot do this. "
                "STOP here. Do not call send_message again or attempt alternative transports (SMTP, himalaya CLI, etc.). "
                "Only the send_message tool is permitted for outbound email."
            ),
        }
    if approval.get("expires_at", 0) < time.time():
        return {
            "action": "block",
            "halt_turn": True,
            "message": (
                "Email send blocked: previous approval expired. The user must restart the workflow. "
                "STOP here. Do not call send_message again or attempt alternative transports (SMTP, himalaya CLI, etc.). "
                "Only the send_message tool is permitted for outbound email."
            ),
        }

    # All gates passed
    return None


# ---------------------------------------------------------------------------
# Registered tools (agent-callable)
# ---------------------------------------------------------------------------

def _email_load_draft(body: str = "", recipient: str = "", **_kw: Any) -> str:
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
    state["current"] = h
    _save_state(state)

    return json.dumps({
        "status": "draft_loaded",
        "draft_id": h,
        "body_length": len(body),
        "recipient": recipient.strip().lower(),
        "next_step": "Call email_show_preview to preview, then user approves via /approve-email",
    })


def _email_show_preview(draft_id: str = "", **_kw: Any) -> str:
    """Show a formatted preview of a loaded draft and mark it as previewed.

    Usage: Agent calls this after email_load_draft. Omit draft_id to preview
    the most recently loaded draft.
    Gotchas: Returns an error if no matching draft exists.
    """
    state = _load_state()

    if not draft_id:
        draft_id = state.get("current", "")
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


def _handle_approve(raw_args: str) -> str:
    """Approve an email draft for sending. Sets a 15-minute approval window.

    Usage: /approve-email [draft_id]
    If no draft_id provided (or arg is not a valid hash), approves the current
    (most recently loaded) draft. Approval is bound to the (recipient, body)
    tuple stored in the draft so it cannot be replayed to a different recipient.
    Gotchas: Only works after the draft has been loaded and previewed.
    """
    raw = raw_args.strip() if raw_args else ""
    draft_id = raw if _SHA256_RE.match(raw) else ""
    state = _load_state()

    if not draft_id:
        draft_id = state.get("current", "")
    if not draft_id:
        return "No draft to approve. Load a draft first with email_load_draft."

    draft = _load_draft(draft_id)
    if not draft:
        return f"No draft found with id {draft_id}."
    if not draft.get("previewed_at"):
        return "Draft not previewed yet. Preview with email_show_preview first."

    # Compute the approval key from (recipient, body) so the approval is
    # bound to this specific recipient. If no recipient was stored (legacy
    # drafts), fall back to body-only hash for backward compat.
    recipient = draft.get("recipient", "")
    ah = _approval_hash(recipient, draft["body"])

    now = time.time()
    state["approvals"][ah] = {
        "approved_at": now,
        "expires_at": now + APPROVAL_TTL_SECONDS,
    }
    _save_state(state)

    recipient_note = f" for {recipient}" if recipient else ""
    return (
        f"Email draft approved{recipient_note} (id: {draft_id[:12]}...). "
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
        "recipient": {
            "type": "string",
            "description": "The email recipient address (e.g. user@example.com). Used to bind approval to this specific recipient.",
        },
    },
    "required": ["body"],
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
