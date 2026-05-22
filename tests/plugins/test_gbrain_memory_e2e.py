# ABOUTME: End-to-end integration tests for the gbrain-memory plugin.
# ABOUTME: Tests the full flow: plugin registration via real PluginContext,
# ABOUTME: tool schema validation, handler invocation, slugification,
# ABOUTME: availability checks, and error handling paths.
# ABOUTME: Mocks only asyncio.create_subprocess_exec (external boundary).

"""End-to-end tests for the gbrain-memory plugin.

Exercises the full tool lifecycle: register(ctx) through real PluginContext
and PluginManager, verify tools land in the global registry with correct
schemas, then invoke handlers through the registered handler references.

Mocks ONLY asyncio.create_subprocess_exec — the external process boundary.
Everything else (registration, schema validation, handler logic, slug
generation) runs un-mocked.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_sys_modules():
    """Remove cached gbrain plugin module between tests so register() is fresh."""
    mod_key = "plugins.memory.gbrain"
    had = mod_key in sys.modules
    yield
    if not had:
        sys.modules.pop(mod_key, None)


def _import_plugin():
    """Import the gbrain plugin module from the repo tree."""
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
    """Build an AsyncMock for asyncio.create_subprocess_exec.

    Returns (create_mock, proc_mock) where proc_mock has communicate()
    returning (stdout, stderr) and a returncode attribute.
    """
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    create_mock = AsyncMock(return_value=proc)
    return create_mock, proc


def _get_handler(ctx_or_registry, tool_name: str):
    """Extract a handler from a real PluginContext-backed registration.

    Tries the global tool registry first (real PluginContext path), then
    falls back to scanning a MagicMock's call_args_list.
    """
    try:
        from tools.registry import registry
        entry = registry.get_entry(tool_name)
        if entry is not None:
            return entry.handler
    except ImportError:
        pass
    # Not reachable in our test setup, but defensive
    raise LookupError(f"Tool {tool_name} not found in registry")


@pytest.fixture()
def real_plugin_ctx():
    """Create a real PluginManager + PluginContext for the gbrain manifest.

    Yields (ctx, manager) and cleans up registered tools on teardown so
    one test's registration doesn't leak into another.
    """
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
    from tools.registry import registry

    manager = PluginManager()
    manifest = PluginManifest(
        name="gbrain-memory",
        version="1.0.0",
        description="gbrain personal knowledge base",
        key="gbrain-memory",
    )
    ctx = PluginContext(manifest, manager)

    yield ctx, manager

    # Teardown: deregister any tools we added so the global registry
    # is clean for the next test.
    for tool_name in list(manager._plugin_tool_names):
        registry.deregister(tool_name)
    manager._plugin_tool_names.clear()


# ---------------------------------------------------------------------------
# Plugin Registration (real PluginContext path)
# ---------------------------------------------------------------------------

class TestPluginRegistration:
    """Load the plugin via register(ctx) with the real PluginContext."""

    def test_register_puts_tools_in_global_registry(self, real_plugin_ctx):
        """Both gbrain tools end up in the global ToolRegistry."""
        ctx, manager = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        from tools.registry import registry
        assert registry.get_entry("gbrain_search") is not None
        assert registry.get_entry("gbrain_add_note") is not None

    def test_registered_tools_tracked_by_manager(self, real_plugin_ctx):
        """PluginManager._plugin_tool_names includes both tools."""
        ctx, manager = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        assert "gbrain_search" in manager._plugin_tool_names
        assert "gbrain_add_note" in manager._plugin_tool_names

    def test_registered_tools_are_async(self, real_plugin_ctx):
        """Both tools are registered with is_async=True."""
        ctx, manager = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        from tools.registry import registry
        for name in ("gbrain_search", "gbrain_add_note"):
            entry = registry.get_entry(name)
            assert entry.is_async is True, f"{name} should be async"

    def test_registered_tools_have_check_fn(self, real_plugin_ctx):
        """Both tools have a non-None check_fn."""
        ctx, manager = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        from tools.registry import registry
        for name in ("gbrain_search", "gbrain_add_note"):
            entry = registry.get_entry(name)
            assert entry.check_fn is not None, f"{name} should have check_fn"

    def test_registered_in_correct_toolset(self, real_plugin_ctx):
        """Both tools belong to the gbrain_memory toolset."""
        ctx, manager = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        from tools.registry import registry
        for name in ("gbrain_search", "gbrain_add_note"):
            entry = registry.get_entry(name)
            assert entry.toolset == "gbrain_memory"


# ---------------------------------------------------------------------------
# Tool Schema Validation
# ---------------------------------------------------------------------------

class TestToolSchemas:
    """Schema structure checks using real registered tool entries."""

    def test_search_schema_structure(self, real_plugin_ctx):
        """gbrain_search schema has name, description, parameters with required query."""
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        from tools.registry import registry
        entry = registry.get_entry("gbrain_search")
        schema = entry.schema

        assert schema["name"] == "gbrain_search"
        assert len(schema["description"]) > 0
        params = schema["parameters"]
        assert params["type"] == "object"
        assert "query" in params["properties"]
        assert "query" in params["required"]

    def test_add_note_schema_structure(self, real_plugin_ctx):
        """gbrain_add_note schema has name, description, parameters with required title+body."""
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        from tools.registry import registry
        entry = registry.get_entry("gbrain_add_note")
        schema = entry.schema

        assert schema["name"] == "gbrain_add_note"
        assert len(schema["description"]) > 0
        params = schema["parameters"]
        assert params["type"] == "object"
        assert "title" in params["properties"]
        assert "body" in params["properties"]
        assert set(params["required"]) == {"title", "body"}

    def test_search_schema_has_optional_limit(self, real_plugin_ctx):
        """gbrain_search exposes an optional limit parameter with default."""
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        from tools.registry import registry
        entry = registry.get_entry("gbrain_search")
        params = entry.schema["parameters"]

        assert "limit" in params["properties"]
        assert params["properties"]["limit"]["type"] == "integer"
        # limit should NOT be required
        assert "limit" not in params.get("required", [])


# ---------------------------------------------------------------------------
# gbrain_search — success
# ---------------------------------------------------------------------------

class TestSearchSuccess:
    """gbrain_search handler returns well-formatted results on success."""

    def test_returns_formatted_results(self, real_plugin_ctx):
        """Valid JSON from gbrain produces {results: [...]} with title/slug/snippet."""
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        from tools.registry import registry
        handler = registry.get_entry("gbrain_search").handler

        json_output = json.dumps([
            {"title": "Board Deck", "slug": "board-deck", "content": "Q3 results..."},
            {"title": "OKRs", "slug": "okrs", "snippet": "Objective 1..."},
        ])
        create_mock, _ = _mock_subprocess(stdout=json_output.encode(), returncode=0)

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"query": "board meeting"})
            )

        parsed = json.loads(result)
        assert "results" in parsed
        assert len(parsed["results"]) == 2
        assert parsed["results"][0]["title"] == "Board Deck"
        assert parsed["results"][0]["slug"] == "board-deck"
        assert parsed["results"][0]["snippet"] == "Q3 results..."
        # Second item uses "snippet" key rather than "content"
        assert parsed["results"][1]["snippet"] == "Objective 1..."


# ---------------------------------------------------------------------------
# gbrain_search — empty results
# ---------------------------------------------------------------------------

class TestSearchEmpty:
    """gbrain_search handles empty result sets gracefully."""

    def test_empty_array_returns_empty_results(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_search")
        create_mock, _ = _mock_subprocess(stdout=b"[]", returncode=0)

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"query": "xyzzy nonexistent"})
            )

        parsed = json.loads(result)
        assert parsed["results"] == []

    def test_empty_stdout_returns_empty_results(self, real_plugin_ctx):
        """Completely empty stdout is handled the same as []."""
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_search")
        create_mock, _ = _mock_subprocess(stdout=b"", returncode=0)

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"query": "nothing here"})
            )

        parsed = json.loads(result)
        assert parsed["results"] == []


# ---------------------------------------------------------------------------
# gbrain_search — gbrain not installed
# ---------------------------------------------------------------------------

class TestSearchNotInstalled:
    """FileNotFoundError from subprocess means gbrain CLI is missing."""

    def test_file_not_found_gives_helpful_error(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_search")
        create_mock = AsyncMock(side_effect=FileNotFoundError("No such file"))

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"query": "test"})
            )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "not found" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# gbrain_search — connection error (non-zero exit)
# ---------------------------------------------------------------------------

class TestSearchConnectionError:
    """Non-zero exit code from gbrain means a backend error."""

    def test_nonzero_exit_returns_error_with_stderr(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_search")
        create_mock, _ = _mock_subprocess(
            stderr=b"FATAL: connection to server refused", returncode=1
        )

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"query": "test"})
            )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "connection" in parsed["error"].lower() or "failed" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# gbrain_search — malformed JSON
# ---------------------------------------------------------------------------

class TestSearchMalformedJson:
    """Garbled stdout from gbrain is handled without crashing."""

    def test_invalid_json_returns_parse_error(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_search")
        create_mock, _ = _mock_subprocess(
            stdout=b"<html>Error page</html>", returncode=0
        )

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"query": "test"})
            )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "parse" in parsed["error"].lower() or "json" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# gbrain_search — custom limit
# ---------------------------------------------------------------------------

class TestSearchCustomLimit:
    """The limit parameter flows through to the CLI --limit flag."""

    def test_custom_limit_forwarded_to_cli(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_search")
        create_mock, _ = _mock_subprocess(stdout=b"[]", returncode=0)

        with patch("asyncio.create_subprocess_exec", create_mock):
            asyncio.get_event_loop().run_until_complete(
                handler({"query": "customer list", "limit": 10})
            )

        args = create_mock.call_args[0]
        limit_idx = list(args).index("--limit")
        assert args[limit_idx + 1] == "10"

    def test_default_limit_is_5(self, real_plugin_ctx):
        """Omitting limit defaults to 5."""
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_search")
        create_mock, _ = _mock_subprocess(stdout=b"[]", returncode=0)

        with patch("asyncio.create_subprocess_exec", create_mock):
            asyncio.get_event_loop().run_until_complete(
                handler({"query": "test"})
            )

        args = create_mock.call_args[0]
        limit_idx = list(args).index("--limit")
        assert args[limit_idx + 1] == "5"


# ---------------------------------------------------------------------------
# gbrain_search — missing query
# ---------------------------------------------------------------------------

class TestSearchMissingQuery:
    """Missing query parameter produces an error, not a crash."""

    def test_missing_query_returns_error(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_search")

        # No mock needed — handler should short-circuit before subprocess
        result = asyncio.get_event_loop().run_until_complete(
            handler({})
        )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "query" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# gbrain_add_note — success
# ---------------------------------------------------------------------------

class TestAddNoteSuccess:
    """gbrain_add_note saves a note via gbrain put <slug> with body on stdin."""

    def test_success_returns_slug(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_add_note")
        create_mock, proc = _mock_subprocess(stdout=b"OK", returncode=0)

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"title": "My Great Note", "body": "Content here"})
            )

        parsed = json.loads(result)
        assert parsed["slug"] == "my-great-note"
        assert "saved" in parsed.get("result", "").lower()

        # Verify CLI args
        args = create_mock.call_args[0]
        assert "put" in args
        assert "my-great-note" in args

        # Verify body passed via stdin
        stdin_data = proc.communicate.call_args[1].get("input") or proc.communicate.call_args[0][0]
        assert stdin_data == b"Content here"


# ---------------------------------------------------------------------------
# gbrain_add_note — slugification
# ---------------------------------------------------------------------------

class TestAddNoteSlugification:
    """Various title formats produce correct slugs."""

    @pytest.mark.parametrize("title,expected_slug", [
        ("My Great Note", "my-great-note"),
        ("Hello, World!", "hello-world"),
        ("  spaces  ", "spaces"),
        ("UPPER_CASE", "upper-case"),
        ("already-a-slug", "already-a-slug"),
        ("Special!@#$%^&*()Chars", "special-chars"),
        ("multiple---hyphens", "multiple-hyphens"),
        ("trailing-", "trailing"),
        ("-leading", "leading"),
        ("CamelCaseTitle", "camelcasetitle"),
        ("123 Numbers", "123-numbers"),
    ])
    def test_slugification(self, title, expected_slug, real_plugin_ctx):
        """Title is slugified correctly before being passed to gbrain put."""
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_add_note")
        create_mock, _ = _mock_subprocess(stdout=b"OK", returncode=0)

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"title": title, "body": "content"})
            )

        parsed = json.loads(result)
        assert parsed["slug"] == expected_slug

        # Verify the slug was passed to the CLI
        args = create_mock.call_args[0]
        assert expected_slug in args


# ---------------------------------------------------------------------------
# gbrain_add_note — subprocess error
# ---------------------------------------------------------------------------

class TestAddNoteSubprocessError:
    """Non-zero exit from gbrain put produces an error response."""

    def test_nonzero_exit_returns_error(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_add_note")
        create_mock, _ = _mock_subprocess(
            stderr=b"permission denied", returncode=1
        )

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"title": "Test Note", "body": "content"})
            )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "permission denied" in parsed["error"].lower() or "failed" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# gbrain_add_note — special chars in body
# ---------------------------------------------------------------------------

class TestAddNoteSpecialBody:
    """Body with quotes, newlines, and unicode is passed correctly via stdin."""

    def test_body_with_special_chars(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_add_note")
        create_mock, proc = _mock_subprocess(stdout=b"OK", returncode=0)

        body = 'He said "hello"\nNew line\nUnicode: \u00e9\u00e0\u00fc \U0001f600'

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"title": "Special Body", "body": body})
            )

        parsed = json.loads(result)
        assert parsed["slug"] == "special-body"

        # Verify the exact body bytes were passed via stdin
        stdin_data = proc.communicate.call_args[1].get("input") or proc.communicate.call_args[0][0]
        assert stdin_data == body.encode("utf-8")

    def test_body_with_markdown(self, real_plugin_ctx):
        """Markdown-formatted body with headers, lists, and code blocks."""
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_add_note")
        create_mock, proc = _mock_subprocess(stdout=b"OK", returncode=0)

        body = "# Title\n\n- item 1\n- item 2\n\n```python\nprint('hello')\n```"

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"title": "Markdown Note", "body": body})
            )

        parsed = json.loads(result)
        assert "error" not in parsed
        stdin_data = proc.communicate.call_args[1].get("input") or proc.communicate.call_args[0][0]
        assert stdin_data == body.encode("utf-8")


# ---------------------------------------------------------------------------
# gbrain_add_note — missing parameters
# ---------------------------------------------------------------------------

class TestAddNoteMissingParams:
    """Missing title or body produces errors."""

    def test_missing_title_returns_error(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_add_note")
        result = asyncio.get_event_loop().run_until_complete(
            handler({"body": "some content"})
        )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "title" in parsed["error"].lower()

    def test_missing_body_returns_error(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_add_note")
        result = asyncio.get_event_loop().run_until_complete(
            handler({"title": "Some Title"})
        )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "body" in parsed["error"].lower()

    def test_empty_slug_title_returns_error(self, real_plugin_ctx):
        """A title that produces an empty slug after sanitization."""
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_add_note")
        result = asyncio.get_event_loop().run_until_complete(
            handler({"title": "!@#$%", "body": "content"})
        )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "slug" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# gbrain_add_note — FileNotFoundError
# ---------------------------------------------------------------------------

class TestAddNoteNotInstalled:
    """FileNotFoundError from subprocess means gbrain CLI is missing."""

    def test_file_not_found_gives_helpful_error(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        handler = _get_handler(None, "gbrain_add_note")
        create_mock = AsyncMock(side_effect=FileNotFoundError("No such file"))

        with patch("asyncio.create_subprocess_exec", create_mock):
            result = asyncio.get_event_loop().run_until_complete(
                handler({"title": "Test", "body": "content"})
            )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "not found" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

class TestAvailabilityCheck:
    """check_fn from the registered tools verifies gbrain CLI presence."""

    def test_check_fn_true_when_which_finds_gbrain(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        from tools.registry import registry, invalidate_check_fn_cache
        invalidate_check_fn_cache()
        entry = registry.get_entry("gbrain_search")

        with patch("shutil.which", return_value="/usr/local/bin/gbrain"):
            assert entry.check_fn() is True

    def test_check_fn_true_when_bun_path_exists(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        from tools.registry import registry, invalidate_check_fn_cache
        invalidate_check_fn_cache()
        entry = registry.get_entry("gbrain_search")

        with patch("shutil.which", return_value=None), \
             patch.object(Path, "exists", return_value=True):
            assert entry.check_fn() is True

    def test_check_fn_false_when_missing(self, real_plugin_ctx):
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        from tools.registry import registry, invalidate_check_fn_cache
        invalidate_check_fn_cache()
        entry = registry.get_entry("gbrain_search")

        with patch("shutil.which", return_value=None), \
             patch.object(Path, "exists", return_value=False):
            assert entry.check_fn() is False

    def test_both_tools_share_same_check_fn(self, real_plugin_ctx):
        """Both tools use the same availability gate."""
        ctx, _ = real_plugin_ctx
        mod = _import_plugin()
        mod.register(ctx)

        from tools.registry import registry
        search_entry = registry.get_entry("gbrain_search")
        add_entry = registry.get_entry("gbrain_add_note")
        assert search_entry.check_fn is add_entry.check_fn
