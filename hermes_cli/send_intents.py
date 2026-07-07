# ABOUTME: Send-intent ledger — exactly-one-send guarantee for the assistant side.
# ABOUTME: Maps caller-supplied intent_key → broker action_id in a durable SQLite DB.
# ABOUTME: enqueue_send() is the ONLY call site for BrokerClient.enqueue_action in the
# ABOUTME: daily-sweep/meeting-prep paths — a retry with the same intent_key is silently
# ABOUTME: deduped here so the broker never sees a second action for the same logical send.
# ABOUTME: Concurrency-safe via INSERT ON CONFLICT DO NOTHING + serialised write lock.
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from broker_client import BrokerClient


_SCHEMA = """
CREATE TABLE IF NOT EXISTS send_intents (
  intent_key  TEXT PRIMARY KEY,
  action_id   TEXT NOT NULL,
  created_ts  INTEGER NOT NULL,
  state       TEXT NOT NULL DEFAULT 'enqueued'
);
"""


class SendIntentLedger:
    """
    Purpose: deduplicate logical sends so a retry never enqueues a second broker action.
    Usage: ledger = SendIntentLedger(path); result = ledger.enqueue_send(intent_key, ...).
    Gotchas: the intent_key must be caller-supplied and stable across retries (e.g. a
    draft-file path, processed_key, or a content+recipient hash chosen by the caller —
    NOT a raw body hash; see design §4). The DB is WAL-mode for crash safety. Thread-safe
    within one process via a write lock; not safe across separate processes (two workers
    would race — use a single ledger process or a separate service boundary if needed).
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL mode: a crashed write does not corrupt a concurrent reader; the DB is
        # useful immediately after a restart without a full journal rollback.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def get(self, intent_key: str) -> Optional[Dict[str, Any]]:
        """
        Purpose: look up an existing intent record by its stable key.
        Usage: row = ledger.get(key); if row: return {"action_id": row["action_id"]}.
        Gotchas: returns None when the key is unknown (first-time send); the caller
        must treat None as "not yet enqueued" and proceed to call the broker.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT intent_key, action_id, created_ts, state"
                " FROM send_intents WHERE intent_key=?",
                (intent_key,),
            ).fetchone()
        return dict(row) if row is not None else None

    def _insert_intent(self, intent_key: str, action_id: str) -> bool:
        """
        Purpose: atomically record a new intent_key → action_id mapping.
        Usage: internal; called after a successful broker enqueue to commit the result.
        Gotchas: INSERT ON CONFLICT DO NOTHING means a racing concurrent enqueue that
        already committed the same key is the winner — we detect the loss by checking
        rowcount. Returns True if this thread won the insert race, False if it lost.
        The lock is held for the full duration so two threads serialise here — the one
        that goes second finds the row already present (ON CONFLICT) and returns False,
        then re-reads the winner's action_id. This is the optimistic-concurrency shape
        documented in the design (§4) and mirrored by held_store.transition.
        """
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO send_intents (intent_key, action_id, created_ts, state)"
                " VALUES (?, ?, ?, 'enqueued')"
                " ON CONFLICT(intent_key) DO NOTHING",
                (intent_key, action_id, int(time.time() * 1000)),
            )
            self._conn.commit()
            return cur.rowcount == 1  # True = we won; False = another thread won

    def enqueue_send(
        self,
        intent_key: str,
        *,
        broker_client: "BrokerClient",
        type: str,
        summary: str,
        payload: str,
        origin: str,
        channel: Optional[str] = None,
        recipient: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Purpose: exactly-once wrapper around BrokerClient.enqueue_action.
        If intent_key already has a recorded action_id (prior enqueue, same key), return
        the existing action_id immediately — do NOT call the broker again. If this is
        the first time (key is new), enqueue via the broker, commit the mapping, then
        return the result. Under concurrency, a thread-level lock serialises the check-
        and-enqueue step so exactly one thread calls the broker for any given intent_key.
        Usage: res = ledger.enqueue_send(draft_id, broker_client=client, type="message", ...).
        Returns {"action_id": str, "disposition": str, ...} — the broker's original result
        for first-time calls, or {"action_id": str, "deduped": True} for retries.
        Gotchas: intent_key is caller-chosen and MUST be stable across retries for the
        same logical send. Do NOT pass a raw body hash (that is the /approve-email bug
        class — see design §4); use a provider-immutable id or a composite of
        processed_key + recipient. BrokerUnavailable propagates unchanged (fail-closed).
        Anti-weakening note: removing the early-return on an existing row makes this
        function call enqueue_action on every call → test_second_enqueue_same_key fails.
        The thread lock held across the broker call is intentional: it prevents the TOCTOU
        race where two threads both see "no row" then both call the broker. The broker call
        is local/fast (unix socket); holding the lock for it is acceptable at this scale.
        """
        # Serialise the full check-and-enqueue sequence under the write lock to close
        # the TOCTOU window: two threads must not both see "no row" and call the broker.
        # The lock is already held by _insert_intent, so we inline the logic here to
        # keep the critical section contiguous without re-entering.
        with self._lock:
            # 1. Dedup check (re-entrant under the same lock via direct SQL).
            row = self._conn.execute(
                "SELECT intent_key, action_id, created_ts, state"
                " FROM send_intents WHERE intent_key=?",
                (intent_key,),
            ).fetchone()
            if row is not None:
                return {"action_id": row["action_id"], "deduped": True}

            # 2. First time: call the broker while holding the lock so no other thread
            # can race through step 1 and also reach step 2 for the same key.
            # BrokerUnavailable propagates to the caller — fail-closed.
            result = broker_client.enqueue_action(
                type=type,
                summary=summary,
                payload=payload,
                origin=origin,
                channel=channel,
                recipient=recipient,
            )
            action_id: str = result["action_id"]

            # 3. Commit-before-ack: persist the mapping while still holding the lock.
            # INSERT ON CONFLICT DO NOTHING is kept as a belt-and-suspenders guard
            # against cross-process races (within one process the lock above suffices).
            self._conn.execute(
                "INSERT INTO send_intents (intent_key, action_id, created_ts, state)"
                " VALUES (?, ?, ?, 'enqueued')"
                " ON CONFLICT(intent_key) DO NOTHING",
                (intent_key, action_id, int(time.time() * 1000)),
            )
            self._conn.commit()

        return result

    def close(self) -> None:
        """
        Purpose: release the DB connection cleanly (useful in tests and finalizers).
        Usage: ledger.close() — safe to call multiple times (sqlite3 handles it).
        Gotchas: any subsequent call to enqueue_send after close() will raise.
        """
        with self._lock:
            self._conn.close()
