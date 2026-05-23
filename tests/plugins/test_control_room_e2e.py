"""
ABOUTME: End-to-end integration tests for the control-room plugin.
ABOUTME: Tests the full audit + policy enforcement flow using real SQLite,
ABOUTME: real YAML policies, and real HTTP monitoring endpoints.
ABOUTME: No mocks -- exercises the complete plugin lifecycle.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import yaml

# Import the plugin module via importlib (hyphenated package name).
cr_mod = importlib.import_module("plugins.control-room")

AuditDB = cr_mod.AuditDB
ControlRoom = cr_mod.ControlRoom
Policy = cr_mod.Policy
PolicyEngine = cr_mod.PolicyEngine
evaluate_condition = cr_mod.evaluate_condition
load_policies = cr_mod.load_policies


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy_file(policy_dir: Path, name: str, data: dict) -> Path:
    """Write a YAML policy file and return its path."""
    p = policy_dir / f"{name}.yaml"
    p.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return p


def _http_get(port: int, path: str) -> tuple[int, dict]:
    """Make a GET request to localhost:port/path and return (status, json_body)."""
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        return exc.code, body


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def policy_dir(tmp_path: Path) -> Path:
    """Provide a fresh tmp directory for policy files."""
    d = tmp_path / "policies"
    d.mkdir()
    return d


@pytest.fixture()
def cr(tmp_path: Path, policy_dir: Path) -> ControlRoom:
    """Provide a fresh ControlRoom with tmp_path DB and policy dir."""
    return ControlRoom(
        db_path=tmp_path / "audit.db",
        policy_dir=policy_dir,
    )


@pytest.fixture()
def cr_with_email_policy(tmp_path: Path, policy_dir: Path) -> ControlRoom:
    """ControlRoom with the email-send block policy loaded."""
    _make_policy_file(policy_dir, "email-send", {
        "name": "email-send",
        "description": "Block email sends without draft approval",
        "action": "block",
        "triggers": [{
            "tool_name": "send_message",
            "args": {"target": {"operator": "contains", "value": "@"}},
        }],
        "preconditions": [
            {"state_key": "draft.email.loaded", "required": True, "message": "Email draft must be loaded before sending"},
            {"state_key": "draft.email.approved", "required": True, "message": "Email draft must be approved before sending"},
        ],
    })
    return ControlRoom(
        db_path=tmp_path / "audit.db",
        policy_dir=policy_dir,
    )


# ---------------------------------------------------------------------------
# a. Audit logging -- pre_tool_call
# ---------------------------------------------------------------------------


class TestAuditPreToolCall:
    """Fire a tool call via pre_tool_call and verify the audit row."""

    def test_pre_tool_call_creates_audit_row(self, cr: ControlRoom) -> None:
        """Calling pre_tool_call on an allowed tool writes a phase='pre' row."""
        result = cr.pre_tool_call("read_file", {"path": "/tmp/test.txt"}, task_id="t1")
        assert result is None, "Should not block"

        rows = cr.db.get_audit_rows(phase="pre")
        assert len(rows) == 1
        row = rows[0]
        assert row["tool_name"] == "read_file"
        assert row["phase"] == "pre"
        assert row["task_id"] == "t1"
        assert json.loads(row["args_json"]) == {"path": "/tmp/test.txt"}


# ---------------------------------------------------------------------------
# b. Audit logging -- post_tool_call
# ---------------------------------------------------------------------------


class TestAuditPostToolCall:
    """Fire pre then post hook and verify result + duration."""

    def test_post_tool_call_records_result_and_duration(self, cr: ControlRoom) -> None:
        """post_tool_call writes phase='post' with result and duration_ms."""
        cr.pre_tool_call("web_search", {"query": "python"}, task_id="t2")
        time.sleep(0.015)  # ensure measurable duration
        cr.post_tool_call(
            "web_search", {"query": "python"},
            result="Found 10 results", task_id="t2",
        )

        rows = cr.db.get_audit_rows(phase="post")
        assert len(rows) == 1
        row = rows[0]
        assert row["tool_name"] == "web_search"
        assert row["phase"] == "post"
        assert row["result"] == "Found 10 results"
        assert row["duration_ms"] is not None
        assert row["duration_ms"] > 0


# ---------------------------------------------------------------------------
# c. Audit logging -- blocked
# ---------------------------------------------------------------------------


class TestAuditBlocked:
    """Fire a call that gets blocked and verify phase='blocked'."""

    def test_blocked_call_audit_row(self, cr_with_email_policy: ControlRoom) -> None:
        """A blocked tool call is recorded with phase='blocked'."""
        cr = cr_with_email_policy
        result = cr.pre_tool_call("send_message", {"target": "user@example.com"})
        assert result is not None
        assert result["action"] == "block"

        rows = cr.db.get_audit_rows(phase="blocked")
        assert len(rows) == 1
        row = rows[0]
        assert row["tool_name"] == "send_message"
        assert row["phase"] == "blocked"
        assert "draft" in (row["result"] or "").lower()


# ---------------------------------------------------------------------------
# d. Policy trigger matching
# ---------------------------------------------------------------------------


class TestPolicyTriggerMatching:
    """Create a policy that triggers on send_message with email target."""

    def test_trigger_matches_email_target(self, cr_with_email_policy: ControlRoom) -> None:
        """Policy triggers correctly for send_message with @ in target."""
        cr = cr_with_email_policy
        result = cr.pre_tool_call("send_message", {"target": "boss@company.com"})
        assert result is not None
        assert result["action"] == "block"
        assert "draft" in result["message"].lower()


# ---------------------------------------------------------------------------
# e. Policy trigger non-matching
# ---------------------------------------------------------------------------


class TestPolicyTriggerNonMatching:
    """Call a different tool and verify the policy does NOT trigger."""

    def test_different_tool_not_blocked(self, cr_with_email_policy: ControlRoom) -> None:
        """A read_file call should not be blocked by the email policy."""
        cr = cr_with_email_policy
        result = cr.pre_tool_call("read_file", {"path": "/etc/hosts"})
        assert result is None, "read_file should not be blocked by email policy"

    def test_send_message_without_email_not_blocked(self, cr_with_email_policy: ControlRoom) -> None:
        """send_message to a non-email target should not trigger."""
        cr = cr_with_email_policy
        result = cr.pre_tool_call("send_message", {"target": "slack-general"})
        assert result is None, "Non-email target should not trigger email policy"


# ---------------------------------------------------------------------------
# f. Policy precondition -- missing state
# ---------------------------------------------------------------------------


class TestPreconditionMissing:
    """Policy requires state key, key missing -> blocked."""

    def test_missing_state_blocks(self, cr_with_email_policy: ControlRoom) -> None:
        """When draft.email.loaded is missing, email send is blocked."""
        cr = cr_with_email_policy
        result = cr.pre_tool_call("send_message", {"target": "x@y.com"})
        assert result is not None
        assert result["action"] == "block"
        assert "loaded" in result["message"].lower()


# ---------------------------------------------------------------------------
# g. Policy precondition -- state present
# ---------------------------------------------------------------------------


class TestPreconditionPresent:
    """Set the required state keys and verify the call is allowed."""

    def test_state_present_allows(self, cr_with_email_policy: ControlRoom) -> None:
        """When both draft keys are set, email send is allowed."""
        cr = cr_with_email_policy
        cr.db.set_state("draft.email.loaded", "true")
        cr.db.set_state("draft.email.approved", "true")

        result = cr.pre_tool_call("send_message", {"target": "x@y.com"})
        assert result is None, "Should be allowed when all preconditions met"


# ---------------------------------------------------------------------------
# h. Policy precondition -- multiple conditions, partial
# ---------------------------------------------------------------------------


class TestPreconditionPartial:
    """Policy with 3 preconditions, only 2 met -> blocked on 3rd."""

    def test_partial_preconditions_block(self, tmp_path: Path) -> None:
        """With 3 required keys and only 2 set, blocked on the missing one."""
        pdir = tmp_path / "policies"
        pdir.mkdir()
        _make_policy_file(pdir, "triple-gate", {
            "name": "triple-gate",
            "action": "block",
            "triggers": [{"tool_name": "deploy"}],
            "preconditions": [
                {"state_key": "build.passed", "required": True, "message": "Build must pass"},
                {"state_key": "tests.passed", "required": True, "message": "Tests must pass"},
                {"state_key": "review.approved", "required": True, "message": "Review must be approved"},
            ],
        })
        cr = ControlRoom(db_path=tmp_path / "audit.db", policy_dir=pdir)
        cr.db.set_state("build.passed", "true")
        cr.db.set_state("tests.passed", "true")
        # review.approved is NOT set

        result = cr.pre_tool_call("deploy", {})
        assert result is not None
        assert result["action"] == "block"
        assert "review" in result["message"].lower()


# ---------------------------------------------------------------------------
# i. Multiple policies -- block wins
# ---------------------------------------------------------------------------


class TestMultiplePolicies:
    """Two policies: one allows, one blocks -> block wins."""

    def test_block_wins_over_allow(self, tmp_path: Path) -> None:
        """When one policy allows and another blocks, block wins."""
        pdir = tmp_path / "policies"
        pdir.mkdir()
        _make_policy_file(pdir, "01-allow", {
            "name": "permissive",
            "action": "allow",
            "triggers": [{"tool_name": "deploy"}],
            "preconditions": [],
        })
        _make_policy_file(pdir, "02-block", {
            "name": "strict-gate",
            "action": "block",
            "triggers": [{"tool_name": "deploy"}],
            "preconditions": [
                {"state_key": "manager.approved", "required": True, "message": "Manager approval required"},
            ],
        })
        cr = ControlRoom(db_path=tmp_path / "audit.db", policy_dir=pdir)

        result = cr.pre_tool_call("deploy", {})
        assert result is not None
        assert result["action"] == "block"
        assert "manager" in result["message"].lower()


# ---------------------------------------------------------------------------
# j. Policy log entries
# ---------------------------------------------------------------------------


class TestPolicyLogEntries:
    """Verify policy_log table has correct decision entries."""

    def test_policy_log_on_block(self, cr_with_email_policy: ControlRoom) -> None:
        """Blocking a tool writes a policy_log entry with decision='block'."""
        cr = cr_with_email_policy
        cr.pre_tool_call("send_message", {"target": "a@b.com"})

        rows = cr.db.get_policy_rows(decision="block")
        assert len(rows) >= 1
        row = rows[0]
        assert row["policy_name"] == "email-send"
        assert row["tool_name"] == "send_message"
        assert row["decision"] == "block"

    def test_policy_log_on_allow(self, cr_with_email_policy: ControlRoom) -> None:
        """Allowing a tool (preconditions met) writes decision='allow'."""
        cr = cr_with_email_policy
        cr.db.set_state("draft.email.loaded", "true")
        cr.db.set_state("draft.email.approved", "true")
        cr.pre_tool_call("send_message", {"target": "a@b.com"})

        rows = cr.db.get_policy_rows(decision="allow")
        assert len(rows) >= 1
        assert rows[0]["policy_name"] == "email-send"


# ---------------------------------------------------------------------------
# k. Workflow state CRUD
# ---------------------------------------------------------------------------


class TestWorkflowStateCRUD:
    """Set, get, update, and verify persistence of workflow state."""

    def test_set_get(self, cr: ControlRoom) -> None:
        cr.db.set_state("project.phase", "build")
        assert cr.db.get_state("project.phase") == "build"

    def test_update(self, cr: ControlRoom) -> None:
        cr.db.set_state("project.phase", "build")
        cr.db.set_state("project.phase", "deploy")
        assert cr.db.get_state("project.phase") == "deploy"

    def test_persistence(self, tmp_path: Path) -> None:
        """State persists across ControlRoom instances (same DB)."""
        db_path = tmp_path / "persist.db"
        cr1 = ControlRoom(db_path=db_path, policy_dir=tmp_path / "p")
        cr1.db.set_state("key1", "value1")

        # Create a new instance pointing at the same DB
        cr2 = ControlRoom(db_path=db_path, policy_dir=tmp_path / "p")
        assert cr2.db.get_state("key1") == "value1"

    def test_delete(self, cr: ControlRoom) -> None:
        cr.db.set_state("temp.key", "temp")
        assert cr.db.delete_state("temp.key") is True
        assert cr.db.get_state("temp.key") is None

    def test_list_states(self, cr: ControlRoom) -> None:
        cr.db.set_state("a", "1")
        cr.db.set_state("b", "2")
        states = cr.db.list_states()
        assert states == {"a": "1", "b": "2"}


# ---------------------------------------------------------------------------
# l. Condition evaluator
# ---------------------------------------------------------------------------


class TestConditionEvaluator:
    """Test startswith, ==, contains with real-world args."""

    def test_startswith_email_domain(self) -> None:
        assert evaluate_condition("startswith", "admin@company.com", "admin@") is True
        assert evaluate_condition("startswith", "user@company.com", "admin@") is False

    def test_equals_exact_command(self) -> None:
        assert evaluate_condition("==", "git push --force", "git push --force") is True
        assert evaluate_condition("==", "git push", "git push --force") is False

    def test_contains_substring(self) -> None:
        assert evaluate_condition("contains", "rm -rf /important/data", "-rf") is True
        assert evaluate_condition("contains", "ls -la", "-rf") is False

    def test_endswith(self) -> None:
        assert evaluate_condition("endswith", "report.pdf", ".pdf") is True
        assert evaluate_condition("endswith", "report.doc", ".pdf") is False

    def test_not_equals(self) -> None:
        assert evaluate_condition("!=", "dev", "prod") is True
        assert evaluate_condition("!=", "prod", "prod") is False


# ---------------------------------------------------------------------------
# m. HTTP monitoring endpoints
# ---------------------------------------------------------------------------


class TestHTTPMonitoring:
    """Start the server and make real HTTP requests to each endpoint."""

    @pytest.fixture()
    def monitored_cr(self, tmp_path: Path) -> ControlRoom:
        """ControlRoom with monitoring on a random port."""
        pdir = tmp_path / "policies"
        pdir.mkdir()
        _make_policy_file(pdir, "test-pol", {
            "name": "test-policy",
            "description": "A test policy",
            "action": "block",
            "triggers": [{"tool_name": "dangerous_tool"}],
            "preconditions": [{"state_key": "safety.cleared", "required": True}],
        })
        cr = ControlRoom(db_path=tmp_path / "audit.db", policy_dir=pdir)
        port = cr.start_monitoring(port=0)  # random available port
        # Seed some data
        cr.pre_tool_call("read_file", {"path": "/tmp/x"}, task_id="t1")
        cr.post_tool_call("read_file", {"path": "/tmp/x"}, result="ok", task_id="t1")
        cr.pre_tool_call("dangerous_tool", {}, task_id="t2")  # blocked
        cr.db.set_state("workflow.step", "running")
        yield cr
        cr.stop_monitoring()

    def test_api_tool_calls(self, monitored_cr: ControlRoom) -> None:
        """GET /api/tool-calls returns audit log entries."""
        port = monitored_cr._server.server_address[1]
        status, body = _http_get(port, "/api/tool-calls")
        assert status == 200
        assert "tool_calls" in body
        assert len(body["tool_calls"]) >= 2  # pre + post at minimum

    def test_api_blocked(self, monitored_cr: ControlRoom) -> None:
        """GET /api/blocked returns only blocked entries."""
        port = monitored_cr._server.server_address[1]
        status, body = _http_get(port, "/api/blocked")
        assert status == 200
        assert "blocked" in body
        assert len(body["blocked"]) >= 1
        assert body["blocked"][0]["phase"] == "blocked"

    def test_api_workflows(self, monitored_cr: ControlRoom) -> None:
        """GET /api/workflows returns workflow state."""
        port = monitored_cr._server.server_address[1]
        status, body = _http_get(port, "/api/workflows")
        assert status == 200
        assert body["workflows"]["workflow.step"] == "running"

    def test_api_policies(self, monitored_cr: ControlRoom) -> None:
        """GET /api/policies returns loaded policy metadata."""
        port = monitored_cr._server.server_address[1]
        status, body = _http_get(port, "/api/policies")
        assert status == 200
        assert len(body["policies"]) == 1
        assert body["policies"][0]["name"] == "test-policy"
        assert body["policies"][0]["action"] == "block"

    def test_api_404(self, monitored_cr: ControlRoom) -> None:
        """GET /api/unknown returns 404."""
        port = monitored_cr._server.server_address[1]
        status, body = _http_get(port, "/api/unknown")
        assert status == 404
        assert "error" in body


# ---------------------------------------------------------------------------
# n. Full email policy flow
# ---------------------------------------------------------------------------


class TestFullEmailPolicyFlow:
    """End-to-end: load bundled email-send.yaml policy and test against it."""

    def test_bundled_email_policy(self, tmp_path: Path) -> None:
        """Load the bundled email-send.yaml and test the full flow."""
        # Copy the bundled policy to a tmp dir
        bundled = Path(__file__).resolve().parent.parent.parent / "plugins" / "control-room" / "policies" / "email-send.yaml"
        pdir = tmp_path / "policies"
        pdir.mkdir()

        # Read and write the bundled policy
        policy_text = bundled.read_text(encoding="utf-8")
        (pdir / "email-send.yaml").write_text(policy_text)

        cr = ControlRoom(db_path=tmp_path / "audit.db", policy_dir=pdir)

        # Step 1: Attempt to send email -- should be blocked (no draft loaded)
        result = cr.pre_tool_call("send_message", {"target": "ceo@company.com", "body": "Q3 report"})
        assert result is not None
        assert result["action"] == "block"
        assert "loaded" in result["message"].lower()

        # Step 2: Load draft
        cr.db.set_state("draft.email.loaded", "true")

        # Step 3: Attempt again -- still blocked (not approved)
        result = cr.pre_tool_call("send_message", {"target": "ceo@company.com", "body": "Q3 report"})
        assert result is not None
        assert result["action"] == "block"
        assert "approved" in result["message"].lower()

        # Step 4: Approve draft
        cr.db.set_state("draft.email.approved", "true")

        # Step 5: Now it should be allowed
        result = cr.pre_tool_call("send_message", {"target": "ceo@company.com", "body": "Q3 report"})
        assert result is None, "Should be allowed after both preconditions met"

        # Verify audit trail
        pre_rows = cr.db.get_audit_rows(phase="pre")
        blocked_rows = cr.db.get_audit_rows(phase="blocked")
        assert len(pre_rows) == 1  # only the successful call
        assert len(blocked_rows) == 2  # two blocked attempts

        # Verify policy log
        block_logs = cr.db.get_policy_rows(decision="block")
        allow_logs = cr.db.get_policy_rows(decision="allow")
        assert len(block_logs) >= 2
        assert len(allow_logs) >= 1


# ---------------------------------------------------------------------------
# Database resilience
# ---------------------------------------------------------------------------


class TestDatabaseResilience:
    """Concurrent writes, large payloads, and special characters."""

    def test_concurrent_writes(self, tmp_path: Path) -> None:
        """Multiple threads writing audit rows concurrently."""
        cr = ControlRoom(db_path=tmp_path / "audit.db", policy_dir=tmp_path / "p")
        errors: list[Exception] = []

        def writer(thread_id: int) -> None:
            try:
                for i in range(20):
                    cr.db.log_audit(
                        f"tool_{thread_id}", {"i": i}, "pre",
                        task_id=f"thread-{thread_id}",
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent write errors: {errors}"
        rows = cr.db.get_audit_rows(limit=200)
        assert len(rows) == 100  # 5 threads * 20 writes

    def test_large_payload(self, cr: ControlRoom) -> None:
        """Large args and result payloads are handled (truncated result)."""
        large_args = {"data": "x" * 10_000}
        cr.pre_tool_call("bulk_process", large_args)
        cr.post_tool_call("bulk_process", large_args, result="y" * 10_000)

        rows = cr.db.get_audit_rows(phase="post")
        assert len(rows) == 1
        # Result is truncated to 4096 chars
        assert len(rows[0]["result"]) == 4096

    def test_special_characters(self, cr: ControlRoom) -> None:
        """Unicode and special characters in args and state."""
        cr.db.set_state("key.with.dots", "value with spaces & 'quotes'")
        assert cr.db.get_state("key.with.dots") == "value with spaces & 'quotes'"

        cr.db.log_audit(
            "tool", {"msg": "Hello\n\tworld\x00end"}, "pre",
        )
        rows = cr.db.get_audit_rows(phase="pre")
        assert len(rows) == 1

        # Unicode
        cr.db.set_state("emoji.key", "Status: complete")
        assert cr.db.get_state("emoji.key") == "Status: complete"

        cr.db.log_audit(
            "translate", {"text": "Hola mundo"}, "pre",
        )
        rows = cr.db.get_audit_rows(tool_name="translate")
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Email-blocking policies (terminal, write_file, patch, execute_code)
# ---------------------------------------------------------------------------


class TestEmailBlockPolicies:
    """E2E tests for the bundled email-blocking policies.

    Loads all four email-block policy files (terminal, write_file, patch,
    execute_code) from plugins/control-room/policies/ and verifies that
    email-sending patterns are blocked while innocent commands pass through.
    """

    @pytest.fixture()
    def cr_email_block(self, tmp_path: Path) -> ControlRoom:
        """ControlRoom with all bundled email-blocking policies loaded."""
        policy_src = (
            Path(__file__).resolve().parent.parent.parent
            / "plugins" / "control-room" / "policies"
        )
        pdir = tmp_path / "policies"
        pdir.mkdir()

        for name in (
            "terminal-email-block",
            "writefile-email-block",
            "patch-email-block",
            "execute-code-email-block",
        ):
            src = policy_src / f"{name}.yaml"
            (pdir / f"{name}.yaml").write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8",
            )

        return ControlRoom(db_path=tmp_path / "audit.db", policy_dir=pdir)

    # -- Blocked scenarios ---------------------------------------------------

    def test_terminal_python_smtplib_blocked(self, cr_email_block: ControlRoom) -> None:
        """terminal with python -c 'import smtplib' is blocked."""
        result = cr_email_block.pre_tool_call(
            "terminal", {"command": "python -c 'import smtplib'"},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_terminal_node_nodemailer_blocked(self, cr_email_block: ControlRoom) -> None:
        """terminal with node requiring nodemailer is blocked."""
        result = cr_email_block.pre_tool_call(
            "terminal", {"command": 'node -e "require(\'nodemailer\')"'},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_terminal_sendmail_blocked(self, cr_email_block: ControlRoom) -> None:
        """terminal with command containing sendMail is blocked."""
        result = cr_email_block.pre_tool_call(
            "terminal", {"command": "curl -X POST https://api.example.com/sendMail"},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_write_file_smtplib_blocked(self, cr_email_block: ControlRoom) -> None:
        """write_file with content importing smtplib is blocked."""
        result = cr_email_block.pre_tool_call(
            "write_file",
            {"content": "import smtplib\nfrom email.mime.text import MIMEText"},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_write_file_nodemailer_blocked(self, cr_email_block: ControlRoom) -> None:
        """write_file with content using nodemailer.createTransport is blocked."""
        result = cr_email_block.pre_tool_call(
            "write_file",
            {"content": "const transport = nodemailer.createTransport({})"},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_patch_smtp_blocked(self, cr_email_block: ControlRoom) -> None:
        """patch with new_string containing smtplib.SMTP( is blocked."""
        result = cr_email_block.pre_tool_call(
            "patch", {"new_string": 'server = smtplib.SMTP("localhost", 25)'},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_execute_code_smtplib_blocked(self, cr_email_block: ControlRoom) -> None:
        """execute_code with code importing smtplib is blocked."""
        result = cr_email_block.pre_tool_call(
            "execute_code", {"code": "import smtplib"},
        )
        assert result is not None
        assert result["action"] == "block"

    # -- Passthrough scenarios -----------------------------------------------

    def test_terminal_ls_allowed(self, cr_email_block: ControlRoom) -> None:
        """terminal with 'ls -la' passes through (no email pattern)."""
        result = cr_email_block.pre_tool_call(
            "terminal", {"command": "ls -la"},
        )
        assert result is None

    def test_terminal_python_test_allowed(self, cr_email_block: ControlRoom) -> None:
        """terminal with 'python test.py' passes through."""
        result = cr_email_block.pre_tool_call(
            "terminal", {"command": "python test.py"},
        )
        assert result is None

    def test_write_file_console_log_allowed(self, cr_email_block: ControlRoom) -> None:
        """write_file with innocent JS content passes through."""
        result = cr_email_block.pre_tool_call(
            "write_file", {"content": "console.log('hello')"},
        )
        assert result is None

    def test_patch_return_allowed(self, cr_email_block: ControlRoom) -> None:
        """patch with harmless new_string passes through."""
        result = cr_email_block.pre_tool_call(
            "patch", {"new_string": "return result"},
        )
        assert result is None

    def test_execute_code_print_allowed(self, cr_email_block: ControlRoom) -> None:
        """execute_code with innocent Python code passes through."""
        result = cr_email_block.pre_tool_call(
            "execute_code", {"code": "print('hello')"},
        )
        assert result is None

    def test_send_message_not_blocked_by_email_block_policies(
        self, cr_email_block: ControlRoom,
    ) -> None:
        """send_message is NOT blocked by these policies (separate policy)."""
        result = cr_email_block.pre_tool_call(
            "send_message", {"target": "email:user@test.com"},
        )
        assert result is None

    # -- pm_os email tool blocking ---------------------------------------------

    def test_terminal_outlook_send_mail_blocked(self, cr_email_block: ControlRoom) -> None:
        """terminal invoking outlook-send-mail.js is blocked."""
        result = cr_email_block.pre_tool_call(
            "terminal",
            {"command": "node ~/Code/pm_os/bin/outlook-send-mail.js --to foo@bar.com --subject test --body hi"},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_terminal_outlook_read_mail_blocked(self, cr_email_block: ControlRoom) -> None:
        """terminal invoking outlook-read-mail.js is blocked."""
        result = cr_email_block.pre_tool_call(
            "terminal",
            {"command": "node ~/Code/pm_os/bin/outlook-read-mail.js"},
        )
        assert result is not None
        assert result["action"] == "block"


# ---------------------------------------------------------------------------
# Token read-deny policies (terminal, write_file, patch, execute_code)
# ---------------------------------------------------------------------------


class TestTokenReadDenyPolicies:
    """E2E tests for FOCI token read-deny policies.

    Loads all four token-read-deny policy files from
    plugins/control-room/policies/ and verifies that references to
    FOCI token file paths are blocked while normal operations pass.
    """

    @pytest.fixture()
    def cr_token_deny(self, tmp_path: Path) -> ControlRoom:
        """ControlRoom with all bundled token-read-deny policies loaded."""
        policy_src = (
            Path(__file__).resolve().parent.parent.parent
            / "plugins" / "control-room" / "policies"
        )
        pdir = tmp_path / "policies"
        pdir.mkdir()

        for name in (
            "terminal-token-read-deny",
            "writefile-token-read-deny",
            "patch-token-read-deny",
            "execute-code-token-read-deny",
        ):
            src = policy_src / f"{name}.yaml"
            (pdir / f"{name}.yaml").write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8",
            )

        return ControlRoom(db_path=tmp_path / "audit.db", policy_dir=pdir)

    # -- Blocked scenarios ---------------------------------------------------

    def test_terminal_cat_foci_token_blocked(self, cr_token_deny: ControlRoom) -> None:
        """terminal with 'cat ~/.pm-os-foci-token.json' is blocked."""
        result = cr_token_deny.pre_tool_call(
            "terminal",
            {"command": "cat ~/.pm-os-foci-token.json"},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_terminal_cat_office_token_blocked(self, cr_token_deny: ControlRoom) -> None:
        """terminal with 'cat ~/.pm-os-foci-office-token.json' is blocked."""
        result = cr_token_deny.pre_tool_call(
            "terminal",
            {"command": "cat ~/.pm-os-foci-office-token.json"},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_terminal_node_script_reading_token_blocked(self, cr_token_deny: ControlRoom) -> None:
        """terminal running a node script that reads the token file is blocked."""
        result = cr_token_deny.pre_tool_call(
            "terminal",
            {"command": "node -e \"const t = require('fs').readFileSync('pm-os-foci-token.json')\""},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_write_file_token_reference_blocked(self, cr_token_deny: ControlRoom) -> None:
        """write_file with content referencing pm-os-foci-token is blocked."""
        result = cr_token_deny.pre_tool_call(
            "write_file",
            {"content": "const token = JSON.parse(fs.readFileSync('~/.pm-os-foci-token.json'))"},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_write_file_office_token_reference_blocked(self, cr_token_deny: ControlRoom) -> None:
        """write_file with content referencing pm-os-foci-office-token is blocked."""
        result = cr_token_deny.pre_tool_call(
            "write_file",
            {"content": "token_path = os.path.expanduser('~/.pm-os-foci-office-token.json')"},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_patch_token_reference_blocked(self, cr_token_deny: ControlRoom) -> None:
        """patch with new_string referencing pm-os-foci-token is blocked."""
        result = cr_token_deny.pre_tool_call(
            "patch",
            {"new_string": "const creds = require('./pm-os-foci-token.json')"},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_patch_content_token_reference_blocked(self, cr_token_deny: ControlRoom) -> None:
        """patch with patch field referencing pm-os-foci-office-token is blocked."""
        result = cr_token_deny.pre_tool_call(
            "patch",
            {"patch": "+token_file = 'pm-os-foci-office-token.json'"},
        )
        assert result is not None
        assert result["action"] == "block"

    def test_execute_code_token_reference_blocked(self, cr_token_deny: ControlRoom) -> None:
        """execute_code with code reading pm-os-foci-token is blocked."""
        result = cr_token_deny.pre_tool_call(
            "execute_code",
            {"code": "import json; t = json.load(open('pm-os-foci-token.json'))"},
        )
        assert result is not None
        assert result["action"] == "block"

    # -- Passthrough scenarios -----------------------------------------------

    def test_terminal_ls_allowed(self, cr_token_deny: ControlRoom) -> None:
        """terminal with 'ls -la' passes through (no token pattern)."""
        result = cr_token_deny.pre_tool_call(
            "terminal", {"command": "ls -la"},
        )
        assert result is None

    def test_terminal_cat_other_file_allowed(self, cr_token_deny: ControlRoom) -> None:
        """terminal with 'cat config.json' passes through."""
        result = cr_token_deny.pre_tool_call(
            "terminal", {"command": "cat config.json"},
        )
        assert result is None

    def test_write_file_normal_code_allowed(self, cr_token_deny: ControlRoom) -> None:
        """write_file with innocent content passes through."""
        result = cr_token_deny.pre_tool_call(
            "write_file", {"content": "console.log('hello world')"},
        )
        assert result is None

    def test_patch_normal_code_allowed(self, cr_token_deny: ControlRoom) -> None:
        """patch with harmless new_string passes through."""
        result = cr_token_deny.pre_tool_call(
            "patch", {"new_string": "return result"},
        )
        assert result is None

    def test_execute_code_normal_allowed(self, cr_token_deny: ControlRoom) -> None:
        """execute_code with innocent Python code passes through."""
        result = cr_token_deny.pre_tool_call(
            "execute_code", {"code": "print('hello')"},
        )
        assert result is None
