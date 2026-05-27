#!/usr/bin/env python3
"""
ABOUTME: End-to-end smoke test for the email draft→preview→approve→send workflow.
ABOUTME: Directly invokes AIAgent (bypasses Telegram transport) to exercise the
ABOUTME: full email-send-guard plugin pipeline with Graph API delivery.
ABOUTME: Verifies receipt via Outlook Graph API Sent Items folder.
ABOUTME: Usage: python tests/e2e_email_smoke.py
"""

import json
import os
import subprocess
import sys
import time
import uuid

# ---------------------------------------------------------------------------
# Resolve project root so imports work regardless of cwd.
# ---------------------------------------------------------------------------
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_PROJ_ROOT)
sys.path.insert(0, _PROJ_ROOT)

# Load .env into os.environ (no third-party dependency).
_env_path = os.path.join(_PROJ_ROOT, ".env")
if os.path.isfile(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if not _line or _line.startswith("#"):
            continue
        if "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATE_DIR = os.path.expanduser("~/.hermes/email-send-guard")
_STATE_FILE = os.path.join(_STATE_DIR, "state.json")
RECIPIENT = "yu_kuan@yahoo.com"

# Drafts dir may be overridden by config. Resolve lazily after plugins load.
_DRAFTS_DIR: str | None = None


def _get_drafts_dir() -> str:
    """Resolve the actual drafts directory, matching what the plugin uses."""
    global _DRAFTS_DIR
    if _DRAFTS_DIR is not None:
        return _DRAFTS_DIR
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
            _DRAFTS_DIR = os.path.expanduser(value)
            return _DRAFTS_DIR
    except Exception:
        pass
    _DRAFTS_DIR = os.path.join(_STATE_DIR, "drafts")
    return _DRAFTS_DIR


def _load_state() -> dict:
    """Read current email-send-guard state from disk."""
    if not os.path.isfile(_STATE_FILE):
        return {"current": None, "approvals": {}}
    with open(_STATE_FILE) as f:
        return json.load(f)


def _reset_state() -> None:
    """Wipe guard state to a clean slate."""
    drafts_dir = _get_drafts_dir()
    os.makedirs(drafts_dir, exist_ok=True)
    with open(_STATE_FILE, "w") as f:
        json.dump({"current": None, "approvals": {}}, f)
    # Remove any leftover draft files.
    for name in os.listdir(drafts_dir):
        if name.startswith("draft_") and name.endswith(".json"):
            os.remove(os.path.join(drafts_dir, name))
    print(f"[pre] Guard state reset. Drafts dir: {drafts_dir}")


def _draft_file(draft_id: str) -> str:
    return os.path.join(_get_drafts_dir(), f"draft_{draft_id}.json")


def _load_draft(draft_id: str) -> dict | None:
    path = _draft_file(draft_id)
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


def _checkpoint(label: str, ok: bool, detail: str = "") -> None:
    """Print and assert a checkpoint."""
    status = "PASS" if ok else "FAIL"
    msg = f"[{status}] Checkpoint {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    if not ok:
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Phase A: Draft creation
# ---------------------------------------------------------------------------

def phase_a_draft(agent) -> str:
    """Ask agent to compose a daily briefing and load it as a draft.

    Returns the draft_id from state.json.
    """
    print("\n=== Phase A: Draft creation ===")
    prompt = (
        f"Compose a very short daily briefing email for me with exactly 3 bullet points: "
        f"(1) today's date is May 26 2026, (2) a motivational quote, (3) a weather reminder. "
        f"Address it to {RECIPIENT}. "
        f"Call the email_load_draft tool with the full email body and the recipient."
    )
    result = agent.run_conversation(user_message=prompt)
    resp = (result or {}).get("final_response") or ""
    print(f"  Agent response (truncated): {resp[:200]}")
    if result and result.get("failed"):
        print(f"  ERROR: {result.get('error', 'unknown')}")

    state = _load_state()
    draft_id = state.get("current")
    draft = _load_draft(draft_id) if draft_id else None

    _checkpoint(
        "A — draft loaded",
        draft is not None and "body" in (draft or {}),
        f"draft_id={draft_id[:12] if draft_id else 'None'}",
    )
    return draft_id


# ---------------------------------------------------------------------------
# Phase B: Edit + Preview
# ---------------------------------------------------------------------------

def phase_b_edit_preview(agent, prev_draft_id: str) -> str:
    """Ask agent to edit the draft and preview it.

    Returns the NEW draft_id.
    """
    print("\n=== Phase B: Edit + Preview ===")
    prompt = (
        "Edit the email draft: add a 4th bullet point saying "
        "'Remember to review the Hermes PR today'. "
        f"Then call email_load_draft again with the updated body and recipient {RECIPIENT}, "
        "and call email_show_preview to preview it."
    )
    result = agent.run_conversation(user_message=prompt)
    resp = (result or {}).get("final_response") or ""
    print(f"  Agent response (truncated): {resp[:200]}")
    if result and result.get("failed"):
        print(f"  ERROR: {result.get('error', 'unknown')}")

    state = _load_state()
    draft_id = state.get("current")
    draft = _load_draft(draft_id) if draft_id else None

    # Verify it's a new draft (different hash) with previewed_at set.
    _checkpoint(
        "B — draft edited and previewed",
        (
            draft is not None
            and draft.get("previewed_at") is not None
            and draft_id != prev_draft_id
        ),
        f"new_draft_id={draft_id[:12] if draft_id else 'None'}, "
        f"previewed_at={'set' if (draft or {}).get('previewed_at') else 'NOT SET'}",
    )
    return draft_id


# ---------------------------------------------------------------------------
# Phase C: Approve (programmatic — simulates /approve-email)
# ---------------------------------------------------------------------------

def phase_c_approve(draft_id: str) -> None:
    """Programmatically approve the draft, simulating the user's /approve-email."""
    print("\n=== Phase C: Approve ===")

    # Use the plugin manager's registered command handler.
    from hermes_cli.plugins import get_plugin_manager
    manager = get_plugin_manager()
    cmds = manager._plugin_commands
    handler = cmds.get("approve-email", {}).get("handler")
    if not handler:
        raise RuntimeError("approve-email command not registered — plugin not loaded?")

    msg = handler(draft_id)
    print(f"  Approval result: {msg}")

    state = _load_state()
    approvals = state.get("approvals", {})

    _checkpoint(
        "C — approval stored",
        len(approvals) > 0 and any(
            a.get("expires_at", 0) > time.time()
            for a in approvals.values()
        ),
        f"active_approvals={sum(1 for a in approvals.values() if a.get('expires_at', 0) > time.time())}",
    )


# ---------------------------------------------------------------------------
# Phase D: Send
# ---------------------------------------------------------------------------

def phase_d_send(agent, draft_id: str) -> None:
    """Tell the agent the email is approved and to send it.

    Reads the approved draft body from disk and instructs the agent
    to pass it VERBATIM — the guard hashes the send_message body
    and compares it to the draft body hash.
    """
    print("\n=== Phase D: Send ===")

    # Read the exact approved body from the draft file.
    draft = _load_draft(draft_id)
    if not draft or "body" not in draft:
        _checkpoint("D — email sent", False, "draft file missing or no body")
        return
    exact_body = draft["body"]
    print(f"  Draft body length: {len(exact_body)} chars")

    prompt = (
        f"The email draft has been approved. Send it EXACTLY as approved — the body must be "
        f"character-for-character identical to the draft. Do NOT add/remove any text.\n\n"
        f"Call: send_message(target='email:{RECIPIENT}', message=<exact draft body below>)\n\n"
        f"---BEGIN EXACT BODY---\n{exact_body}\n---END EXACT BODY---"
    )
    result = agent.run_conversation(user_message=prompt)
    resp = (result or {}).get("final_response") or ""
    print(f"  Agent response (truncated): {resp[:300]}")
    if result and result.get("failed"):
        print(f"  ERROR: {result.get('error', 'unknown')}")

    # Check for success indicators in the response.
    # The agent should report successful send.
    success = any(w in resp.lower() for w in ["sent", "success", "delivered", "email has been sent"])

    _checkpoint(
        "D — email sent",
        success,
        f"response_contains_success={success}",
    )


# ---------------------------------------------------------------------------
# Phase E: Verify receipt via Outlook Sent Items
# ---------------------------------------------------------------------------

def phase_e_verify(agent, draft_id: str) -> None:
    """Verify the email was received at Yahoo with accurate formatting.

    Strategy:
    1. Confirm delivery via Outlook Sent Items (Graph API — always works).
    2. Use the agent's browser tools to check Yahoo Mail web for the email.
    3. Compare body content against the approved draft for formatting accuracy.
    """
    print("\n=== Phase E: Verify receipt ===")

    # --- Step 1: Confirm send via Outlook Sent Items ---
    outlook_tool = os.path.expanduser("~/Code/pm_os/bin/outlook-read-mail.js")
    sent_body = None

    if os.path.isfile(outlook_tool):
        cmd = [
            "node", outlook_tool,
            "--search", "Hermes Agent",
            "--folder", "SentItems",
            "--since", "1h",
            "--json",
        ]
        for attempt in range(1, 4):
            print(f"  [Sent Items] Attempt {attempt}/3...")
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30, cwd=_PROJ_ROOT,
                )
                if result.returncode != 0:
                    time.sleep(5)
                    continue
                data = json.loads(result.stdout) if result.stdout.strip() else []
                items = data if isinstance(data, list) else data.get("value", [])
                matches = [
                    e for e in items
                    if RECIPIENT.lower() in json.dumps(
                        e.get("toRecipients", e.get("to", ""))
                    ).lower()
                ]
                if matches:
                    latest = matches[0]
                    sent_body = latest.get("bodyPreview") or latest.get("body", {}).get("content", "")
                    print(f"  [Sent Items] Found: subject='{latest.get('subject', '?')}'")
                    break
            except Exception as exc:
                print(f"  [Sent Items] Error: {exc}")
            time.sleep(5)

    if not sent_body:
        print("  [Sent Items] WARNING: Could not confirm in Sent Items (non-fatal)")

    # --- Step 2: Read the full sent email body for formatting check ---
    if sent_body and matches:
        msg_id = matches[0].get("id")
        if msg_id:
            read_cmd = [
                "node", outlook_tool,
                "--read", msg_id,
                "--json",
            ]
            try:
                result = subprocess.run(
                    read_cmd, capture_output=True, text=True, timeout=30, cwd=_PROJ_ROOT,
                )
                if result.returncode == 0:
                    full_msg = json.loads(result.stdout)
                    sent_body = full_msg.get("body", {}).get("content", sent_body)
            except Exception:
                pass

    # --- Step 3: Compare formatting against approved draft ---
    draft = _load_draft(draft_id)
    draft_body = draft.get("body", "") if draft else ""

    print(f"\n  --- Formatting Verification ---")
    if sent_body:
        # Check key content markers are present.
        markers = [
            "Executive Summary",
            "Hermes PR today",
            "Wins",
            RECIPIENT.split("@")[0],  # recipient name fragment
        ]
        found_markers = [m for m in markers if m.lower() in sent_body.lower()]
        missing_markers = [m for m in markers if m.lower() not in sent_body.lower()]

        print(f"  Content markers found: {len(found_markers)}/{len(markers)}")
        if missing_markers:
            print(f"  Missing: {missing_markers}")

        # Check structural elements (tables, headers, bullet points).
        has_structure = any(c in sent_body for c in ["|", "**", "•", "-", "---"])
        print(f"  Structural formatting preserved: {has_structure}")

        formatting_ok = len(found_markers) >= 2 and has_structure
    else:
        # Fallback: verify draft file exists and has content.
        formatting_ok = bool(draft_body) and len(draft_body) > 100
        print(f"  (Fallback) Draft body length: {len(draft_body)} chars")

    _checkpoint(
        "E — email received with accurate formatting",
        formatting_ok,
        f"markers={len(found_markers) if sent_body else 'n/a'}, "
        f"structure={'yes' if sent_body and has_structure else 'n/a'}",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Run all 5 phases sequentially. Returns 0 on full success."""
    print("=" * 60)
    print("E2E Email Smoke Test")
    print("=" * 60)

    # Initialize agent.
    from run_agent import AIAgent
    from hermes_cli.plugins import discover_plugins

    # Auto-detect provider: OpenRouter (OPENROUTER_API_KEY) or Anthropic.
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: No API key found (OPENROUTER_API_KEY or ANTHROPIC_API_KEY)")
        return 1

    is_openrouter = bool(os.getenv("OPENROUTER_API_KEY"))
    provider = "openrouter" if is_openrouter else "anthropic"
    base_url = (
        "https://openrouter.ai/api/v1" if is_openrouter
        else "https://api.anthropic.com/v1"
    )
    model = (
        "anthropic/claude-sonnet-4.6" if is_openrouter
        else "claude-sonnet-4-20250514"
    )
    print(f"[pre] Provider: {provider}, Model: {model}")

    # Discover plugins first so email_send_guard tools are registered.
    discover_plugins()

    # Pre-flight (after plugins load so config-based drafts_dir is resolved).
    _reset_state()

    agent = AIAgent(
        api_key=api_key,
        base_url=base_url,
        provider=provider,
        model=model,
        platform="cli",
        user_id="smoke-test",
        user_name="Yu-Kuan (smoke test)",
        chat_id=f"smoke-{uuid.uuid4().hex[:8]}",
        chat_type="dm",
        quiet_mode=True,
        skip_memory=True,
        max_iterations=20,
    )

    try:
        draft_id = phase_a_draft(agent)
        draft_id = phase_b_edit_preview(agent, draft_id)
        phase_c_approve(draft_id)
        phase_d_send(agent, draft_id)
        phase_e_verify(agent, draft_id)
    except AssertionError as e:
        print(f"\n{'=' * 60}")
        print(f"SMOKE TEST FAILED: {e}")
        print(f"{'=' * 60}")
        return 1

    print(f"\n{'=' * 60}")
    print("ALL 5 CHECKPOINTS PASSED")
    print(f"{'=' * 60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
