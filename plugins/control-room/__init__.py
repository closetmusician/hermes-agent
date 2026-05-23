"""
ABOUTME: Control-room plugin -- audit logging and policy enforcement for tool calls.
ABOUTME: Tracks all tool invocations in SQLite, evaluates YAML-defined policies
ABOUTME: with preconditions and workflow state, and exposes HTTP monitoring
ABOUTME: endpoints for real-time observability.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQLite audit database
# ---------------------------------------------------------------------------

_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    tool_name   TEXT    NOT NULL,
    args_json   TEXT    NOT NULL DEFAULT '{}',
    phase       TEXT    NOT NULL,  -- 'pre', 'post', 'blocked'
    result      TEXT,
    duration_ms REAL,
    task_id     TEXT    NOT NULL DEFAULT '',
    session_id  TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS policy_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    policy_name TEXT    NOT NULL,
    tool_name   TEXT    NOT NULL,
    decision    TEXT    NOT NULL,  -- 'allow', 'block'
    reason      TEXT    NOT NULL DEFAULT '',
    args_json   TEXT    NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS workflow_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    ts    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_phase ON audit_log(phase);
CREATE INDEX IF NOT EXISTS idx_audit_tool  ON audit_log(tool_name);
CREATE INDEX IF NOT EXISTS idx_policy_decision ON policy_log(decision);
"""


class AuditDB:
    """Thread-safe SQLite wrapper for audit, policy, and workflow state.

    All public methods acquire _lock to serialise writes. Reads use
    the same lock for simplicity -- the database is small and local.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DB_SCHEMA)

    # -- audit log ----------------------------------------------------------

    def log_audit(
        self,
        tool_name: str,
        args: dict,
        phase: str,
        *,
        result: Optional[str] = None,
        duration_ms: Optional[float] = None,
        task_id: str = "",
        session_id: str = "",
    ) -> int:
        """Insert an audit row and return its rowid."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO audit_log (ts, tool_name, args_json, phase, result, duration_ms, task_id, session_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    tool_name,
                    json.dumps(args, default=str),
                    phase,
                    result,
                    duration_ms,
                    task_id,
                    session_id,
                ),
            )
            self._conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def get_audit_rows(
        self, *, phase: Optional[str] = None, tool_name: Optional[str] = None, limit: int = 100
    ) -> List[dict]:
        """Fetch audit rows with optional filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if phase:
            clauses.append("phase = ?")
            params.append(phase)
        if tool_name:
            clauses.append("tool_name = ?")
            params.append(tool_name)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM audit_log{where} ORDER BY id DESC LIMIT ?",
                params + [limit],
            ).fetchall()
        return [dict(r) for r in rows]

    # -- policy log ---------------------------------------------------------

    def log_policy(
        self,
        policy_name: str,
        tool_name: str,
        decision: str,
        reason: str = "",
        args: Optional[dict] = None,
    ) -> int:
        """Insert a policy decision row and return its rowid."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO policy_log (ts, policy_name, tool_name, decision, reason, args_json)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    policy_name,
                    tool_name,
                    decision,
                    reason,
                    json.dumps(args or {}, default=str),
                ),
            )
            self._conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def get_policy_rows(
        self, *, decision: Optional[str] = None, limit: int = 100
    ) -> List[dict]:
        """Fetch policy log rows."""
        clauses: list[str] = []
        params: list[Any] = []
        if decision:
            clauses.append("decision = ?")
            params.append(decision)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM policy_log{where} ORDER BY id DESC LIMIT ?",
                params + [limit],
            ).fetchall()
        return [dict(r) for r in rows]

    # -- workflow state -----------------------------------------------------

    def set_state(self, key: str, value: str) -> None:
        """Upsert a workflow state key."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO workflow_state (key, value, ts) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
                (key, value, time.time()),
            )
            self._conn.commit()

    def get_state(self, key: str) -> Optional[str]:
        """Return a workflow state value or None if missing."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM workflow_state WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def delete_state(self, key: str) -> bool:
        """Delete a workflow state key. Returns True if it existed."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM workflow_state WHERE key = ?", (key,))
            self._conn.commit()
            return cur.rowcount > 0

    def list_states(self) -> Dict[str, str]:
        """Return all workflow state key/value pairs."""
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM workflow_state ORDER BY key").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Condition evaluator
# ---------------------------------------------------------------------------

def evaluate_condition(operator: str, actual: str, expected: str) -> bool:
    """Evaluate a single condition.

    Supported operators: '==', '!=', 'startswith', 'endswith', 'contains'.
    All comparisons are case-sensitive on the string representation.
    """
    actual_s = str(actual) if actual is not None else ""
    expected_s = str(expected)
    if operator == "==":
        return actual_s == expected_s
    if operator == "!=":
        return actual_s != expected_s
    if operator == "startswith":
        return actual_s.startswith(expected_s)
    if operator == "endswith":
        return actual_s.endswith(expected_s)
    if operator == "contains":
        return expected_s in actual_s
    if operator == "regex":
        try:
            return bool(re.search(expected_s, actual_s, re.IGNORECASE))
        except re.error:
            logger.warning("Invalid regex pattern in policy: %s", expected_s)
            return False
    logger.warning("Unknown condition operator '%s', treating as False", operator)
    return False


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------

class Policy:
    """Parsed representation of a single YAML policy file.

    A policy specifies:
      - triggers: list of dicts with 'tool_name' and optional 'args' matchers
      - preconditions: list of workflow-state requirements
      - action: 'allow' or 'block'
    """

    def __init__(self, data: dict, name: str = "") -> None:
        self.name: str = data.get("name", name or "unnamed")
        self.description: str = data.get("description", "")
        self.triggers: List[dict] = data.get("triggers", [])
        self.preconditions: List[dict] = data.get("preconditions", [])
        self.action: str = data.get("action", "block")

    def matches_trigger(self, tool_name: str, args: dict) -> bool:
        """Return True if any trigger matches the tool call."""
        for trigger in self.triggers:
            if trigger.get("tool_name") != tool_name:
                continue
            # Check arg matchers if present
            arg_matchers = trigger.get("args", {})
            if self._match_args(arg_matchers, args):
                return True
        return False

    def _match_args(self, matchers: dict, args: dict) -> bool:
        """Evaluate arg-level matchers against actual args.

        Each matcher key is an arg name. The value can be:
          - a plain string: exact match
          - a dict with {operator, value}: uses evaluate_condition
        """
        for arg_name, matcher in matchers.items():
            actual = args.get(arg_name, "")
            if isinstance(actual, (list, dict)):
                actual = json.dumps(actual, default=str)
            else:
                actual = str(actual)
            if isinstance(matcher, dict):
                op = matcher.get("operator", "==")
                val = str(matcher.get("value", ""))
                if not evaluate_condition(op, actual, val):
                    return False
            else:
                if actual != str(matcher):
                    return False
        return True

    def check_preconditions(self, db: AuditDB) -> tuple[bool, str]:
        """Check all preconditions against workflow state.

        Returns (all_met, first_failing_reason).
        """
        for pre in self.preconditions:
            key = pre.get("state_key", "")
            required = pre.get("required", True)
            if required:
                value = db.get_state(key)
                if value is None:
                    reason = pre.get("message", f"Missing required state: {key}")
                    return False, reason
                # Optional value check
                if "value" in pre:
                    op = pre.get("operator", "==")
                    expected = str(pre["value"])
                    if not evaluate_condition(op, value, expected):
                        reason = pre.get(
                            "message",
                            f"State '{key}' failed check: {value} {op} {expected}",
                        )
                        return False, reason
        return True, ""


def load_policies(policy_dir: Path) -> List[Policy]:
    """Load all .yaml/.yml policy files from a directory.

    Returns an empty list if the directory doesn't exist or yaml is unavailable.
    """
    if yaml is None:
        logger.warning("PyYAML not installed -- cannot load policies")
        return []
    if not policy_dir.is_dir():
        return []
    policies: List[Policy] = []
    for p in sorted(policy_dir.iterdir()):
        if p.suffix not in (".yaml", ".yml"):
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            policies.append(Policy(data, name=p.stem))
        except Exception as exc:
            logger.warning("Failed to load policy %s: %s", p, exc)
    return policies


class PolicyEngine:
    """Evaluate tool calls against loaded policies.

    Evaluation order: all matching policies are checked. If ANY matching
    policy has action='block' and its preconditions are not met, the call
    is blocked. A policy with action='block' whose preconditions ARE all
    met means the block condition is satisfied, so it ALLOWS the call.

    Put differently: a block-policy says "block UNLESS preconditions are met".
    An allow-policy says "allow WHEN triggers match" (preconditions optional).

    If multiple policies match and at least one results in a block decision,
    block wins.
    """

    def __init__(self, policies: List[Policy], db: AuditDB) -> None:
        self._policies = policies
        self._db = db

    @property
    def policies(self) -> List[Policy]:
        """Return the loaded policy list."""
        return list(self._policies)

    def evaluate(self, tool_name: str, args: dict) -> tuple[str, str, Optional[str]]:
        """Evaluate all policies against a tool call.

        Returns (decision, reason, blocking_policy_name):
          - ("allow", "", None) if no policy blocks
          - ("block", reason, policy_name) if blocked
        """
        block_decision: Optional[tuple[str, str]] = None

        for policy in self._policies:
            if not policy.matches_trigger(tool_name, args):
                continue

            if policy.action == "block":
                met, reason = policy.check_preconditions(self._db)
                if not met:
                    # Preconditions not met -> block
                    self._db.log_policy(policy.name, tool_name, "block", reason, args)
                    if block_decision is None:
                        block_decision = (reason, policy.name)
                else:
                    # Preconditions met -> this block-policy is satisfied, allow
                    self._db.log_policy(policy.name, tool_name, "allow", "preconditions met", args)

            elif policy.action == "allow":
                met, reason = policy.check_preconditions(self._db)
                if met:
                    self._db.log_policy(policy.name, tool_name, "allow", "policy allows", args)
                else:
                    # Allow policy with unmet preconditions -> block
                    self._db.log_policy(policy.name, tool_name, "block", reason, args)
                    if block_decision is None:
                        block_decision = (reason, policy.name)

        if block_decision is not None:
            return "block", block_decision[0], block_decision[1]
        return "allow", "", None


# ---------------------------------------------------------------------------
# HTTP monitoring server
# ---------------------------------------------------------------------------

class MonitoringHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler exposing audit and policy data as JSON."""

    # Set by ControlRoom.start_monitoring() before server.serve_forever()
    control_room: Optional["ControlRoom"] = None

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr logging."""
        pass

    def do_GET(self) -> None:
        """Route GET requests to the appropriate handler."""
        cr = self.__class__.control_room
        if cr is None:
            self._send_json(500, {"error": "control-room not initialised"})
            return

        path = self.path.split("?")[0].rstrip("/")

        if path == "/api/tool-calls":
            rows = cr.db.get_audit_rows(limit=50)
            self._send_json(200, {"tool_calls": rows})
        elif path == "/api/blocked":
            rows = cr.db.get_audit_rows(phase="blocked", limit=50)
            self._send_json(200, {"blocked": rows})
        elif path == "/api/workflows":
            states = cr.db.list_states()
            self._send_json(200, {"workflows": states})
        elif path == "/api/policies":
            policies = [
                {"name": p.name, "description": p.description, "action": p.action}
                for p in cr.engine.policies
            ]
            self._send_json(200, {"policies": policies})
        else:
            self._send_json(404, {"error": f"not found: {path}"})

    def _send_json(self, status: int, body: dict) -> None:
        """Send a JSON response."""
        payload = json.dumps(body, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ControlRoom:
    """Orchestrates audit logging, policy evaluation, and monitoring.

    Holds references to AuditDB and PolicyEngine. Hook callbacks are
    closures that capture this instance.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        policy_dir: Optional[Path] = None,
    ) -> None:
        if db_path is None:
            from hermes_constants import get_hermes_home
            db_path = get_hermes_home() / "control-room" / "audit.db"
        if policy_dir is None:
            policy_dir = Path(__file__).parent / "policies"

        self.db = AuditDB(db_path)
        policies = load_policies(policy_dir)
        self.engine = PolicyEngine(policies, self.db)

        # Track in-flight tool calls for duration calculation
        self._inflight: Dict[str, float] = {}
        self._inflight_lock = threading.Lock()

        # Monitoring server
        self._server: Optional[HTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None

    # -- hook callbacks -----------------------------------------------------

    def pre_tool_call(
        self, tool_name: str, args: dict, task_id: str = "", session_id: str = "", **kwargs: Any
    ) -> Optional[dict]:
        """pre_tool_call hook: audit + policy check.

        Returns {"action": "block", "message": ...} if blocked, else None.
        """
        # Hardcoded block: pm_os outlook email JS tools must not be invoked
        # directly. Email goes through send_message → email-send-guard → Graph API.
        if tool_name == "terminal":
            cmd = args.get("command") or ""
            if "outlook-send-mail" in cmd or "outlook-read-mail" in cmd or "pm_os/bin/outlook" in cmd:
                self.db.log_audit(
                    tool_name, args, "blocked",
                    result="Direct invocation of pm_os outlook JS tools blocked",
                    task_id=task_id, session_id=session_id,
                )
                return {
                    "action": "block",
                    "message": "[control-room] Direct use of pm_os outlook JS tools is blocked. Use the send_message tool to send email — it routes through the email-send-guard approval workflow and Graph API backend automatically.",
                }

        # Hardcoded block: write-protect security plugin source code.
        # Hermes must not modify its own guards — closes code-rewrite escalation.
        if tool_name in ("write_file", "patch"):
            target_path = args.get("path") or ""
            if "plugins/control-room" in target_path or "plugins/email-send-guard" in target_path:
                self.db.log_audit(
                    tool_name, args, "blocked",
                    result="Write to security plugin source blocked",
                    task_id=task_id, session_id=session_id,
                )
                return {
                    "action": "block",
                    "message": "[control-room] Modifying security plugin source code is blocked. plugins/control-room/ and plugins/email-send-guard/ are write-protected.",
                }

        # Hardcoded block: prevent execute_code from importing hermes internals.
        # Stops forging guard state, calling internal APIs, and module abuse.
        if tool_name == "execute_code":
            code = args.get("code") or ""
            if any(pat in code for pat in (
                "hermes_tools", "hermes_cli", "plugins.", "hermes_constants",
                "from tools", "import tools", "from agent", "import agent",
                "from gateway", "import gateway", "from model_tools",
            )):
                self.db.log_audit(
                    tool_name, args, "blocked",
                    result="execute_code importing hermes internals blocked",
                    task_id=task_id, session_id=session_id,
                )
                return {
                    "action": "block",
                    "message": "[control-room] execute_code cannot import hermes internal modules (hermes_tools, hermes_cli, plugins, agent, gateway, model_tools). Use the official tool APIs instead.",
                }

        # Hardcoded block: prevent direct token extraction via get-foci-token.js.
        # Tokens are used internally by Graph API backend, not exposed to agent.
        if tool_name == "terminal":
            cmd = args.get("command") or ""
            if "get-foci-token" in cmd:
                self.db.log_audit(
                    tool_name, args, "blocked",
                    result="Direct FOCI token extraction blocked",
                    task_id=task_id, session_id=session_id,
                )
                return {
                    "action": "block",
                    "message": "[control-room] Direct use of get-foci-token.js is blocked. Auth tokens are managed internally by the Graph API email backend.",
                }

        # Evaluate policies
        decision, reason, policy_name = self.engine.evaluate(tool_name, args)

        if decision == "block":
            self.db.log_audit(
                tool_name, args, "blocked",
                result=reason, task_id=task_id, session_id=session_id,
            )
            return {"action": "block", "message": f"[control-room] {reason}"}

        # Record pre-phase audit
        self.db.log_audit(
            tool_name, args, "pre", task_id=task_id, session_id=session_id,
        )
        # Track start time for duration
        call_key = f"{tool_name}:{task_id}:{time.monotonic()}"
        with self._inflight_lock:
            self._inflight[f"{tool_name}:{task_id}"] = time.monotonic()

        return None

    def post_tool_call(
        self,
        tool_name: str,
        args: dict,
        result: str = "",
        task_id: str = "",
        session_id: str = "",
        **kwargs: Any,
    ) -> None:
        """post_tool_call hook: record completion audit row with duration."""
        call_key = f"{tool_name}:{task_id}"
        duration_ms: Optional[float] = None
        with self._inflight_lock:
            start = self._inflight.pop(call_key, None)
        if start is not None:
            duration_ms = (time.monotonic() - start) * 1000.0

        # Truncate result for storage (keep first 4KB)
        stored_result = result[:4096] if isinstance(result, str) else str(result)[:4096]

        self.db.log_audit(
            tool_name, args, "post",
            result=stored_result, duration_ms=duration_ms,
            task_id=task_id, session_id=session_id,
        )

    # -- monitoring server --------------------------------------------------

    def start_monitoring(self, host: str = "127.0.0.1", port: int = 8787) -> int:
        """Start the HTTP monitoring server on a background thread.

        Returns the port actually bound (useful when port=0 for random).
        """
        if self._server is not None:
            return self._server.server_address[1]

        # Create a handler class with a reference to this ControlRoom
        handler_class = type(
            "BoundHandler",
            (MonitoringHandler,),
            {"control_room": self},
        )
        self._server = HTTPServer((host, port), handler_class)
        actual_port = self._server.server_address[1]

        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="control-room-monitor",
            daemon=True,
        )
        self._server_thread.start()
        logger.info("Control-room monitoring on http://%s:%d", host, actual_port)
        return actual_port

    def stop_monitoring(self) -> None:
        """Shut down the monitoring server if running."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
            self._server_thread = None


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

# Module-level reference for tests and slash-command access.
_instance: Optional[ControlRoom] = None


def register(ctx: Any) -> None:
    """Plugin entry point -- called by PluginManager."""
    global _instance

    # Determine paths from env or defaults
    db_path_env = os.getenv("CONTROL_ROOM_DB")
    policy_dir_env = os.getenv("CONTROL_ROOM_POLICIES")

    db_path = Path(db_path_env) if db_path_env else None
    policy_dir = Path(policy_dir_env) if policy_dir_env else None

    cr = ControlRoom(db_path=db_path, policy_dir=policy_dir)
    _instance = cr

    ctx.register_hook("pre_tool_call", cr.pre_tool_call)
    ctx.register_hook("post_tool_call", cr.post_tool_call)

    # Start monitoring server if configured
    monitor_port = os.getenv("CONTROL_ROOM_MONITOR_PORT")
    if monitor_port:
        try:
            cr.start_monitoring(port=int(monitor_port))
        except Exception as exc:
            logger.warning("Failed to start monitoring server: %s", exc)

    logger.info(
        "control-room: loaded %d policies, audit db at %s",
        len(cr.engine.policies),
        cr.db._db_path,
    )
