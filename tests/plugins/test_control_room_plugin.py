"""
ABOUTME: Unit tests for the control-room plugin components.
ABOUTME: Tests AuditDB, evaluate_condition, Policy, PolicyEngine,
ABOUTME: and ControlRoom in isolation with in-memory or tmp_path SQLite.
ABOUTME: No mocks on core components -- real SQLite, real YAML parsing.
"""

import json
import sqlite3
import time
from pathlib import Path

import pytest
import yaml

# We import directly from the plugin package. The test runner's sys.path
# must include the repo root so ``plugins.control-room`` resolves.  Since
# Python doesn't allow hyphens in package names, we use importlib.
import importlib

cr_mod = importlib.import_module("plugins.control-room")

AuditDB = cr_mod.AuditDB
ControlRoom = cr_mod.ControlRoom
Policy = cr_mod.Policy
PolicyEngine = cr_mod.PolicyEngine
evaluate_condition = cr_mod.evaluate_condition
load_policies = cr_mod.load_policies


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> AuditDB:
    """Provide a fresh AuditDB backed by tmp_path."""
    return AuditDB(tmp_path / "test_audit.db")


@pytest.fixture()
def policy_dir(tmp_path: Path) -> Path:
    """Provide a tmp directory pre-loaded with one policy file."""
    d = tmp_path / "policies"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# AuditDB tests
# ---------------------------------------------------------------------------


class TestAuditDB:
    """Verify audit, policy, and workflow-state CRUD."""

    def test_log_and_fetch_audit(self, db: AuditDB) -> None:
        """Insert an audit row and read it back."""
        rowid = db.log_audit("terminal", {"command": "ls"}, "pre", task_id="t1")
        assert rowid >= 1
        rows = db.get_audit_rows(phase="pre")
        assert len(rows) == 1
        assert rows[0]["tool_name"] == "terminal"
        assert rows[0]["phase"] == "pre"

    def test_audit_post_with_duration(self, db: AuditDB) -> None:
        """Audit rows with post phase store result and duration."""
        db.log_audit(
            "web_search", {"query": "test"}, "post",
            result="found 5 results", duration_ms=123.4,
        )
        rows = db.get_audit_rows(phase="post")
        assert len(rows) == 1
        assert rows[0]["result"] == "found 5 results"
        assert rows[0]["duration_ms"] == pytest.approx(123.4)

    def test_audit_blocked_phase(self, db: AuditDB) -> None:
        """Blocked tool calls are recorded with phase='blocked'."""
        db.log_audit("send_message", {"to": "a@b.com"}, "blocked", result="policy block")
        rows = db.get_audit_rows(phase="blocked")
        assert len(rows) == 1

    def test_policy_log(self, db: AuditDB) -> None:
        """Policy decisions are logged correctly."""
        db.log_policy("email-send", "send_message", "block", "missing draft")
        rows = db.get_policy_rows(decision="block")
        assert len(rows) == 1
        assert rows[0]["policy_name"] == "email-send"
        assert rows[0]["reason"] == "missing draft"

    def test_workflow_state_crud(self, db: AuditDB) -> None:
        """Set, get, update, list, and delete workflow state."""
        # Set
        db.set_state("draft.email.loaded", "true")
        assert db.get_state("draft.email.loaded") == "true"

        # Update
        db.set_state("draft.email.loaded", "false")
        assert db.get_state("draft.email.loaded") == "false"

        # List
        db.set_state("draft.email.approved", "true")
        states = db.list_states()
        assert "draft.email.loaded" in states
        assert "draft.email.approved" in states

        # Delete
        assert db.delete_state("draft.email.loaded") is True
        assert db.get_state("draft.email.loaded") is None
        assert db.delete_state("draft.email.loaded") is False

    def test_get_state_missing(self, db: AuditDB) -> None:
        """get_state returns None for missing keys."""
        assert db.get_state("nonexistent") is None


# ---------------------------------------------------------------------------
# evaluate_condition tests
# ---------------------------------------------------------------------------


class TestEvaluateCondition:
    """Verify each supported condition operator."""

    def test_eq(self) -> None:
        assert evaluate_condition("==", "hello", "hello") is True
        assert evaluate_condition("==", "hello", "world") is False

    def test_neq(self) -> None:
        assert evaluate_condition("!=", "a", "b") is True
        assert evaluate_condition("!=", "a", "a") is False

    def test_startswith(self) -> None:
        assert evaluate_condition("startswith", "user@example.com", "user@") is True
        assert evaluate_condition("startswith", "admin@test.com", "user@") is False

    def test_endswith(self) -> None:
        assert evaluate_condition("endswith", "file.txt", ".txt") is True
        assert evaluate_condition("endswith", "file.py", ".txt") is False

    def test_contains(self) -> None:
        assert evaluate_condition("contains", "hello world", "world") is True
        assert evaluate_condition("contains", "hello world", "mars") is False

    def test_none_actual(self) -> None:
        """None actual values are coerced to empty string."""
        assert evaluate_condition("==", None, "") is True
        assert evaluate_condition("contains", None, "x") is False

    def test_unknown_operator(self) -> None:
        """Unknown operators return False."""
        assert evaluate_condition("like", "abc", "a%") is False


# ---------------------------------------------------------------------------
# evaluate_condition regex operator tests
# ---------------------------------------------------------------------------


class TestEvaluateConditionRegex:
    """Verify the regex operator in evaluate_condition."""

    def test_regex_match(self) -> None:
        """Simple regex pattern matches target string."""
        assert evaluate_condition("regex", "import smtplib", "smtplib") is True

    def test_regex_no_match(self) -> None:
        """Regex returns False when pattern is absent from target."""
        assert evaluate_condition("regex", "import json", "smtplib") is False

    def test_regex_case_insensitive(self) -> None:
        """Regex matching is case-insensitive (re.IGNORECASE)."""
        assert evaluate_condition("regex", "import SMTPLIB", "smtplib") is True

    def test_regex_complex_pattern(self) -> None:
        """Alternation pattern matches any branch."""
        assert evaluate_condition(
            "regex",
            "nodemailer.createTransport()",
            "smtplib|nodemailer|sendgrid",
        ) is True

    def test_regex_invalid_pattern(self) -> None:
        """Invalid regex pattern returns False instead of raising."""
        assert evaluate_condition("regex", "some text", "[invalid(") is False

    def test_regex_partial_match(self) -> None:
        """Regex matches a substring within a longer string."""
        assert evaluate_condition(
            "regex",
            "server = smtplib.SMTP('smtp.gmail.com')",
            r"SMTP\(",
        ) is True

    def test_regex_graph_api_pattern(self) -> None:
        """Real Graph API pattern from policies matches mail endpoints."""
        pattern = r"graph\.microsoft\.com.*(sendMail|messages)"
        assert evaluate_condition(
            "regex",
            "https://graph.microsoft.com/v1.0/me/sendMail",
            pattern,
        ) is True
        assert evaluate_condition(
            "regex",
            "graph.microsoft.com/v1.0/users/a@b.com/messages",
            pattern,
        ) is True
        # Non-mail Graph API call should not match
        assert evaluate_condition(
            "regex",
            "https://graph.microsoft.com/v1.0/me/drive/root",
            pattern,
        ) is False


# ---------------------------------------------------------------------------
# Policy tests
# ---------------------------------------------------------------------------


class TestPolicy:
    """Verify trigger matching and precondition checking."""

    def _make_policy(self, **overrides: object) -> Policy:
        data = {
            "name": "test-policy",
            "action": "block",
            "triggers": [{"tool_name": "send_message"}],
            "preconditions": [],
        }
        data.update(overrides)
        return Policy(data)

    def test_trigger_match_simple(self) -> None:
        p = self._make_policy()
        assert p.matches_trigger("send_message", {}) is True
        assert p.matches_trigger("read_file", {}) is False

    def test_trigger_match_with_args(self) -> None:
        p = self._make_policy(triggers=[{
            "tool_name": "send_message",
            "args": {"target": {"operator": "contains", "value": "@"}},
        }])
        assert p.matches_trigger("send_message", {"target": "user@example.com"}) is True
        assert p.matches_trigger("send_message", {"target": "slack-channel"}) is False

    def test_trigger_exact_arg_match(self) -> None:
        p = self._make_policy(triggers=[{
            "tool_name": "terminal",
            "args": {"command": "rm -rf /"},
        }])
        assert p.matches_trigger("terminal", {"command": "rm -rf /"}) is True
        assert p.matches_trigger("terminal", {"command": "ls"}) is False

    def test_preconditions_met(self, db: AuditDB) -> None:
        p = self._make_policy(preconditions=[
            {"state_key": "draft.loaded", "required": True},
        ])
        db.set_state("draft.loaded", "true")
        met, reason = p.check_preconditions(db)
        assert met is True
        assert reason == ""

    def test_preconditions_missing(self, db: AuditDB) -> None:
        p = self._make_policy(preconditions=[
            {"state_key": "draft.loaded", "required": True, "message": "Load draft first"},
        ])
        met, reason = p.check_preconditions(db)
        assert met is False
        assert reason == "Load draft first"


# ---------------------------------------------------------------------------
# PolicyEngine tests
# ---------------------------------------------------------------------------


class TestPolicyEngine:
    """Verify policy evaluation and decision logic."""

    def test_no_matching_policy_allows(self, db: AuditDB) -> None:
        engine = PolicyEngine([], db)
        decision, reason, pname = engine.evaluate("read_file", {})
        assert decision == "allow"

    def test_block_when_precondition_missing(self, db: AuditDB) -> None:
        policy = Policy({
            "name": "test-block",
            "action": "block",
            "triggers": [{"tool_name": "send_message"}],
            "preconditions": [{"state_key": "approved", "required": True}],
        })
        engine = PolicyEngine([policy], db)
        decision, reason, pname = engine.evaluate("send_message", {})
        assert decision == "block"
        assert pname == "test-block"

    def test_allow_when_preconditions_met(self, db: AuditDB) -> None:
        db.set_state("approved", "yes")
        policy = Policy({
            "name": "test-block",
            "action": "block",
            "triggers": [{"tool_name": "send_message"}],
            "preconditions": [{"state_key": "approved", "required": True}],
        })
        engine = PolicyEngine([policy], db)
        decision, reason, pname = engine.evaluate("send_message", {})
        assert decision == "allow"

    def test_block_wins_over_allow(self, db: AuditDB) -> None:
        """When one policy allows and another blocks, block wins."""
        allow_policy = Policy({
            "name": "permissive",
            "action": "allow",
            "triggers": [{"tool_name": "send_message"}],
            "preconditions": [],
        })
        block_policy = Policy({
            "name": "strict",
            "action": "block",
            "triggers": [{"tool_name": "send_message"}],
            "preconditions": [{"state_key": "strict_approval", "required": True}],
        })
        engine = PolicyEngine([allow_policy, block_policy], db)
        decision, _, pname = engine.evaluate("send_message", {})
        assert decision == "block"
        assert pname == "strict"


# ---------------------------------------------------------------------------
# load_policies tests
# ---------------------------------------------------------------------------


class TestLoadPolicies:
    """Verify YAML policy file loading."""

    def test_load_from_directory(self, policy_dir: Path) -> None:
        (policy_dir / "test.yaml").write_text(yaml.dump({
            "name": "test-pol",
            "action": "block",
            "triggers": [{"tool_name": "terminal"}],
        }))
        policies = load_policies(policy_dir)
        assert len(policies) == 1
        assert policies[0].name == "test-pol"

    def test_load_nonexistent_dir(self, tmp_path: Path) -> None:
        policies = load_policies(tmp_path / "nope")
        assert policies == []

    def test_ignores_non_yaml(self, policy_dir: Path) -> None:
        (policy_dir / "readme.txt").write_text("not a policy")
        policies = load_policies(policy_dir)
        assert policies == []


# ---------------------------------------------------------------------------
# ControlRoom integration (still unit-level, no server)
# ---------------------------------------------------------------------------


class TestControlRoom:
    """Verify ControlRoom wires hooks correctly."""

    def test_pre_tool_call_allowed(self, tmp_path: Path) -> None:
        cr = ControlRoom(
            db_path=tmp_path / "audit.db",
            policy_dir=tmp_path / "no-policies",
        )
        result = cr.pre_tool_call("read_file", {"path": "/tmp/x"}, task_id="t1")
        assert result is None  # no block
        rows = cr.db.get_audit_rows(phase="pre")
        assert len(rows) == 1

    def test_pre_tool_call_blocked(self, tmp_path: Path) -> None:
        pdir = tmp_path / "policies"
        pdir.mkdir()
        (pdir / "block-all-terminal.yaml").write_text(yaml.dump({
            "name": "no-terminal",
            "action": "block",
            "triggers": [{"tool_name": "terminal"}],
            "preconditions": [{"state_key": "terminal_approved", "required": True}],
        }))
        cr = ControlRoom(db_path=tmp_path / "audit.db", policy_dir=pdir)
        result = cr.pre_tool_call("terminal", {"command": "rm -rf /"})
        assert result is not None
        assert result["action"] == "block"
        rows = cr.db.get_audit_rows(phase="blocked")
        assert len(rows) == 1

    def test_pre_tool_call_blocks_outlook_send_mail(self, tmp_path: Path) -> None:
        """Hardcoded block: terminal invoking outlook-send-mail.js is blocked."""
        cr = ControlRoom(
            db_path=tmp_path / "audit.db",
            policy_dir=tmp_path / "no-policies",
        )
        result = cr.pre_tool_call(
            "terminal",
            {"command": "node outlook-send-mail.js --to foo@bar.com"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "send_message" in result["message"]
        # Verify audit row recorded
        rows = cr.db.get_audit_rows(phase="blocked")
        assert len(rows) == 1

    def test_pre_tool_call_blocks_outlook_read_mail(self, tmp_path: Path) -> None:
        """Hardcoded block: terminal invoking outlook-read-mail.js is blocked."""
        cr = ControlRoom(
            db_path=tmp_path / "audit.db",
            policy_dir=tmp_path / "no-policies",
        )
        result = cr.pre_tool_call(
            "terminal",
            {"command": "node outlook-read-mail.js"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "send_message" in result["message"]
        # Verify audit row recorded
        rows = cr.db.get_audit_rows(phase="blocked")
        assert len(rows) == 1

    def test_pre_tool_call_blocks_pm_os_outlook_tools(self, tmp_path: Path) -> None:
        """Hardcoded block: any pm_os/bin/outlook command is blocked."""
        cr = ControlRoom(
            db_path=tmp_path / "audit.db",
            policy_dir=tmp_path / "no-policies",
        )
        result = cr.pre_tool_call(
            "terminal",
            {"command": "node ~/Code/pm_os/bin/outlook-calendar.js --list"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "send_message" in result["message"]
        rows = cr.db.get_audit_rows(phase="blocked")
        assert len(rows) == 1

    def test_pre_tool_call_allows_normal_terminal(self, tmp_path: Path) -> None:
        """Hardcoded block does not affect normal terminal commands."""
        cr = ControlRoom(
            db_path=tmp_path / "audit.db",
            policy_dir=tmp_path / "no-policies",
        )
        result = cr.pre_tool_call("terminal", {"command": "ls -la"})
        assert result is None

    def test_default_policy_dir_loads_bundled_policies(self, tmp_path: Path) -> None:
        """ControlRoom with no policy_dir loads from plugin's own policies/ dir."""
        cr = ControlRoom(db_path=tmp_path / "audit.db")
        assert len(cr.engine._policies) > 0, (
            "Default policy_dir should load bundled policies from plugin directory"
        )

    def test_post_tool_call_records(self, tmp_path: Path) -> None:
        cr = ControlRoom(
            db_path=tmp_path / "audit.db",
            policy_dir=tmp_path / "no-policies",
        )
        # Simulate pre then post
        cr.pre_tool_call("read_file", {"path": "/x"}, task_id="t1")
        time.sleep(0.01)  # ensure measurable duration
        cr.post_tool_call("read_file", {"path": "/x"}, result="contents", task_id="t1")
        rows = cr.db.get_audit_rows(phase="post")
        assert len(rows) == 1
        assert rows[0]["result"] == "contents"
        assert rows[0]["duration_ms"] is not None
        assert rows[0]["duration_ms"] > 0
