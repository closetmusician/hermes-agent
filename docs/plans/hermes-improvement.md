# Hermes Agent: Deterministic Enforcement Plan

*Goal: Make the agent UNABLE to skip steps, not just INSTRUCTED not to skip them.*

---

## Architecture Context

Hermes tool execution pipeline (`agent/tool_executor.py`):
```
1. FIRST GATE:  get_pre_tool_call_block_message()  ← plugin hook, blocks with error
2. SECOND GATE: agent._tool_guardrails.before_call()  ← allow/warn/block/halt
3. Checkpoint (before file-mutating tools)
4. Execution: agent._invoke_tool()
5. Post-result: _append_guardrail_observation()
```

Key fact: Hermes sends email via `send_message` tool with `target: "email"` or `target: "email:recipient"`,
backed by `tools/send_message_tool.py`. There is no standalone `send_email` tool.

---

## Phase 1: Email Send Guard Plugin (pre_tool_call enforcement)

**What**: A Hermes plugin that makes it impossible to send email without: (a) loading the exact draft from disk, (b) previewing it to the user, (c) receiving explicit user approval via slash command.

**Why this works**: `pre_tool_call` fires before execution in `tool_executor.py`. The plugin returns `{"action": "block", "message": "..."}` and the tool never runs. The model cannot bypass this — it's code, not instructions.

### File Layout

```
~/.hermes/plugins/email-send-guard/
├── plugin.yaml
└── __init__.py
```

### plugin.yaml

```yaml
name: email-send-guard
version: "0.1.0"
description: Deterministic email send policy — blocks sends without loaded+previewed+approved draft.
author: yklin
provides_hooks:
  - pre_tool_call
provides_tools:
  - email_load_draft
  - email_show_preview
```

### Enable in ~/.hermes/config.yaml

```yaml
plugins:
  enabled:
    - email-send-guard
```

### __init__.py (full implementation)

```python
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

STATE_PATH = Path.home() / ".hermes" / "email-send-guard" / "state.json"
APPROVAL_TTL_SECONDS = 15 * 60  # 15 minutes


def _now() -> int:
    return int(time.time())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm_body(text: str) -> str:
    """Only normalize line endings. Strict enough to prevent content drift."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"drafts": {}, "current": None, "approvals": {}}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _email_send_payload(tool_name: str, args: dict[str, Any]) -> tuple[bool, str]:
    """Detect email send attempts across tool variants."""
    if tool_name == "send_email":
        return True, str(args.get("body") or args.get("message") or "")

    if tool_name == "send_message":
        if args.get("action", "send") != "send":
            return False, ""
        target = str(args.get("target") or "").strip().lower()
        if target == "email" or target.startswith("email:"):
            return True, str(args.get("message") or "")

    return False, ""


def email_load_draft(args: dict[str, Any], **_: Any) -> str:
    """Load an email draft from disk. Records its exact content hash."""
    path = Path(str(args.get("path") or "")).expanduser()
    if not path.is_file():
        return json.dumps({"success": False, "error": f"Draft not found: {path}"})

    body = _norm_body(path.read_text(encoding="utf-8"))
    digest = _sha256(body)

    state = _load_state()
    state["drafts"][digest] = {
        "path": str(path),
        "body": body,
        "loaded_at": _now(),
        "previewed_at": None,
    }
    state["current"] = digest
    _save_state(state)

    return json.dumps({
        "success": True,
        "draft_id": digest,
        "path": str(path),
        "sha256": digest,
        "bytes": len(body.encode("utf-8")),
    })


def email_show_preview(args: dict[str, Any], **_: Any) -> str:
    """Show exact draft body to user. Required before approval."""
    state = _load_state()
    draft_id = str(args.get("draft_id") or state.get("current") or "")
    draft = state.get("drafts", {}).get(draft_id)
    if not draft:
        return json.dumps({"success": False, "error": "No loaded draft. Call email_load_draft first."})

    draft["previewed_at"] = _now()
    state["drafts"][draft_id] = draft
    _save_state(state)

    return json.dumps({
        "success": True,
        "draft_id": draft_id,
        "path": draft["path"],
        "body": draft["body"],
        "approval_command": f"/approve-email {draft_id}",
    })


def approve_email(raw_args: str) -> str:
    """User-only slash command. The model CANNOT call this."""
    draft_id = raw_args.strip()
    state = _load_state()
    draft = state.get("drafts", {}).get(draft_id)
    if not draft:
        return f"No loaded draft for id {draft_id}"

    if not draft.get("previewed_at"):
        return "Draft has not been previewed. Run email_show_preview first."

    state.setdefault("approvals", {})[draft_id] = {
        "approved_at": _now(),
        "expires_at": _now() + APPROVAL_TTL_SECONDS,
    }
    _save_state(state)
    return f"Approved email draft {draft_id} for {APPROVAL_TTL_SECONDS // 60} minutes."


def pre_tool_call(tool_name: str, args: dict[str, Any], **_: Any):
    """The enforcement gate. Fires before every tool call."""
    is_email_send, body = _email_send_payload(tool_name, args)
    if not is_email_send:
        return None  # Not an email send, allow

    body = _norm_body(body)
    body_hash = _sha256(body)
    state = _load_state()

    # Gate 1: body must match a loaded draft
    draft = state.get("drafts", {}).get(body_hash)
    if not draft:
        return {
            "action": "block",
            "message": (
                "BLOCKED: Email body does not match any draft loaded with email_load_draft(path=...). "
                "Load the approved draft file first."
            ),
        }

    # Gate 2: draft must have been previewed
    if not draft.get("previewed_at"):
        return {
            "action": "block",
            "message": "BLOCKED: Draft loaded but not previewed. Call email_show_preview first.",
        }

    # Gate 3: user must have approved via /approve-email
    approval = state.get("approvals", {}).get(body_hash)
    if not approval or int(approval.get("expires_at", 0)) < _now():
        return {
            "action": "block",
            "message": (
                f"BLOCKED: User approval missing or expired. "
                f"User must run: /approve-email {body_hash}"
            ),
        }

    return None  # All gates passed


def register(ctx):
    """Plugin registration — hooks, commands, and tools."""
    ctx.register_hook("pre_tool_call", pre_tool_call)

    ctx.register_command(
        "approve-email",
        approve_email,
        description="Approve a previewed email draft for sending.",
        args_hint="<draft_id>",
    )

    ctx.register_tool(
        name="email_load_draft",
        toolset="email_guard",
        description="Load an email draft from disk for guarded sending.",
        schema={
            "name": "email_load_draft",
            "description": "Load an email draft from disk and record its exact hash.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path to the draft file"}},
                "required": ["path"],
            },
        },
        handler=email_load_draft,
    )

    ctx.register_tool(
        name="email_show_preview",
        toolset="email_guard",
        description="Show the exact loaded email body before approval.",
        schema={
            "name": "email_show_preview",
            "description": "Display the loaded email draft body and approval command.",
            "parameters": {
                "type": "object",
                "properties": {"draft_id": {"type": "string", "description": "Hash of the draft (optional, uses current)"}},
                "required": [],
            },
        },
        handler=email_show_preview,
    )
```

### State Machine (enforced by code, not instructions)

```
EMPTY → email_load_draft(path) → LOADED[hash]
LOADED → email_show_preview(hash) → PREVIEWED[hash]
PREVIEWED → /approve-email hash → APPROVED[hash]   ← USER ONLY, not model-callable
APPROVED → send_message(body matching hash) → ALLOWED
```

Any attempt to skip a step returns a block with a clear error message telling the model what to do next.

---

## Phase 2: Local Control Room (Audit + Policy + Monitoring)

**What**: A local Hermes plugin providing: audit logging of all tool calls, composable policy enforcement, workflow state tracking, and a localhost monitoring UI.

**Why local**: No VPS needed. SQLite + localhost HTTP. Runs on macOS natively.

### File Layout

```
~/.hermes/plugins/control-room/
├── plugin.yaml
├── __init__.py          # hooks: audit logging + policy dispatch
├── policies/            # YAML-defined policy files
│   └── email-send.yaml
├── server.py            # localhost FastAPI/Flask UI
└── state.sqlite         # audit log + workflow state + approvals
```

### Schema (state.sqlite)

```sql
CREATE TABLE tool_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    phase TEXT NOT NULL,          -- 'pre' | 'post' | 'blocked'
    session_id TEXT,
    tool_name TEXT NOT NULL,
    args_json TEXT,
    result_json TEXT,
    blocked INTEGER DEFAULT 0,
    reason TEXT,
    duration_ms INTEGER
);

CREATE TABLE workflow_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE policy_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    policy_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    decision TEXT NOT NULL,       -- 'allow' | 'block' | 'ask'
    reason TEXT
);

CREATE INDEX idx_audit_ts ON tool_audit(ts);
CREATE INDEX idx_audit_tool ON tool_audit(tool_name);
```

### Plugin Hooks

```python
def pre_tool_call(tool_name, args, task_id="", session_id="", **kw):
    """Audit + policy enforcement before every tool call."""
    log_tool("pre", tool_name, args, session_id)

    # Run all active policies
    block_reason = evaluate_policies(tool_name, args, session_id)
    if block_reason:
        log_tool("blocked", tool_name, args, session_id, reason=block_reason)
        return {"action": "block", "message": block_reason}
    return None


def post_tool_call(tool_name, args, result, duration_ms=0, session_id="", **kw):
    """Audit logging after execution."""
    log_tool("post", tool_name, args, session_id, result=result, duration_ms=duration_ms)
```

### Localhost Monitoring

```bash
python ~/.hermes/plugins/control-room/server.py --port 8787
# Opens http://localhost:8787
```

Endpoints:
- `GET /tool-calls` — recent audit rows, filterable
- `GET /blocked` — blocked attempts with reasons
- `GET /workflows` — current workflow states
- `GET /policies` — loaded policies and their status

### YAML-Based Policy Definition (v2)

```yaml
# policies/email-send.yaml
name: email-send-guard
description: Require draft → preview → approval before email send
triggers:
  - tool: send_message
    condition: "args.target starts_with 'email'"
  - tool: send_email
preconditions:
  - state_key: "draft.{body_hash}.loaded"
    error: "Load draft first with email_load_draft"
  - state_key: "draft.{body_hash}.previewed"
    error: "Preview draft first with email_show_preview"
  - state_key: "approval.{body_hash}.valid"
    error: "User approval required: /approve-email {body_hash}"
```

---

## Phase 2a: Tool Registry Guard Plugin (pre_tool_call enforcement)

**What**: A Hermes plugin that deterministically forces the agent to use registered tools instead of reimplementing their functionality. Blocks tool calls that install banned packages, import banned modules, or write files importing banned modules — pointing to the correct registered tool.

**Why this works**: Same mechanism as Phase 1. `pre_tool_call` fires before execution, and `pre_llm_call` injects the registry into context so the agent always knows what tools exist before planning. The agent cannot bypass this — it's pattern matching on tool arguments, not instructions.

### File Layout

```
~/.hermes/plugins/tool-registry-guard/
├── plugin.yaml
├── __init__.py          # hooks: pre_llm_call, pre_tool_call, post_tool_call
└── ...
~/.hermes/tool-registry.yaml   # user-configurable registry of available tools
```

### plugin.yaml

```yaml
name: tool-registry-guard
version: "0.1.0"
description: Deterministic tool routing enforcement — blocks code that reimplements registered tool functionality.
author: yklin
provides_hooks:
  - pre_llm_call
  - pre_tool_call
  - post_tool_call
```

### Registry Format (~/.hermes/tool-registry.yaml)

```yaml
version: 1
tools:
  - name: graph-workbook
    path: ~/Code/pm_os/bin/graph-workbook.js
    description: "Excel read/write (ALL: read, write, format, formulas, ranges)"
    operations: [excel-read, excel-write, excel-format]
    banned_alternatives:
      packages: [pandas, openpyxl, xlrd, xlsxwriter]
      imports: [pandas, openpyxl, xlrd, xlsxwriter]

  - name: ooxml-surgery
    path: ~/Code/pm_os/bin/ooxml-surgery.py
    description: "Word/PPT package operations"
    operations: [word-read, word-write, pptx-read]
    banned_alternatives:
      packages: [python-docx, docx, mammoth, docx2txt, textract]
      imports: [docx, mammoth, docx2txt, textract]

  - name: graph-file-ops
    path: ~/Code/pm_os/bin/graph-file-ops.js
    description: "File download/upload from SharePoint/OneDrive"
    operations: [sharepoint-download, sharepoint-upload]
    banned_alternatives:
      packages: []
      imports: []

  - name: graph-edit-pptx
    path: ~/Code/pm_os/bin/graph-edit-pptx.js
    description: "Complex PPT edits (charts, images, tables)"
    operations: [pptx-chart, pptx-image, pptx-table]
    banned_alternatives:
      packages: [python-pptx, pptx]
      imports: [pptx]

  - name: graph-list-crud
    path: ~/Code/pm_os/bin/graph-list-crud.js
    description: "SharePoint list CRUD"
    operations: [sharepoint-list]
    banned_alternatives:
      packages: []
      imports: []
```

### Hooks

**1. `pre_llm_call` — Registry context injection**

Before every LLM call, appends a condensed `TOOL REGISTRY` block to the system prompt listing available tools and their operations. This ensures the agent always knows what exists before planning.

```python
def pre_llm_call(messages, **kw):
    """Inject condensed registry into system prompt context."""
    registry = _load_registry()
    if not registry:
        return None

    lines = ["## TOOL REGISTRY — Use these instead of writing custom code\n"]
    for tool in registry.get("tools", []):
        banned = tool.get("banned_alternatives", {})
        pkgs = banned.get("packages", [])
        lines.append(f"- **{tool['name']}**: {tool['description']}")
        lines.append(f"  Path: `{tool['path']}`")
        if pkgs:
            lines.append(f"  BANNED alternatives: {', '.join(pkgs)}")
    return {"system_suffix": "\n".join(lines)}
```

**2. `pre_tool_call` — Banned pattern blocking**

Blocks tool calls matching banned patterns:
- `bash` commands containing `pip install <banned_package>` or `pip3 install <banned_package>`
- `bash` commands containing `import <banned_module>` in inline Python (`python -c "..."`)
- `write_file` where the content imports banned modules

Returns a block message pointing to the correct registered tool with its `--help` command.

```python
def pre_tool_call(tool_name, args, **kw):
    """Block tool calls that reimplements registered tool functionality."""
    registry = _load_registry()
    if not registry:
        return None

    if tool_name == "bash":
        cmd = str(args.get("command", ""))
        for tool in registry.get("tools", []):
            banned = tool.get("banned_alternatives", {})
            for pkg in banned.get("packages", []):
                if f"pip install {pkg}" in cmd or f"pip3 install {pkg}" in cmd:
                    return {
                        "action": "block",
                        "message": (
                            f"REGISTRY VIOLATION: {pkg} is banned. "
                            f"Use {tool['name']} instead: "
                            f"`{tool['path']} --help`"
                        ),
                    }
            for mod in banned.get("imports", []):
                if f"import {mod}" in cmd:
                    return {
                        "action": "block",
                        "message": (
                            f"REGISTRY VIOLATION: import {mod} is banned. "
                            f"Use {tool['name']} instead: "
                            f"`{tool['path']} --help`"
                        ),
                    }

    if tool_name == "write_file":
        content = str(args.get("content", ""))
        for tool in registry.get("tools", []):
            banned = tool.get("banned_alternatives", {})
            for mod in banned.get("imports", []):
                if f"import {mod}" in content:
                    return {
                        "action": "block",
                        "message": (
                            f"REGISTRY VIOLATION: File imports {mod}. "
                            f"Use {tool['name']} instead: "
                            f"`{tool['path']} --help`"
                        ),
                    }

    return None
```

**3. `post_tool_call` — Audit logging**

Audits code-generation tool calls (`write_file`, `bash`) for retrospective analysis. Logs any near-misses or patterns that should be added to the registry.

```python
def post_tool_call(tool_name, args, result, **kw):
    """Audit code-generation tool calls for retrospective analysis."""
    if tool_name not in ("write_file", "bash"):
        return None
    # Log to stderr or structured log for offline review
    _audit_log(tool_name, args, result)
```

### Enforcement Behavior Examples

| Tool Call | Result |
|---|---|
| `bash: "pip install openpyxl"` | Block: "REGISTRY VIOLATION: openpyxl is banned. Use graph-workbook.js instead: `node ~/Code/pm_os/bin/graph-workbook.js --help`" |
| `write_file: content="import openpyxl\n..."` | Block: "REGISTRY VIOLATION: File imports openpyxl. Use graph-workbook.js instead." |
| `bash: "python -c 'import docx; ...'"` | Block: "REGISTRY VIOLATION: import docx is banned. Use ooxml-surgery.py instead." |
| `pre_llm_call` | Injects condensed registry so agent knows available tools before planning |

### State

Stateless — registry is read-only config, blocking is pattern-based. No state machine needed.

### Separation of Concerns

Phase 2a handles tool routing enforcement. Phase 2 (Control Room) handles audit logging. When both are active, Phase 2a blocks violations and Phase 2 logs them.

### ~LOC

~200 Python

---

## Phase 3: gbrain Memory Integration

**What**: A Hermes memory provider that uses the existing gbrain CLI (`~/.bun/bin/gbrain`) as the durable knowledge store.

**Why**: gbrain already has Postgres/pgvector, hybrid search, typed links, markdown pages. Don't rebuild what exists.

### Separation of Concerns

| Layer | System | Purpose |
|-------|--------|---------|
| Durable knowledge | gbrain | Facts, preferences, people, decisions, procedures |
| Session memory | Hermes built-in | Current task context, short-term operational state |
| Audit trail | control-room SQLite | What happened, when, what was blocked |

### File Layout

```
~/.hermes/plugins/gbrain-memory/
├── plugin.yaml
└── __init__.py
```

### Integration Points

```python
import subprocess
import json
from pathlib import Path

GBRAIN = str(Path.home() / ".bun" / "bin" / "gbrain")


def gbrain_query(query: str, limit: int = 5) -> str:
    """Hybrid search across gbrain pages."""
    result = subprocess.run(
        [GBRAIN, "query", query, "--limit", str(limit), "--json"],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout


def gbrain_put(slug: str, content: str) -> str:
    """Write/update a gbrain page."""
    result = subprocess.run(
        [GBRAIN, "put", slug],
        input=content, capture_output=True, text=True, timeout=10,
    )
    return result.stdout


def gbrain_search_tool(args: dict, **_) -> str:
    """Tool handler: search gbrain knowledge."""
    query = args.get("query", "")
    limit = args.get("limit", 5)
    return gbrain_query(query, limit)


def gbrain_add_note_tool(args: dict, **_) -> str:
    """Tool handler: add a note to gbrain."""
    title = args.get("title", "untitled")
    body = args.get("body", "")
    slug = title.lower().replace(" ", "-").replace("/", "-")
    content = f"# {title}\n\n{body}\n"
    return gbrain_put(slug, content)


def register(ctx):
    ctx.register_tool(
        name="gbrain_search",
        toolset="memory",
        description="Search local gbrain knowledge base (Postgres/pgvector hybrid search).",
        schema={
            "name": "gbrain_search",
            "description": "Search personal knowledge base.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
        handler=gbrain_search_tool,
    )

    ctx.register_tool(
        name="gbrain_add_note",
        toolset="memory",
        description="Add a note to gbrain knowledge base.",
        schema={
            "name": "gbrain_add_note",
            "description": "Write a knowledge note to gbrain.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["title", "body"],
            },
        },
        handler=gbrain_add_note_tool,
    )
```

---

## Known Failure Modes & Mitigations

| Failure Mode | Risk | Mitigation |
|---|---|---|
| Plugin disabled in config.yaml | All enforcement gone | Startup validator that warns if email-send-guard not enabled |
| Terminal bypass: `python -c 'smtplib...'` | Agent sends via shell | Terminal tool policy blocking SMTP commands/ports |
| Concurrent batch: load+preview+send in one tool batch | Send evaluated before preview state persisted | pre_tool_call evaluates sequentially; send blocks because state not yet written |
| Approval spoofing | Model tries to approve itself | /approve-email is a slash command (user-only), not a tool |
| Draft mutation after load | File changed between load and send | Re-read file at send time, compare hash (enhancement) |
| Whitespace manipulation | Alter body to bypass hash | Only normalize \r\n → \n, nothing else |
| Cross-session approval reuse | Stale approval from old session | Bind approval to session_id + TTL expiry |
| Recipient/subject spoofing | Right body, wrong recipient | Hash body + recipient + subject together (enhancement) |
| Alternate send paths (gateway adapters) | Platform sends outside tool system | Adapter-level policy needed if gateway routes email |
| State file race condition (multiple sessions) | Concurrent read/write corruption | Use SQLite WAL mode instead of JSON for production |

---

## Implementation Order

1. **Phase 1** — email-send-guard plugin. Immediate, self-contained, ~150 lines of Python.
2. **Phase 2** — control-room plugin with SQLite audit + policy engine. Migrate email-send-guard state into it.
3. **Phase 2a** — tool-registry-guard plugin. Stateless pattern-based enforcement, ~200 lines of Python. Independent of Phase 2 but complementary (2a blocks, 2 logs).
4. **Phase 3** — gbrain memory provider. Independent of phases 1-2.

---

## Appendix: Deprioritized Approaches (Soft/LLM-Dependent)

These approaches are valid for context/documentation but NOT enforcement. They cannot prevent the agent from skipping steps — they can only ask it not to.

### A. SOP-as-Skill (SKILL.md)

Create a pinned `send-email` skill in `~/.hermes/skills/` with step-by-step workflow instructions. The agent loads this as procedural memory.

**Why deprioritized**: The agent can still skip the skill if it doesn't load it, or deviate mid-workflow. Pinning helps availability but not compliance. Real-world failure: AGENTS.md mandated `/yk-voice` skill but agent wrote from memory instead of reading it.

### B. AGENTS.md / Context Files

Multiple context files defining operating procedures, loaded on session start.

**Why deprioritized**: Only applies inside the specific repo directory. Agent reads on start but can ignore mid-turn. No enforcement mechanism — purely informational.

### C. hermes-agent-control-room (github.com/shannhk/hermes-agent-control-room)

A template repo of markdown docs + SKILL.md files for governing Hermes agents on a VPS.

**Why deprioritized**: Zero executable code. Everything is LLM-dependent (skills the agent reads and hopefully follows). The task-bus is a file-path convention, not a message queue. The security auditor is a checklist the agent reads, not a hook that blocks. Useful as documentation structure inspiration, not as enforcement.

### D. Two-Phase Tool Pattern (Design Pattern Only)

Split operations into preview + confirm phases at the tool level.

**Why partially incorporated**: The email-send-guard plugin in Phase 1 implements this pattern via code (`email_load_draft` → `email_show_preview` → `/approve-email` → `send_message`). The pattern is good but must be enforced by a pre_tool_call hook, not by trusting the agent to call preview before commit.

---

## References

- Hermes plugin API: `register(ctx)` with `ctx.register_hook()`, `ctx.register_command()`, `ctx.register_tool()`
- Hermes tool execution: `agent/tool_executor.py` → `get_pre_tool_call_block_message()` → `before_call()`
- Hermes guardrails: `agent/tool_guardrails.py` — `ToolGuardrailDecision` (allow/warn/block/halt)
- Hermes config: `~/.hermes/config.yaml` with `plugins.enabled` list
- gbrain CLI: `~/.bun/bin/gbrain` (query, search, get, put, list, stats)
- Claude Code hooks (analogous pattern): PreToolUse with deny > defer > ask > allow precedence
