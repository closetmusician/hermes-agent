"""
ABOUTME: RED-first acceptance tests for the email-send-guard plugin (P0-2c).
ABOUTME: These tests must FAIL on the clean base (no plugin) and PASS after
ABOUTME: implementation. Load the plugin via spec_from_file_location because the
ABOUTME: directory name is hyphenated and is not a Python package.
ABOUTME: Covers REQ-01–03: dormant state machine preservation, recipient binding,
ABOUTME: TTL expiration, long body roundtrips, plugin dormant status in config.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pytest


# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_email_send_guard():
    """Load plugins/email-send-guard/__init__.py via spec_from_file_location.

    Purpose: The dir name is hyphenated so it is not a Python package;
             mirror the upstream PluginManager's load path.
    Usage:   Call once per test session (cached by fixture).
    Gotchas: Subsequent imports of this module use sys.modules key
             'email_send_guard_under_test'.
    """
    init_path = _repo_root() / "plugins" / "email-send-guard" / "__init__.py"
    spec = importlib.util.spec_from_file_location("email_send_guard_under_test", init_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["email_send_guard_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_hermes_home(tmp_path, monkeypatch):
    """Redirect HERMES_HOME to tmp_path so tests never touch ~/.hermes."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    yield hermes_home


@pytest.fixture()
def esg_mod():
    """Return the loaded email-send-guard module (per-test reload to isolate state)."""
    sys.modules.pop("email_send_guard_under_test", None)
    return _load_email_send_guard()


@pytest.fixture
def temp_hermes_home(tmp_path):
    """Fixture: set HERMES_HOME to a temp directory for test isolation."""
    old_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(tmp_path)
    yield tmp_path
    if old_home is not None:
        os.environ["HERMES_HOME"] = old_home
    else:
        os.environ.pop("HERMES_HOME", None)


class TestPluginLoads:
    """Verify the plugin can be loaded via spec_from_file_location (REQ-01)."""

    def test_module_loads_without_error(self, esg_mod):
        """REQ-01: The plugin module imports successfully without crashing."""
        assert esg_mod is not None

    def test_register_function_exists(self, esg_mod):
        """REQ-01: The register function exists and is callable."""
        assert hasattr(esg_mod, "register")
        assert callable(esg_mod.register)


class TestStateMachineGates:
    """Test the three gates of the email send approval state machine."""

    def test_block_email_send_without_draft(self, esg_mod):
        """Gate 1: Block send_message(email) if no draft is loaded."""
        result = esg_mod._pre_tool_call(
            tool_name="send_message",
            args={
                "target": "email:alice@example.com",
                "message": "Hello, Alice!",
            },
        )
        assert result is not None
        assert result.get("action") == "block"
        assert "no draft loaded" in result.get("message", "").lower()

    def test_block_email_send_if_not_previewed(self, esg_mod):
        """Gate 2: Block send if draft loaded but not previewed."""
        body = "Test email body"
        h = esg_mod._body_hash(body)

        # Load a draft (but don't preview it)
        esg_mod._save_draft(h, {
            "body": body,
            "recipient": "alice@example.com",
            "loaded_at": time.time(),
            "previewed_at": None,
        })

        result = esg_mod._pre_tool_call(
            tool_name="send_message",
            args={
                "target": "email:alice@example.com",
                "message": body,
            },
        )
        assert result is not None
        assert result.get("action") == "block"
        assert "not been previewed" in result.get("message", "").lower()

    def test_block_email_send_if_not_approved(self, esg_mod):
        """Gate 3: Block send if draft previewed but not yet approved."""
        body = "Test email body"
        h = esg_mod._body_hash(body)

        # Load and preview a draft
        esg_mod._save_draft(h, {
            "body": body,
            "recipient": "alice@example.com",
            "loaded_at": time.time(),
            "previewed_at": time.time(),
        })

        result = esg_mod._pre_tool_call(
            tool_name="send_message",
            args={
                "target": "email:alice@example.com",
                "message": body,
            },
        )
        assert result is not None
        assert result.get("action") == "block"
        assert "not yet approved" in result.get("message", "").lower()

    def test_full_state_machine_allows_send(self, esg_mod):
        """All gates passed: draft loaded, previewed, and approved → send allowed."""
        body = "Test email body"
        h = esg_mod._body_hash(body)
        recipient = "alice@example.com"

        # Step 1: Load draft
        esg_mod._save_draft(h, {
            "body": body,
            "recipient": recipient,
            "loaded_at": time.time(),
            "previewed_at": time.time(),
        })

        # Step 2: Approve
        state = esg_mod._load_state()
        now = time.time()
        state["approvals"][h] = {
            "recipient": recipient,
            "approved_at": now,
            "expires_at": now + esg_mod.APPROVAL_TTL_SECONDS,
        }
        esg_mod._save_state(state)

        # Step 3: Attempt send — should be allowed
        result = esg_mod._pre_tool_call(
            tool_name="send_message",
            args={
                "target": f"email:{recipient}",
                "message": body,
            },
        )
        assert result is None, "Expected send to be allowed (None = allow)"


class TestRecipientBinding:
    """Test that approval is bound to a specific recipient."""

    def test_recipient_mismatch_blocks_send(self, esg_mod):
        """Block send if recipient differs from approved recipient."""
        body = "Test email"
        h = esg_mod._body_hash(body)
        approved_recipient = "alice@example.com"
        wrong_recipient = "bob@example.com"

        # Load, preview, and approve for alice@example.com
        esg_mod._save_draft(h, {
            "body": body,
            "recipient": approved_recipient,
            "loaded_at": time.time(),
            "previewed_at": time.time(),
        })

        state = esg_mod._load_state()
        now = time.time()
        state["approvals"][h] = {
            "recipient": approved_recipient,
            "approved_at": now,
            "expires_at": now + esg_mod.APPROVAL_TTL_SECONDS,
        }
        esg_mod._save_state(state)

        # Try to send to a different recipient — should be blocked
        result = esg_mod._pre_tool_call(
            tool_name="send_message",
            args={
                "target": f"email:{wrong_recipient}",
                "message": body,
            },
        )
        assert result is not None
        assert result.get("action") == "block"
        assert "recipient mismatch" in result.get("message", "").lower()

    def test_extract_recipient_formats(self, esg_mod):
        """Test _extract_recipient handles various email target formats."""
        assert esg_mod._extract_recipient("email:alice@example.com") == "alice@example.com"
        assert esg_mod._extract_recipient("email:Alice Name <alice@example.com>") == "alice@example.com"
        assert esg_mod._extract_recipient("email:") == ""
        assert esg_mod._extract_recipient("email") == ""
        # Lowercased
        assert esg_mod._extract_recipient("email:ALICE@EXAMPLE.COM") == "alice@example.com"


class TestApprovalExpiration:
    """Test approval TTL and expiration."""

    def test_approval_expires_after_ttl(self, esg_mod):
        """Block send if approval has expired (>15 min old)."""
        body = "Test email"
        h = esg_mod._body_hash(body)
        recipient = "alice@example.com"

        # Load and preview
        esg_mod._save_draft(h, {
            "body": body,
            "recipient": recipient,
            "loaded_at": time.time(),
            "previewed_at": time.time(),
        })

        # Approve with an expiration in the past
        state = esg_mod._load_state()
        state["approvals"][h] = {
            "recipient": recipient,
            "approved_at": time.time() - esg_mod.APPROVAL_TTL_SECONDS - 1,
            "expires_at": time.time() - 1,  # Expired!
        }
        esg_mod._save_state(state)

        # Try to send — should be blocked as expired
        result = esg_mod._pre_tool_call(
            tool_name="send_message",
            args={
                "target": f"email:{recipient}",
                "message": body,
            },
        )
        assert result is not None
        assert result.get("action") == "block"
        assert "expired" in result.get("message", "").lower()


class TestLongBodyRoundtrip:
    """Test that long email bodies are not truncated."""

    def test_long_body_roundtrip(self, esg_mod):
        """A >100KB body should load, preview, approve, and send without truncation."""
        # Create a body larger than 100KB
        body = "x" * (100 * 1024 + 1)  # 100KB + 1 byte
        h = esg_mod._body_hash(body)
        recipient = "alice@example.com"

        # Load draft
        esg_mod._save_draft(h, {
            "body": body,
            "recipient": recipient,
            "loaded_at": time.time(),
            "previewed_at": time.time(),
        })

        # Verify body round-trips
        draft = esg_mod._load_draft(h)
        assert draft is not None
        assert len(draft["body"]) == len(body), "Body was truncated!"
        assert draft["body"] == body

        # Approve and verify send is allowed
        state = esg_mod._load_state()
        now = time.time()
        state["approvals"][h] = {
            "recipient": recipient,
            "approved_at": now,
            "expires_at": now + esg_mod.APPROVAL_TTL_SECONDS,
        }
        esg_mod._save_state(state)

        result = esg_mod._pre_tool_call(
            tool_name="send_message",
            args={
                "target": f"email:{recipient}",
                "message": body,
            },
        )
        assert result is None, "Long body should be allowed"


class TestOneTimeApprovalConsumption:
    """Test that approval is consumed after successful send."""

    def test_approval_consumed_after_successful_send(self, esg_mod):
        """After successful send, approval should be consumed (one-time use)."""
        body = "Test email"
        h = esg_mod._body_hash(body)
        recipient = "alice@example.com"

        # Load, preview, approve
        esg_mod._save_draft(h, {
            "body": body,
            "recipient": recipient,
            "loaded_at": time.time(),
            "previewed_at": time.time(),
        })

        state = esg_mod._load_state()
        now = time.time()
        state["approvals"][h] = {
            "recipient": recipient,
            "approved_at": now,
            "expires_at": now + esg_mod.APPROVAL_TTL_SECONDS,
        }
        esg_mod._save_state(state)

        # Send (pretend success with empty result)
        esg_mod._post_tool_call(
            tool_name="send_message",
            args={
                "target": f"email:{recipient}",
                "message": body,
            },
            result="",  # Empty result = success
        )

        # Verify approval was consumed
        state = esg_mod._load_state()
        assert h not in state.get("approvals", {}), "Approval should be consumed"

    def test_approval_preserved_on_failed_send(self, esg_mod):
        """If send fails, approval should be preserved (not one-time until success)."""
        body = "Test email"
        h = esg_mod._body_hash(body)
        recipient = "alice@example.com"

        # Load, preview, approve
        esg_mod._save_draft(h, {
            "body": body,
            "recipient": recipient,
            "loaded_at": time.time(),
            "previewed_at": time.time(),
        })

        state = esg_mod._load_state()
        now = time.time()
        state["approvals"][h] = {
            "recipient": recipient,
            "approved_at": now,
            "expires_at": now + esg_mod.APPROVAL_TTL_SECONDS,
        }
        esg_mod._save_state(state)

        # Send fails (return an error JSON)
        esg_mod._post_tool_call(
            tool_name="send_message",
            args={
                "target": f"email:{recipient}",
                "message": body,
            },
            result=json.dumps({"error": "Network timeout"}),
        )

        # Verify approval is still there
        state = esg_mod._load_state()
        assert h in state.get("approvals", {}), "Approval should be preserved on failed send"


class TestToolRegistration:
    """Test that registered tools function correctly."""

    def test_email_load_draft_tool(self, esg_mod):
        """Test the email_load_draft tool."""
        result_json = esg_mod._email_load_draft(
            body="Test body",
            recipient="alice@example.com",
        )
        result = json.loads(result_json)
        assert result.get("status") == "draft_loaded"
        assert "draft_id" in result
        assert result.get("body_length") == len("Test body")

    def test_email_show_preview_tool(self, esg_mod):
        """Test the email_show_preview tool."""
        # Load a draft first
        load_result = json.loads(esg_mod._email_load_draft(
            body="Preview test",
            recipient="alice@example.com",
        ))
        draft_id = load_result["draft_id"]

        # Preview it
        preview_result = json.loads(esg_mod._email_show_preview(draft_id=draft_id))
        assert preview_result.get("status") == "draft_previewed"
        assert preview_result.get("preview") == "Preview test"

    def test_approve_email_command(self, esg_mod):
        """Test the /approve-email command."""
        # Load and preview a draft
        load_result = json.loads(esg_mod._email_load_draft(
            body="Approve test",
            recipient="alice@example.com",
        ))
        draft_id = load_result["draft_id"]

        json.loads(esg_mod._email_show_preview(draft_id=draft_id))

        # Approve it
        approve_result = esg_mod._handle_approve(raw_args=draft_id)
        assert isinstance(approve_result, dict)
        assert "approved_tool_call" in approve_result
        assert approve_result["approved_tool_call"]["name"] == "send_message"


class TestDormantStatusInConfig:
    """Test that the plugin is marked dormant (not enabled by default)."""

    def test_plugin_not_in_enabled_by_default(self):
        """REQ-03: Verify email-send-guard is NOT in the base config plugins.enabled."""
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly()
        enabled = cfg.get("plugins", {}).get("enabled", [])
        # The plugin should be ported but NOT enabled (Option A — dormant)
        assert "email-send-guard" not in enabled, \
            "email-send-guard should be dormant (not in plugins.enabled)"
