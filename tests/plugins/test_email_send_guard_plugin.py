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

    def test_no_draft_block_does_not_halt_turn(self, pre_hook):
        """No-draft block should NOT halt the turn (agent can fix by loading a draft)."""
        result = pre_hook(
            tool_name="send_message",
            args={"target": "email:user@example.com", "message": "Hello"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert result.get("halt_turn") is not True


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

    def test_not_previewed_block_does_not_halt_turn(self, pre_hook, load_draft):
        """Not-previewed block should NOT halt the turn (agent can fix by previewing)."""
        body = "Not previewed body"
        load_draft(body=body)
        result = pre_hook(
            tool_name="send_message",
            args={"target": "email:me@test.com", "message": body},
        )
        assert result is not None
        assert result["action"] == "block"
        assert result.get("halt_turn") is not True


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

    def test_not_approved_block_has_halt_turn(self, pre_hook, load_draft, show_preview):
        """Not-approved block should include halt_turn=True (requires user action)."""
        body = "Halt turn draft"
        load_draft(body=body)
        show_preview(draft_id=_body_hash(body))
        result = pre_hook(
            tool_name="send_message",
            args={"target": "email:halt@example.com", "message": body},
        )
        assert result is not None
        assert result["action"] == "block"
        assert result.get("halt_turn") is True


# ---------------------------------------------------------------------------
# 6. Email send with valid approval -> allowed
# ---------------------------------------------------------------------------

class TestAllGatesPass:
    def test_allows_when_all_gates_pass(self, pre_hook, load_draft, show_preview, approve_cmd):
        body = "Fully approved draft"
        load_draft(body=body, recipient="ok@example.com")
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
        recipient = "late@example.com"
        load_draft(body=body, recipient=recipient)
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)
        approve_cmd(draft_id)

        # Manually expire the approval by backdating expires_at.
        # Approval key is now _approval_hash(recipient, body), not draft_id.
        ah = plugin._approval_hash(recipient, body)
        state = plugin._load_state()
        state["approvals"][ah]["expires_at"] = time.time() - 1
        plugin._save_state(state)

        result = pre_hook(
            tool_name="send_message",
            args={"target": f"email:{recipient}", "message": body},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "expir" in result["message"].lower() or "approval" in result["message"].lower()

    def test_expired_approval_block_has_halt_turn(self, plugin, pre_hook, load_draft, show_preview, approve_cmd):
        """Expired approval block should include halt_turn=True (requires user action)."""
        body = "Expiring halt draft"
        recipient = "halt-expire@example.com"
        load_draft(body=body, recipient=recipient)
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)
        approve_cmd(draft_id)

        ah = plugin._approval_hash(recipient, body)
        state = plugin._load_state()
        state["approvals"][ah]["expires_at"] = time.time() - 1
        plugin._save_state(state)

        result = pre_hook(
            tool_name="send_message",
            args={"target": f"email:{recipient}", "message": body},
        )
        assert result is not None
        assert result["action"] == "block"
        assert result.get("halt_turn") is True


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

        # Verify draft stored as individual file
        draft = plugin._load_draft(expected_hash)
        assert draft is not None
        assert draft["body"] == body
        assert draft["loaded_at"] is not None

        # Verify state tracks current pointer
        state = plugin._load_state()
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

        draft = plugin._load_draft(draft_id)
        previewed = draft["previewed_at"]
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
        recipient = "test@example.com"
        load_draft(body=body, recipient=recipient)
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)

        before = time.time()
        result = approve_cmd(draft_id)
        after = time.time()

        # Approval is now keyed by hash(recipient, body), not draft_id
        ah = plugin._approval_hash(recipient, body)
        state = plugin._load_state()
        assert ah in state["approvals"]
        approval = state["approvals"][ah]
        assert before <= approval["approved_at"] <= after
        # TTL is 15 minutes
        assert approval["expires_at"] - approval["approved_at"] == pytest.approx(
            15 * 60, abs=1,
        )

    def test_approve_current_draft_when_no_id(self, plugin, load_draft, show_preview, approve_cmd):
        body = "Current draft"
        recipient = "current@example.com"
        load_draft(body=body, recipient=recipient)
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)

        # Pass empty string (no draft_id) -- should approve current
        approve_cmd("")

        ah = plugin._approval_hash(recipient, body)
        state = plugin._load_state()
        assert ah in state["approvals"]


# ---------------------------------------------------------------------------
# 11. Body hash changes invalidate prior approvals
# ---------------------------------------------------------------------------

class TestHashInvalidation:
    def test_different_body_not_approved(self, pre_hook, load_draft, show_preview, approve_cmd):
        # Approve draft A
        body_a = "Original body"
        load_draft(body=body_a, recipient="x@y.com")
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
        load_draft(body=body_a, recipient="x@y.com")
        show_preview(draft_id=_body_hash(body_a))
        approve_cmd(_body_hash(body_a))

        # Body A should pass
        assert pre_hook(
            tool_name="send_message",
            args={"target": "email:x@y.com", "message": body_a},
        ) is None

        # Body B needs its own full flow
        body_b = "Body B"
        load_draft(body=body_b, recipient="x@y.com")
        show_preview(draft_id=_body_hash(body_b))
        approve_cmd(_body_hash(body_b))

        assert pre_hook(
            tool_name="send_message",
            args={"target": "email:x@y.com", "message": body_b},
        ) is None


# ---------------------------------------------------------------------------
# 12. Case-insensitive target matching (Bug 1: case-sensitivity bypass)
# ---------------------------------------------------------------------------

class TestCaseInsensitiveTarget:
    """Guard must catch email targets regardless of casing.

    send_message_tool.py normalizes `target.split(":")[0].strip().lower()`
    AFTER hooks fire. So "Email:victim@evil.com" bypasses a guard that only
    checks `target.startswith("email")` (lowercase). The guard must
    case-fold the target before the startswith check.
    """

    def test_capital_email_is_caught(self, pre_hook):
        """target="Email:foo@bar.com" must be intercepted by the guard."""
        result = pre_hook(
            tool_name="send_message",
            args={"target": "Email:foo@bar.com", "message": "pwned"},
        )
        assert result is not None, (
            "Guard failed to intercept 'Email:foo@bar.com' — "
            "case-sensitivity bypass"
        )
        assert result["action"] == "block"

    def test_all_caps_email_is_caught(self, pre_hook):
        """target="EMAIL:foo@bar.com" must be intercepted by the guard."""
        result = pre_hook(
            tool_name="send_message",
            args={"target": "EMAIL:foo@bar.com", "message": "pwned"},
        )
        assert result is not None, (
            "Guard failed to intercept 'EMAIL:foo@bar.com' — "
            "case-sensitivity bypass"
        )
        assert result["action"] == "block"

    def test_mixed_case_email_is_caught(self, pre_hook):
        """target="eMaIl:foo@bar.com" must be intercepted by the guard."""
        result = pre_hook(
            tool_name="send_message",
            args={"target": "eMaIl:foo@bar.com", "message": "pwned"},
        )
        assert result is not None, (
            "Guard failed to intercept 'eMaIl:foo@bar.com' — "
            "case-sensitivity bypass"
        )
        assert result["action"] == "block"

    def test_capital_email_full_flow_works(
        self, pre_hook, load_draft, show_preview, approve_cmd
    ):
        """A properly approved draft must pass even with capitalized target."""
        body = "Legit email"
        load_draft(body=body, recipient="legit@example.com")
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)
        approve_cmd(draft_id)

        result = pre_hook(
            tool_name="send_message",
            args={"target": "Email:legit@example.com", "message": body},
        )
        assert result is None, "Approved email blocked with capitalized target"


# ---------------------------------------------------------------------------
# 13. Approval bound to (recipient, subject, body) not just body
#     (Bug 2: approval replay across recipients)
# ---------------------------------------------------------------------------

class TestApprovalReplayAcrossRecipients:
    """Approval for body+recipient_A must NOT transfer to recipient_B.

    The approval hash must include the recipient (and subject when present)
    so that an attacker cannot get a body approved for a safe recipient
    then replay it to a dangerous one.
    """

    def test_approval_not_transferable_to_different_recipient(
        self, pre_hook, load_draft, show_preview, approve_cmd
    ):
        """Approve body for safe@co.com, then send same body to evil@attacker.com."""
        body = "Quarterly report attached"

        # Full approval flow for safe recipient
        load_draft(body=body, recipient="safe@company.com")
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)
        approve_cmd(draft_id)

        # Verify it works for the original target
        result_safe = pre_hook(
            tool_name="send_message",
            args={"target": "email:safe@company.com", "message": body},
        )
        assert result_safe is None, "Approved email blocked for original recipient"

        # Now try the SAME body but a DIFFERENT recipient -- must be blocked
        result_evil = pre_hook(
            tool_name="send_message",
            args={"target": "email:evil@attacker.com", "message": body},
        )
        assert result_evil is not None, (
            "SECURITY BUG: approval for safe@company.com was replayed to "
            "evil@attacker.com — approval must bind to recipient"
        )
        assert result_evil["action"] == "block"


# ---------------------------------------------------------------------------
# 14. show_preview falls back to current draft when no draft_id given
# ---------------------------------------------------------------------------

class TestShowPreviewFallsBackToCurrentDraft:
    """show_preview with empty draft_id should use the current (most recent) draft."""

    def test_preview_uses_current_when_no_id(self, plugin, load_draft, show_preview):
        body = "test body"
        result_load = load_draft(body=body, recipient="a@b.com")
        loaded = json.loads(result_load)
        expected_draft_id = loaded["draft_id"]

        # Call show_preview with empty string (no explicit draft_id)
        result_preview = show_preview(draft_id="")
        parsed = json.loads(result_preview)

        assert parsed["status"] == "draft_previewed"
        assert parsed["draft_id"] == expected_draft_id


# ---------------------------------------------------------------------------
# 15. show_preview with no draft and no current -> error
# ---------------------------------------------------------------------------

class TestShowPreviewNoDraftNoCurrent:
    """show_preview with no draft_id and no loaded drafts returns an error."""

    def test_preview_no_draft_returns_error(self, show_preview):
        result = show_preview(draft_id="")
        parsed = json.loads(result)

        assert "error" in parsed
        assert "no draft" in parsed["error"].lower() or "load" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# 16. /approve-email ignores non-hash freetext args (falls back to current)
# ---------------------------------------------------------------------------

class TestApproveIgnoresNonHashArgs:
    """Freetext arg (not a sha256 hash) should be ignored; approve current draft."""

    def test_freetext_arg_falls_back_to_current(self, plugin, load_draft, show_preview, approve_cmd):
        body = "Freetext approve body"
        recipient = "yu_kuan@yahoo.com"
        load_draft(body=body, recipient=recipient)
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)

        # Freetext arg — not a valid sha256 hash
        result = approve_cmd("to yu_kuan@yahoo.com")

        # Handler returns dict on success with user_message + followup_agent_message
        msg = result["user_message"] if isinstance(result, dict) else result
        assert "approved" in msg.lower()


# ---------------------------------------------------------------------------
# 17. /approve-email accepts a valid sha256 hash
# ---------------------------------------------------------------------------

class TestApproveAcceptsValidHash:
    """Passing a real sha256 draft_id hash to /approve-email should succeed."""

    def test_valid_hash_approves(self, plugin, load_draft, show_preview, approve_cmd):
        body = "Hash approve body"
        recipient = "hash@example.com"
        load_draft(body=body, recipient=recipient)
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)

        result = approve_cmd(draft_id)

        # Handler returns dict on success with user_message + followup_agent_message
        msg = result["user_message"] if isinstance(result, dict) else result
        assert "approved" in msg.lower()


# ---------------------------------------------------------------------------
# 18. /approve-email rejects invalid hash gracefully (falls back to current)
# ---------------------------------------------------------------------------

class TestApproveRejectsInvalidHashGracefully:
    """Non-hex / wrong-length arg should be treated as freetext, not a hash.

    The approve handler should fall back to the current draft rather than
    returning "No draft found".
    """

    def test_invalid_hash_falls_back_to_current(self, plugin, load_draft, show_preview, approve_cmd):
        body = "Invalid hash body"
        recipient = "fallback@example.com"
        load_draft(body=body, recipient=recipient)
        draft_id = _body_hash(body)
        show_preview(draft_id=draft_id)

        # "not-a-hash-at-all" is not hex and not 64 chars — should be ignored
        result = approve_cmd("not-a-hash-at-all")

        # Handler returns dict on success with user_message + followup_agent_message
        msg = result["user_message"] if isinstance(result, dict) else result
        assert "approved" in msg.lower()


# ---------------------------------------------------------------------------
# 19. Drafts stored as individual files, not in state.json
# ---------------------------------------------------------------------------

class TestDraftFiles:
    """Verify drafts are stored as individual files, not in state.json."""

    def test_draft_saved_as_individual_file(self, plugin, load_draft):
        """Loading a draft creates a file in the drafts directory."""
        body = "Test draft body for file storage"
        result_json = plugin._email_load_draft(body=body, recipient="test@example.com")
        result = json.loads(result_json)
        draft_hash = result["draft_id"]

        drafts_dir = plugin._get_drafts_dir()
        draft_file = drafts_dir / f"draft_{draft_hash}.json"
        assert draft_file.exists()

        data = json.loads(draft_file.read_text())
        assert data["body"] == body
        assert data["recipient"] == "test@example.com"
        assert data["loaded_at"] is not None
        assert data["previewed_at"] is None

    def test_state_json_has_no_drafts_key(self, plugin, load_draft):
        """After loading a draft, state.json should not contain 'drafts'."""
        plugin._email_load_draft(body="No drafts in state", recipient="a@b.com")
        state = plugin._load_state()
        assert "drafts" not in state

    def test_load_draft_returns_none_for_missing(self, plugin):
        """_load_draft returns None for a hash that doesn't exist."""
        assert plugin._load_draft("nonexistent_hash_abc123") is None


# ---------------------------------------------------------------------------
# 20. Drafts dir is configurable via hermes config
# ---------------------------------------------------------------------------

class TestConfigOverride:
    """Verify drafts_dir is configurable via hermes config."""

    def test_custom_drafts_dir(self, plugin, tmp_path, monkeypatch):
        """When config sets drafts_dir, drafts land there."""
        custom_dir = tmp_path / "custom_drafts"
        # Monkeypatch the config to return our custom dir
        monkeypatch.setattr(
            plugin, "_get_drafts_dir",
            lambda: (custom_dir.mkdir(parents=True, exist_ok=True) or custom_dir),
        )
        result = json.loads(plugin._email_load_draft(body="custom dir test", recipient="x@y.com"))
        draft_file = custom_dir / f"draft_{result['draft_id']}.json"
        assert draft_file.exists()


# ---------------------------------------------------------------------------
# 21. Migration from legacy state.json with inline drafts
# ---------------------------------------------------------------------------

class TestMigration:
    """Verify automatic migration from legacy state.json with inline drafts."""

    def test_migrates_legacy_drafts_to_files(self, plugin):
        """Old state.json with 'drafts' key triggers migration to individual files."""
        body = "legacy draft body"
        bh = hashlib.sha256(body.encode()).hexdigest()

        # Write legacy-format state.json
        state_dir = plugin._state_dir()
        state_path = state_dir / "state.json"
        legacy_state = {
            "drafts": {
                bh: {
                    "body": body,
                    "recipient": "old@example.com",
                    "loaded_at": 1700000000.0,
                    "previewed_at": None,
                }
            },
            "current": bh,
            "approvals": {},
        }
        state_path.write_text(json.dumps(legacy_state))

        # Trigger load — should auto-migrate
        state = plugin._load_state()

        # Verify migration happened
        assert "drafts" not in state
        assert state["current"] == bh

        # Verify individual file created
        draft = plugin._load_draft(bh)
        assert draft is not None
        assert draft["body"] == body
        assert draft["recipient"] == "old@example.com"

    def test_migration_is_idempotent(self, plugin):
        """Running migration twice doesn't error or duplicate."""
        body = "idempotent test"
        bh = hashlib.sha256(body.encode()).hexdigest()

        state_dir = plugin._state_dir()
        state_path = state_dir / "state.json"
        legacy_state = {
            "drafts": {bh: {"body": body, "recipient": "x@y.com", "loaded_at": 1.0, "previewed_at": None}},
            "current": bh,
            "approvals": {},
        }

        # First migration
        state_path.write_text(json.dumps(legacy_state))
        plugin._load_state()

        # Re-write legacy state (simulating incomplete previous run)
        state_path.write_text(json.dumps(legacy_state))
        state = plugin._load_state()

        assert "drafts" not in state
        assert plugin._load_draft(bh)["body"] == body
