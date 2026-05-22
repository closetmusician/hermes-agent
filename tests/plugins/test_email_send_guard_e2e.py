"""
ABOUTME: End-to-end integration tests for the email-send-guard plugin.
ABOUTME: Tests the full flow through real PluginManager / PluginContext
ABOUTME: infrastructure — no mocked plugin system. Validates the state machine
ABOUTME: (EMPTY -> LOADED -> PREVIEWED -> APPROVED -> ALLOWED), approval
ABOUTME: expiry, body-hash binding, multi-draft isolation, and state persistence.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from hermes_cli.plugins import (
    PluginContext,
    PluginManager,
    PluginManifest,
    get_pre_tool_call_block_message,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _body_hash(body: str) -> str:
    """Compute SHA256 hex digest — mirrors the plugin's hashing."""
    return hashlib.sha256(body.encode()).hexdigest()


def _load_plugin_module() -> Any:
    """Import the email-send-guard plugin module from the repo.

    Returns the raw module so we can call register() and inspect internals.
    Direct import avoids any cached sys.modules copy.
    """
    repo_root = Path(__file__).resolve().parents[2]
    plugin_dir = repo_root / "plugins" / "email-send-guard"
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins._e2e_email_send_guard",
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_manifest() -> PluginManifest:
    """Build a PluginManifest matching the email-send-guard plugin.yaml."""
    return PluginManifest(
        name="email-send-guard",
        version="1.0.0",
        description="Deterministic email send guard",
        author="NousResearch",
        provides_hooks=["pre_tool_call"],
        provides_tools=["email_load_draft", "email_show_preview"],
        source="bundled",
        kind="standalone",
        key="email-send-guard",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_hermes_home(tmp_path, monkeypatch):
    """Redirect HERMES_HOME to tmp so state files don't touch real home.

    Also resets the global PluginManager singleton after each test to
    prevent cross-test hook leakage.
    """
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    yield hermes_home

    # Teardown: reset global singleton so hooks don't leak across tests
    import hermes_cli.plugins as _pm_mod
    _pm_mod._plugin_manager = None


@pytest.fixture
def manager():
    """Return a fresh PluginManager (not the global singleton)."""
    return PluginManager()


@pytest.fixture
def plugin_module():
    """Return a freshly imported email-send-guard module."""
    return _load_plugin_module()


@pytest.fixture
def registered(manager, plugin_module):
    """Register the email-send-guard plugin into a real PluginManager.

    Returns (manager, ctx) where ctx is the real PluginContext used
    during registration.
    """
    manifest = _make_manifest()
    ctx = PluginContext(manifest, manager)
    plugin_module.register(ctx)
    return manager, ctx


# ---------------------------------------------------------------------------
# 1. Cold send attempt -- blocked with "load draft first"
# ---------------------------------------------------------------------------

class TestColdSendBlocked:
    """Sending email with no prior draft load must be blocked."""

    def test_cold_send_blocked_via_invoke_hook(self, registered):
        manager, _ = registered
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "email:test@example.com", "message": "Hello"},
        )
        assert len(results) == 1
        result = results[0]
        assert result["action"] == "block"
        assert "email_load_draft" in result["message"]


# ---------------------------------------------------------------------------
# 2. Load draft, then send without preview -- blocked
# ---------------------------------------------------------------------------

class TestLoadWithoutPreviewBlocked:
    """Loading a draft then sending without previewing must be blocked."""

    def test_load_then_send_blocked(self, registered, plugin_module):
        manager, ctx = registered
        body = "Draft without preview"

        # Call the real tool handler registered by the plugin
        handler = None
        from tools.registry import registry
        entry = registry.get_entry("email_load_draft")
        assert entry is not None, "email_load_draft should be in the tool registry"
        result_json = entry.handler(body=body)
        result = json.loads(result_json)
        assert result["status"] == "draft_loaded"

        # Now try to send -- should be blocked (not previewed)
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "email:test@example.com", "message": body},
        )
        assert len(results) == 1
        assert results[0]["action"] == "block"
        assert "preview" in results[0]["message"].lower()


# ---------------------------------------------------------------------------
# 3. Load + preview, then send without approval -- blocked
# ---------------------------------------------------------------------------

class TestLoadPreviewNoApprovalBlocked:
    """After load and preview, sending without /approve-email is blocked."""

    def test_load_preview_no_approval_blocked(self, registered, plugin_module):
        manager, ctx = registered
        body = "Draft needing approval"
        draft_id = _body_hash(body)

        from tools.registry import registry
        load_entry = registry.get_entry("email_load_draft")
        preview_entry = registry.get_entry("email_show_preview")

        load_entry.handler(body=body)
        preview_result = json.loads(preview_entry.handler(draft_id=draft_id))
        assert preview_result["status"] == "draft_previewed"

        # Send attempt -- should need approval
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "email:user@example.com", "message": body},
        )
        assert len(results) == 1
        assert results[0]["action"] == "block"
        assert "approv" in results[0]["message"].lower()


# ---------------------------------------------------------------------------
# 4. Full happy path: load -> preview -> approve -> send ALLOWED
# ---------------------------------------------------------------------------

class TestHappyPath:
    """Complete flow: load, preview, approve, send -- no block."""

    def test_full_happy_path_returns_none(self, registered, plugin_module):
        manager, ctx = registered
        body = "Fully approved email body"
        draft_id = _body_hash(body)

        from tools.registry import registry
        load_entry = registry.get_entry("email_load_draft")
        preview_entry = registry.get_entry("email_show_preview")

        # Step 1: load
        load_result = json.loads(load_entry.handler(body=body))
        assert load_result["status"] == "draft_loaded"
        assert load_result["draft_id"] == draft_id

        # Step 2: preview
        preview_result = json.loads(preview_entry.handler(draft_id=draft_id))
        assert preview_result["status"] == "draft_previewed"

        # Step 3: approve via slash command
        approve_handler = manager._plugin_commands.get("approve-email", {}).get("handler")
        assert approve_handler is not None, "/approve-email must be registered"
        approval_msg = approve_handler(draft_id)
        assert "approved" in approval_msg.lower()

        # Step 4: send -- should pass (hook returns None => empty list)
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "email:happy@example.com", "message": body},
        )
        # All callbacks returned None => no results
        assert results == []


# ---------------------------------------------------------------------------
# 5. Approval expiry -- backdated approval is blocked
# ---------------------------------------------------------------------------

class TestApprovalExpiry:
    """An expired approval must block the send."""

    def test_expired_approval_blocks(self, registered, plugin_module):
        manager, ctx = registered
        body = "Expiring email"
        draft_id = _body_hash(body)

        from tools.registry import registry
        registry.get_entry("email_load_draft").handler(body=body)
        registry.get_entry("email_show_preview").handler(draft_id=draft_id)

        approve_handler = manager._plugin_commands["approve-email"]["handler"]
        approve_handler(draft_id)

        # Backdate the approval to make it expired
        state = plugin_module._load_state()
        state["approvals"][draft_id]["expires_at"] = time.time() - 1
        plugin_module._save_state(state)

        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "email:expired@example.com", "message": body},
        )
        assert len(results) == 1
        assert results[0]["action"] == "block"
        assert "expir" in results[0]["message"].lower()


# ---------------------------------------------------------------------------
# 6. Body modification after approval -- different hash blocked
# ---------------------------------------------------------------------------

class TestBodyModificationBlocked:
    """Approving hash A then sending hash B must be blocked."""

    def test_modified_body_blocked(self, registered, plugin_module):
        manager, ctx = registered
        original_body = "Original approved body"
        modified_body = "Modified body after approval"
        draft_id_a = _body_hash(original_body)

        from tools.registry import registry
        load = registry.get_entry("email_load_draft").handler
        preview = registry.get_entry("email_show_preview").handler

        load(body=original_body)
        preview(draft_id=draft_id_a)

        approve_handler = manager._plugin_commands["approve-email"]["handler"]
        approve_handler(draft_id_a)

        # Try to send with DIFFERENT body -- should block
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "email:modified@example.com", "message": modified_body},
        )
        assert len(results) == 1
        assert results[0]["action"] == "block"


# ---------------------------------------------------------------------------
# 7. Multiple drafts -- independent state machines
# ---------------------------------------------------------------------------

class TestMultipleDrafts:
    """Two drafts maintain independent state: approving B doesn't approve A."""

    def test_draft_a_blocked_draft_b_allowed(self, registered, plugin_module):
        manager, ctx = registered
        body_a = "Draft A body"
        body_b = "Draft B body"
        hash_a = _body_hash(body_a)
        hash_b = _body_hash(body_b)

        from tools.registry import registry
        load = registry.get_entry("email_load_draft").handler
        preview = registry.get_entry("email_show_preview").handler
        approve = manager._plugin_commands["approve-email"]["handler"]

        # Load and preview both drafts
        load(body=body_a)
        load(body=body_b)
        preview(draft_id=hash_a)
        preview(draft_id=hash_b)

        # Only approve B
        approve(hash_b)

        # A should be blocked (not approved)
        results_a = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "email:a@example.com", "message": body_a},
        )
        assert len(results_a) == 1
        assert results_a[0]["action"] == "block"
        assert "approv" in results_a[0]["message"].lower()

        # B should be allowed
        results_b = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "email:b@example.com", "message": body_b},
        )
        assert results_b == []


# ---------------------------------------------------------------------------
# 8. Non-email send_message passes through
# ---------------------------------------------------------------------------

class TestNonEmailPassthrough:
    """send_message with a non-email target must pass through unblocked."""

    def test_telegram_target_passes(self, registered):
        manager, _ = registered
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "telegram:12345", "message": "Hi there"},
        )
        assert results == []

    def test_discord_target_passes(self, registered):
        manager, _ = registered
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "discord:guild:channel", "message": "Hello"},
        )
        assert results == []


# ---------------------------------------------------------------------------
# 9. Non-send_message tools pass through
# ---------------------------------------------------------------------------

class TestNonSendMessagePassthrough:
    """Non-send_message tool calls must not be intercepted."""

    def test_bash_tool_passes(self, registered):
        manager, _ = registered
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="bash",
            args={"command": "ls"},
        )
        assert results == []

    def test_terminal_tool_passes(self, registered):
        manager, _ = registered
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="terminal",
            args={"command": "echo hello"},
        )
        assert results == []

    def test_write_file_tool_passes(self, registered):
        manager, _ = registered
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="write_file",
            args={"path": "/tmp/test.txt", "content": "hi"},
        )
        assert results == []


# ---------------------------------------------------------------------------
# 10. State persistence across plugin instances
# ---------------------------------------------------------------------------

class TestStatePersistence:
    """State saved by one plugin instance is readable by a new one.

    Simulates agent restart: register plugin, approve a draft, then create
    a new PluginManager + re-register and verify approval survives.
    """

    def test_state_survives_re_registration(self, plugin_module):
        body = "Persistent draft"
        draft_id = _body_hash(body)

        # First instance: full approval flow
        mgr1 = PluginManager()
        manifest = _make_manifest()
        ctx1 = PluginContext(manifest, mgr1)
        plugin_module.register(ctx1)

        from tools.registry import registry
        registry.get_entry("email_load_draft").handler(body=body)
        registry.get_entry("email_show_preview").handler(draft_id=draft_id)
        mgr1._plugin_commands["approve-email"]["handler"](draft_id)

        # Verify first instance allows
        results1 = mgr1.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "email:persist@example.com", "message": body},
        )
        assert results1 == []

        # Second instance: new manager, re-register plugin
        mgr2 = PluginManager()
        ctx2 = PluginContext(manifest, mgr2)
        plugin_module.register(ctx2)

        # The second instance should read state from disk and allow
        results2 = mgr2.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "email:persist@example.com", "message": body},
        )
        assert results2 == []


# ---------------------------------------------------------------------------
# 11. /approve-email command handler via PluginManager
# ---------------------------------------------------------------------------

class TestApproveEmailCommand:
    """Test the /approve-email command through the plugin command registry."""

    def test_approve_no_draft_returns_message(self, registered):
        manager, _ = registered
        handler = manager._plugin_commands["approve-email"]["handler"]
        result = handler("")
        assert "no draft" in result.lower() or "load" in result.lower()

    def test_approve_not_previewed_returns_message(self, registered, plugin_module):
        manager, _ = registered
        body = "Unapproved draft"
        draft_id = _body_hash(body)

        from tools.registry import registry
        registry.get_entry("email_load_draft").handler(body=body)

        handler = manager._plugin_commands["approve-email"]["handler"]
        result = handler(draft_id)
        assert "preview" in result.lower()

    def test_approve_with_explicit_draft_id(self, registered, plugin_module):
        manager, _ = registered
        body = "Explicit ID draft"
        draft_id = _body_hash(body)

        from tools.registry import registry
        registry.get_entry("email_load_draft").handler(body=body)
        registry.get_entry("email_show_preview").handler(draft_id=draft_id)

        handler = manager._plugin_commands["approve-email"]["handler"]
        result = handler(draft_id)
        assert "approved" in result.lower()

    def test_approve_current_when_no_id_given(self, registered, plugin_module):
        manager, _ = registered
        body = "Current draft auto-approve"
        draft_id = _body_hash(body)

        from tools.registry import registry
        registry.get_entry("email_load_draft").handler(body=body)
        registry.get_entry("email_show_preview").handler(draft_id=draft_id)

        handler = manager._plugin_commands["approve-email"]["handler"]
        result = handler("")
        assert "approved" in result.lower()

        # Verify it actually works for sending
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "email:auto@example.com", "message": body},
        )
        assert results == []


# ---------------------------------------------------------------------------
# 12. get_pre_tool_call_block_message — full pipeline via global singleton
# ---------------------------------------------------------------------------

class TestGlobalSingletonPipeline:
    """Test through the actual get_pre_tool_call_block_message() function.

    This patches the global _plugin_manager singleton to wire in the
    email-send-guard hook, simulating what happens in production when
    the agent executor calls get_pre_tool_call_block_message() before
    dispatching a tool.
    """

    def test_block_message_via_global_singleton(self, plugin_module, monkeypatch):
        """Cold send through the singleton pipeline returns a block message."""
        import hermes_cli.plugins as pm_mod

        mgr = PluginManager()
        manifest = _make_manifest()
        ctx = PluginContext(manifest, mgr)
        plugin_module.register(ctx)

        # Inject our manager as the global singleton
        monkeypatch.setattr(pm_mod, "_plugin_manager", mgr)

        msg = get_pre_tool_call_block_message(
            tool_name="send_message",
            args={"target": "email:test@example.com", "message": "Blocked!"},
        )
        assert msg is not None
        assert "email_load_draft" in msg

    def test_allowed_via_global_singleton(self, plugin_module, monkeypatch):
        """Full happy path through the singleton pipeline returns None."""
        import hermes_cli.plugins as pm_mod

        mgr = PluginManager()
        manifest = _make_manifest()
        ctx = PluginContext(manifest, mgr)
        plugin_module.register(ctx)
        monkeypatch.setattr(pm_mod, "_plugin_manager", mgr)

        body = "Singleton happy path"
        draft_id = _body_hash(body)

        from tools.registry import registry
        registry.get_entry("email_load_draft").handler(body=body)
        registry.get_entry("email_show_preview").handler(draft_id=draft_id)
        mgr._plugin_commands["approve-email"]["handler"](draft_id)

        msg = get_pre_tool_call_block_message(
            tool_name="send_message",
            args={"target": "email:ok@example.com", "message": body},
        )
        assert msg is None

    def test_non_email_passes_via_global_singleton(self, plugin_module, monkeypatch):
        """Non-email tool calls pass through the singleton pipeline."""
        import hermes_cli.plugins as pm_mod

        mgr = PluginManager()
        manifest = _make_manifest()
        ctx = PluginContext(manifest, mgr)
        plugin_module.register(ctx)
        monkeypatch.setattr(pm_mod, "_plugin_manager", mgr)

        msg = get_pre_tool_call_block_message(
            tool_name="terminal",
            args={"command": "whoami"},
        )
        assert msg is None


# ---------------------------------------------------------------------------
# 13. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary conditions and defensive behavior."""

    def test_send_message_with_no_args_passes(self, registered):
        """send_message without args dict should not crash."""
        manager, _ = registered
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args=None,
        )
        # The hook checks `isinstance(args, dict)` and returns None if not
        assert results == []

    def test_send_message_with_empty_target_passes(self, registered):
        """send_message with empty target should not be treated as email."""
        manager, _ = registered
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "", "message": "Hello"},
        )
        assert results == []

    def test_email_load_draft_empty_body_returns_error(self, registered):
        """email_load_draft with empty body should return an error."""
        from tools.registry import registry
        result = json.loads(registry.get_entry("email_load_draft").handler(body=""))
        assert "error" in result

    def test_email_show_preview_invalid_id_returns_error(self, registered):
        """email_show_preview with non-existent draft_id returns an error."""
        from tools.registry import registry
        result = json.loads(
            registry.get_entry("email_show_preview").handler(draft_id="nonexistent")
        )
        assert "error" in result

    def test_corrupt_state_file_resets(self, registered, plugin_module, _isolate_hermes_home):
        """Corrupt state.json should be handled gracefully."""
        state_dir = _isolate_hermes_home / "email-send-guard"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "state.json").write_text("NOT VALID JSON", encoding="utf-8")

        manager, _ = registered
        # Should not crash, should treat as empty state (block)
        results = manager.invoke_hook(
            "pre_tool_call",
            tool_name="send_message",
            args={"target": "email:corrupt@example.com", "message": "test"},
        )
        assert len(results) == 1
        assert results[0]["action"] == "block"

    def test_email_colon_prefix_variants(self, registered, plugin_module):
        """Various email target formats should all be caught."""
        manager, _ = registered

        for target in ["email:user@test.com", "email:x@y.z", "email:a"]:
            results = manager.invoke_hook(
                "pre_tool_call",
                tool_name="send_message",
                args={"target": target, "message": "body"},
            )
            assert len(results) == 1, f"Expected block for target={target}"
            assert results[0]["action"] == "block"

    def test_approve_nonexistent_draft_returns_error(self, registered):
        """Approving a draft that doesn't exist returns an error."""
        manager, _ = registered
        handler = manager._plugin_commands["approve-email"]["handler"]
        result = handler("nonexistent_hash_value")
        assert "no draft" in result.lower() or "not found" in result.lower()
