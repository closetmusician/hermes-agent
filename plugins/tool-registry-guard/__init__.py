"""
ABOUTME: Tool Registry Guard plugin for Hermes.
ABOUTME: Deterministic enforcement: blocks pip-install and import of banned
ABOUTME: packages, injecting a condensed registry table into LLM context so
ABOUTME: the agent knows which tools to use instead.
ABOUTME: Stateless — registry is read-only config loaded once at startup.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Module-level state (populated by _load_registry)
# ---------------------------------------------------------------------------

_registry: Optional[Dict[str, Any]] = None
_banned_packages: Set[str] = set()
_banned_imports: Set[str] = set()
# Maps each banned import name to its registered tool dict for block messages
_import_to_tool: Dict[str, Dict[str, str]] = {}
# Maps each banned package name to its registered tool dict
_package_to_tool: Dict[str, Dict[str, str]] = {}
# Pre-built context string for LLM injection
_llm_context_block: str = ""
# Audit log path
_audit_path: Optional[Path] = None

# Tools that generate code and should be scanned/audited
_CODEGEN_TOOLS = {"write_file", "patch", "terminal"}


# ---------------------------------------------------------------------------
# Registry loading
# ---------------------------------------------------------------------------

def _find_registry_path() -> Optional[str]:
    """Find the registry YAML file in priority order.

    Checks user override at ~/.hermes/tool-registry.yaml first, then
    falls back to the bundled default-registry.yaml shipped with the
    plugin.

    Returns:
        Absolute path string, or None if neither exists.
    """
    # 1. User override
    hermes_home = os.environ.get("HERMES_HOME") or str(
        Path.home() / ".hermes"
    )
    user_path = Path(hermes_home) / "tool-registry.yaml"
    if user_path.exists():
        return str(user_path)

    # 2. Bundled default
    default_path = Path(__file__).parent / "default-registry.yaml"
    if default_path.exists():
        return str(default_path)

    return None


def _load_registry(path: Optional[str] = None) -> None:
    """Load the tool registry from YAML and build derived data structures.

    Called once at plugin registration. If path is None, auto-discovers
    the registry file via _find_registry_path(). Populates module-level
    _registry, _banned_packages, _banned_imports, and lookup maps.

    Args:
        path: Explicit registry file path, or None for auto-discovery.
    """
    global _registry, _banned_packages, _banned_imports
    global _import_to_tool, _package_to_tool, _llm_context_block

    if yaml is None:
        logger.warning("tool-registry-guard: PyYAML not installed, plugin disabled")
        _registry = {"version": 0, "tools": []}
        return

    registry_path = path or _find_registry_path()
    if registry_path is None:
        logger.warning("tool-registry-guard: no registry file found, plugin disabled")
        _registry = {"version": 0, "tools": []}
        return

    with open(registry_path, encoding="utf-8") as f:
        _registry = yaml.safe_load(f) or {}

    if "tools" not in _registry:
        _registry["tools"] = []

    # Build flat sets and reverse mappings
    _banned_packages = set()
    _banned_imports = set()
    _import_to_tool = {}
    _package_to_tool = {}

    for tool in _registry["tools"]:
        alts = tool.get("banned_alternatives", {})
        tool_info = {"name": tool["name"], "path": tool.get("path", "")}

        for pkg in alts.get("packages", []):
            _banned_packages.add(pkg)
            _package_to_tool[pkg] = tool_info

        for imp in alts.get("imports", []):
            _banned_imports.add(imp)
            _import_to_tool[imp] = tool_info

    # Build the pre-formatted LLM context block
    _llm_context_block = _build_llm_context()

    logger.info(
        "tool-registry-guard: loaded %d tools, %d banned packages, "
        "%d banned imports from %s",
        len(_registry["tools"]),
        len(_banned_packages),
        len(_banned_imports),
        registry_path,
    )


def _build_llm_context() -> str:
    """Build the condensed registry block injected into LLM context.

    Generates a compact table of registered tools and a list of all
    banned packages. Designed to fit within ~300 tokens.

    Returns:
        Formatted context string for injection into user messages.
    """
    if not _registry or not _registry.get("tools"):
        return ""

    lines = [
        "TOOL REGISTRY -- CHECK BEFORE WRITING CODE:",
        "Before writing code for file I/O, Office docs, Excel, SharePoint, or similar operations,",
        "you MUST use these registered tools instead of writing custom code:",
        "",
        "| Tool | Path | Operations | Run --help for details |",
        "|------|------|------------|----------------------|",
    ]

    for tool in _registry["tools"]:
        ops = ", ".join(tool.get("operations", []))
        path = tool.get("path", "")
        # Build help command based on file extension
        if path.endswith(".js"):
            help_cmd = f"node {path} --help"
        elif path.endswith(".py"):
            help_cmd = f"uv run --with lxml {path} --help"
        else:
            help_cmd = f"{path} --help"
        lines.append(f"| {tool['name']} | {path} | {ops} | `{help_cmd}` |")

    lines.append("")
    if _banned_packages:
        pkg_list = ", ".join(sorted(_banned_packages))
        lines.append(f"BANNED packages (will be blocked): {pkg_list}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Block message formatting
# ---------------------------------------------------------------------------

def _format_block_message(
    violation_type: str,
    matched: str,
    tool_info: Dict[str, str],
) -> str:
    """Build a human-readable block message for a registry violation.

    Includes the banned package/import name, the registered tool to use
    instead, and the --help command for that tool.

    Args:
        violation_type: "package" or "import" for the message prefix.
        matched: The specific banned name that was detected.
        tool_info: Dict with "name" and "path" keys for the registered tool.

    Returns:
        Block message string.
    """
    path = tool_info.get("path", "")
    if path.endswith(".js"):
        help_cmd = f"node {path} --help"
    elif path.endswith(".py"):
        help_cmd = f"uv run --with lxml {path} --help"
    else:
        help_cmd = f"{path} --help"

    return (
        f"REGISTRY VIOLATION: '{matched}' is banned. "
        f"Use {tool_info['name']} instead: `{help_cmd}`"
    )


# ---------------------------------------------------------------------------
# pre_tool_call hook — enforcement
# ---------------------------------------------------------------------------

# Pre-compiled regex patterns for matching pip install and import statements.
# Each pattern uses word boundaries to avoid substring false positives.

def _build_pip_pattern(banned: Set[str]) -> Optional[re.Pattern]:
    """Build regex matching pip/pip3 install of any banned package.

    Uses word-boundary anchors and alternation. Returns None if the
    banned set is empty.
    """
    if not banned:
        return None
    escaped = [re.escape(p) for p in sorted(banned, key=len, reverse=True)]
    alt = "|".join(escaped)
    return re.compile(
        rf"\bpip3?\s+install\s+(?:.*\s)?({alt})\b",
        re.IGNORECASE,
    )


def _build_import_pattern(banned: Set[str]) -> Optional[re.Pattern]:
    """Build regex matching Python import of any banned module.

    Matches both 'import <name>' and 'from <name> import ...' forms,
    with optional leading whitespace. Returns None if the banned set
    is empty.
    """
    if not banned:
        return None
    escaped = [re.escape(p) for p in sorted(banned, key=len, reverse=True)]
    alt = "|".join(escaped)
    return re.compile(
        rf"^\s*(?:import|from)\s+({alt})\b",
        re.MULTILINE,
    )


def _build_inline_import_pattern(banned: Set[str]) -> Optional[re.Pattern]:
    """Build regex matching inline Python import in shell one-liners.

    Catches patterns like python -c "import pandas" or
    python3 -c "from docx import ...". Returns None if the banned
    set is empty.
    """
    if not banned:
        return None
    escaped = [re.escape(p) for p in sorted(banned, key=len, reverse=True)]
    alt = "|".join(escaped)
    return re.compile(
        rf"\bimport\s+({alt})\b|from\s+({alt})\b",
    )


# Patterns are built lazily after registry load
_pip_pattern: Optional[re.Pattern] = None
_import_pattern: Optional[re.Pattern] = None
_inline_import_pattern: Optional[re.Pattern] = None


def _ensure_patterns() -> None:
    """Compile regex patterns from the current banned sets if not yet built.

    Called lazily on first pre_tool_call invocation. Separated from
    _load_registry so tests can reload the registry and get fresh patterns.
    """
    global _pip_pattern, _import_pattern, _inline_import_pattern
    _pip_pattern = _build_pip_pattern(_banned_packages)
    _import_pattern = _build_import_pattern(_banned_imports)
    _inline_import_pattern = _build_inline_import_pattern(_banned_imports)


def _check_terminal(command: str) -> Optional[Dict[str, Any]]:
    """Scan a terminal command for registry violations.

    Checks for pip install of banned packages and inline Python imports
    of banned modules. Returns a block directive dict or None.

    Args:
        command: Shell command string from terminal tool args.

    Returns:
        Block dict with action/message or None if clean.
    """
    if not command:
        return None

    # Check pip install
    if _pip_pattern:
        m = _pip_pattern.search(command)
        if m:
            matched = m.group(1)
            tool_info = _package_to_tool.get(matched, {})
            return {
                "action": "block",
                "message": _format_block_message("package", matched, tool_info),
            }

    # Check python -c inline imports
    if _inline_import_pattern and re.search(r"\bpython3?\b", command):
        m = _inline_import_pattern.search(command)
        if m:
            matched = m.group(1) or m.group(2)
            tool_info = _import_to_tool.get(matched, {})
            return {
                "action": "block",
                "message": _format_block_message("import", matched, tool_info),
            }

    return None


def _check_file_content(content: str) -> Optional[Dict[str, Any]]:
    """Scan file content for banned Python imports.

    Checks for 'import <banned>' and 'from <banned> import' patterns.
    Returns a block directive dict or None.

    Args:
        content: File content string from write_file/patch tool args.

    Returns:
        Block dict with action/message or None if clean.
    """
    if not content or not _import_pattern:
        return None

    m = _import_pattern.search(content)
    if m:
        matched = m.group(1)
        tool_info = _import_to_tool.get(matched, {})
        return {
            "action": "block",
            "message": _format_block_message("import", matched, tool_info),
        }

    return None


def _pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    **_: Any,
) -> Optional[Dict[str, Any]]:
    """Pre-tool-call hook: block banned package installs and imports.

    Scans terminal commands for pip install of banned packages and
    python -c inline imports. Scans write_file/patch content for
    banned import statements. Returns None to allow or a block dict.

    Args:
        tool_name: Name of the tool being called.
        args: Tool call arguments dict.

    Returns:
        None to allow, or {"action": "block", "message": "..."} to block.
    """
    if not _registry or not _registry.get("tools"):
        return None

    _ensure_patterns()

    if args is None:
        args = {}

    if tool_name == "terminal":
        command = args.get("command", "")
        if isinstance(command, str):
            return _check_terminal(command)

    elif tool_name in {"write_file", "patch"}:
        content = args.get("content", "")
        if isinstance(content, str):
            return _check_file_content(content)

    return None


# ---------------------------------------------------------------------------
# pre_llm_call hook — registry injection
# ---------------------------------------------------------------------------

def _pre_llm_call(
    session_id: str = "",
    user_message: str = "",
    conversation_history: Optional[list] = None,
    is_first_turn: bool = False,
    model: str = "",
    **_: Any,
) -> Optional[Dict[str, str]]:
    """Pre-LLM-call hook: inject registry context into the user message.

    Returns a dict with a "context" key containing the condensed registry
    table. This gets appended to the current turn's user message by
    the conversation loop.

    Returns:
        Dict with context string or None if registry is empty.
    """
    if not _llm_context_block:
        return None
    return {"context": _llm_context_block}


# ---------------------------------------------------------------------------
# post_tool_call hook — audit logging
# ---------------------------------------------------------------------------

def _detect_violation_in_args(
    tool_name: str,
    args: Dict[str, Any],
) -> Optional[str]:
    """Check tool args for banned patterns without blocking.

    Used by the audit logger to flag violations in post_tool_call
    (after the call has already executed). Returns the matched rule
    name or None.

    Args:
        tool_name: Name of the tool that was called.
        args: Tool call arguments dict.

    Returns:
        Matched rule name string or None.
    """
    _ensure_patterns()

    if tool_name == "terminal":
        cmd = args.get("command", "")
        if isinstance(cmd, str) and _pip_pattern:
            m = _pip_pattern.search(cmd)
            if m:
                return m.group(1)
        if isinstance(cmd, str) and _inline_import_pattern:
            m = _inline_import_pattern.search(cmd)
            if m:
                return m.group(1) or m.group(2)

    elif tool_name in {"write_file", "patch"}:
        content = args.get("content", "")
        if isinstance(content, str) and _import_pattern:
            m = _import_pattern.search(content)
            if m:
                return m.group(1)

    return None


def _post_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    **_: Any,
) -> None:
    """Post-tool-call hook: log code-gen calls to audit JSONL.

    Appends a JSON line to the audit file for every write_file,
    patch, or terminal tool call. Each entry records whether a
    registry violation was detected.

    Args:
        tool_name: Name of the tool that was called.
        args: Tool call arguments dict.
        result: Tool execution result (unused, logged for context).
    """
    if tool_name not in _CODEGEN_TOOLS:
        return

    if _audit_path is None:
        return

    if args is None:
        args = {}

    matched_rule = _detect_violation_in_args(tool_name, args)

    # Build truncated args summary (avoid logging full file contents)
    args_summary = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 200:
            args_summary[k] = v[:200] + "..."
        else:
            args_summary[k] = v

    entry = {
        "ts": time.time(),
        "tool": tool_name,
        "args_summary": args_summary,
        "violation": matched_rule is not None,
        "matched_rule": matched_rule,
    }

    try:
        _audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(_audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.debug("tool-registry-guard audit write failed: %s", exc)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register hooks with the Hermes plugin system.

    Loads the tool registry, sets up the audit log path, and
    registers pre_tool_call, pre_llm_call, and post_tool_call hooks.

    Args:
        ctx: PluginContext provided by the Hermes plugin manager.
    """
    global _audit_path

    _load_registry()

    # Set up audit log path
    hermes_home = os.environ.get("HERMES_HOME") or str(
        Path.home() / ".hermes"
    )
    _audit_path = Path(hermes_home) / "tool-registry-guard" / "audit.jsonl"

    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
