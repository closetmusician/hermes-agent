"""
ABOUTME: Tests for the tool-registry-guard plugin (P0-2b).
ABOUTME: Exercises T1 (ban pip/import), T2 (pre_llm_call context injection),
ABOUTME: T3 (audit JSONL), and registry-load behaviour from default-registry.yaml.
ABOUTME: Plugin loaded via importlib.util.spec_from_file_location (hyphenated dir).
ABOUTME: RED first: these tests failed before plugins/tool-registry-guard/ existed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers — load the plugin module via file path (hyphenated dir is not importable)
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    """Return the repository root (3 parents up from this test file)."""
    return Path(__file__).resolve().parents[3]


def _load_plugin(hermes_home: Path):
    """Load plugins/tool-registry-guard/__init__.py via spec_from_file_location.

    Sets HERMES_HOME so the plugin resolves its audit path and registry
    search to the temp dir. Registers the module under a deterministic
    name so repeated calls within a session reuse it cleanly after
    module-state reset.

    Args:
        hermes_home: Temporary HERMES_HOME directory to point the plugin at.

    Returns:
        Loaded module object.
    """
    plugin_dir = _repo_root() / "plugins" / "tool-registry-guard"
    mod_name = "hermes_plugins_trg_under_test"

    # Register a namespace parent so relative-import machinery is happy
    # (this plugin has no relative imports, but mirror the established pattern).
    if "hermes_plugins_trg" not in sys.modules:
        ns = types.ModuleType("hermes_plugins_trg")
        ns.__path__ = []
        sys.modules["hermes_plugins_trg"] = ns

    spec = importlib.util.spec_from_file_location(
        mod_name,
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = mod_name
    mod.__path__ = [str(plugin_dir)]
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _reset_module_state(mod) -> None:
    """Reset module-level globals to a clean state between tests.

    The plugin module uses module globals for registry, patterns, and
    audit path. This avoids cross-test pollution when the same module
    object is reused.

    Args:
        mod: The loaded plugin module to reset.
    """
    mod._registry = None
    mod._banned_packages = set()
    mod._banned_imports = set()
    mod._import_to_tool = {}
    mod._package_to_tool = {}
    mod._llm_context_block = ""
    mod._audit_path = None
    mod._pip_pattern = None
    mod._import_pattern = None
    mod._inline_import_pattern = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    """Provide an isolated HERMES_HOME directory for each test.

    Redirects both the environment variable and the Path.home() fallback
    so the plugin always resolves to a predictable temp location.
    """
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


@pytest.fixture()
def plugin(hermes_home):
    """Load the plugin module and reset its state for a clean test.

    Uses the default-registry.yaml bundled with the plugin (no user
    override in the temp HERMES_HOME). Returns the module after _load_registry
    has populated the module globals.
    """
    mod = _load_plugin(hermes_home)
    _reset_module_state(mod)
    mod._load_registry()
    return mod


# ---------------------------------------------------------------------------
# REQ-01 / REQ-04 — T1: block banned pip install (B12)
# ---------------------------------------------------------------------------

class TestBlockPipInstall:
    """T1: pip install of banned packages must be blocked."""

    def test_block_pip_install_pandas(self, plugin):
        """pip install pandas must be blocked; block message names graph-workbook."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install pandas"},
        )
        assert result is not None, "Expected block; got None (allowed)"
        assert result.get("action") == "block"
        assert "graph-workbook" in result["message"]

    def test_block_pip3_install_openpyxl(self, plugin):
        """pip3 install openpyxl must be blocked."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip3 install openpyxl"},
        )
        assert result is not None
        assert result.get("action") == "block"

    def test_block_pip_install_python_docx(self, plugin):
        """pip install python-docx must be blocked; block message names ooxml-surgery."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install python-docx"},
        )
        assert result is not None
        assert result.get("action") == "block"
        assert "ooxml-surgery" in result["message"]

    def test_block_pip_install_python_pptx(self, plugin):
        """pip install python-pptx must be blocked; block message names graph-edit-pptx."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install python-pptx"},
        )
        assert result is not None
        assert result.get("action") == "block"
        assert "graph-edit-pptx" in result["message"]

    def test_no_false_positive_pip_install_requests(self, plugin):
        """pip install requests is not banned — must be allowed (returns None)."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install requests"},
        )
        assert result is None, f"False positive: {result}"

    def test_no_false_positive_non_terminal_tool(self, plugin):
        """A read_file call with 'pip install pandas' in path is NOT a terminal — no block."""
        result = plugin._pre_tool_call(
            tool_name="read_file",
            args={"path": "pip install pandas"},
        )
        assert result is None


# ---------------------------------------------------------------------------
# REQ-01 / REQ-04 — T1: block banned import in write_file / patch
# ---------------------------------------------------------------------------

class TestBlockBannedImportInContent:
    """T1: import of banned packages in write_file/patch content must be blocked."""

    def test_block_import_openpyxl_in_write_file(self, plugin):
        """write_file with 'import openpyxl' in content must be blocked."""
        result = plugin._pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/foo.py", "content": "import openpyxl\nwb = openpyxl.load_workbook('f.xlsx')"},
        )
        assert result is not None
        assert result.get("action") == "block"

    def test_block_from_docx_import_in_patch(self, plugin):
        """patch with 'from docx import Document' must be blocked."""
        result = plugin._pre_tool_call(
            tool_name="patch",
            args={"content": "from docx import Document\nd = Document()"},
        )
        assert result is not None
        assert result.get("action") == "block"

    def test_block_import_pandas_in_write_file(self, plugin):
        """write_file with 'import pandas as pd' must be blocked."""
        result = plugin._pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/foo.py", "content": "import pandas as pd\ndf = pd.read_csv('f.csv')"},
        )
        assert result is not None
        assert result.get("action") == "block"

    def test_no_false_positive_import_os(self, plugin):
        """write_file with 'import os' must be allowed (benign import)."""
        result = plugin._pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/foo.py", "content": "import os\nimport sys\nprint(os.getcwd())"},
        )
        assert result is None, f"False positive on benign import: {result}"

    def test_no_false_positive_import_json(self, plugin):
        """write_file with 'import json' must be allowed."""
        result = plugin._pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/foo.py", "content": "import json\ndata = json.loads('{}')"},
        )
        assert result is None


# ---------------------------------------------------------------------------
# REQ-01 / REQ-04 — T1: block inline python -c import
# ---------------------------------------------------------------------------

class TestBlockInlinePythonImport:
    """T1: python -c "import <banned>" in terminal must be blocked."""

    def test_block_inline_python_import_docx(self, plugin):
        """terminal with python -c "import docx" must be blocked."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": 'python -c "import docx; print(docx.__version__)"'},
        )
        assert result is not None
        assert result.get("action") == "block"

    def test_block_inline_python3_import_pptx(self, plugin):
        """terminal with python3 -c "import pptx" must be blocked."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "python3 -c 'import pptx'"},
        )
        assert result is not None
        assert result.get("action") == "block"

    def test_no_false_positive_python_c_import_os(self, plugin):
        """terminal with python -c "import os" must be allowed."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": 'python -c "import os; print(os.getcwd())"'},
        )
        assert result is None


# ---------------------------------------------------------------------------
# REQ-01 / REQ-04 — T2: pre_llm_call injects registry context
# ---------------------------------------------------------------------------

class TestPreLlmCallInjectsContext:
    """T2: _pre_llm_call must return {"context": <table>} with registry content."""

    def test_returns_context_dict(self, plugin):
        """_pre_llm_call must return a dict with a non-empty 'context' key."""
        result = plugin._pre_llm_call(user_message="write me some Excel code")
        assert result is not None
        assert isinstance(result, dict)
        assert "context" in result
        assert isinstance(result["context"], str)
        assert result["context"]

    def test_context_contains_tool_registry_header(self, plugin):
        """Injected context must contain the TOOL REGISTRY header."""
        result = plugin._pre_llm_call(user_message="x")
        assert "TOOL REGISTRY" in result["context"]

    def test_context_contains_banned_package(self, plugin):
        """Injected context must list at least one banned package."""
        result = plugin._pre_llm_call(user_message="x")
        # The banned packages line should appear
        assert "BANNED packages" in result["context"]
        # pandas is a concrete banned package in default-registry.yaml
        assert "pandas" in result["context"]

    def test_pre_llm_call_absorbs_extra_kwargs(self, plugin):
        """_pre_llm_call must accept the extra kwargs upstream passes (task_id, turn_id, etc)."""
        # Upstream passes: session_id, task_id, turn_id, user_message, conversation_history,
        # is_first_turn, model, platform, sender_id — the **_ must absorb them silently.
        result = plugin._pre_llm_call(
            session_id="s1",
            user_message="test",
            conversation_history=[],
            is_first_turn=True,
            model="claude-3-5-sonnet-20241022",
            task_id="t1",
            turn_id="r1",
            platform="cli",
            sender_id="user",
        )
        assert result is not None
        assert "context" in result


# ---------------------------------------------------------------------------
# REQ-04 — T3: audit JSONL is written for codegen tool calls
# ---------------------------------------------------------------------------

class TestAuditJsonlWritten:
    """T3: post_tool_call must append a JSONL entry for every codegen call."""

    def test_audit_entry_written_for_write_file(self, plugin, hermes_home, tmp_path):
        """A write_file call must produce an audit JSONL entry."""
        import os
        audit_path = hermes_home / "tool-registry-guard" / "audit.jsonl"
        plugin._audit_path = audit_path

        plugin._post_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/x.py", "content": "import os"},
            result="ok",
        )

        assert audit_path.exists(), "Audit file was not created"
        entries = [json.loads(line) for line in audit_path.read_text().strip().splitlines()]
        assert len(entries) == 1
        assert entries[0]["tool"] == "write_file"
        assert entries[0]["violation"] is False

    def test_audit_entry_flags_violation(self, plugin, hermes_home):
        """A write_file with a banned import must set violation=True in audit."""
        audit_path = hermes_home / "tool-registry-guard" / "audit.jsonl"
        plugin._audit_path = audit_path

        plugin._post_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/x.py", "content": "import openpyxl\nwb = openpyxl.load_workbook('f.xlsx')"},
            result="ok",
        )

        assert audit_path.exists()
        entries = [json.loads(line) for line in audit_path.read_text().strip().splitlines()]
        assert entries[0]["violation"] is True
        assert entries[0]["matched_rule"] == "openpyxl"

    def test_non_codegen_tool_not_audited(self, plugin, hermes_home):
        """read_file calls must NOT produce audit entries."""
        audit_path = hermes_home / "tool-registry-guard" / "audit.jsonl"
        plugin._audit_path = audit_path

        plugin._post_tool_call(
            tool_name="read_file",
            args={"path": "/tmp/x.py"},
            result="contents",
        )

        assert not audit_path.exists(), "Audit entry incorrectly written for non-codegen tool"

    def test_terminal_banned_pip_flagged_in_audit(self, plugin, hermes_home):
        """A terminal pip install of a banned package must set violation=True."""
        audit_path = hermes_home / "tool-registry-guard" / "audit.jsonl"
        plugin._audit_path = audit_path

        plugin._post_tool_call(
            tool_name="terminal",
            args={"command": "pip install pandas"},
            result="ok",
        )

        assert audit_path.exists()
        entries = [json.loads(line) for line in audit_path.read_text().strip().splitlines()]
        assert entries[0]["violation"] is True
        assert entries[0]["matched_rule"] == "pandas"


# ---------------------------------------------------------------------------
# REQ-04 — Registry file loading drives allow/deny (REQ-04 specific assertion)
# ---------------------------------------------------------------------------

class TestRegistryDrivesDecisions:
    """REQ-04: the registry file must be loaded and drive actual enforcement."""

    def test_default_registry_loaded(self, plugin):
        """_registry must be populated with at least one tool after load."""
        assert plugin._registry is not None
        assert len(plugin._registry.get("tools", [])) >= 1

    def test_registry_populates_banned_sets(self, plugin):
        """_banned_packages and _banned_imports must be non-empty after load."""
        assert len(plugin._banned_packages) > 0
        assert len(plugin._banned_imports) > 0

    def test_registry_drives_allow_decision(self, plugin):
        """A tool not in any banned list must be allowed."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install boto3"},
        )
        assert result is None, "boto3 should not be blocked"

    def test_registry_drives_deny_decision(self, plugin):
        """A tool in the banned list must be blocked — registry is the authority."""
        # xlrd is banned by graph-workbook in default-registry.yaml
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install xlrd"},
        )
        assert result is not None
        assert result.get("action") == "block"

    def test_custom_registry_overrides_default(self, hermes_home):
        """A user-supplied registry at HERMES_HOME/tool-registry.yaml overrides default."""
        custom_yaml = hermes_home / "tool-registry.yaml"
        custom_yaml.write_text(
            "version: 1\ntools:\n"
            "  - name: my-tool\n    path: /my/tool.js\n    operations: []\n"
            "    banned_alternatives:\n      packages: [my-banned-lib]\n      imports: [my_banned_lib]\n",
            encoding="utf-8",
        )

        mod = _load_plugin(hermes_home)
        _reset_module_state(mod)
        mod._load_registry()

        # Custom registry loads one tool
        assert len(mod._registry["tools"]) == 1
        assert mod._registry["tools"][0]["name"] == "my-tool"

        # Banning in custom registry must block
        result = mod._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install my-banned-lib"},
        )
        assert result is not None
        assert result.get("action") == "block"

        # pandas is NOT in custom registry → must be allowed
        result2 = mod._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install pandas"},
        )
        assert result2 is None, "pandas should not be blocked when not in custom registry"


# ---------------------------------------------------------------------------
# REQ-01 — Hook returns block dict, never raises (F5 contract)
# ---------------------------------------------------------------------------

class TestHookNeverRaises:
    """REQ-01: hooks must return block dicts, never raise (F5-safe)."""

    def test_pre_tool_call_with_none_args_does_not_raise(self, plugin):
        """_pre_tool_call(args=None) must not raise."""
        result = plugin._pre_tool_call(tool_name="terminal", args=None)
        # args=None defaults to {} in the hook; empty command is clean
        assert result is None

    def test_pre_tool_call_with_empty_command_does_not_raise(self, plugin):
        """_pre_tool_call with an empty command string must return None."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": ""},
        )
        assert result is None

    def test_pre_llm_call_with_no_registry_returns_none(self, hermes_home):
        """_pre_llm_call with an empty registry must return None, not raise."""
        mod = _load_plugin(hermes_home)
        _reset_module_state(mod)
        # Do NOT call _load_registry — simulate no-registry state
        result = mod._pre_llm_call(user_message="test")
        assert result is None

    def test_post_tool_call_without_audit_path_does_not_raise(self, plugin):
        """_post_tool_call with _audit_path=None must not raise."""
        plugin._audit_path = None
        plugin._post_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/x.py", "content": "import os"},
            result="ok",
        )
        # No assertion — just must not raise
