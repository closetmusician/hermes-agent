# ABOUTME: gbrain-memory plugin for Hermes.
# ABOUTME: Wraps the gbrain CLI (Postgres/pgvector hybrid search knowledge base)
# ABOUTME: as agent tools for durable knowledge storage and retrieval.
# ABOUTME: Provides gbrain_search (hybrid vector+keyword search) and
# ABOUTME: gbrain_add_note (save knowledge notes by slug).

"""gbrain-memory plugin — durable knowledge via the gbrain CLI.

Exposes Yu-Kuan's personal knowledge base (790+ pages, Postgres/pgvector
hybrid search) as Hermes tools. Wraps the ``~/.bun/bin/gbrain`` CLI via
asyncio.create_subprocess_exec so the agent can search and add notes
without blocking the event loop.

Two tools:
  gbrain_search   — hybrid vector+keyword search over the knowledge base.
  gbrain_add_note — save a markdown note to the knowledge base by slug.

NOT a session-memory provider. This is durable knowledge only:
facts, preferences, people, decisions, procedures that persist across
all sessions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_GBRAIN_BIN = str(Path("~/.bun/bin/gbrain").expanduser())


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def _check_gbrain() -> bool:
    """Return True when the gbrain CLI is reachable.

    Checks PATH via shutil.which first, then the well-known bun install
    location. Used as check_fn so tools only appear when gbrain exists.
    """
    if shutil.which("gbrain") is not None:
        return True
    return Path(_GBRAIN_BIN).exists()


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------

def _slugify(title: str) -> str:
    """Convert a note title to a URL-safe slug.

    Lowercase, replace non-alphanumeric chars with hyphens, collapse
    consecutive hyphens, strip leading/trailing hyphens.
    """
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    return slug


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = {
    "name": "gbrain_search",
    "description": (
        "Search the personal knowledge base (gbrain) for facts, preferences, "
        "people, decisions, procedures, and work documents. Uses hybrid "
        "vector+keyword search."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return (default: 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

ADD_NOTE_SCHEMA = {
    "name": "gbrain_add_note",
    "description": (
        "Save a knowledge note to the personal knowledge base (gbrain). "
        "For durable facts, decisions, preferences, and procedures that "
        "should persist across sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Note title (will be slugified for storage)",
            },
            "body": {
                "type": "string",
                "description": "Note content in markdown format",
            },
        },
        "required": ["title", "body"],
    },
}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def _handle_search(args: dict) -> str:
    """Run gbrain query and return formatted results.

    Executes: ~/.bun/bin/gbrain query "<query>" --limit <N> --json
    Parses JSON output and returns results with title, slug, snippet.
    """
    query = args.get("query", "")
    if not query:
        return json.dumps({"error": "Missing required parameter: query"})

    limit = args.get("limit", 5)

    try:
        proc = await asyncio.create_subprocess_exec(
            _GBRAIN_BIN, "query", query, "--limit", str(limit), "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            return json.dumps({"error": f"gbrain query failed: {err_msg or 'exit code ' + str(proc.returncode)}"})

        raw = stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            return json.dumps({"results": []})

        results = json.loads(raw)
        if not isinstance(results, list):
            results = []

        formatted = []
        for item in results:
            formatted.append({
                "title": item.get("title", ""),
                "slug": item.get("slug", ""),
                "snippet": item.get("content", item.get("snippet", "")),
            })

        return json.dumps({"results": formatted})

    except FileNotFoundError:
        return json.dumps({"error": "gbrain CLI not found. Install it or check ~/.bun/bin/gbrain."})
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Failed to parse gbrain output: {exc}"})
    except Exception as exc:
        return json.dumps({"error": f"gbrain search failed: {exc}"})


async def _handle_add_note(args: dict) -> str:
    """Save a note via gbrain put.

    Slugifies the title and pipes the body via stdin:
      echo "<body>" | ~/.bun/bin/gbrain put <slug>
    """
    title = args.get("title", "")
    body = args.get("body", "")

    if not title:
        return json.dumps({"error": "Missing required parameter: title"})
    if not body:
        return json.dumps({"error": "Missing required parameter: body"})

    slug = _slugify(title)
    if not slug:
        return json.dumps({"error": "Title produced an empty slug after sanitization"})

    try:
        proc = await asyncio.create_subprocess_exec(
            _GBRAIN_BIN, "put", slug,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=body.encode("utf-8"))

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            return json.dumps({"error": f"gbrain put failed: {err_msg or 'exit code ' + str(proc.returncode)}"})

        return json.dumps({"result": "Note saved.", "slug": slug})

    except FileNotFoundError:
        return json.dumps({"error": "gbrain CLI not found. Install it or check ~/.bun/bin/gbrain."})
    except Exception as exc:
        return json.dumps({"error": f"gbrain add_note failed: {exc}"})


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register gbrain tools with the Hermes plugin system.

    Provides gbrain_search and gbrain_add_note as async tools in the
    gbrain_memory toolset. Both tools are gated behind _check_gbrain
    so they only appear when the gbrain CLI is installed.
    """
    ctx.register_tool(
        name="gbrain_search",
        toolset="gbrain_memory",
        schema=SEARCH_SCHEMA,
        handler=_handle_search,
        check_fn=_check_gbrain,
        is_async=True,
        description="Search the personal knowledge base (gbrain) via hybrid vector+keyword search.",
    )

    ctx.register_tool(
        name="gbrain_add_note",
        toolset="gbrain_memory",
        schema=ADD_NOTE_SCHEMA,
        handler=_handle_add_note,
        check_fn=_check_gbrain,
        is_async=True,
        description="Save a durable knowledge note to the personal knowledge base (gbrain).",
    )
