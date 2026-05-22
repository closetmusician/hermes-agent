"""Tests for the control-room plugin.

ABOUTME: TDD tests for the control-room governance plugin.
Covers: SQLite audit trail, YAML policy engine, workflow state,
policy log, hook handlers (pre/post_tool_call), and monitoring API.

Covers the bundled plugin at ``plugins/control-room/``:

  * ``AuditDB``: table/index creation, audit logging, workflow state,
    policy log entries.
  * Policy engine: YAML loading, trigger matching (tool name + condition),
    precondition checking against workflow_state, block/allow decisions.
  * Hook handlers: ``pre_tool_call`` logs + evaluates policies;
    ``post_tool_call`` logs result and duration.
  * Monitoring HTTP API: JSON endpoints for tool-calls, blocked,
    workflows, policies.
  * Plugin registration: hooks + slash command wired via ``register(ctx)``.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import sqlite3
import sys
import textwrap
import time
import types
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Point HERMES_HOME to a tempdir for each test."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return hermes_home


@pytest.fixture
def db_path(tmp_path):
    """Return a path for a test SQLite database."""
    return tmp_path / "test_audit.sqlite"


@pytest.fixture
def policies_dir(tmp_path):
    """Create a temporary policies directory with a sample policy."""
    d = tmp_path / "policies"
    d.mkdir()
    return d


@pytest.fixture
def sample_policy_file(policies_dir):
    """Write a sample email-send-guard policy YAML to the policies dir."""
    policy = {
        "name": "email-send-guard",
        "description": "Require draft flow before email send",
        "triggers": [
            {
                "tool": "send_message",
                "condition": "args.target.startswith('email')",
            }
        ],
        "preconditions": [
            {
                "state_key": "draft.{body_hash}.loaded",
                "error": "Load draft first",
            },
            {
                "state_key": "draft.{body_hash}.previewed",
                "error": "Preview draft first",
            },
        ],
    }
    path = policies_dir / "email-send.yaml"
    path.write_text(yaml.dump(policy))
    return path


def _load_plugin():
    """Import the control-room plugin module from the repo path."""
    repo_root = Path(__file__).resolve().parents[2]
    plugin_dir = repo_root / "plugins" / "control-room"

    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []
        sys.modules["hermes_plugins"] = ns

    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.control_room",
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "hermes_plugins.control_room"
    mod.__path__ = [str(plugin_dir)]
    sys.modules["hermes_plugins.control_room"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cr():
    """Load the control-room plugin module."""
    return _load_plugin()


# ---------------------------------------------------------------------------
# 1. Database initialization
# ---------------------------------------------------------------------------

class TestDatabaseInit:
    """AuditDB creates all tables and indices on init."""

    def test_creates_tables_and_indices(self, cr, db_path):
        db = cr.AuditDB(db_path)
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        # Check tables exist
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cur.fetchall()}
        assert "tool_audit" in tables
        assert "workflow_state" in tables
        assert "policy_log" in tables

        # Check indices exist
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        )
        indices = {row[0] for row in cur.fetchall()}
        assert "idx_audit_ts" in indices
        assert "idx_audit_tool" in indices

        conn.close()

    def test_creates_parent_directory(self, cr, tmp_path):
        nested = tmp_path / "deep" / "nested" / "state.sqlite"
        db = cr.AuditDB(nested)
        assert nested.parent.exists()

    def test_idempotent_init(self, cr, db_path):
        """Creating AuditDB twice on the same path does not fail."""
        db1 = cr.AuditDB(db_path)
        db2 = cr.AuditDB(db_path)
        # Both should be functional
        db2.log_tool_call("pre", "test_tool", {})


# ---------------------------------------------------------------------------
# 2. pre_tool_call audit logging
# ---------------------------------------------------------------------------

class TestPreToolCallAudit:
    """pre_tool_call hook logs an audit entry with phase='pre'."""

    def test_logs_pre_phase(self, cr, db_path):
        db = cr.AuditDB(db_path)
        db.log_tool_call(
            phase="pre",
            tool_name="terminal",
            args={"command": "ls"},
            session_id="sess-1",
        )
        rows = db.get_recent_calls(limit=10)
        assert len(rows) == 1
        row = rows[0]
        assert row["phase"] == "pre"
        assert row["tool_name"] == "terminal"
        assert row["session_id"] == "sess-1"
        assert row["blocked"] == 0

    def test_args_stored_as_json(self, cr, db_path):
        db = cr.AuditDB(db_path)
        args = {"command": "echo hello", "workdir": "/tmp"}
        db.log_tool_call("pre", "terminal", args)
        rows = db.get_recent_calls(limit=1)
        stored = json.loads(rows[0]["args_json"])
        assert stored == args


# ---------------------------------------------------------------------------
# 3. post_tool_call audit logging
# ---------------------------------------------------------------------------

class TestPostToolCallAudit:
    """post_tool_call hook logs result and duration."""

    def test_logs_post_with_result_and_duration(self, cr, db_path):
        db = cr.AuditDB(db_path)
        db.log_tool_call(
            phase="post",
            tool_name="read_file",
            args={"path": "/tmp/test.txt"},
            result="file contents here",
            duration_ms=42,
        )
        rows = db.get_recent_calls(limit=1)
        row = rows[0]
        assert row["phase"] == "post"
        assert row["result_json"] == "file contents here"
        assert row["duration_ms"] == 42


# ---------------------------------------------------------------------------
# 4. Policy loading from YAML files
# ---------------------------------------------------------------------------

class TestPolicyLoading:
    """Policies are loaded from YAML files in a directory."""

    def test_loads_single_policy(self, cr, sample_policy_file, policies_dir):
        engine = cr.PolicyEngine([policies_dir])
        assert len(engine.policies) == 1
        assert engine.policies[0]["name"] == "email-send-guard"

    def test_loads_from_multiple_dirs(self, cr, sample_policy_file, policies_dir, tmp_path):
        extra_dir = tmp_path / "extra"
        extra_dir.mkdir()
        extra_policy = {
            "name": "rate-limiter",
            "description": "Limit tool calls",
            "triggers": [{"tool": "web_search"}],
            "preconditions": [],
        }
        (extra_dir / "rate-limit.yaml").write_text(yaml.dump(extra_policy))

        engine = cr.PolicyEngine([policies_dir, extra_dir])
        assert len(engine.policies) == 2
        names = {p["name"] for p in engine.policies}
        assert names == {"email-send-guard", "rate-limiter"}

    def test_skips_nonexistent_dir(self, cr, tmp_path):
        """Non-existent directories are silently skipped."""
        missing = tmp_path / "nope"
        engine = cr.PolicyEngine([missing])
        assert len(engine.policies) == 0

    def test_skips_malformed_yaml(self, cr, policies_dir):
        """Malformed YAML files are skipped without crashing."""
        (policies_dir / "bad.yaml").write_text(": : : invalid\n  - {{")
        engine = cr.PolicyEngine([policies_dir])
        # Should not crash; may load zero or skip the bad file
        assert isinstance(engine.policies, list)


# ---------------------------------------------------------------------------
# 5. Policy trigger matching
# ---------------------------------------------------------------------------

class TestPolicyTriggerMatching:
    """Policies trigger on tool name + condition evaluation."""

    def test_matches_tool_name_and_condition(self, cr, sample_policy_file, policies_dir):
        engine = cr.PolicyEngine([policies_dir])
        matched = engine.find_matching_policies(
            "send_message", {"target": "email:foo@bar.com", "body": "hello"}
        )
        assert len(matched) == 1
        assert matched[0]["name"] == "email-send-guard"

    def test_no_match_wrong_tool(self, cr, sample_policy_file, policies_dir):
        engine = cr.PolicyEngine([policies_dir])
        matched = engine.find_matching_policies("read_file", {"path": "/tmp"})
        assert len(matched) == 0

    def test_no_match_condition_false(self, cr, sample_policy_file, policies_dir):
        engine = cr.PolicyEngine([policies_dir])
        matched = engine.find_matching_policies(
            "send_message", {"target": "slack:channel"}
        )
        assert len(matched) == 0

    def test_matches_without_condition(self, cr, policies_dir):
        """A trigger with no condition matches on tool name alone."""
        policy = {
            "name": "no-condition",
            "triggers": [{"tool": "web_search"}],
            "preconditions": [],
        }
        (policies_dir / "no-cond.yaml").write_text(yaml.dump(policy))
        engine = cr.PolicyEngine([policies_dir])
        matched = engine.find_matching_policies("web_search", {})
        assert len(matched) >= 1


# ---------------------------------------------------------------------------
# 6. Policy precondition checking
# ---------------------------------------------------------------------------

class TestPolicyPreconditions:
    """Preconditions are checked against workflow_state."""

    def test_fails_when_state_missing(self, cr, db_path, sample_policy_file, policies_dir):
        db = cr.AuditDB(db_path)
        engine = cr.PolicyEngine([policies_dir])
        policy = engine.policies[0]
        body = "hello world"
        body_hash = hashlib.sha256(body.encode()).hexdigest()[:12]
        args = {"target": "email:foo@bar.com", "body": body}

        result = engine.check_preconditions(policy, args, db)
        assert result is not None  # Should return an error
        assert "Load draft first" in result or "Preview draft first" in result

    def test_passes_when_all_states_set(self, cr, db_path, sample_policy_file, policies_dir):
        db = cr.AuditDB(db_path)
        engine = cr.PolicyEngine([policies_dir])
        policy = engine.policies[0]
        body = "hello world"
        body_hash = hashlib.sha256(body.encode()).hexdigest()[:12]

        # Set all required workflow states
        db.set_workflow_state(f"draft.{body_hash}.loaded", True)
        db.set_workflow_state(f"draft.{body_hash}.previewed", True)
        db.set_workflow_state(f"approval.{body_hash}.valid", True)

        args = {"target": "email:foo@bar.com", "body": body}
        result = engine.check_preconditions(policy, args, db)
        assert result is None  # No error = all preconditions met


# ---------------------------------------------------------------------------
# 7. Policy block returns correct error
# ---------------------------------------------------------------------------

class TestPolicyBlockMessage:
    """Blocked policies return the first failing precondition's error."""

    def test_block_error_from_first_failing_precondition(
        self, cr, db_path, sample_policy_file, policies_dir
    ):
        db = cr.AuditDB(db_path)
        engine = cr.PolicyEngine([policies_dir])
        policy = engine.policies[0]
        body = "test email body"
        body_hash = hashlib.sha256(body.encode()).hexdigest()[:12]
        args = {"target": "email:user@test.com", "body": body}

        error = engine.check_preconditions(policy, args, db)
        assert error == "Load draft first"

    def test_block_skips_to_second_when_first_met(
        self, cr, db_path, sample_policy_file, policies_dir
    ):
        db = cr.AuditDB(db_path)
        engine = cr.PolicyEngine([policies_dir])
        policy = engine.policies[0]
        body = "test email body"
        body_hash = hashlib.sha256(body.encode()).hexdigest()[:12]

        db.set_workflow_state(f"draft.{body_hash}.loaded", True)
        args = {"target": "email:user@test.com", "body": body}

        error = engine.check_preconditions(policy, args, db)
        assert error == "Preview draft first"


# ---------------------------------------------------------------------------
# 8. Policy allow when all preconditions met
# ---------------------------------------------------------------------------

class TestPolicyAllow:
    """Policy allows when all preconditions are satisfied."""

    def test_allow_returns_none(self, cr, db_path, policies_dir):
        policy = {
            "name": "simple-guard",
            "triggers": [{"tool": "write_file"}],
            "preconditions": [
                {"state_key": "draft.ready", "error": "Not ready"},
            ],
        }
        (policies_dir / "simple.yaml").write_text(yaml.dump(policy))
        engine = cr.PolicyEngine([policies_dir])

        db = cr.AuditDB(db_path)
        db.set_workflow_state("draft.ready", True)

        result = engine.check_preconditions(
            engine.policies[0], {"path": "/tmp/x"}, db
        )
        assert result is None


# ---------------------------------------------------------------------------
# 9. Workflow state get/set operations
# ---------------------------------------------------------------------------

class TestWorkflowState:
    """AuditDB supports key/value workflow state storage."""

    def test_set_and_get(self, cr, db_path):
        db = cr.AuditDB(db_path)
        db.set_workflow_state("draft.abc123.loaded", True)
        val = db.get_workflow_state("draft.abc123.loaded")
        assert val is True

    def test_get_missing_returns_none(self, cr, db_path):
        db = cr.AuditDB(db_path)
        val = db.get_workflow_state("nonexistent.key")
        assert val is None

    def test_overwrite(self, cr, db_path):
        db = cr.AuditDB(db_path)
        db.set_workflow_state("counter", 1)
        db.set_workflow_state("counter", 2)
        val = db.get_workflow_state("counter")
        assert val == 2

    def test_get_all_states(self, cr, db_path):
        db = cr.AuditDB(db_path)
        db.set_workflow_state("a", 1)
        db.set_workflow_state("b", "hello")
        states = db.get_all_workflow_states()
        assert len(states) >= 2
        keys = {s["key"] for s in states}
        assert "a" in keys
        assert "b" in keys


# ---------------------------------------------------------------------------
# 10. Policy log entries
# ---------------------------------------------------------------------------

class TestPolicyLog:
    """Decisions are logged to the policy_log table."""

    def test_log_block_decision(self, cr, db_path):
        db = cr.AuditDB(db_path)
        db.log_policy_decision(
            policy_name="email-send-guard",
            tool_name="send_message",
            decision="block",
            reason="Load draft first",
        )
        rows = db.get_policy_log(limit=10)
        assert len(rows) == 1
        assert rows[0]["decision"] == "block"
        assert rows[0]["policy_name"] == "email-send-guard"

    def test_log_allow_decision(self, cr, db_path):
        db = cr.AuditDB(db_path)
        db.log_policy_decision(
            policy_name="email-send-guard",
            tool_name="send_message",
            decision="allow",
        )
        rows = db.get_policy_log(limit=10)
        assert len(rows) == 1
        assert rows[0]["decision"] == "allow"


# ---------------------------------------------------------------------------
# 11. Condition evaluator safety
# ---------------------------------------------------------------------------

class TestConditionEvaluator:
    """Safe condition evaluator handles common patterns without eval()."""

    def test_startswith(self, cr):
        assert cr.safe_eval_condition(
            "args.target.startswith('email')",
            {"target": "email:foo@bar.com"},
        ) is True

    def test_startswith_false(self, cr):
        assert cr.safe_eval_condition(
            "args.target.startswith('email')",
            {"target": "slack:channel"},
        ) is False

    def test_equality(self, cr):
        assert cr.safe_eval_condition(
            "args.action == 'delete'",
            {"action": "delete"},
        ) is True

    def test_equality_false(self, cr):
        assert cr.safe_eval_condition(
            "args.action == 'delete'",
            {"action": "read"},
        ) is False

    def test_contains(self, cr):
        assert cr.safe_eval_condition(
            "args.path contains '/sensitive/'",
            {"path": "/home/user/sensitive/data.txt"},
        ) is True

    def test_contains_false(self, cr):
        assert cr.safe_eval_condition(
            "args.path contains '/sensitive/'",
            {"path": "/home/user/public/data.txt"},
        ) is False

    def test_missing_key_returns_false(self, cr):
        """Missing args key returns False, not an exception."""
        assert cr.safe_eval_condition(
            "args.target.startswith('email')",
            {"other_key": "value"},
        ) is False

    def test_unsupported_condition_returns_false(self, cr):
        """Unsupported condition patterns return False (safe default)."""
        assert cr.safe_eval_condition(
            "import os; os.system('rm -rf /')",
            {},
        ) is False


# ---------------------------------------------------------------------------
# 12. Blocked calls query
# ---------------------------------------------------------------------------

class TestBlockedCallsQuery:
    """get_blocked_calls returns only blocked audit entries."""

    def test_returns_blocked_only(self, cr, db_path):
        db = cr.AuditDB(db_path)
        db.log_tool_call("pre", "safe_tool", {})
        db.log_tool_call(
            "blocked", "send_message", {"target": "email:x"},
            blocked=True, reason="Missing draft",
        )
        db.log_tool_call("post", "safe_tool", {}, result="ok")

        blocked = db.get_blocked_calls(limit=10)
        assert len(blocked) == 1
        assert blocked[0]["tool_name"] == "send_message"
        assert blocked[0]["reason"] == "Missing draft"


# ---------------------------------------------------------------------------
# 13. Tool name filter on recent calls
# ---------------------------------------------------------------------------

class TestToolNameFilter:
    """get_recent_calls supports tool name filtering."""

    def test_filter_by_tool_name(self, cr, db_path):
        db = cr.AuditDB(db_path)
        db.log_tool_call("pre", "terminal", {"command": "ls"})
        db.log_tool_call("pre", "read_file", {"path": "/tmp/x"})
        db.log_tool_call("pre", "terminal", {"command": "pwd"})

        rows = db.get_recent_calls(limit=10, tool_name="terminal")
        assert len(rows) == 2
        assert all(r["tool_name"] == "terminal" for r in rows)


# ---------------------------------------------------------------------------
# 14. Hook integration — pre_tool_call with policy evaluation
# ---------------------------------------------------------------------------

class TestPreToolCallHook:
    """The pre_tool_call hook logs audit + evaluates policies."""

    def test_pre_hook_allows_unmatched_tool(self, cr, db_path, policies_dir, sample_policy_file):
        db = cr.AuditDB(db_path)
        engine = cr.PolicyEngine([policies_dir])
        handler = cr.make_pre_tool_call_handler(db, engine)

        result = handler(
            tool_name="read_file",
            args={"path": "/tmp/test"},
            session_id="s1",
        )
        assert result is None  # Not blocked

        # But an audit entry was still logged
        rows = db.get_recent_calls(limit=10)
        assert len(rows) == 1
        assert rows[0]["phase"] == "pre"

    def test_pre_hook_blocks_when_policy_fails(self, cr, db_path, policies_dir, sample_policy_file):
        db = cr.AuditDB(db_path)
        engine = cr.PolicyEngine([policies_dir])
        handler = cr.make_pre_tool_call_handler(db, engine)

        result = handler(
            tool_name="send_message",
            args={"target": "email:test@test.com", "body": "hello"},
            session_id="s1",
        )
        assert result is not None
        assert result["action"] == "block"
        assert "Load draft first" in result["message"]

        # Should have both a 'pre' and a 'blocked' audit entry
        rows = db.get_recent_calls(limit=10)
        phases = {r["phase"] for r in rows}
        assert "blocked" in phases


# ---------------------------------------------------------------------------
# 15. Hook integration — post_tool_call
# ---------------------------------------------------------------------------

class TestPostToolCallHook:
    """The post_tool_call hook logs result and duration."""

    def test_post_hook_logs_result(self, cr, db_path):
        db = cr.AuditDB(db_path)
        handler = cr.make_post_tool_call_handler(db)

        handler(
            tool_name="read_file",
            args={"path": "/tmp/x"},
            result="file contents",
            duration_ms=55,
            session_id="s1",
        )
        rows = db.get_recent_calls(limit=10)
        assert len(rows) == 1
        assert rows[0]["phase"] == "post"
        assert rows[0]["duration_ms"] == 55


# ---------------------------------------------------------------------------
# 16. Plugin registration
# ---------------------------------------------------------------------------

class TestPluginRegistration:
    """register(ctx) wires hooks and the slash command."""

    def test_register_wires_hooks_and_command(self, cr):
        hooks_registered = {}
        commands_registered = {}

        class FakeCtx:
            def register_hook(self, name, handler):
                hooks_registered[name] = handler

            def register_command(self, name, handler, description="", args_hint=""):
                commands_registered[name] = handler

        # Monkey-patch the module-level DB + engine so register() doesn't
        # create real resources
        cr.register(FakeCtx())
        assert "pre_tool_call" in hooks_registered
        assert "post_tool_call" in hooks_registered
        assert "control-room" in commands_registered


# ---------------------------------------------------------------------------
# 17. Body hash extraction
# ---------------------------------------------------------------------------

class TestBodyHashExtraction:
    """body_hash is computed from SHA256 of message body."""

    def test_body_hash_consistent(self, cr):
        body = "Hello, World!"
        h1 = cr.compute_body_hash(body)
        h2 = cr.compute_body_hash(body)
        assert h1 == h2
        assert len(h1) == 12  # truncated SHA256

    def test_body_hash_differs_for_different_bodies(self, cr):
        h1 = cr.compute_body_hash("body A")
        h2 = cr.compute_body_hash("body B")
        assert h1 != h2


# ---------------------------------------------------------------------------
# 18. State key interpolation
# ---------------------------------------------------------------------------

class TestStateKeyInterpolation:
    """State keys support {body_hash} interpolation."""

    def test_interpolation(self, cr):
        body = "test message"
        body_hash = cr.compute_body_hash(body)
        result = cr.interpolate_state_key(
            "draft.{body_hash}.loaded",
            {"body": body},
        )
        assert result == f"draft.{body_hash}.loaded"

    def test_no_interpolation_needed(self, cr):
        result = cr.interpolate_state_key(
            "draft.ready",
            {"body": "test"},
        )
        assert result == "draft.ready"
