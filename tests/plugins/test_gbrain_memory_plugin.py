"""Tests for the gbrain-memory plugin.

Verifies tool registration, CLI command construction, response parsing,
slugification, availability checks, and error handling. Mocks
asyncio.create_subprocess_exec since that is the external boundary
(gbrain CLI is an external process).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_sys_modules():
    """Remove cached gbrain plugin module between tests so register() is fresh."""
    mod_key = "plugins.memory.gbrain"
    had = mod_key in sys.modules
    yield
    if not had:
        sys.modules.pop(mod_key, None)


def _make_ctx():
    """Return a fake PluginContext that records register_tool calls."""
    ctx = MagicMock()
    ctx.register_tool = MagicMock()
    return ctx


def _import_plugin():
    """Import the gbrain plugin module."""
    # Ensure the plugins/memory/gbrain package is importable
    repo_root = Path(__file__).resolve().parent.parent.parent
    plugins_dir = repo_root / "plugins" / "memory" / "gbrain"
    assert plugins_dir.exists(), f"Plugin dir missing: {plugins_dir}"

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "plugins.memory.gbrain",
        str(plugins_dir / "__init__.py"),
        submodule_search_locations=[str(plugins_dir)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["plugins.memory.gbrain"] = mod
    spec.loader.exec_module(mod)
    return mod


def _mock_subprocess(stdout=b"", stderr=b"", returncode=0):
    """Return an AsyncMock for asyncio.create_subprocess_exec.

    The mock process has communicate() returning (stdout, stderr)
    and a returncode attribute.
    """
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode

    create_mock = AsyncMock(return_value=proc)
    return create_mock, proc


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    """register() wires both tools with correct schemas and check_fn."""

    def test_register_registers_both_tools(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        assert ctx.register_tool.call_count == 2
        tool_names = {call.kwargs["name"] for call in ctx.register_tool.call_args_list}
        assert tool_names == {"gbrain_search", "gbrain_add_note"}

    def test_register_provides_check_fn(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        for call in ctx.register_tool.call_args_list:
            assert call.kwargs.get("check_fn") is not None

    def test_register_tools_are_async(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        for call in ctx.register_tool.call_args_list:
            assert call.kwargs.get("is_async") is True

    def test_register_tool_schemas_have_required_fields(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        for call in ctx.register_tool.call_args_list:
            schema = call.kwargs["schema"]
            assert "name" in schema
            assert "description" in schema
            assert "parameters" in schema


# ---------------------------------------------------------------------------
# gbrain_search
# ---------------------------------------------------------------------------

class TestGbrainSearch:
    """gbrain_search tool handler tests."""

    def test_builds_correct_cli_command(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        # Find the search handler
        search_call = next(
            c for c in ctx.register_tool.call_args_list
            if c.kwargs["name"] == "gbrain_search"
        )
        handler = search_call.kwargs["handler"]

        json_output = json.dumps([
            {"title": "Test", "slug": "test", "content": "snippet"}
        ])
        create_mock, proc = _mock_subprocess(
            stdout=json_output.encode(), returncode=0
        )

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"query": "test query", "limit": 3})
            )

        # Verify the CLI command was called correctly
        create_mock.assert_called_once()
        args = create_mock.call_args[0]
        assert args[-1] == "--json"
        assert "test query" in args
        assert "--limit" in args
        limit_idx = list(args).index("--limit")
        assert args[limit_idx + 1] == "3"

    def test_handles_valid_json_response(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        search_call = next(
            c for c in ctx.register_tool.call_args_list
            if c.kwargs["name"] == "gbrain_search"
        )
        handler = search_call.kwargs["handler"]

        json_output = json.dumps([
            {"title": "Meeting Notes", "slug": "meeting-notes", "content": "Q3 planning..."},
            {"title": "Decision Log", "slug": "decision-log", "content": "We decided to..."},
        ])
        create_mock, _ = _mock_subprocess(
            stdout=json_output.encode(), returncode=0
        )

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"query": "planning"})
            )

        parsed = json.loads(result)
        assert "results" in parsed
        assert len(parsed["results"]) == 2
        assert parsed["results"][0]["title"] == "Meeting Notes"

    def test_handles_empty_results(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        search_call = next(
            c for c in ctx.register_tool.call_args_list
            if c.kwargs["name"] == "gbrain_search"
        )
        handler = search_call.kwargs["handler"]

        create_mock, _ = _mock_subprocess(stdout=b"[]", returncode=0)

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"query": "nonexistent"})
            )

        parsed = json.loads(result)
        assert parsed["results"] == []

    def test_handles_gbrain_not_found(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        search_call = next(
            c for c in ctx.register_tool.call_args_list
            if c.kwargs["name"] == "gbrain_search"
        )
        handler = search_call.kwargs["handler"]

        create_mock = AsyncMock(side_effect=FileNotFoundError("gbrain not found"))

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"query": "test"})
            )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "not found" in parsed["error"].lower() or "gbrain" in parsed["error"].lower()

    def test_handles_nonzero_exit_code(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        search_call = next(
            c for c in ctx.register_tool.call_args_list
            if c.kwargs["name"] == "gbrain_search"
        )
        handler = search_call.kwargs["handler"]

        create_mock, _ = _mock_subprocess(
            stderr=b"connection refused", returncode=1
        )

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"query": "test"})
            )

        parsed = json.loads(result)
        assert "error" in parsed

    def test_default_limit_is_5(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        search_call = next(
            c for c in ctx.register_tool.call_args_list
            if c.kwargs["name"] == "gbrain_search"
        )
        handler = search_call.kwargs["handler"]

        create_mock, _ = _mock_subprocess(stdout=b"[]", returncode=0)

        with patch("asyncio.create_subprocess_exec", create_mock):
            asyncio.get_event_loop().run_until_complete(
                handler({"query": "test"})
            )

        args = create_mock.call_args[0]
        limit_idx = list(args).index("--limit")
        assert args[limit_idx + 1] == "5"


# ---------------------------------------------------------------------------
# gbrain_add_note
# ---------------------------------------------------------------------------

class TestGbrainAddNote:
    """gbrain_add_note tool handler tests."""

    def test_slugifies_title_correctly(self):
        mod = _import_plugin()
        # Test the slugify function directly
        slugify = mod._slugify
        assert slugify("Hello World") == "hello-world"
        assert slugify("  Spaces  Around  ") == "spaces-around"
        assert slugify("Special!@#$%Chars") == "special-chars"
        assert slugify("Already-a-slug") == "already-a-slug"
        assert slugify("UPPERCASE") == "uppercase"
        assert slugify("multiple---hyphens") == "multiple-hyphens"
        assert slugify("trailing-hyphen-") == "trailing-hyphen"
        assert slugify("-leading-hyphen") == "leading-hyphen"

    def test_builds_correct_cli_command_with_piped_input(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        add_call = next(
            c for c in ctx.register_tool.call_args_list
            if c.kwargs["name"] == "gbrain_add_note"
        )
        handler = add_call.kwargs["handler"]

        create_mock, proc = _mock_subprocess(
            stdout=b"OK", returncode=0
        )

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"title": "My Test Note", "body": "Some content here"})
            )

        create_mock.assert_called_once()
        args = create_mock.call_args[0]
        assert "put" in args
        assert "my-test-note" in args

        # Verify body was passed via stdin
        proc.communicate.assert_called_once()
        stdin_data = proc.communicate.call_args[1].get("input") or proc.communicate.call_args[0][0]
        assert stdin_data == b"Some content here"

    def test_handles_success_response(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        add_call = next(
            c for c in ctx.register_tool.call_args_list
            if c.kwargs["name"] == "gbrain_add_note"
        )
        handler = add_call.kwargs["handler"]

        create_mock, _ = _mock_subprocess(stdout=b"saved", returncode=0)

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"title": "Decision Log", "body": "We decided X"})
            )

        parsed = json.loads(result)
        assert "slug" in parsed
        assert parsed["slug"] == "decision-log"
        assert "saved" in parsed.get("result", "").lower() or "stored" in parsed.get("result", "").lower()

    def test_handles_errors(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        add_call = next(
            c for c in ctx.register_tool.call_args_list
            if c.kwargs["name"] == "gbrain_add_note"
        )
        handler = add_call.kwargs["handler"]

        create_mock, _ = _mock_subprocess(
            stderr=b"database connection failed", returncode=1
        )

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"title": "Test", "body": "content"})
            )

        parsed = json.loads(result)
        assert "error" in parsed

    def test_handles_gbrain_not_found(self):
        mod = _import_plugin()
        ctx = _make_ctx()
        mod.register(ctx)

        add_call = next(
            c for c in ctx.register_tool.call_args_list
            if c.kwargs["name"] == "gbrain_add_note"
        )
        handler = add_call.kwargs["handler"]

        create_mock = AsyncMock(side_effect=FileNotFoundError("gbrain not found"))

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"title": "Test", "body": "content"})
            )

        parsed = json.loads(result)
        assert "error" in parsed


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

class TestAvailabilityCheck:
    """check_fn verifies gbrain CLI is accessible."""

    def test_returns_true_when_gbrain_exists(self):
        mod = _import_plugin()
        check = mod._check_gbrain

        with patch("shutil.which", return_value="/usr/local/bin/gbrain"):
            assert check() is True

    def test_returns_true_when_bun_gbrain_exists(self):
        mod = _import_plugin()
        check = mod._check_gbrain

        with patch("shutil.which", return_value=None), \
             patch.object(Path, "exists", return_value=True):
            assert check() is True

    def test_returns_false_when_gbrain_missing(self):
        mod = _import_plugin()
        check = mod._check_gbrain

        with patch("shutil.which", return_value=None), \
             patch.object(Path, "exists", return_value=False):
            assert check() is False
