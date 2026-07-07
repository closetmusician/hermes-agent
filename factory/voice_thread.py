# ABOUTME: P6-b voice-note threading — groups notes by (sender, 6h window) into
# ABOUTME: one consolidated task card per thread at the morning tick. P2-2 fix:
# ABOUTME: the CONCATENATION of threaded notes is injection-scanned BEFORE split,
# ABOUTME: catching cross-note payloads that per-note scans alone would miss.
# ABOUTME: Reuses task_splitter.split, morning_packet.enqueue_question; no duplicating.
"""
Voice-note threading (P6-b) — multi-note → one spec'd task card by morning.

Design authoritative source:
  docs/plans/harness/fable/p6/P6-design.md §2 (REQ-01)
  docs/plans/harness/fable/p6/P6-review.md P2-2

Threading contract:
  Notes from the same sender within a 6-hour rolling window accumulate in a
  SQLite-backed ``VoiceThread`` table.  At the morning consolidation tick,
  ``consolidate_threads()`` groups each open thread's transcripts, concatenates
  them in timestamp order, then hands the concatenation to ``split()`` ONCE.

Security invariant (P2-2 fix — load-bearing):
  The concatenation of threaded notes is injection-scanned with
  ``injection_scan.scan()`` + ``injection_scan.fence()`` BEFORE the single
  ``split()`` call.  This is ADDITIONAL to any per-note scans done at ingest
  time (``intake_sources.ingest_voice_note``).

  Why per-note scans are insufficient:
    A payload like "push to main" can be split across two notes —
    note 1: "push to "  (passes scan individually)
    note 2: "main"       (passes scan individually)
    concat: "push to main"  (caught only on the concatenation)

  The cross-note injection test (test_cross_note_injection_caught_on_concat)
  is specifically designed to FAIL if only per-note scanning is done.

Question-back flow:
  When consolidated split returns ``ParkedSpec`` (too vague), instead of
  silently parking, the thread emits a 'question' held-action via
  ``morning_packet.enqueue_question``.  The flow is idempotent per thread_id —
  a thread already questioned is not re-asked.

Priority tagging:
  Plain-code keyword map: urgent/asap/p0 → high; whenever/someday → low;
  default → normal.  Priority is advisory metadata on the card and is NEVER
  auto-escalated past the broker.

Durable idempotency:
  The seen note_id set is on-disk (SQLite WAL+FULL) so a gateway restart
  does not re-thread a note that was already added.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from factory.injection_scan import fence, scan
from factory.morning_packet import enqueue_question
from factory.task_graph import ParkedSpec, TaskGraph
from factory.task_splitter import split

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The rolling window within which notes from the same sender thread together.
THREAD_WINDOW_SECONDS: int = 6 * 3600  # 6 hours

# Priority keyword map (plain-code, no AI).
# Checked against the lower-cased transcript; first match wins.
_PRIORITY_MAP = [
    (("urgent", "asap", "p0"), "high"),
    (("whenever", "someday"), "low"),
]
_DEFAULT_PRIORITY = "normal"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_threads (
  thread_id  TEXT NOT NULL,
  note_id    TEXT NOT NULL,
  sender     TEXT NOT NULL,
  transcript TEXT NOT NULL,
  priority   TEXT NOT NULL DEFAULT 'normal',
  ts         INTEGER NOT NULL,
  PRIMARY KEY (note_id)
);
CREATE TABLE IF NOT EXISTS thread_state (
  thread_id   TEXT PRIMARY KEY,
  questioned  INTEGER NOT NULL DEFAULT 0,
  consolidated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_vt_sender_ts ON voice_threads(sender, ts);
"""


# ---------------------------------------------------------------------------
# Priority helper
# ---------------------------------------------------------------------------

def _detect_priority(transcript: str) -> str:
    """
    Detect a priority tag from a transcript using a plain-code keyword map.

    Purpose: assign advisory priority to a voice thread based on keyword
    presence.  This is NOT an AI inference — it is a simple string match.
    Usage: priority = _detect_priority("urgent: fix billing service now")
    Gotchas: first matching tier wins; no partial word matching required — the
    keywords are common enough to be unambiguous in natural speech.
    """
    lowered = transcript.lower()
    for keywords, priority in _PRIORITY_MAP:
        if any(kw in lowered for kw in keywords):
            return priority
    return _DEFAULT_PRIORITY


def _derive_thread_id(sender: str, ts: int) -> str:
    """
    Derive a deterministic thread_id for a (sender, 6h window) bucket.

    Purpose: group notes into windows by truncating the timestamp to a 6h
    boundary, so all notes from the same sender in the same window share a
    thread_id.  The window epoch is the floor(ts / THREAD_WINDOW_SECONDS).
    Usage: thread_id = _derive_thread_id("yk", int(time.time()))
    Gotchas: two notes in different 6h buckets produce different thread_ids
    even from the same sender.
    """
    window_epoch = ts // THREAD_WINDOW_SECONDS
    return f"{sender}:{window_epoch}"


# ---------------------------------------------------------------------------
# VoiceThread — durable note accumulator (WAL+FULL SQLite)
# ---------------------------------------------------------------------------

class VoiceThread:
    """
    Durable SQLite-backed accumulator for voice notes grouped by (sender, 6h window).

    Purpose: accumulate transcripts across notes, persist them across gateway
    restarts, and support the morning consolidation tick.  The durable seen-set
    prevents a re-delivered note_id from being threaded twice.

    Usage:
        thread = VoiceThread(db_path=Path("~/.hermes/voice_threads.db"))
        thread.add_note(sender="yk", note_id="tg-001", transcript="...", ts=...)
        notes = thread.get_notes(sender="yk", window_ts=int(time.time()))

    Gotchas:
      * add_note returns True on first add, False on duplicate (idempotent).
      * The seen-set is the SQLite PRIMARY KEY on note_id — durably enforced.
      * WAL+FULL gives crash safety (same posture as held_store.py).
    """

    def __init__(self, db_path: Path) -> None:
        """
        Open or create the voice_threads SQLite database.

        Purpose: ensure the schema exists and configure WAL+FULL durability.
        Usage: thread = VoiceThread(db_path=Path("~/.hermes/voice_threads.db"))
        Gotchas: db_path parent is created if absent; existing DB is opened
        in append mode (no data loss on re-open).
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _find_open_thread_for_sender(self, sender: str, ts: int) -> Optional[str]:
        """
        Find an existing, not-yet-consolidated thread for this sender where the
        latest note arrived within THREAD_WINDOW_SECONDS of the given ts.

        Purpose: implements the rolling 6h window — a note is threaded with prior
        notes if the most recent one in the thread is ≤6h earlier (not a fixed
        time-bucket, which is fragile near bucket boundaries).
        Usage: called inside add_note while self._lock is held.
        Gotchas: returns None if no open thread is within window; must only be
        called from lock-held code (the caller holds self._lock).
        """
        # Find all open (not consolidated) thread_ids for this sender, ordered
        # by the most recent note in each thread descending.
        rows = self._conn.execute(
            """
            SELECT vt.thread_id, MAX(vt.ts) AS latest_ts
              FROM voice_threads vt
              LEFT JOIN thread_state ts_row ON vt.thread_id = ts_row.thread_id
             WHERE vt.sender = ?
               AND (ts_row.consolidated IS NULL OR ts_row.consolidated = 0)
             GROUP BY vt.thread_id
             ORDER BY latest_ts DESC
             LIMIT 1
            """,
            (sender,),
        ).fetchall()
        for row in rows:
            if ts - row["latest_ts"] <= THREAD_WINDOW_SECONDS:
                return row["thread_id"]
        return None

    def add_note(
        self,
        *,
        sender: str,
        note_id: str,
        transcript: str,
        ts: Optional[int] = None,
    ) -> bool:
        """
        Add a voice note to the thread table.

        Purpose: insert the note under the correct rolling 6h window thread_id;
        skip silently if note_id has already been seen (durable idempotency).

        Rolling window rule: if the sender has an existing open thread whose most
        recent note is within 6h of this note's ts, the note joins that thread;
        otherwise a new thread_id is minted.

        Usage:
            accepted = thread.add_note(sender="yk", note_id="tg-001",
                                       transcript="...", ts=1700000000)
        Gotchas:
          * Returns True if the note was inserted, False if it was already present.
          * ts defaults to current time if not provided.
          * Priority is computed from the transcript and stored per-note; the
            thread's consolidated priority is the maximum of all note priorities.
        """
        if ts is None:
            ts = int(time.time())
        priority = _detect_priority(transcript)

        with self._lock:
            # Rolling-window lookup: join an existing open thread if within 6h.
            thread_id = self._find_open_thread_for_sender(sender, ts)
            if thread_id is None:
                # No open thread within window — start a new one.
                thread_id = _derive_thread_id(sender, ts)

            try:
                self._conn.execute(
                    "INSERT INTO voice_threads (thread_id, note_id, sender, transcript, priority, ts)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (thread_id, note_id, sender, transcript, priority, ts),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                # PRIMARY KEY conflict on note_id — already seen.
                return False

    def get_notes(self, *, sender: str, window_ts: int) -> List[Dict[str, Any]]:
        """
        Return all notes for a sender in the window that contains window_ts.

        Purpose: retrieve the ordered transcript list for consolidation.  Notes
        are returned newest-first within the thread.
        Usage: notes = thread.get_notes(sender="yk", window_ts=int(time.time()))
        Gotchas: window_ts is used only to derive the thread_id; it need not be
        the same as any note's ts — use the consolidation tick timestamp.
        """
        thread_id = _derive_thread_id(sender, window_ts)
        with self._lock:
            rows = self._conn.execute(
                "SELECT note_id, sender, transcript, priority, ts"
                " FROM voice_threads WHERE thread_id = ? ORDER BY ts ASC",
                (thread_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_open_threads(self) -> List[Dict[str, Any]]:
        """
        Return one row per open (not yet consolidated and not yet questioned) thread.

        Purpose: the morning tick iterates open threads and consolidates each one.
        Usage: for t in thread.get_open_threads(): consolidate(t, ...)
        Gotchas: 'open' means thread_state.consolidated == 0; threads in the
        'questioned' state are also returned here because the answer may have
        arrived and the thread needs re-consolidation — callers filter if needed.
        """
        with self._lock:
            # Get all distinct thread_ids from voice_threads.
            all_threads = self._conn.execute(
                "SELECT DISTINCT thread_id, sender FROM voice_threads"
            ).fetchall()
            consolidated_ids = {
                row["thread_id"]
                for row in self._conn.execute(
                    "SELECT thread_id FROM thread_state WHERE consolidated = 1"
                ).fetchall()
            }
        result = []
        for row in all_threads:
            tid = row["thread_id"]
            if tid not in consolidated_ids:
                result.append({"thread_id": tid, "sender": row["sender"]})
        return result

    def get_thread_notes(self, thread_id: str) -> List[Dict[str, Any]]:
        """
        Return all notes for a specific thread_id, ordered by ts ascending.

        Purpose: retrieve the raw notes for concatenation during consolidation.
        Usage: notes = thread.get_thread_notes(thread_id="yk:12345")
        Gotchas: returns empty list if the thread has no notes (should not
        happen in normal flow, but callers must handle it).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT note_id, sender, transcript, priority, ts"
                " FROM voice_threads WHERE thread_id = ? ORDER BY ts ASC",
                (thread_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _is_questioned_nolock(self, thread_id: str) -> bool:
        """
        Return True if this thread has already had a question-back sent (lock not held).

        Purpose: internal helper for use inside lock-held code paths (mark_consolidated)
        to avoid a deadlock from nested lock acquisition.
        Usage: called only from methods that already hold self._lock.
        Gotchas: MUST NOT be called from outside a self._lock context.
        """
        row = self._conn.execute(
            "SELECT questioned FROM thread_state WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return bool(row and row["questioned"])

    def is_questioned(self, thread_id: str) -> bool:
        """
        Return True if this thread has already had a question-back sent.

        Purpose: idempotency guard — prevents duplicate question cards per thread.
        Usage: if thread.is_questioned(thread_id): skip re-asking.
        Gotchas: returns False for threads not in the thread_state table yet.
        """
        with self._lock:
            return self._is_questioned_nolock(thread_id)

    def mark_questioned(self, thread_id: str) -> None:
        """
        Record that a question-back has been sent for this thread_id.

        Purpose: prevent duplicate question cards on subsequent consolidation runs.
        Usage: thread.mark_questioned(thread_id)
        Gotchas: uses INSERT OR REPLACE so it works on first mark and on updates.
        """
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO thread_state (thread_id, questioned, consolidated)"
                " VALUES (?, 1, 0)",
                (thread_id,),
            )
            self._conn.commit()

    def mark_consolidated(self, thread_id: str) -> None:
        """
        Record that this thread has been successfully consolidated into a task card.

        Purpose: prevent re-consolidation of the same thread on subsequent morning
        ticks once a card has already been emitted.
        Usage: thread.mark_consolidated(thread_id)
        Gotchas: sets consolidated=1; questioned state is preserved.
        """
        with self._lock:
            # Use the no-lock variant to avoid a deadlock (we already hold self._lock).
            already_questioned = self._is_questioned_nolock(thread_id)
            self._conn.execute(
                "INSERT OR REPLACE INTO thread_state (thread_id, questioned, consolidated)"
                " VALUES (?, ?, 1)",
                (thread_id, 1 if already_questioned else 0),
            )
            self._conn.commit()


# ---------------------------------------------------------------------------
# Consolidation tick — the morning entry point
# ---------------------------------------------------------------------------

def _consolidate_priority(notes: List[Dict[str, Any]]) -> str:
    """
    Compute the consolidated priority for a thread from its individual note priorities.

    Purpose: a thread tagged 'high' in any note should carry 'high' overall.
    Usage: priority = _consolidate_priority(notes)
    Gotchas: high > normal > low; falls back to 'normal' if notes is empty.
    """
    tier = {"high": 2, "normal": 1, "low": 0}
    reverse = {2: "high", 1: "normal", 0: "low"}
    if not notes:
        return _DEFAULT_PRIORITY
    max_tier = max(tier.get(n.get("priority", "normal"), 1) for n in notes)
    return reverse[max_tier]


def _format_thread_card(graph: TaskGraph, priority: str, thread_id: str) -> str:
    """
    Render a human-readable card text for a consolidated voice thread.

    Purpose: produce the broker card summary for the owner — shows task count,
    thread origin, and the advisory priority tag.
    Usage: text = _format_thread_card(graph, priority="high", thread_id="yk:12345")
    Gotchas: card text is NOT the full spec; it is for owner spot-check only.
    """
    lines = [
        f"Consolidated voice thread ({len(graph.nodes)} task(s) — priority: {priority}):",
    ]
    for node in graph.nodes:
        ac_count = len(node.acceptance)
        depends = f" [after {', '.join(node.depends_on)}]" if node.depends_on else ""
        lines.append(
            f"  {node.id}: {node.title} — {ac_count} acceptance criterion(a){depends}"
        )
    lines.append(f"Max ambiguity score: {graph.max_ambiguity:.2f}")
    lines.append(f"Thread: {thread_id}  |  Priority tag: {priority} (advisory — tap to promote)")
    return "\n".join(lines)


def consolidate_threads(
    thread_store: VoiceThread,
    *,
    broker: Any,
    repo: str,
    base_branch: str,
    held_store: Any = None,
) -> int:
    """
    Morning consolidation tick: process all open threads → emit task cards.

    Purpose: the scheduler calls this once at the morning tick.  For each open
    thread (notes from the same sender in one 6h window), the transcripts are:
      1. Concatenated in ts order.
      2. Injection-scanned on the CONCATENATION (P2-2 fix — load-bearing).
         This catches cross-note injection payloads that per-note scans miss.
      3. Fenced if findings are present.
      4. Passed to task_splitter.split() ONCE (one card per thread, not per note).
      5. Routed to:
           TaskGraph  → broker.enqueue_action(type="task_card")
           ParkedSpec → morning_packet.enqueue_question (question-back flow)

    Returns the number of task cards emitted (0 if all threads were vague).

    Security invariant (P2-2):
      The concatenation scan in step 2 is IN ADDITION to any per-note scans
      performed at ingest time (intake_sources.ingest_voice_note).  It is
      required because an injection payload split across two notes
      ("push to " in note 1, "main" in note 2) passes per-note scans
      individually but is caught by scanning the concatenation.

    Usage:
        n_cards = consolidate_threads(
            thread_store, broker=broker, repo="my-repo", base_branch="main"
        )

    Gotchas:
      * held_store is optional — if None, question-back actions are skipped
        (the enqueue_question call is still made but may fail without a store;
        pass a real HeldStore in production).
      * broker.enqueue_action is called ONCE per task-graph thread.
      * mark_questioned prevents re-asking on subsequent runs.
      * mark_consolidated prevents re-emitting a card on subsequent runs.
    """
    cards_emitted = 0
    open_threads = thread_store.get_open_threads()

    for thread_info in open_threads:
        thread_id = thread_info["thread_id"]
        notes = thread_store.get_thread_notes(thread_id)

        if not notes:
            continue  # Defensive: shouldn't happen in normal flow.

        # --- Step 1: concatenate in ts order ---
        concat_transcript = "\n".join(n["transcript"] for n in notes)

        # --- Step 2: injection-scan the CONCATENATION (P2-2 load-bearing fix) ---
        # Per-note scans at ingest are NOT sufficient: a payload split across
        # two notes (e.g., "push to " + "main") passes each per-note scan but
        # triggers "push-to-protected" on the concatenation.  This is the
        # ADDITIONAL concat scan the P2-2 fix mandates.
        findings = scan(concat_transcript)
        safe_transcript = fence(concat_transcript, findings) if findings else concat_transcript

        # --- Step 3: derive consolidated priority from all notes ---
        priority = _consolidate_priority(notes)

        # --- Step 4: split ONCE (one call per thread) ---
        result: Union[TaskGraph, ParkedSpec] = split(
            safe_transcript, repo=repo, base_branch=base_branch
        )

        # --- Step 5: route to card or question-back ---
        if isinstance(result, ParkedSpec):
            # Question-back flow (idempotent per thread_id).
            if thread_store.is_questioned(thread_id):
                # Already asked — skip to avoid duplicate questions.
                continue

            # Render 2–3 proposed answers from the park reason.
            proposed = [
                "Please clarify the acceptance criteria",
                "Provide an example or a concrete outcome",
                f"Reason given: {result.reason[:80]}",
            ]
            enqueue_question(
                job_id=thread_id,
                question=f"Consolidated voice thread is too vague to split.\n\n{result.reason}",
                proposed_answers=proposed[:3],
                held_store=held_store,
            )
            thread_store.mark_questioned(thread_id)
            # No task_card emitted; do NOT increment cards_emitted.

        else:
            # TaskGraph path — emit the consolidated task card.
            card_text = _format_thread_card(result, priority, thread_id)
            broker.enqueue_action(
                type="task_card",
                summary=f"[voice-thread:{thread_id}] {len(result.nodes)} task(s) — {priority} priority",
                payload=card_text,
                origin=f"voice_thread:{thread_id}",
            )
            thread_store.mark_consolidated(thread_id)
            cards_emitted += 1

    return cards_emitted
