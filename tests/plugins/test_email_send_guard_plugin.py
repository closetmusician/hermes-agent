"""Tests for the email-send-guard plugin.

Covers the plugin at ``plugins/email-send-guard/``:

  * ``register()`` wires pre_tool_call hook, two tools, and one slash command.
  * ``pre_tool_call`` hook blocks email sends through the LOADED -> PREVIEWED
    -> APPROVED state machine and allows when all gates pass.
  * Non-email tool calls pass through unblocked.
  * ``email_load_draft`` tool stores body and computes SHA256 hash.
  * ``email_show_preview`` tool marks draft as previewed.
  * ``/approve-email`` slash command sets time-bounded approval.
  * Expired approvals are rejected.
  * Body-hash changes invalidate prior approvals.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Test infrastructure -- lightweight PluginContext mock
# ---------------------------------------------------------------------------

class FakePluginContext:
    """Captures registrations made by the plugin's register() function.

    This is test infrastructure (not production mocking) -- it replaces the
    real PluginContext facade so we can inspect what the plugin registered
    without importing the full Hermes plugin system.
    """

    def __init__(self):
        self.hooks: Dict[str, List[Callable]] = {}
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.commands: Dict[str, Dict[str, Any]] = {}

    def register_hook(self, hook_name: str, callback: Callable) -> None:
        self.hooks.setdefault(hook_name, []).append(callback)

    def register_tool(
        self,
        name: str,
        toolset: str,
        schema: dict,
        handler: Callable,
        **kwargs,
    ) -> None:
        self.tools[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            **kwargs,
        }

    def register_command(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        args_hint: str = "",
    ) -> None:
        self.commands[name] = {
            "handler": handler,
            "description": description,
            "args_hint": args_hint,
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Point HERMES_HOME to a temp dir so state files are isolated."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return hermes_home


def _load_plugin():
    """Import the plugin's __init__.py directly from the repo."""
    repo_root = Path(__file__).resolve().parents[2]
    plugin_dir = repo_root / "plugins" / "email-send-guard"
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.email_send_guard",
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def plugin():
    """Load and return the plugin module, freshly imported each test."""
    return _load_plugin()


@pytest.fixture
def ctx(plugin):
    """Register the plugin and return the FakePluginContext."""
    fake = FakePluginContext()
    plugin.register(fake)
    return fake


@pytest.fixture
def pre_hook(ctx):
    """Return the pre_tool_call callback registered by the plugin."""
    callbacks = ctx.hooks.get("pre_tool_call", [])
    assert len(callbacks) == 1, "Expected exactly one pre_tool_call hook"
    return callbacks[0]


@pytest.fixture
def load_draft(ctx):
    """Return the email_load_draft tool handler."""
    assert "email_load_draft" in ctx.tools
    return ctx.tools["email_load_draft"]["handler"]


@pytest.fixture
def show_preview(ctx):
    """Return the email_show_preview tool handler."""
    assert "email_show_preview" in ctx.tools
    return ctx.tools["email_show_preview"]["handler"]


@pytest.fixture
def approve_cmd(ctx):
    """Return the /approve-email command handler."""
    assert "approve-email" in ctx.commands
    return ctx.commands["approve-email"]["handler"]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _body_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 1. register() wires hooks, tools, and commands correctly
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_registers_pre_tool_call_hook(self, ctx):
        assert "pre_tool_call" in ctx.hooks
        assert len(ctx.hooks["pre_tool_call"]) == 1

    def test_registers_email_load_draft_tool(self, ctx):
        assert "email_load_draft" in ctx.tools
        tool = ctx.tools["email_load_draft"]
        assert tool["toolset"] == "email_send_guard"
        assert "body" in json.dumps(tool["schema"])

    def test_registers_email_show_preview_tool(self, ctx):
        assert "email_show_preview" in ctx.tools
        tool = ctx.tools["email_show_preview"]
        assert tool["toolset"] == "email_send_guard"
        assert "draft_id" in json.dumps(tool["schema"])

    def test_registers_approve_email_command(self, ctx):
        assert "approve-email" in ctx.commands


# ---------------------------------------------------------------------------
# 2. Non-email tool calls pass through
# ---------------------------------------------------------------------------

class TestNonEmailPassthrough:
    def test_non_send_message_tool_passes(self, pre_hook):
        result = pre_hook(tool_name="terminal", args={"command": "ls"})
        assert result is None

    def test_send_message_non_email_target_passes(self, pre_hook):
        result = pre_hook(
            tool_name="send_message",
            args={"target": "telegram:12345", "message": "hello"},
        )
        assert result is None


# ---------------------------------------------------------------------------
# 3. Email send without loaded draft -> blocked
# ---------------------------------------------------------------------------

class TestGateLoaded:
    def test_blocks_when_no_draft_loaded(self, pre_hook):
        result = pre_hook(
            tool_name="send_message",
            args={"target": "email:user@example.com", "message": "Hello"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "email_load_draft" in result["message"]


# ---------------------------------------------------------------------------
# 4. Email send with loaded but not previewed draft -> blocked
# ---------------------------------------------------------------------------

class TestGatePreviewed:
    def test_blocks_when_not_previewed(self, pre_hook, load_draft):
        body = "Hello world"
        load_draft(body=body)
        result = pre_hook(
            tool_name="send_message",
            args={"target": "email:me@test.com", "message": body},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "email_show_preview" in result["message"]


# ---------------------------------------------------------------------------
# 5. Email send with previewed but not approved draft -> blocked
# ---------------------------------------------------------------------------

class TestGateApproved:
    def test_blocks_when_not_approved(self, pre_hook, load_draft, show_preview):
        body = "Draft email body"
        result_load = load_draft(body=body)
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)
        result = pre_hook(
            tool_name="send_message",
            args={"target": "email:recipient@co.com", "message": body},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "approve-email" in result["message"].lower() or "approval" in result["message"].lower()


# ---------------------------------------------------------------------------
# 6. Email send with valid approval -> allowed
# ---------------------------------------------------------------------------

class TestAllGatesPass:
    def test_allows_when_all_gates_pass(self, pre_hook, load_draft, show_preview, approve_cmd):
        body = "Fully approved draft"
        load_draft(body=body)
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)
        approve_cmd(draft_id)
        result = pre_hook(
            tool_name="send_message",
            args={"target": "email:ok@example.com", "message": body},
        )
        assert result is None


# ---------------------------------------------------------------------------
# 7. Expired approval -> blocked
# ---------------------------------------------------------------------------

class TestExpiredApproval:
    def test_blocks_when_approval_expired(self, plugin, pre_hook, load_draft, show_preview, approve_cmd):
        body = "Expiring draft"
        load_draft(body=body)
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)
        approve_cmd(draft_id)

        # Manually expire the approval by backdating expires_at
        state = plugin._load_state()
        state["approvals"][draft_id]["expires_at"] = time.time() - 1
        plugin._save_state(state)

        result = pre_hook(
            tool_name="send_message",
            args={"target": "email:late@example.com", "message": body},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "expir" in result["message"].lower() or "approval" in result["message"].lower()


# ---------------------------------------------------------------------------
# 8. email_load_draft stores draft and computes hash
# ---------------------------------------------------------------------------

class TestLoadDraft:
    def test_stores_draft_body_and_hash(self, plugin, load_draft):
        body = "Draft content to hash"
        result = load_draft(body=body)
        expected_hash = _body_hash(body)
        # Tool should return something containing the hash
        assert expected_hash in result

        # Verify state file
        state = plugin._load_state()
        assert expected_hash in state["drafts"]
        assert state["drafts"][expected_hash]["body"] == body
        assert state["drafts"][expected_hash]["loaded_at"] is not None
        assert state["current"] == expected_hash


# ---------------------------------------------------------------------------
# 9. email_show_preview sets previewed_at
# ---------------------------------------------------------------------------

class TestShowPreview:
    def test_sets_previewed_at(self, plugin, load_draft, show_preview):
        body = "Preview me"
        load_draft(body=body)
        draft_id = _body_hash(body)

        before = time.time()
        result = show_preview(draft_id=draft_id)
        after = time.time()

        state = plugin._load_state()
        previewed = state["drafts"][draft_id]["previewed_at"]
        assert previewed is not None
        assert before <= previewed <= after
        # Preview should contain the body text
        assert body in result


# ---------------------------------------------------------------------------
# 10. /approve-email sets approval with TTL
# ---------------------------------------------------------------------------

class TestApproveCommand:
    def test_sets_approval_with_ttl(self, plugin, load_draft, show_preview, approve_cmd):
        body = "Approve me"
        load_draft(body=body)
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)

        before = time.time()
        result = approve_cmd(draft_id)
        after = time.time()

        state = plugin._load_state()
        assert draft_id in state["approvals"]
        approval = state["approvals"][draft_id]
        assert before <= approval["approved_at"] <= after
        # TTL is 15 minutes
        assert approval["expires_at"] - approval["approved_at"] == pytest.approx(
            15 * 60, abs=1,
        )

    def test_approve_current_draft_when_no_id(self, plugin, load_draft, show_preview, approve_cmd):
        body = "Current draft"
        load_draft(body=body)
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)

        # Pass empty string (no draft_id) -- should approve current
        approve_cmd("")

        state = plugin._load_state()
        assert draft_id in state["approvals"]


# ---------------------------------------------------------------------------
# 11. Body hash changes invalidate prior approvals
# ---------------------------------------------------------------------------

class TestHashInvalidation:
    def test_different_body_not_approved(self, pre_hook, load_draft, show_preview, approve_cmd):
        # Approve draft A
        body_a = "Original body"
        load_draft(body=body_a)
        show_preview(draft_id=_body_hash(body_a))
        approve_cmd(_body_hash(body_a))

        # Try to send draft B (different body) -- should be blocked
        body_b = "Modified body"
        result = pre_hook(
            tool_name="send_message",
            args={"target": "email:x@y.com", "message": body_b},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_must_reload_for_changed_body(self, pre_hook, load_draft, show_preview, approve_cmd):
        # Full flow for body A
        body_a = "Body A"
        load_draft(body=body_a)
        show_preview(draft_id=_body_hash(body_a))
        approve_cmd(_body_hash(body_a))

        # Body A should pass
        assert pre_hook(
            tool_name="send_message",
            args={"target": "email:x@y.com", "message": body_a},
        ) is None

        # Body B needs its own full flow
        body_b = "Body B"
        load_draft(body=body_b)
        show_preview(draft_id=_body_hash(body_b))
        approve_cmd(_body_hash(body_b))

        assert pre_hook(
            tool_name="send_message",
            args={"target": "email:x@y.com", "message": body_b},
        ) is None
