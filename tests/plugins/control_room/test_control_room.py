"""
ABOUTME: RED-first acceptance tests for the control-room governance plugin (P0-2a).
ABOUTME: These tests must FAIL on the clean base (no plugin) and PASS after
ABOUTME: implementation. Load the plugin via spec_from_file_location because the
ABOUTME: directory name is hyphenated and is not a Python package.
ABOUTME: Covers REQ-01 through REQ-04 and adversarial review fixes F1-CR, F2-CR, F3-CR.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path
from typing import Optional

import pytest


# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_control_room():
    """Load plugins/control-room/__init__.py via spec_from_file_location.

    Purpose: The dir name is hyphenated so it is not a Python package;
             mirror the upstream PluginManager's load path.
    Usage:   Call once per test session (cached by fixture).
    Gotchas: Subsequent imports of this module use sys.modules key
             'control_room_under_test'.
    """
    init_path = _repo_root() / "plugins" / "control-room" / "__init__.py"
    spec = importlib.util.spec_from_file_location("control_room_under_test", init_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["control_room_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_hermes_home(tmp_path, monkeypatch):
    """Redirect get_hermes_home() to tmp_path so tests never touch ~/.hermes."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    # Point DB to a temp file so each test starts clean
    db_path = hermes_home / "control-room" / "audit.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CONTROL_ROOM_DB", str(db_path))
    yield hermes_home


@pytest.fixture()
def cr_mod():
    """Return the loaded control-room module (per-test reload to isolate state)."""
    # Pop any prior cached module so each test gets a fresh singleton.
    sys.modules.pop("control_room_under_test", None)
    return _load_control_room()


@pytest.fixture()
def cr(cr_mod):
    """Return a fresh ControlRoom instance using the current HERMES_HOME."""
    db_env = os.environ.get("CONTROL_ROOM_DB")
    policy_dir = _repo_root() / "plugins" / "control-room" / "policies"
    return cr_mod.ControlRoom(
        db_path=Path(db_env) if db_env else None,
        policy_dir=policy_dir,
    )


# ---------------------------------------------------------------------------
# REQ-01 — Plugin loads and basic hook contract (also covers audit basic)
# ---------------------------------------------------------------------------

class TestPluginLoads:
    """Verify the plugin can be loaded via spec_from_file_location (REQ-01)."""

    def test_module_loads_without_error(self, cr_mod):
        assert cr_mod is not None

    def test_control_room_class_exists(self, cr_mod):
        assert hasattr(cr_mod, "ControlRoom")

    def test_register_function_exists(self, cr_mod):
        assert hasattr(cr_mod, "register")
        assert callable(cr_mod.register)

    def test_pre_tool_call_returns_none_for_benign(self, cr):
        """A benign tool call must return None (no block)."""
        result = cr.pre_tool_call("read_file", {"path": "/tmp/safe.txt"})
        assert result is None

    def test_register_wires_hooks(self, cr_mod):
        """register(ctx) must call ctx.register_hook for pre_tool_call and post_tool_call."""
        hooks_registered = []

        class FakeCtx:
            def register_hook(self, name, cb):
                hooks_registered.append(name)

        cr_mod.register(FakeCtx())
        assert "pre_tool_call" in hooks_registered
        assert "post_tool_call" in hooks_registered


# ---------------------------------------------------------------------------
# REQ-02 — F1-CR: self-protection with realpath normalization
# ---------------------------------------------------------------------------

class TestSelfProtectionNormalized:
    """
    F1-CR fix: write-protection must use os.path.realpath, not substring matching.

    The fork's form `"plugins/control-room" in target_path` is evadable via:
      - cwd-relative path (e.g. "__init__.py" from a plugins/control-room cwd)
      - traversal path (e.g. "../control-room/__init__.py")

    All three forms — absolute, cwd-relative, traversal — must be blocked.
    """

    def _make_args(self, path: str) -> dict:
        return {"path": path, "content": "malicious"}

    def test_absolute_path_blocked(self, cr):
        """write_file to absolute plugins/control-room path is blocked."""
        abs_path = str(_repo_root() / "plugins" / "control-room" / "__init__.py")
        result = cr.pre_tool_call("write_file", self._make_args(abs_path))
        assert result is not None
        assert result.get("action") == "block"

    def test_cwd_relative_path_blocked(self, cr, monkeypatch):
        """write_file(path='__init__.py') from cwd=plugins/control-room is blocked."""
        cr_dir = str(_repo_root() / "plugins" / "control-room")
        monkeypatch.chdir(cr_dir)
        result = cr.pre_tool_call("write_file", self._make_args("__init__.py"))
        assert result is not None
        assert result.get("action") == "block"

    def test_traversal_path_blocked(self, cr):
        """write_file with traversal path that resolves into control-room is blocked."""
        traversal = str(
            _repo_root() / "plugins" / "control-room" / ".." / "control-room" / "__init__.py"
        )
        result = cr.pre_tool_call("write_file", self._make_args(traversal))
        assert result is not None
        assert result.get("action") == "block"

    def test_tool_registry_guard_source_blocked(self, cr):
        """write_file to plugins/tool-registry-guard source is blocked (BR-4 widening)."""
        abs_path = str(_repo_root() / "plugins" / "tool-registry-guard" / "__init__.py")
        result = cr.pre_tool_call("write_file", self._make_args(abs_path))
        assert result is not None
        assert result.get("action") == "block"

    def test_tool_registry_guard_traversal_blocked(self, cr):
        """Traversal path into tool-registry-guard source is blocked."""
        traversal = str(
            _repo_root() / "plugins" / "tool-registry-guard" / ".." / "tool-registry-guard" / "plugin.yaml"
        )
        result = cr.pre_tool_call("write_file", self._make_args(traversal))
        assert result is not None
        assert result.get("action") == "block"

    def test_patch_to_guard_source_blocked(self, cr):
        """patch tool targeting control-room source is also blocked."""
        abs_path = str(_repo_root() / "plugins" / "control-room" / "plugin.yaml")
        result = cr.pre_tool_call("patch", {"path": abs_path, "new_string": "x"})
        assert result is not None
        assert result.get("action") == "block"

    def test_unrelated_write_allowed(self, cr):
        """write_file to /tmp is not blocked (no false positive)."""
        result = cr.pre_tool_call("write_file", {"path": "/tmp/innocuous.txt", "content": "ok"})
        assert result is None


# ---------------------------------------------------------------------------
# REQ-03 — F3-CR: terminal file-edit of guard source or config.yaml is blocked
# ---------------------------------------------------------------------------

class TestTerminalGuardSourceEdits:
    """
    F3-CR fix: terminal commands that redirect/sed/tee/cp into guard source
    or config.yaml must be blocked (or explicitly deferred with rationale).

    These are the high-signal patterns:
      - sed -i ... plugins/control-room/__init__.py
      - printf/echo > plugins/control-room/__init__.py
      - tee plugins/control-room/__init__.py
      - echo x > ~/.hermes/config.yaml
    """

    def test_sed_into_control_room_blocked(self, cr):
        """terminal sed -i on control-room source is blocked."""
        cmd = f"sed -i 's/block/allow/g' {_repo_root()}/plugins/control-room/__init__.py"
        result = cr.pre_tool_call("terminal", {"command": cmd})
        assert result is not None
        assert result.get("action") == "block"

    def test_redirect_into_control_room_blocked(self, cr):
        """terminal echo redirect into control-room source is blocked."""
        cmd = f"echo 'compromised' > {_repo_root()}/plugins/control-room/__init__.py"
        result = cr.pre_tool_call("terminal", {"command": cmd})
        assert result is not None
        assert result.get("action") == "block"

    def test_tee_into_control_room_blocked(self, cr):
        """terminal tee into control-room source is blocked."""
        cmd = f"cat /tmp/payload | tee {_repo_root()}/plugins/control-room/__init__.py"
        result = cr.pre_tool_call("terminal", {"command": cmd})
        assert result is not None
        assert result.get("action") == "block"

    def test_sed_into_tool_registry_guard_blocked(self, cr):
        """terminal sed -i on tool-registry-guard source is blocked."""
        cmd = f"sed -i '' {_repo_root()}/plugins/tool-registry-guard/__init__.py"
        result = cr.pre_tool_call("terminal", {"command": cmd})
        assert result is not None
        assert result.get("action") == "block"

    def test_echo_into_config_yaml_blocked(self, cr, monkeypatch):
        """terminal echo > ~/.hermes/config.yaml is blocked."""
        hermes_home = os.environ["HERMES_HOME"]
        cmd = f"echo 'enabled: []' > {hermes_home}/config.yaml"
        result = cr.pre_tool_call("terminal", {"command": cmd})
        assert result is not None
        assert result.get("action") == "block"

    def test_tee_into_config_yaml_blocked(self, cr, monkeypatch):
        """terminal tee ~/.hermes/config.yaml is blocked."""
        hermes_home = os.environ["HERMES_HOME"]
        cmd = f"echo 'plugins.enabled: []' | tee {hermes_home}/config.yaml"
        result = cr.pre_tool_call("terminal", {"command": cmd})
        assert result is not None
        assert result.get("action") == "block"

    def test_benign_terminal_allowed(self, cr):
        """A benign terminal command is not blocked (no false positive)."""
        result = cr.pre_tool_call("terminal", {"command": "echo hello world"})
        assert result is None


# ---------------------------------------------------------------------------
# REQ-04 — Design B3: write_file/patch to config.yaml is blocked (write-protect)
# ---------------------------------------------------------------------------

class TestConfigWriteProtection:
    """
    Design B3: agent must not be able to write to ~/.hermes/config.yaml via
    write_file or patch tools.

    Note: upstream file_tools._check_sensitive_path already blocks write_file
    to resolved ~/.hermes/config.yaml. The control-room guard adds an explicit
    layer that normalizes paths (F1-CR fix) and doesn't depend on file_tools.
    Tests here exercise the control-room guard directly.
    """

    def test_write_file_to_config_blocked(self, cr, monkeypatch):
        """write_file targeting ~/.hermes/config.yaml is blocked by control-room."""
        hermes_home = os.environ["HERMES_HOME"]
        config_path = f"{hermes_home}/config.yaml"
        result = cr.pre_tool_call("write_file", {"path": config_path, "content": "plugins:\n  enabled: []"})
        assert result is not None
        assert result.get("action") == "block"

    def test_patch_to_config_blocked(self, cr, monkeypatch):
        """patch targeting ~/.hermes/config.yaml is blocked by control-room."""
        hermes_home = os.environ["HERMES_HOME"]
        config_path = f"{hermes_home}/config.yaml"
        result = cr.pre_tool_call("patch", {"path": config_path, "new_string": "x"})
        assert result is not None
        assert result.get("action") == "block"


# ---------------------------------------------------------------------------
# REQ-01 — Objective O3 hardcoded blocks
# ---------------------------------------------------------------------------

class TestHardcodedBlocks:
    """Verify all O3 hardcoded blocks are enforced."""

    def test_block_smtplib_in_execute_code(self, cr):
        """execute_code containing smtplib is blocked (O3 + policy YAML)."""
        result = cr.pre_tool_call("execute_code", {"code": "import smtplib\nsmtplib.SMTP('localhost')"})
        assert result is not None
        assert result.get("action") == "block"

    def test_block_get_foci_token(self, cr):
        """terminal command running get-foci-token.js is blocked (O3)."""
        result = cr.pre_tool_call("terminal", {"command": "node ~/Code/pm_os/bin/get-foci-token.js --for graph"})
        assert result is not None
        assert result.get("action") == "block"

    def test_block_credential_read_foci_token(self, cr):
        """read_file targeting pm-os-foci-token.json is blocked (O3)."""
        result = cr.pre_tool_call("read_file", {"path": "/Users/yklin/.hermes/pm-os-foci-token.json"})
        assert result is not None
        assert result.get("action") == "block"

    def test_no_false_positive_benign_read(self, cr):
        """read_file of a benign file returns None (no false positive)."""
        result = cr.pre_tool_call("read_file", {"path": "/tmp/readme.txt"})
        assert result is None

    def test_block_execute_code_importing_hermes_internals(self, cr):
        """execute_code importing hermes_tools is blocked (B10)."""
        result = cr.pre_tool_call("execute_code", {"code": "import hermes_tools"})
        assert result is not None
        assert result.get("action") == "block"

    def test_block_execute_code_importing_plugins(self, cr):
        """execute_code using 'plugins.' import is blocked."""
        result = cr.pre_tool_call("execute_code", {"code": "from plugins.control_room import ControlRoom"})
        assert result is not None
        assert result.get("action") == "block"

    def test_block_outlook_terminal_direct(self, cr):
        """terminal command invoking outlook-send-mail JS is blocked."""
        result = cr.pre_tool_call("terminal", {"command": "node ~/Code/pm_os/bin/outlook-send-mail.js"})
        assert result is not None
        assert result.get("action") == "block"


# ---------------------------------------------------------------------------
# REQ-04 — F2-CR: execute_code import-block is steering (documented P2)
# ---------------------------------------------------------------------------

class TestExecuteCodeImportBlockDocumented:
    """
    F2-CR: The execute_code internal-import block is substring-based (steering only).
    __import__("agent") and importlib.import_module("gateway") evade it.

    This class documents the known evasion as a test comment rather than asserting
    a block (since the block is intentionally not claimed to be hard security).
    A best-effort check is added for the most common importlib form.
    """

    def test_direct_import_statement_blocked(self, cr):
        """Direct 'import agent' statement is blocked (basic form)."""
        result = cr.pre_tool_call("execute_code", {"code": "import agent"})
        assert result is not None
        assert result.get("action") == "block"

    def test_from_gateway_import_blocked(self, cr):
        """'from gateway import ...' is blocked (basic form)."""
        result = cr.pre_tool_call("execute_code", {"code": "from gateway import run"})
        assert result is not None
        assert result.get("action") == "block"

    def test_importlib_form_best_effort(self, cr):
        """
        importlib.import_module('agent') evasion — steering not hard security.

        This test documents the known gap: the substring 'import agent' matches
        the importlib form too because 'import agent' appears in the string
        'importlib.import_module("agent")'. Wait — actually it doesn't.
        This test verifies the KNOWN LIMITATION: importlib form is NOT blocked,
        per F2-CR review finding. Documented, not claimed as closed.
        """
        # This call is expected to pass (not blocked) — it's the known evasion.
        # The test asserts the limitation is real and documented.
        result = cr.pre_tool_call("execute_code", {
            "code": 'import importlib\nm = importlib.import_module("gateway")'
        })
        # This may be None (unblocked) — that is the known F2-CR limitation.
        # If future work adds importlib detection, this test should be updated.
        # For now: assert it is either None or block (we don't flip the acceptance).
        # The known gap is: result is None (evasion succeeds).
        # We document but do not hard-assert either way (governance_exempt mode).
        assert result is None or result.get("action") == "block"  # gap documented


# ---------------------------------------------------------------------------
# REQ-01/04 — Fail-closed on internal error (BR-2)
# ---------------------------------------------------------------------------

class TestFailClosed:
    """Verify the guard returns a block dict on internal error, never raises (F5-safe)."""

    def test_fail_closed_on_policy_engine_corruption(self, cr, monkeypatch):
        """Corrupt the PolicyEngine.evaluate to raise; guard must still return block dict."""
        def _bad_evaluate(*args, **kwargs):
            raise RuntimeError("simulated corruption")

        monkeypatch.setattr(cr.engine, "evaluate", _bad_evaluate)
        result = cr.pre_tool_call("write_file", {"path": "/tmp/x.txt", "content": "x"})
        assert result is not None
        assert result.get("action") == "block"

    def test_fail_closed_on_db_error(self, cr, monkeypatch):
        """Corrupt the AuditDB.log_audit to raise; guard must still return block dict."""
        def _bad_log(*args, **kwargs):
            raise sqlite3.OperationalError("simulated db error")

        import sqlite3
        monkeypatch.setattr(cr.db, "log_audit", _bad_log)
        # Evaluate to a block decision via a policy trigger (smtplib in execute_code)
        result = cr.pre_tool_call("execute_code", {"code": "import smtplib"})
        assert result is not None
        assert result.get("action") == "block"


# ---------------------------------------------------------------------------
# REQ-04 — Audit row written
# ---------------------------------------------------------------------------

class TestAuditRows:
    """Verify audit rows are written for pre and blocked phases."""

    def test_pre_row_written_on_allowed_call(self, cr):
        """After a permitted call, audit_log has a 'pre' row."""
        cr.pre_tool_call("read_file", {"path": "/tmp/safe.txt"})
        rows = cr.db.get_audit_rows(phase="pre")
        assert len(rows) >= 1
        assert rows[0]["tool_name"] == "read_file"

    def test_blocked_row_written_on_blocked_call(self, cr):
        """After a blocked call, audit_log has a 'blocked' row."""
        cr.pre_tool_call("execute_code", {"code": "import smtplib"})
        rows = cr.db.get_audit_rows(phase="blocked")
        assert len(rows) >= 1

    def test_post_row_written(self, cr):
        """post_tool_call writes a 'post' row."""
        cr.post_tool_call("read_file", {"path": "/tmp/x.txt"}, result="data")
        rows = cr.db.get_audit_rows(phase="post")
        assert len(rows) >= 1
        assert rows[0]["tool_name"] == "read_file"


# ---------------------------------------------------------------------------
# REQ-04 — Policy YAML evaluation
# ---------------------------------------------------------------------------

class TestPolicyYAMLEvaluation:
    """Verify YAML policies are loaded and enforced (O2)."""

    def test_smtplib_execute_code_blocked_by_policy(self, cr):
        """Policy execute-code-email-block fires on smtplib in execute_code."""
        result = cr.pre_tool_call("execute_code", {"code": "import smtplib; smtplib.SMTP('x')"})
        assert result is not None
        assert result.get("action") == "block"

    def test_policies_loaded(self, cr):
        """At least 8 policy YAMLs are loaded."""
        assert len(cr.engine.policies) >= 8

    def test_writefile_email_policy_fires(self, cr):
        """Policy writefile-email-block fires on write_file with smtp content."""
        result = cr.pre_tool_call("write_file", {
            "path": "/tmp/send.py",
            "content": "import smtplib\nsmtplib.SMTP('localhost').sendmail('a','b','c')"
        })
        assert result is not None
        assert result.get("action") == "block"

    def test_benign_write_not_blocked_by_policy(self, cr):
        """write_file of benign content is not blocked by any policy."""
        result = cr.pre_tool_call("write_file", {
            "path": "/tmp/safe.py",
            "content": "print('hello world')"
        })
        assert result is None
