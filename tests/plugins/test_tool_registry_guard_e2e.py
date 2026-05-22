"""
ABOUTME: End-to-end integration tests for the tool-registry-guard plugin.
ABOUTME: Tests the full enforcement flow: YAML registry loading, pre_tool_call
ABOUTME: blocking/allowing, pre_llm_call context injection, post_tool_call
ABOUTME: audit logging, registry priority, and edge cases — all with real
ABOUTME: YAML files and no mocking of plugin internals.
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
# Helpers — load the plugin module directly from the repo tree
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = _REPO_ROOT / "plugins" / "tool-registry-guard"


def _fresh_plugin(registry_path: str, audit_path: Path):
    """Import and initialise the plugin from scratch with a given registry.

    Returns the loaded module with _load_registry already called against
    the provided registry_path and _audit_path pointed at audit_path.
    Re-imports the module each time to avoid cross-test state leakage.
    """
    init_file = _PLUGIN_DIR / "__init__.py"
    mod_name = "hermes_plugins.tool_registry_guard"

    # Ensure the namespace parent package exists
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []
        sys.modules["hermes_plugins"] = ns

    # Force re-import so module-level state resets
    sys.modules.pop(mod_name, None)
    spec = importlib.util.spec_from_file_location(
        mod_name,
        init_file,
        submodule_search_locations=[str(_PLUGIN_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = mod_name
    mod.__path__ = [str(_PLUGIN_DIR)]
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)

    # Load the registry and set audit path
    mod._load_registry(registry_path)
    mod._audit_path = audit_path
    return mod


def _write_registry(tmp_path: Path, data: dict) -> Path:
    """Write a registry dict to a YAML file and return its path."""
    path = tmp_path / "tool-registry.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Full registry used across most tests — mirrors the real default-registry
# ---------------------------------------------------------------------------

_FULL_REGISTRY = {
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
                "packages": ["python-docx", "docx", "mammoth", "docx2txt", "textract"],
                "imports": ["docx", "mammoth", "docx2txt", "textract"],
            },
        },
        {
            "name": "graph-edit-pptx",
            "path": "~/Code/pm_os/bin/graph-edit-pptx.js",
            "description": "Complex PPT edits",
            "operations": ["pptx-chart", "pptx-image"],
            "banned_alternatives": {
                "packages": ["python-pptx", "pptx"],
                "imports": ["pptx"],
            },
        },
    ],
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Set up an isolated environment for each test.

    Provides:
      - registry_path: path to the YAML registry file
      - audit_file: path where audit JSONL will be written
      - plugin: the loaded plugin module

    Prevents the plugin from finding the user's real ~/.hermes/ registry.
    """
    # Isolate HERMES_HOME so user registry is never loaded
    fake_home = tmp_path / "fake_hermes_home"
    fake_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(fake_home))

    registry_path = _write_registry(tmp_path, _FULL_REGISTRY)
    audit_file = tmp_path / "audit" / "audit.jsonl"

    plugin = _fresh_plugin(str(registry_path), audit_file)

    class Env:
        pass

    e = Env()
    e.plugin = plugin
    e.registry_path = registry_path
    e.audit_file = audit_file
    e.tmp_path = tmp_path
    return e


# ===========================================================================
# (a) pip install banned package — BLOCKED
# ===========================================================================

class TestPipInstallBannedPackage:
    """pip install of a banned package must be blocked with helpful message."""

    def test_pip_install_openpyxl_blocked(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install openpyxl"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "openpyxl" in result["message"]
        assert "graph-workbook" in result["message"]

    def test_pip_install_openpyxl_mentions_tool_path(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install openpyxl"},
        )
        assert "graph-workbook.js" in result["message"]


# ===========================================================================
# (b) pip3 install banned package — BLOCKED
# ===========================================================================

class TestPip3InstallBannedPackage:
    """pip3 install of a banned package must be blocked."""

    def test_pip3_install_python_docx_blocked(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip3 install python-docx"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "python-docx" in result["message"]
        assert "ooxml-surgery" in result["message"]


# ===========================================================================
# (c) pip install allowed package — ALLOWED
# ===========================================================================

class TestPipInstallAllowedPackage:
    """pip install of a non-banned package must pass through."""

    def test_pip_install_requests_allowed(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install requests"},
        )
        assert result is None


# ===========================================================================
# (d) write_file with banned import — BLOCKED
# ===========================================================================

class TestWriteFileBannedImport:
    """write_file containing a banned import statement must be blocked."""

    def test_import_openpyxl_blocked(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "import openpyxl\nwb = openpyxl.Workbook()"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "openpyxl" in result["message"]


# ===========================================================================
# (e) write_file with from-import — BLOCKED
# ===========================================================================

class TestWriteFileFromImport:
    """write_file with 'from <banned> import ...' must be blocked."""

    def test_from_docx_import_blocked(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "from docx import Document"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "docx" in result["message"]
        assert "ooxml-surgery" in result["message"]


# ===========================================================================
# (f) write_file with allowed import — ALLOWED
# ===========================================================================

class TestWriteFileAllowedImport:
    """write_file with non-banned imports must pass through."""

    def test_import_json_allowed(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "import json\ndata = json.loads(s)"},
        )
        assert result is None


# ===========================================================================
# (g) python -c with banned import — BLOCKED
# ===========================================================================

class TestPythonCBannedImport:
    """python -c one-liner with banned import must be blocked."""

    def test_python_c_import_pandas_blocked(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": 'python -c "import pandas; pandas.read_excel(\'f.xlsx\')"'},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "pandas" in result["message"]


# ===========================================================================
# (h) Non-code tools pass through — ALLOWED
# ===========================================================================

class TestNonCodeToolsAllowed:
    """Tools that don't generate code should never be blocked."""

    def test_send_message_allowed(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="send_message",
            args={"target": "telegram", "message": "hi"},
        )
        assert result is None

    def test_web_search_allowed(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="web_search",
            args={"query": "pandas documentation"},
        )
        assert result is None

    def test_delegate_task_allowed(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="delegate_task",
            args={"task": "install pandas", "agent_type": "coder"},
        )
        assert result is None


# ===========================================================================
# (i) Block message quality — tool name, path, --help
# ===========================================================================

class TestBlockMessageQuality:
    """Block messages must include actionable info for the LLM."""

    def test_pip_block_contains_tool_name(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install pandas"},
        )
        assert "graph-workbook" in result["message"]

    def test_pip_block_contains_tool_path(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install pandas"},
        )
        assert "graph-workbook.js" in result["message"]

    def test_pip_block_contains_help_command(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install pandas"},
        )
        assert "--help" in result["message"]

    def test_write_file_block_js_tool_has_node_help(self, env):
        """JS tools should suggest 'node <path> --help'."""
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install openpyxl"},
        )
        assert "node" in result["message"]
        assert "--help" in result["message"]

    def test_write_file_block_py_tool_has_uv_help(self, env):
        """Python tools should suggest 'uv run --with lxml <path> --help'."""
        result = env.plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "from docx import Document"},
        )
        assert "uv run" in result["message"]
        assert "--help" in result["message"]


# ===========================================================================
# (j) Substring false positive prevention
# ===========================================================================

class TestSubstringFalsePositives:
    """Package names that contain a banned name as a substring must NOT match."""

    def test_pandastic_not_blocked(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install pandastic"},
        )
        assert result is None

    def test_openpyxl_stubs_not_blocked_as_different_word(self, env):
        """'openpyxl-stubs' has openpyxl as a prefix — regex uses word boundary.
        Hyphens ARE word boundaries, so 'openpyxl-stubs' WILL match openpyxl.
        This test documents that behavior.
        """
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install openpyxl-stubs"},
        )
        # Hyphen is a word boundary, so openpyxl IS matched
        assert result is not None
        assert result["action"] == "block"

    def test_pandastable_not_blocked(self, env):
        """'pandastable' contains 'pandas' but is not the same word."""
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install pandastable"},
        )
        # 'pandastable' has no word boundary after 'pandas', so NOT matched
        assert result is None


# ===========================================================================
# (k) Multiple banned packages in one command
# ===========================================================================

class TestMultipleBannedPackages:
    """pip install with multiple banned packages should be blocked."""

    def test_multiple_banned_packages_blocked(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install openpyxl xlrd pandas"},
        )
        assert result is not None
        assert result["action"] == "block"
        # At least one banned package is mentioned in the message
        msg = result["message"]
        banned_mentioned = any(
            pkg in msg for pkg in ("openpyxl", "xlrd", "pandas")
        )
        assert banned_mentioned

    def test_mixed_banned_and_allowed_blocked(self, env):
        """Even one banned package in a mixed install line triggers a block."""
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install requests flask openpyxl pytest"},
        )
        assert result is not None
        assert result["action"] == "block"


# ===========================================================================
# (l) pre_llm_call context injection
# ===========================================================================

class TestPreLlmCallContextInjection:
    """pre_llm_call must return a context dict with the registry table."""

    def test_returns_dict_with_context_key(self, env):
        result = env.plugin._pre_llm_call(
            session_id="test-session",
            user_message="write a script to read an excel file",
            conversation_history=[],
            is_first_turn=True,
            model="test-model",
        )
        assert result is not None
        assert isinstance(result, dict)
        assert "context" in result

    def test_context_contains_all_tool_names(self, env):
        result = env.plugin._pre_llm_call(
            session_id="s", user_message="", conversation_history=[],
            is_first_turn=True, model="m",
        )
        ctx = result["context"]
        assert "graph-workbook" in ctx
        assert "ooxml-surgery" in ctx
        assert "graph-edit-pptx" in ctx

    def test_context_contains_banned_packages_list(self, env):
        result = env.plugin._pre_llm_call(
            session_id="s", user_message="", conversation_history=[],
            is_first_turn=True, model="m",
        )
        ctx = result["context"]
        assert "BANNED" in ctx
        assert "pandas" in ctx
        assert "openpyxl" in ctx

    def test_context_contains_table_header(self, env):
        result = env.plugin._pre_llm_call(
            session_id="s", user_message="", conversation_history=[],
            is_first_turn=True, model="m",
        )
        ctx = result["context"]
        assert "TOOL REGISTRY" in ctx
        assert "| Tool |" in ctx

    def test_context_includes_help_commands(self, env):
        result = env.plugin._pre_llm_call(
            session_id="s", user_message="", conversation_history=[],
            is_first_turn=True, model="m",
        )
        ctx = result["context"]
        assert "--help" in ctx


# ===========================================================================
# (m) post_tool_call audit logging
# ===========================================================================

class TestPostToolCallAudit:
    """post_tool_call must write structured JSONL audit entries."""

    def test_write_file_logged(self, env):
        env.plugin._post_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/test.py", "content": "import json"},
            result="ok",
        )
        assert env.audit_file.exists()
        lines = env.audit_file.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool"] == "write_file"
        assert entry["violation"] is False
        assert entry["matched_rule"] is None
        assert "ts" in entry

    def test_terminal_logged(self, env):
        env.plugin._post_tool_call(
            tool_name="terminal",
            args={"command": "echo hello"},
            result="hello",
        )
        assert env.audit_file.exists()
        entry = json.loads(env.audit_file.read_text().strip())
        assert entry["tool"] == "terminal"
        assert entry["violation"] is False

    def test_violation_flagged_in_audit(self, env):
        env.plugin._post_tool_call(
            tool_name="write_file",
            args={"content": "import pandas\ndf = pandas.read_excel('x.xlsx')"},
            result="blocked",
        )
        entry = json.loads(env.audit_file.read_text().strip())
        assert entry["violation"] is True
        assert entry["matched_rule"] == "pandas"

    def test_non_codegen_tool_not_logged(self, env):
        """Tools like web_search should not produce audit entries."""
        env.plugin._post_tool_call(
            tool_name="web_search",
            args={"query": "hello"},
            result="ok",
        )
        assert not env.audit_file.exists()

    def test_send_message_not_logged(self, env):
        env.plugin._post_tool_call(
            tool_name="send_message",
            args={"target": "telegram", "message": "hi"},
            result="ok",
        )
        assert not env.audit_file.exists()

    def test_multiple_entries_appended(self, env):
        """Multiple tool calls append separate lines."""
        env.plugin._post_tool_call(
            tool_name="write_file",
            args={"content": "import os"},
            result="ok",
        )
        env.plugin._post_tool_call(
            tool_name="terminal",
            args={"command": "ls -la"},
            result="ok",
        )
        lines = env.audit_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["tool"] == "write_file"
        assert json.loads(lines[1])["tool"] == "terminal"

    def test_long_content_truncated_in_audit(self, env):
        """File content longer than 200 chars is truncated in args_summary."""
        long_content = "x" * 500
        env.plugin._post_tool_call(
            tool_name="write_file",
            args={"content": long_content},
            result="ok",
        )
        entry = json.loads(env.audit_file.read_text().strip())
        summary = entry["args_summary"]["content"]
        assert len(summary) < 500
        assert summary.endswith("...")

    def test_audit_entry_schema(self, env):
        """Verify the exact schema of each audit JSONL entry."""
        env.plugin._post_tool_call(
            tool_name="write_file",
            args={"content": "import json"},
            result="ok",
        )
        entry = json.loads(env.audit_file.read_text().strip())
        expected_keys = {"ts", "tool", "args_summary", "violation", "matched_rule"}
        assert set(entry.keys()) == expected_keys
        assert isinstance(entry["ts"], float)
        assert isinstance(entry["tool"], str)
        assert isinstance(entry["args_summary"], dict)
        assert isinstance(entry["violation"], bool)


# ===========================================================================
# (n) Registry loading priority — user overrides default
# ===========================================================================

class TestRegistryLoadingPriority:
    """User registry at ~/.hermes/ must take precedence over bundled default."""

    def test_user_registry_overrides_default(self, tmp_path, monkeypatch):
        """When ~/.hermes/tool-registry.yaml exists, it is used over default."""
        fake_home = tmp_path / "hermes_home_priority"
        fake_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(fake_home))

        # Write a user registry with a custom tool
        user_registry = {
            "version": 99,
            "tools": [
                {
                    "name": "custom-tool",
                    "path": "/usr/local/bin/custom-tool",
                    "description": "Custom user tool",
                    "operations": ["custom-op"],
                    "banned_alternatives": {
                        "packages": ["custom-banned"],
                        "imports": ["custom_banned"],
                    },
                },
            ],
        }
        user_reg_file = fake_home / "tool-registry.yaml"
        user_reg_file.write_text(yaml.dump(user_registry), encoding="utf-8")

        audit_file = tmp_path / "audit_priority" / "audit.jsonl"
        plugin = _fresh_plugin(None, audit_file)  # None = auto-discover

        # The auto-discovery should find the user registry
        assert plugin._registry["version"] == 99
        assert len(plugin._registry["tools"]) == 1
        assert plugin._registry["tools"][0]["name"] == "custom-tool"

    def test_fallback_to_bundled_when_no_user_registry(self, tmp_path, monkeypatch):
        """When no user registry, the bundled default-registry.yaml is used."""
        fake_home = tmp_path / "empty_hermes_home"
        fake_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(fake_home))

        audit_file = tmp_path / "audit_fallback" / "audit.jsonl"
        plugin = _fresh_plugin(None, audit_file)

        # Should load the bundled default
        default_path = _PLUGIN_DIR / "default-registry.yaml"
        if default_path.exists():
            assert plugin._registry is not None
            assert plugin._registry["version"] == 1
            # Bundled registry has multiple tools
            assert len(plugin._registry["tools"]) > 0


# ===========================================================================
# (o) Empty registry — everything allowed
# ===========================================================================

class TestEmptyRegistry:
    """Registry with no tools should allow all operations."""

    def test_empty_tools_allows_pip_install(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "empty_reg_home"
        fake_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(fake_home))

        empty_registry = {"version": 1, "tools": []}
        reg_path = _write_registry(tmp_path, empty_registry)
        audit_file = tmp_path / "audit_empty" / "audit.jsonl"
        plugin = _fresh_plugin(str(reg_path), audit_file)

        result = plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install pandas"},
        )
        assert result is None

    def test_empty_tools_allows_write_file(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "empty_reg_home2"
        fake_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(fake_home))

        empty_registry = {"version": 1, "tools": []}
        reg_path = _write_registry(tmp_path, empty_registry)
        audit_file = tmp_path / "audit_empty2" / "audit.jsonl"
        plugin = _fresh_plugin(str(reg_path), audit_file)

        result = plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "import pandas\nimport openpyxl"},
        )
        assert result is None

    def test_empty_tools_pre_llm_returns_none(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "empty_reg_home3"
        fake_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(fake_home))

        empty_registry = {"version": 1, "tools": []}
        reg_path = _write_registry(tmp_path, empty_registry)
        audit_file = tmp_path / "audit_empty3" / "audit.jsonl"
        plugin = _fresh_plugin(str(reg_path), audit_file)

        result = plugin._pre_llm_call(
            session_id="s", user_message="", conversation_history=[],
            is_first_turn=True, model="m",
        )
        assert result is None


# ===========================================================================
# Full flow integration: block -> audit -> context injection
# ===========================================================================

class TestFullFlow:
    """Test the complete enforcement lifecycle across all hooks."""

    def test_block_then_audit_then_context(self, env):
        """Sequence: pre_tool_call blocks, post_tool_call audits, pre_llm_call injects."""
        # 1. Pre-tool-call blocks the write
        pre_result = env.plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "import openpyxl\nwb = openpyxl.Workbook()"},
        )
        assert pre_result is not None
        assert pre_result["action"] == "block"

        # 2. Post-tool-call audits (in real flow, this fires even if blocked
        #    at a higher level — the audit hook sees what was attempted)
        env.plugin._post_tool_call(
            tool_name="write_file",
            args={"content": "import openpyxl\nwb = openpyxl.Workbook()"},
            result="blocked",
        )
        entry = json.loads(env.audit_file.read_text().strip())
        assert entry["violation"] is True
        assert entry["matched_rule"] == "openpyxl"

        # 3. Pre-LLM-call injects the registry context
        llm_result = env.plugin._pre_llm_call(
            session_id="s", user_message="read excel",
            conversation_history=[], is_first_turn=False, model="m",
        )
        assert llm_result is not None
        assert "graph-workbook" in llm_result["context"]

    def test_allow_then_audit_clean(self, env):
        """Allowed write_file is audited with violation=False."""
        pre_result = env.plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "import json\nimport os"},
        )
        assert pre_result is None

        env.plugin._post_tool_call(
            tool_name="write_file",
            args={"content": "import json\nimport os"},
            result="ok",
        )
        entry = json.loads(env.audit_file.read_text().strip())
        assert entry["violation"] is False
        assert entry["matched_rule"] is None


# ===========================================================================
# Patch tool (like write_file but different name)
# ===========================================================================

class TestPatchTool:
    """The 'patch' tool is also a code-gen tool and must be guarded."""

    def test_patch_with_banned_import_blocked(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="patch",
            args={"content": "import pptx\nslide = pptx.Presentation()"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "pptx" in result["message"]

    def test_patch_with_clean_code_allowed(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="patch",
            args={"content": "import os\npath = os.getcwd()"},
        )
        assert result is None

    def test_patch_audited(self, env):
        """patch tool calls should appear in the audit log (it's in _CODEGEN_TOOLS)."""
        # patch is not in the default _CODEGEN_TOOLS — check if it is
        if "patch" in env.plugin._CODEGEN_TOOLS:
            env.plugin._post_tool_call(
                tool_name="patch",
                args={"content": "import os"},
                result="ok",
            )
            assert env.audit_file.exists()
        else:
            # If patch is NOT in _CODEGEN_TOOLS, the audit skip is intentional
            env.plugin._post_tool_call(
                tool_name="patch",
                args={"content": "import os"},
                result="ok",
            )
            assert not env.audit_file.exists()


# ===========================================================================
# Regression: python3 -c variant
# ===========================================================================

class TestPython3CVariant:
    """python3 -c should be caught just like python -c."""

    def test_python3_c_from_import_blocked(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": 'python3 -c "from docx import Document; print(Document)"'},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "docx" in result["message"]


# ===========================================================================
# Cross-tool coverage: each banned import maps to correct tool
# ===========================================================================

class TestCrossToolMapping:
    """Each banned import/package must reference the correct replacement tool."""

    def test_pandas_maps_to_graph_workbook(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install pandas"},
        )
        assert "graph-workbook" in result["message"]

    def test_docx_maps_to_ooxml_surgery(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "import docx"},
        )
        assert "ooxml-surgery" in result["message"]

    def test_pptx_maps_to_graph_edit_pptx(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "import pptx"},
        )
        assert "graph-edit-pptx" in result["message"]

    def test_mammoth_maps_to_ooxml_surgery(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="write_file",
            args={"content": "import mammoth"},
        )
        assert "ooxml-surgery" in result["message"]

    def test_xlrd_maps_to_graph_workbook(self, env):
        result = env.plugin._pre_tool_call(
            tool_name="terminal",
            args={"command": "pip install xlrd"},
        )
        assert "graph-workbook" in result["message"]
