"""Tests for the tool-registry-guard plugin.

Covers:
  * Registry loading from YAML file (priority, fallback)
  * Banned set construction from loaded registry
  * pre_tool_call: allows clean tool calls (returns None)
  * pre_tool_call: blocks pip install of banned packages
  * pre_tool_call: blocks write_file with banned imports
  * pre_tool_call: allows non-banned packages/imports
  * Block messages: correct tool name, path, --help hint
  * pre_llm_call: injects condensed registry into context
  * Audit logging: writes JSONL entries for code-gen tools
"""

import importlib
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = _REPO_ROOT / "plugins" / "tool-registry-guard"


def _load_plugin():
    """Import the plugin __init__.py directly from the repo path."""
    init_file = _PLUGIN_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.tool_registry_guard",
        init_file,
        submodule_search_locations=[str(_PLUGIN_DIR)],
    )
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []
        sys.modules["hermes_plugins"] = ns
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "hermes_plugins.tool_registry_guard"
    mod.__path__ = [str(_PLUGIN_DIR)]
    sys.modules["hermes_plugins.tool_registry_guard"] = mod
    spec.loader.exec_module(mod)
    return mod


def _sample_registry_data():
    """Minimal registry YAML data for tests."""
    return {
        "version": 1,
        "tools": [
            {
                "name": "graph-workbook",
                "path": "~/Code/pm_os/bin/graph-workbook.js",
                "description": "Excel read/write",
                "operations": ["excel-read", "excel-write"],
                "banned_alternatives": {
                    "packages": ["pandas", "openpyxl", "xlrd", "xlsxwriter"],
                    "imports": ["pandas", "openpyxl", "xlrd", "xlsxwriter"],
                },
            },
            {
                "name": "ooxml-surgery",
                "path": "~/Code/pm_os/bin/ooxml-surgery.py",
                "description": "Word/PPT package operations",
                "operations": ["word-read", "word-write"],
                "banned_alternatives": {
                    "packages": ["python-docx", "docx", "mammoth"],
                    "imports": ["docx", "mammoth"],
                },
            },
            {
                "name": "graph-edit-pptx",
                "path": "~/Code/pm_os/bin/graph-edit-pptx.js",
                "description": "Complex PPT edits",
                "operations": ["pptx-chart"],
                "banned_alternatives": {
                    "packages": ["python-pptx", "pptx"],
                    "imports": ["pptx"],
                },
            },
        ],
    }


@pytest.fixture()
def registry_file(tmp_path):
    """Write sample registry to a temp YAML file and return its path."""
    path = tmp_path / "tool-registry.yaml"
    path.write_text(yaml.dump(_sample_registry_data()), encoding="utf-8")
    return path


@pytest.fixture()
def plugin(registry_file, tmp_path, monkeypatch):
    """Load the plugin module with a test registry file."""
    # Prevent it from loading the user-home registry
    fake_home = tmp_path / "fake_hermes_home"
    fake_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(fake_home))
    mod = _load_plugin()
    # Re-initialise the plugin with our test registry
    mod._load_registry(str(registry_file))
    return mod


# ---------------------------------------------------------------------------
# 1. Registry loading
# ---------------------------------------------------------------------------

class TestRegistryLoading:
    def test_loads_from_yaml_file(self, plugin, registry_file):
        """Registry loads tools from a YAML file."""
        reg = plugin._registry
        assert reg is not None
        assert reg["version"] == 1
        assert len(reg["tools"]) == 3

    def test_tool_names_present(self, plugin):
        """All tool names from the YAML are present."""
        names = {t["name"] for t in plugin._registry["tools"]}
        assert names == {"graph-workbook", "ooxml-surgery", "graph-edit-pptx"}

    def test_fallback_to_default_registry(self, tmp_path, monkeypatch):
        """When user override missing, loads bundled default-registry.yaml."""
        fake_home = tmp_path / "no_hermes_home"
        fake_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(fake_home))
        mod = _load_plugin()
        # Load with no explicit path — should fallback to default-registry.yaml
        mod._load_registry(None)
        default_path = _PLUGIN_DIR / "default-registry.yaml"
        if default_path.exists():
            assert mod._registry is not None
            assert mod._registry["version"] == 1


# ---------------------------------------------------------------------------
# 2. Banned set construction
# ---------------------------------------------------------------------------

class TestBannedSets:
    def test_banned_packages_built(self, plugin):
        """All banned packages from all tools are in the flat set."""
        expected = {
            "pandas", "openpyxl", "xlrd", "xlsxwriter",
            "python-docx", "docx", "mammoth",
            "python-pptx", "pptx",
        }
        assert plugin._banned_packages == expected

    def test_banned_imports_built(self, plugin):
        """All banned imports from all tools are in the flat set."""
        expected = {
            "pandas", "openpyxl", "xlrd", "xlsxwriter",
            "docx", "mammoth", "pptx",
        }
        assert plugin._banned_imports == expected

    def test_ban_to_tool_mapping(self, plugin):
        """Each banned import maps back to its registered tool."""
        mapping = plugin._import_to_tool
        assert mapping["pandas"]["name"] == "graph-workbook"
        assert mapping["docx"]["name"] == "ooxml-surgery"
        assert mapping["pptx"]["name"] == "graph-edit-pptx"


# ---------------------------------------------------------------------------
# 3. pre_tool_call: allows non-matching calls
# ---------------------------------------------------------------------------

class TestPreToolCallAllows:
    def test_allows_unrelated_tool(self, plugin):
        """Tools other than terminal/write_file are ignored."""
        result = plugin._pre_tool_call(
            tool_name="web_search", args={"query": "hello"})
        assert result is None

    def test_allows_terminal_safe_command(self, plugin):
        """Terminal command not matching any banned package passes."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install requests"},
        )
        assert result is None

    def test_allows_write_file_safe_import(self, plugin):
        """write_file with non-banned import passes."""
        result = plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "import json\nimport os\n"},
        )
        assert result is None

    def test_allows_terminal_non_pip(self, plugin):
        """Terminal command that mentions banned name but is not pip install."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "echo pandas is a library"},
        )
        assert result is None


# ---------------------------------------------------------------------------
# 4. pre_tool_call: blocks pip install of banned packages
# ---------------------------------------------------------------------------

class TestPreToolCallBlocksPip:
    def test_blocks_pip_install_openpyxl(self, plugin):
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install openpyxl"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "openpyxl" in result["message"]

    def test_blocks_pip3_install_python_docx(self, plugin):
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip3 install python-docx"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "python-docx" in result["message"]

    def test_blocks_pip_install_with_other_packages(self, plugin):
        """pip install line with mixed packages, one banned."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install requests openpyxl flask"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "openpyxl" in result["message"]

    def test_blocks_python_c_import(self, plugin):
        """python -c with banned import."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": 'python -c "import pandas; print(1)"'},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "pandas" in result["message"]

    def test_blocks_python3_c_import(self, plugin):
        """python3 -c with banned import."""
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": 'python3 -c "from docx import Document"'},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "docx" in result["message"]

    def test_blocks_pip_install_pptx(self, plugin):
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install python-pptx"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "python-pptx" in result["message"]


# ---------------------------------------------------------------------------
# 5. pre_tool_call: blocks write_file with banned imports
# ---------------------------------------------------------------------------

class TestPreToolCallBlocksWriteFile:
    def test_blocks_import_openpyxl(self, plugin):
        result = plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "import openpyxl\nwb = openpyxl.Workbook()"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "openpyxl" in result["message"]

    def test_blocks_from_docx_import(self, plugin):
        result = plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "from docx import Document\n"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "docx" in result["message"]

    def test_blocks_import_mammoth(self, plugin):
        result = plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "import mammoth\n"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "mammoth" in result["message"]

    def test_blocks_indented_import(self, plugin):
        """Indented imports in a function body are also caught."""
        result = plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "def foo():\n    import pandas\n    return pandas.DataFrame()"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "pandas" in result["message"]

    def test_allows_import_json(self, plugin):
        result = plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "import json\nimport os\n"},
        )
        assert result is None


# ---------------------------------------------------------------------------
# 6. Block message quality
# ---------------------------------------------------------------------------

class TestBlockMessages:
    def test_includes_tool_name(self, plugin):
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install openpyxl"},
        )
        assert "graph-workbook" in result["message"]

    def test_includes_tool_path(self, plugin):
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install openpyxl"},
        )
        assert "graph-workbook.js" in result["message"]

    def test_includes_help_hint(self, plugin):
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install openpyxl"},
        )
        assert "--help" in result["message"]

    def test_write_file_block_includes_tool_info(self, plugin):
        result = plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "from docx import Document"},
        )
        assert "ooxml-surgery" in result["message"]
        assert "--help" in result["message"]


# ---------------------------------------------------------------------------
# 7. pre_llm_call: registry context injection
# ---------------------------------------------------------------------------

class TestPreLlmCall:
    def test_returns_context_string(self, plugin):
        """pre_llm_call returns a non-empty context string."""
        result = plugin._pre_llm_call(
            session_id="test-session",
            user_message="write a script",
            conversation_history=[],
            is_first_turn=True,
            model="test-model",
        )
        assert result is not None
        # May be a dict with "context" key or a plain string
        if isinstance(result, dict):
            ctx = result.get("context", "")
        else:
            ctx = result
        assert len(ctx) > 0

    def test_context_contains_tool_table(self, plugin):
        """Injected context contains the registry table."""
        result = plugin._pre_llm_call(
            session_id="s", user_message="", conversation_history=[],
            is_first_turn=True, model="m",
        )
        ctx = result.get("context", "") if isinstance(result, dict) else result
        assert "graph-workbook" in ctx
        assert "ooxml-surgery" in ctx

    def test_context_contains_banned_list(self, plugin):
        """Injected context lists banned packages."""
        result = plugin._pre_llm_call(
            session_id="s", user_message="", conversation_history=[],
            is_first_turn=True, model="m",
        )
        ctx = result.get("context", "") if isinstance(result, dict) else result
        assert "pandas" in ctx
        assert "openpyxl" in ctx


# ---------------------------------------------------------------------------
# 8. Audit logging
# ---------------------------------------------------------------------------

class TestAuditLogging:
    def test_post_tool_call_writes_jsonl(self, plugin, tmp_path, monkeypatch):
        """post_tool_call logs an audit entry for code-gen tools."""
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        audit_file = audit_dir / "audit.jsonl"
        monkeypatch.setattr(plugin, "_audit_path", audit_file)

        plugin._post_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/test.py", "content": "import json"},
            result="ok",
        )
        assert audit_file.exists()
        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool"] == "write_file"
        assert entry["violation"] is False

    def test_audit_records_violation(self, plugin, tmp_path, monkeypatch):
        """Audit entry marks violation=True when a banned import is found."""
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        audit_file = audit_dir / "audit.jsonl"
        monkeypatch.setattr(plugin, "_audit_path", audit_file)

        plugin._post_tool_call(
            tool_name="write_file",
            args={"content": "import pandas"},
            result="blocked",
        )
        lines = audit_file.read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["violation"] is True
        assert entry["matched_rule"] is not None

    def test_audit_skips_non_codegen_tools(self, plugin, tmp_path, monkeypatch):
        """Non code-gen tools (web_search etc.) are not audited."""
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        audit_file = audit_dir / "audit.jsonl"
        monkeypatch.setattr(plugin, "_audit_path", audit_file)

        plugin._post_tool_call(
            tool_name="web_search",
            args={"query": "hello"},
            result="ok",
        )
        assert not audit_file.exists()


# ---------------------------------------------------------------------------
# 9. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_command(self, plugin):
        """Terminal with empty command is allowed."""
        result = plugin._pre_tool_call(
            tool_name="terminal", args={"command": ""})
        assert result is None

    def test_missing_args(self, plugin):
        """Missing args dict does not crash."""
        result = plugin._pre_tool_call(tool_name="terminal", args={})
        assert result is None

    def test_none_args(self, plugin):
        """None args handled gracefully."""
        result = plugin._pre_tool_call(tool_name="terminal", args=None)
        assert result is None

    def test_pip_install_substring_not_blocked(self, plugin):
        """Package name that contains a banned name as substring is not blocked.
        e.g. 'openpyxl-stubs' should not match 'openpyxl' with word boundary.
        Wait -- actually it SHOULD match because openpyxl-stubs implies openpyxl.
        But 'pandastic' should NOT match 'pandas'.
        """
        # 'pandastic' is not 'pandas'
        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install pandastic"},
        )
        assert result is None

    def test_patch_tool_with_banned_import(self, plugin):
        """The patch tool writing banned imports should also be caught."""
        result = plugin._pre_tool_call(
            tool_name="patch",
            args={"content": "import pandas\n", "path": "/tmp/x.py"},
        )
        # patch tool should be checked like write_file
        assert result is not None
        assert result["action"] == "block"
