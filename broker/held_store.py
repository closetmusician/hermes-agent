# ABOUTME: Durable SQLite held-action store — the broker's authoritative queue.
# ABOUTME: enqueue() commits (and fsyncs via WAL checkpoint semantics) the row
# ABOUTME: BEFORE returning the action_id (commit-before-ack: no fail-open window
# ABOUTME: where the assistant believes an action is queued that isn't). State
# ABOUTME: transitions are one-way and optimistically guarded; payloads are never
# ABOUTME: truncated. Held actions survive a broker restart (same file, new object).
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from broker.action_id import new_action_id

# One-way state machine. A transition is legal only if the target is in the set
# reachable from the current state. Terminal states (executed/rejected/failed)
# have no outgoing transitions — this is what makes "approve then un-approve" or
# "re-hold an executed action" impossible.
_ALLOWED: Dict[str, set] = {
    "held": {"approved", "rejected", "failed"},
    "approved": {"executed", "failed"},
    "rejected": set(),
    "executed": set(),
    "failed": set(),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS held_actions (
  action_id      TEXT PRIMARY KEY,
  type           TEXT NOT NULL,
  channel        TEXT,
  recipient      TEXT,
  summary        TEXT NOT NULL,
  payload        TEXT NOT NULL,
  origin         TEXT NOT NULL,
  safe_lane_json TEXT NOT NULL,
  state          TEXT NOT NULL,
  created_ts     INTEGER NOT NULL,
  decided_ts     INTEGER,
  decided_by     TEXT,
  result_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_held_state ON held_actions(state);
"""


class IllegalTransition(Exception):
    """Raised when a state transition would violate the one-way state machine."""


class HeldStore:
    """
    Purpose: the broker's durable, restart-surviving record of held actions.
    Usage: store = HeldStore(path); aid = store.enqueue(...); store.transition(...).
    Gotchas: enqueue commits before returning (commit-before-ack); WAL mode gives
    crash safety; the connection is shared across threads with a lock (the server
    is single-process, low-concurrency) — do NOT share a HeldStore across processes.
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL + FULL synchronous: durability on commit is the whole point — a
        # committed enqueue must survive a power loss so held actions are never
        # silently dropped.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def enqueue(
        self,
        *,
        type: str,
        summary: str,
        payload: str,
        origin: str,
        safe_lane_json: str,
        state: str = "held",
        channel: Optional[str] = None,
        recipient: Optional[str] = None,
    ) -> str:
        """
        Purpose: durably record a new action and return its stable id.
        Usage: aid = store.enqueue(type="message", summary=..., payload=..., ...).
        Gotchas: the INSERT is COMMITTED before this returns (commit-before-ack) —
        a separate reader connection can see the row the instant enqueue returns,
        so a crash after the RPC ack never leaves a phantom-queued action.
        """
        aid = new_action_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO held_actions (action_id, type, channel, recipient, summary,"
                " payload, origin, safe_lane_json, state, created_ts)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    aid,
                    type,
                    channel,
                    recipient,
                    summary,
                    payload,
                    origin,
                    safe_lane_json,
                    state,
                    int(time.time() * 1000),
                ),
            )
            self._conn.commit()  # commit-before-ack: durable before we return aid
        return aid

    def get(self, action_id: str) -> Optional[Dict[str, Any]]:
        """
        Purpose: fetch the full held-action row (including the untruncated payload).
        Usage: row = store.get(aid); row["payload"] is the complete body.
        Gotchas: returns None when the id is unknown — callers must handle it.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM held_actions WHERE action_id=?", (action_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_pending(self) -> List[Dict[str, Any]]:
        """
        Purpose: list actions still awaiting a decision (state='held').
        Usage: for p in store.list_pending(): show p["summary"] on Telegram.
        Gotchas: returns summaries + metadata; the full payload is fetched by id
        via get() so the approval surface never carries the whole body.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT action_id, type, channel, recipient, summary, origin,"
                " safe_lane_json, created_ts FROM held_actions WHERE state='held'"
                " ORDER BY action_id"
            ).fetchall()
        return [dict(r) for r in rows]

    def transition(
        self, action_id: str, expected: str, target: str, *, decided_by: Optional[str] = None
    ) -> None:
        """
        Purpose: atomically move an action from `expected` state to `target`.
        Usage: store.transition(aid, "held", "approved", decided_by="yk").
        Gotchas: raises IllegalTransition if the current state != expected
        (optimistic-concurrency guard against double-decide) or if the move is not
        permitted by the one-way state machine. The UPDATE is conditioned on the
        current state in SQL so two racing transitions cannot both win.
        """
        if target not in _ALLOWED.get(expected, set()):
            raise IllegalTransition(f"{expected} -> {target} not permitted")
        with self._lock:
            cur = self._conn.execute(
                "UPDATE held_actions SET state=?, decided_ts=?, decided_by=?"
                " WHERE action_id=? AND state=?",
                (target, int(time.time() * 1000), decided_by, action_id, expected),
            )
            self._conn.commit()
            if cur.rowcount != 1:
                # Either unknown id or the row was not in `expected` state.
                raise IllegalTransition(
                    f"action {action_id} not in expected state {expected!r}"
                )

    def record_result(self, action_id: str, result_json: str) -> None:
        """
        Purpose: persist the execution outcome for idempotency + audit.
        Usage: store.record_result(aid, json.dumps(exec_result)).
        Gotchas: stored so a repeat approve returns the cached result instead of
        re-executing egress; committed immediately.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE held_actions SET result_json=? WHERE action_id=?",
                (result_json, action_id),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
