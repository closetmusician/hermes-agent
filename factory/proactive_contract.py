# ABOUTME: Quiet-by-default proactive nudge engine (P6-c REQ-01 + REQ-03).
# ABOUTME: Enforces at-most-1 nudge per anomaly_key, never-repeat — via a durable
# ABOUTME: on-disk SQLite ledger so the property holds across process restarts.
# ABOUTME: All anomaly events (watchdogs, fleet completion, morning replay) route
# ABOUTME: through send_nudge() before any broker_client.enqueue_action / notification.
"""
factory.proactive_contract — quiet-by-default nudge engine.

Design authority: docs/plans/harness/fable/p6/P6-design.md §3.3
Config data: docs/factory/proactive-contract.md (read as DATA, never as instructions)

Quiet semantics:
  * For each anomaly_key the engine emits AT MOST 1 nudge, NEVER repeats.
  * The record is kept in a durable SQLite ledger so a process restart does not
    re-fire a nudge for an already-seen key.
  * anomaly_key is a deterministic string (e.g. "drift:job-42") so the same
    underlying event cannot double-fire even across restarts.

Anti-weakening: the never-repeat test FAILS if should_nudge() always returns True
(i.e. the ledger check is bypassed).  The ledger file path is set at construction
and must be on disk — an in-memory-only implementation breaks the restart test.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Optional


# Default ledger location (under the hermes home directory; overridable in tests).
_DEFAULT_LEDGER_PATH = Path.home() / ".hermes" / "nudge_ledger.db"

# SQL schema for the nudge ledger.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS nudges (
    anomaly_key  TEXT PRIMARY KEY,
    first_sent_ts TEXT NOT NULL
);
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = FULL;
"""


class ProactiveContract:
    """
    Quiet-by-default nudge engine.

    Purpose: ensure that an anomaly event triggers AT MOST 1 nudge and NEVER
    repeats for the same anomaly_key, even across process restarts.  All factory
    surfaces that may want to notify the owner of an anomaly (watchdogs, fleet
    completion, morning replay) should call send_nudge() — if the key was already
    seen the call is a silent no-op.

    Usage:
        pc = ProactiveContract()                 # uses default ledger path
        pc.send_nudge("drift:job-42", "Worker drift detected on job 42")
        # On the next identical event: silent no-op

    Gotchas:
      * The ledger is on disk; creating a new instance with the same ledger_path
        inherits all past records — this is intentional (restart-durability).
      * enqueue_fn must not raise on a duplicate key; ProactiveContract does the
        dedup so enqueue_fn is called at most once per key.
      * The anomaly_key is caller-defined; keep it deterministic and specific enough
        to identify the underlying condition (e.g. "drift:job-42", "regression:job-7").
    """

    def __init__(
        self,
        ledger_path: Optional[Path] = None,
        enqueue_fn: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """
        Purpose: initialise the engine, opening (or creating) the ledger DB.
        Usage: ProactiveContract(ledger_path=Path(...), enqueue_fn=my_enqueue)
        Gotchas: ledger_path parent directory must exist or be creatable; the DB
        is created on first open if it does not yet exist.
        """
        self._ledger_path = ledger_path or _DEFAULT_LEDGER_PATH
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._enqueue_fn: Callable[[str, str], None] = enqueue_fn or _noop_enqueue
        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_nudge(self, anomaly_key: str) -> bool:
        """
        Purpose: check whether a nudge for anomaly_key is still eligible (not yet
        sent).  Returns True iff no row exists for this key in the ledger.
        Usage: if pc.should_nudge(key): ... # gate before expensive work
        Gotchas: calling should_nudge does NOT record the key — only send_nudge
        atomically records + emits.  Use send_nudge for the combined check+emit.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM nudges WHERE anomaly_key = ?", (anomaly_key,)
            ).fetchone()
        return row is None

    def send_nudge(self, anomaly_key: str, summary: str) -> bool:
        """
        Purpose: if anomaly_key has not been nudged before, record it in the durable
        ledger and call enqueue_fn(anomaly_key, summary).  If already seen, this is
        a silent no-op.  Returns True iff the nudge was emitted, False if suppressed.

        Usage:
            emitted = pc.send_nudge("drift:job-42", "Worker drift detected")
            if not emitted:
                pass  # already suppressed — no action needed

        Gotchas: the INSERT and the enqueue_fn call are NOT in the same DB transaction
        (enqueue_fn is an external side-effect); the INSERT happens BEFORE the call so
        that a crash after INSERT but before enqueue does not re-fire on restart (the
        conservative direction: miss one nudge rather than send a duplicate).
        """
        from datetime import datetime, timezone  # local import to keep top-level clean

        ts = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO nudges(anomaly_key, first_sent_ts) VALUES (?,?)",
                    (anomaly_key, ts),
                )
                inserted = conn.execute(
                    "SELECT changes()"
                ).fetchone()[0]
        except sqlite3.DatabaseError:
            # Fail open on ledger errors (don't crash the observer), but do NOT emit
            # the nudge — safer to miss one than to double-fire or crash the caller.
            return False

        if inserted == 0:
            # Key already existed — suppressed
            return False

        # Key is new and committed to the ledger — safe to emit
        self._enqueue_fn(anomaly_key, summary)
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """
        Purpose: create the nudges table and set WAL+FULL pragmas if the DB is new.
        Usage: called once at construction time.
        Gotchas: safe to call on an existing DB — CREATE TABLE IF NOT EXISTS is idempotent.
        """
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        """
        Purpose: open a connection to the ledger SQLite file.
        Usage: with self._connect() as conn: conn.execute(...)
        Gotchas: isolation_level=None (autocommit) is NOT used — the context manager
        commits on clean exit and rolls back on exception.
        """
        return sqlite3.connect(str(self._ledger_path))


def _noop_enqueue(anomaly_key: str, summary: str) -> None:
    """
    Purpose: default enqueue_fn when none is provided — a silent no-op.
    Usage: fallback so ProactiveContract is usable without wiring up a broker client.
    Gotchas: callers in production MUST supply a real enqueue_fn; the default does nothing.
    """
