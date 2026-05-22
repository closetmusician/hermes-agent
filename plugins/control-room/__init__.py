"""control-room plugin — governance audit trail + policy engine + monitoring.

ABOUTME: Phase 2 governance plugin for Hermes. Provides three capabilities:
1. SQLite audit trail for ALL tool calls (pre/post/blocked).
2. YAML-based policy engine for declarative enforcement via preconditions.
3. Localhost HTTP monitoring UI with JSON API endpoints.

The plugin hooks into ``pre_tool_call`` (audit + policy evaluation) and
``post_tool_call`` (audit with result/duration). Policies are loaded from
``plugins/control-room/policies/`` and ``~/.hermes/control-room/policies/``
at startup. The monitoring server runs on ``localhost:8787`` in a daemon
thread.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AuditDB — SQLite audit trail + workflow state + policy log
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS tool_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    phase TEXT NOT NULL,
    session_id TEXT,
    tool_name TEXT NOT NULL,
    args_json TEXT,
    result_json TEXT,
    blocked INTEGER DEFAULT 0,
    reason TEXT,
    duration_ms INTEGER
);
CREATE TABLE IF NOT EXISTS workflow_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS policy_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    policy_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON tool_audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_tool ON tool_audit(tool_name);
"""


class AuditDB:
    """SQLite-backed audit trail, workflow state store, and policy log.

    Manages all persistence for the control-room plugin. Thread-safe
    via check_same_thread=False and a dedicated lock for writes.
    Creates parent directories and tables on init.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    # -- tool audit ----------------------------------------------------------

    def log_tool_call(
        self,
        phase: str,
        tool_name: str,
        args: Dict[str, Any],
        *,
        result: str = "",
        blocked: bool = False,
        reason: str = "",
        duration_ms: int = 0,
        session_id: str = "",
    ) -> None:
        """Insert a row into the tool_audit table.

        Called by pre/post hooks. phase is 'pre', 'post', or 'blocked'.
        args are serialised to JSON. Thread-safe via internal lock.
        """
        with self._lock:
            self._conn.execute(
                """INSERT INTO tool_audit
                   (ts, phase, session_id, tool_name, args_json,
                    result_json, blocked, reason, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(time.time()),
                    phase,
                    session_id,
                    tool_name,
                    json.dumps(args, ensure_ascii=False, default=str),
                    result,
                    1 if blocked else 0,
                    reason,
                    duration_ms,
                ),
            )
            self._conn.commit()

    def get_recent_calls(
        self, limit: int = 50, tool_name: str = "",
    ) -> List[Dict[str, Any]]:
        """Return recent audit rows, newest first.

        Optionally filters by tool_name. Returns list of dicts.
        """
        if tool_name:
            rows = self._conn.execute(
                "SELECT * FROM tool_audit WHERE tool_name = ? "
                "ORDER BY id DESC LIMIT ?",
                (tool_name, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tool_audit ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_blocked_calls(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return only blocked audit entries, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM tool_audit WHERE blocked = 1 "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- workflow state -------------------------------------------------------

    def set_workflow_state(self, key: str, value: Any) -> None:
        """Upsert a key/value pair in the workflow_state table.

        Value is JSON-serialised. Uses INSERT OR REPLACE for upsert.
        """
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO workflow_state
                   (key, value_json, updated_at) VALUES (?, ?, ?)""",
                (key, json.dumps(value, ensure_ascii=False, default=str), int(time.time())),
            )
            self._conn.commit()

    def get_workflow_state(self, key: str) -> Any:
        """Read a workflow state value by key. Returns None if missing."""
        row = self._conn.execute(
            "SELECT value_json FROM workflow_state WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["value_json"])

    def get_all_workflow_states(self) -> List[Dict[str, Any]]:
        """Return all workflow state entries."""
        rows = self._conn.execute(
            "SELECT key, value_json, updated_at FROM workflow_state "
            "ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- policy log -----------------------------------------------------------

    def log_policy_decision(
        self,
        policy_name: str,
        tool_name: str,
        decision: str,
        reason: str = "",
    ) -> None:
        """Log a policy evaluation decision (allow/block/ask)."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO policy_log
                   (ts, policy_name, tool_name, decision, reason)
                   VALUES (?, ?, ?, ?, ?)""",
                (int(time.time()), policy_name, tool_name, decision, reason),
            )
            self._conn.commit()

    def get_policy_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent policy decisions, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM policy_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Safe condition evaluator — NO eval()
# ---------------------------------------------------------------------------

# Patterns:
#   args.<key>.startswith('<value>')
#   args.<key> == '<value>'
#   args.<key> contains '<value>'

_RE_STARTSWITH = re.compile(
    r"^args\.(\w+)\.startswith\(['\"](.+?)['\"]\)$"
)
_RE_EQUALITY = re.compile(
    r"^args\.(\w+)\s*==\s*['\"](.+?)['\"]$"
)
_RE_CONTAINS = re.compile(
    r"^args\.(\w+)\s+contains\s+['\"](.+?)['\"]$"
)


def safe_eval_condition(condition: str, args: Dict[str, Any]) -> bool:
    """Evaluate a condition string against tool args safely.

    Supports three patterns (no eval):
      - args.<key>.startswith('<value>')
      - args.<key> == '<value>'
      - args.<key> contains '<value>'

    Returns False for missing keys, unsupported patterns, or errors.
    """
    condition = condition.strip()

    m = _RE_STARTSWITH.match(condition)
    if m:
        key, prefix = m.group(1), m.group(2)
        val = args.get(key)
        return isinstance(val, str) and val.startswith(prefix)

    m = _RE_EQUALITY.match(condition)
    if m:
        key, expected = m.group(1), m.group(2)
        val = args.get(key)
        return val == expected

    m = _RE_CONTAINS.match(condition)
    if m:
        key, substr = m.group(1), m.group(2)
        val = args.get(key)
        return isinstance(val, str) and substr in val

    # Unsupported pattern — safe default is deny (False).
    return False


# ---------------------------------------------------------------------------
# Body hash + state key interpolation
# ---------------------------------------------------------------------------

def compute_body_hash(body: str) -> str:
    """SHA256 hash of message body, truncated to 12 chars.

    Used as a correlation key in policy preconditions. Deterministic
    for the same body text.
    """
    return hashlib.sha256(body.encode()).hexdigest()[:12]


def interpolate_state_key(template: str, args: Dict[str, Any]) -> str:
    """Substitute {body_hash} in a state_key template.

    Extracts the 'body' field from args and computes its hash.
    If 'body' is missing or template has no placeholder, returns as-is.
    """
    if "{body_hash}" not in template:
        return template
    body = args.get("body", "")
    if not isinstance(body, str):
        body = str(body)
    bh = compute_body_hash(body)
    return template.replace("{body_hash}", bh)


# ---------------------------------------------------------------------------
# PolicyEngine — load YAML policies and evaluate triggers/preconditions
# ---------------------------------------------------------------------------

class PolicyEngine:
    """Declarative policy evaluation engine.

    Loads policies from one or more directories. Each YAML file
    defines triggers (tool name + optional condition) and preconditions
    (workflow state keys that must exist). Evaluation returns the first
    failing precondition error or None if all pass.
    """

    def __init__(self, policy_dirs: List[Path]) -> None:
        self.policies: List[Dict[str, Any]] = []
        for d in policy_dirs:
            self._load_dir(d)

    def _load_dir(self, d: Path) -> None:
        """Load all *.yaml / *.yml files from a directory."""
        if not d.is_dir():
            return
        for f in sorted(d.iterdir()):
            if f.suffix not in (".yaml", ".yml"):
                continue
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "name" in data:
                    self.policies.append(data)
            except Exception as exc:
                logger.warning("control-room: failed to load policy %s: %s", f, exc)

    def find_matching_policies(
        self, tool_name: str, args: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Return policies whose triggers match the given tool call.

        A trigger matches when its 'tool' field equals tool_name AND
        its optional 'condition' evaluates to True (or is absent).
        """
        matched = []
        for policy in self.policies:
            triggers = policy.get("triggers", [])
            for trigger in triggers:
                if trigger.get("tool") != tool_name:
                    continue
                condition = trigger.get("condition", "")
                if not condition or safe_eval_condition(condition, args):
                    matched.append(policy)
                    break  # One trigger match per policy is enough
        return matched

    def check_preconditions(
        self,
        policy: Dict[str, Any],
        args: Dict[str, Any],
        db: AuditDB,
    ) -> Optional[str]:
        """Check all preconditions for a policy against workflow state.

        Returns the error message of the first failing precondition,
        or None if all pass.
        """
        for precond in policy.get("preconditions", []):
            state_key = interpolate_state_key(
                precond.get("state_key", ""), args,
            )
            if not state_key:
                continue
            val = db.get_workflow_state(state_key)
            if not val:
                return precond.get("error", f"Precondition not met: {state_key}")
        return None

    def to_dict_list(self) -> List[Dict[str, Any]]:
        """Return policies as a serialisable list (for the monitoring API)."""
        return [
            {
                "name": p.get("name", ""),
                "description": p.get("description", ""),
                "triggers": p.get("triggers", []),
                "preconditions": p.get("preconditions", []),
            }
            for p in self.policies
        ]


# ---------------------------------------------------------------------------
# Hook handler factories
# ---------------------------------------------------------------------------

def make_pre_tool_call_handler(
    db: AuditDB, engine: PolicyEngine,
) -> Callable:
    """Build the pre_tool_call hook handler.

    Logs a 'pre' audit entry for every call. Then evaluates matching
    policies. If any policy blocks, logs a 'blocked' audit entry and
    a policy_log row, and returns {'action': 'block', 'message': ...}.
    Returns None to allow the call otherwise.
    """

    def handler(
        tool_name: str = "",
        args: Optional[Dict[str, Any]] = None,
        task_id: str = "",
        session_id: str = "",
        **kw: Any,
    ) -> Optional[Dict[str, str]]:
        if args is None:
            args = {}

        # Always log the pre-call audit entry
        db.log_tool_call("pre", tool_name, args, session_id=session_id)

        # Evaluate policies
        matched = engine.find_matching_policies(tool_name, args)
        for policy in matched:
            error = engine.check_preconditions(policy, args, db)
            if error:
                # Log blocked audit + policy decision
                db.log_tool_call(
                    "blocked", tool_name, args,
                    blocked=True, reason=error, session_id=session_id,
                )
                db.log_policy_decision(
                    policy_name=policy.get("name", "unknown"),
                    tool_name=tool_name,
                    decision="block",
                    reason=error,
                )
                return {"action": "block", "message": error}
            else:
                # Policy matched but all preconditions passed
                db.log_policy_decision(
                    policy_name=policy.get("name", "unknown"),
                    tool_name=tool_name,
                    decision="allow",
                )

        return None

    return handler


def make_post_tool_call_handler(db: AuditDB) -> Callable:
    """Build the post_tool_call hook handler.

    Logs a 'post' audit entry with the tool result and duration.
    Pure audit — no return value needed.
    """

    def handler(
        tool_name: str = "",
        args: Optional[Dict[str, Any]] = None,
        result: Any = None,
        duration_ms: int = 0,
        session_id: str = "",
        **kw: Any,
    ) -> None:
        if args is None:
            args = {}
        result_str = result if isinstance(result, str) else json.dumps(
            result, ensure_ascii=False, default=str,
        )
        db.log_tool_call(
            "post", tool_name, args,
            result=result_str,
            duration_ms=duration_ms,
            session_id=session_id,
        )

    return handler


# ---------------------------------------------------------------------------
# Monitoring HTTP server
# ---------------------------------------------------------------------------

class _MonitorHandler(BaseHTTPRequestHandler):
    """Minimal JSON API handler for the control-room monitoring server.

    Endpoints:
      GET /api/tool-calls?limit=50&tool=<name>  — recent audit rows
      GET /api/blocked?limit=50                  — blocked attempts
      GET /api/workflows                         — current workflow states
      GET /api/policies                          — loaded policies
    """

    # Set by the server factory — shared across requests.
    db: AuditDB
    engine: PolicyEngine

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler convention
        """Route GET requests to the appropriate handler."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        try:
            if path == "/api/tool-calls":
                data = self._handle_tool_calls(params)
            elif path == "/api/blocked":
                data = self._handle_blocked(params)
            elif path == "/api/workflows":
                data = self._handle_workflows()
            elif path == "/api/policies":
                data = self._handle_policies()
            else:
                self._send_json({"error": "not found"}, status=404)
                return
            self._send_json(data)
        except Exception as exc:
            logger.warning("control-room API error: %s", exc)
            self._send_json({"error": str(exc)}, status=500)

    def _handle_tool_calls(self, params: Dict) -> Any:
        limit = int(params.get("limit", ["50"])[0])
        tool = params.get("tool", [""])[0]
        return self.db.get_recent_calls(limit=limit, tool_name=tool)

    def _handle_blocked(self, params: Dict) -> Any:
        limit = int(params.get("limit", ["50"])[0])
        return self.db.get_blocked_calls(limit=limit)

    def _handle_workflows(self) -> Any:
        return self.db.get_all_workflow_states()

    def _handle_policies(self) -> Any:
        return self.engine.to_dict_list()

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr logging — route through logger instead."""
        logger.debug("control-room HTTP: %s", format % args)


def _start_monitor_server(
    db: AuditDB, engine: PolicyEngine, port: int = 8787,
) -> Optional[HTTPServer]:
    """Start the monitoring HTTP server in a daemon thread.

    Binds to localhost only. Returns the server instance (or None on
    failure). The server is started in a daemon thread so it shuts down
    with the main process.
    """
    handler_cls = type(
        "_BoundMonitorHandler",
        (_MonitorHandler,),
        {"db": db, "engine": engine},
    )
    try:
        server = HTTPServer(("127.0.0.1", port), handler_cls)
    except OSError as exc:
        logger.warning(
            "control-room: could not bind monitoring server on port %d: %s",
            port, exc,
        )
        return None

    thread = threading.Thread(
        target=server.serve_forever,
        name="hermes-control-room-monitor",
        daemon=True,
    )
    thread.start()
    logger.info("control-room: monitoring server started on http://127.0.0.1:%d", port)
    return server


# ---------------------------------------------------------------------------
# Slash command handler
# ---------------------------------------------------------------------------

_SLASH_HELP = """\
/control-room — governance monitoring

The control-room monitoring UI is running at:
  http://127.0.0.1:{port}

Endpoints:
  /api/tool-calls?limit=50&tool=<name>  Recent audit entries
  /api/blocked?limit=50                 Blocked tool calls
  /api/workflows                        Current workflow states
  /api/policies                         Loaded policy definitions
"""


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

# Module-level references set during register() so the singleton
# resources survive for the process lifetime.
_db: Optional[AuditDB] = None
_engine: Optional[PolicyEngine] = None
_server: Optional[HTTPServer] = None


def register(ctx) -> None:
    """Wire hooks, slash command, and monitoring server.

    Called by the PluginManager during plugin discovery. Sets up:
    - AuditDB at ~/.hermes/control-room/state.sqlite
    - PolicyEngine loading from bundled + user policy directories
    - pre_tool_call + post_tool_call hooks
    - /control-room slash command
    - Monitoring HTTP server on localhost:8787
    """
    global _db, _engine, _server

    # Resolve DB path — configurable via env, default under HERMES_HOME
    try:
        from hermes_constants import get_hermes_home
        hermes_home = get_hermes_home()
    except ImportError:
        hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))

    db_dir = Path(os.environ.get(
        "CONTROL_ROOM_DB_DIR",
        str(hermes_home / "control-room"),
    ))
    db_path = db_dir / "state.sqlite"
    _db = AuditDB(db_path)

    # Resolve policy directories
    plugin_dir = Path(__file__).resolve().parent
    bundled_policies = plugin_dir / "policies"
    user_policies = hermes_home / "control-room" / "policies"
    _engine = PolicyEngine([bundled_policies, user_policies])

    logger.info(
        "control-room: loaded %d policies, DB at %s",
        len(_engine.policies), db_path,
    )

    # Register hooks
    ctx.register_hook("pre_tool_call", make_pre_tool_call_handler(_db, _engine))
    ctx.register_hook("post_tool_call", make_post_tool_call_handler(_db))

    # Start monitoring server (best-effort — port conflict is non-fatal)
    port = int(os.environ.get("CONTROL_ROOM_PORT", "8787"))
    _server = _start_monitor_server(_db, _engine, port=port)

    # Register slash command
    def _handle_slash(raw_args: str) -> str:
        return _SLASH_HELP.format(port=port)

    ctx.register_command(
        "control-room",
        handler=_handle_slash,
        description="Show the control-room monitoring server URL.",
    )
