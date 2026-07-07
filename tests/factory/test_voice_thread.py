# ABOUTME: RED-first tests for P6-b: voice_thread.py — multi-note threading by
# ABOUTME: (sender, 6h window), durable idempotency, question-back flow via
# ABOUTME: morning_packet.enqueue_question, priority tagging, and the load-bearing
# ABOUTME: cross-note injection scan (P2-2 fix: concatenation scanned BEFORE split).
# ABOUTME: Design source: P6-design.md §2 (REQ-01) + P6-review.md P2-2.
"""
Tests for factory.voice_thread (P6-b).

Design source: P6-design.md §2 (REQ-01) + P6-review.md P2-2.

REQ-01: notes from the same sender within a 6-hour window thread into ONE
        consolidated task card at the morning consolidation tick; notes outside
        the window start a new thread; the card carries a priority tag.
REQ-02 (P2-2 fix): the CONCATENATION of threaded notes is injection-scanned
        BEFORE the single split call.  Per-note scans miss a payload split
        across two notes; the concat scan catches it.  This is the load-bearing
        security fix that a test must falsify if only per-note scanning is done.
REQ-03: question-back flow — a vague consolidated thread emits a 'question'
        held-action (via morning_packet.enqueue_question); re-processing the
        same thread is a no-op (idempotent per thread_id).
REQ-04: durable idempotency — re-delivered note_ids survive a restart (on-disk
        seen-set, not in-memory).
REQ-05: priority tag is advisory metadata, never auto-escalates a job.

Test list (per design P6-b):
  1. test_multi_note_thread_single_card          — REQ-01
  2. test_notes_outside_window_new_thread        — REQ-01
  3. test_durable_idempotency_survives_restart   — REQ-04
  4. test_vague_thread_triggers_question_back    — REQ-03
  5. test_priority_tag_applied                   — REQ-01 priority
  6. test_priority_is_advisory_not_auto_escalation — REQ-01 advisory
  7. test_cross_note_injection_caught_on_concat  — REQ-02 (P2-2 load-bearing)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import MagicMock, call, patch

import pytest

from factory.task_graph import ParkedSpec, TaskGraph, TaskNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_graph(
    nodes: int = 2,
    max_ambiguity: float = 0.0,
    spec_hash: str = "abc123",
) -> TaskGraph:
    """
    Build a minimal valid TaskGraph fixture for splitter mock returns.

    Purpose: keeps each test concise; pre-bakes a two-task graph.
    Usage: graph = _make_graph()
    Gotchas: set max_ambiguity>0 to exercise the park path.
    """
    node_list = [
        TaskNode(
            id=f"T{i}",
            title=f"task {i}",
            task_type="feature",
            acceptance=[f"criterion {i}"],
            depends_on=[] if i == 1 else [f"T{i - 1}"],
            est_files=[f"file{i}.py"],
            ambiguity=max_ambiguity,
        )
        for i in range(1, nodes + 1)
    ]
    return TaskGraph(
        spec_hash=spec_hash,
        repo="test-repo",
        nodes=node_list,
        max_ambiguity=max_ambiguity,
    )


def _parked(reason: str = "too vague to split") -> ParkedSpec:
    """Return a ParkedSpec sentinel for tests that exercise the question-back path."""
    return ParkedSpec(reason=reason, spec_text="ambiguous voice thread")


def _make_broker() -> MagicMock:
    """
    Build a minimal broker mock with a stable enqueue_action side-effect.

    Purpose: avoids real socket connections while exercising card-emit logic.
    Usage: broker = _make_broker(); broker.enqueue_action.return_value = {...}
    Gotchas: action_id is set to 'mock-action-id' by default.
    """
    broker = MagicMock()
    broker.enqueue_action.return_value = {"action_id": "mock-action-id"}
    return broker


# ---------------------------------------------------------------------------
# REQ-01: Multi-note threading → one card
# ---------------------------------------------------------------------------

class TestMultiNoteThreading:
    """Three notes within the 6h window → ONE consolidated task card."""

    def test_multi_note_thread_single_card(self, tmp_path: Path) -> None:
        """
        REQ-01 test 1: 3 notes from the same sender within 6 hours → exactly
        ONE task card emitted at the morning consolidation tick.

        Purpose: verify the core threading contract — multiple notes in a window
        are concatenated and split once, producing one card, not three.
        Usage: pytest -k test_multi_note_thread_single_card
        Gotchas: broker.enqueue_action must be called exactly once (one card).
        """
        from factory.voice_thread import VoiceThread, consolidate_threads

        broker = _make_broker()
        graph = _make_graph()
        thread = VoiceThread(db_path=tmp_path / "threads.db")

        base_ts = int(time.time())
        thread.add_note(sender="yk", note_id="n-1", transcript="fix the login bug", ts=base_ts)
        thread.add_note(sender="yk", note_id="n-2", transcript="also update the docs", ts=base_ts + 3600)
        thread.add_note(sender="yk", note_id="n-3", transcript="and add a test for it", ts=base_ts + 7200)

        # The split is called once with the concatenation of all 3 notes.
        with patch("factory.voice_thread.split", return_value=graph) as mock_split, \
             patch("factory.voice_thread.scan", return_value=[]) as mock_scan:
            cards_emitted = consolidate_threads(thread, broker=broker, repo="r", base_branch="main")

        # Exactly one card is emitted for the one in-window thread.
        assert broker.enqueue_action.call_count == 1
        assert cards_emitted == 1
        # split was called exactly once (one consolidated call, not three).
        assert mock_split.call_count == 1
        # The concat was scanned exactly once before split.
        assert mock_scan.call_count == 1

    def test_notes_outside_window_new_thread(self, tmp_path: Path) -> None:
        """
        REQ-01 test 2: a note arriving >6h after the first starts a new thread;
        the morning tick emits TWO separate cards (one per thread).

        Purpose: verifies the 6h window boundary — notes that arrive too late
        don't collapse into the earlier thread.
        Usage: pytest -k test_notes_outside_window_new_thread
        Gotchas: broker.enqueue_action called twice — once per thread.
        """
        from factory.voice_thread import VoiceThread, consolidate_threads

        broker = _make_broker()
        graph1 = _make_graph(spec_hash="h1")
        graph2 = _make_graph(spec_hash="h2")

        thread = VoiceThread(db_path=tmp_path / "threads.db")
        base_ts = int(time.time())
        # Note 1 is in window.
        thread.add_note(sender="yk", note_id="n-1", transcript="first task", ts=base_ts)
        # Note 2 is >6h later — must start a new thread.
        thread.add_note(sender="yk", note_id="n-2", transcript="second separate task",
                        ts=base_ts + 7 * 3600)

        split_results = iter([graph1, graph2])

        with patch("factory.voice_thread.split", side_effect=lambda *a, **kw: next(split_results)), \
             patch("factory.voice_thread.scan", return_value=[]):
            cards_emitted = consolidate_threads(thread, broker=broker, repo="r", base_branch="main")

        assert broker.enqueue_action.call_count == 2
        assert cards_emitted == 2


# ---------------------------------------------------------------------------
# REQ-04: Durable idempotency survives restart
# ---------------------------------------------------------------------------

class TestDurableIdempotency:
    """A re-delivered note_id must be a no-op after a simulated restart."""

    def test_durable_idempotency_survives_restart(self, tmp_path: Path) -> None:
        """
        REQ-04 test 3: adding the same note_id twice — even after re-instantiating
        VoiceThread from the same DB path — is a no-op (durable seen-set).

        Purpose: proves idempotency is on-disk, not in-memory, so a gateway restart
        doesn't re-thread a note that was already added.
        Usage: pytest -k test_durable_idempotency_survives_restart
        Gotchas: the second VoiceThread instance reads the same SQLite file and
        must already know about note_id n-1.
        """
        from factory.voice_thread import VoiceThread

        db_path = tmp_path / "threads.db"
        ts = int(time.time())

        # First instance: add the note.
        thread1 = VoiceThread(db_path=db_path)
        result1 = thread1.add_note(sender="yk", note_id="n-1",
                                   transcript="deploy the hotfix", ts=ts)
        assert result1 is True  # accepted

        # Second instance (simulated restart): same DB, same note_id → no-op.
        thread2 = VoiceThread(db_path=db_path)
        result2 = thread2.add_note(sender="yk", note_id="n-1",
                                   transcript="deploy the hotfix", ts=ts)
        assert result2 is False  # rejected (already seen)

        # The note should appear only once in the thread.
        notes = thread2.get_notes(sender="yk", window_ts=ts)
        assert sum(1 for n in notes if n["note_id"] == "n-1") == 1


# ---------------------------------------------------------------------------
# REQ-03: Question-back flow
# ---------------------------------------------------------------------------

class TestQuestionBackFlow:
    """Vague thread → question-back via morning_packet; idempotent per thread_id."""

    def test_vague_thread_triggers_question_back(self, tmp_path: Path) -> None:
        """
        REQ-03 test 4: a thread whose consolidated split returns ParkedSpec must
        emit a 'question' held-action (via morning_packet.enqueue_question) with
        2–3 proposed answers, NOT a dead-end park.

        Purpose: prove that vague consolidated notes trigger the interactive
        clarification path rather than silently parking.
        Usage: pytest -k test_vague_thread_triggers_question_back
        Gotchas: enqueue_question is called with ≥2 proposed_answers; the broker
        enqueue_action for a task_card must NOT be called on this path.
        """
        from factory.voice_thread import VoiceThread, consolidate_threads

        broker = _make_broker()
        parked = _parked("too vague to split")

        thread = VoiceThread(db_path=tmp_path / "threads.db")
        ts = int(time.time())
        thread.add_note(sender="yk", note_id="n-1", transcript="do something", ts=ts)

        with patch("factory.voice_thread.split", return_value=parked), \
             patch("factory.voice_thread.scan", return_value=[]), \
             patch("factory.voice_thread.enqueue_question") as mock_enq:
            mock_enq.return_value = "q-action-id"
            cards_emitted = consolidate_threads(
                thread, broker=broker, repo="r", base_branch="main"
            )

        # No task_card — it's a question card.
        broker.enqueue_action.assert_not_called()
        # The question-back was enqueued.
        mock_enq.assert_called_once()
        call_kwargs = mock_enq.call_args.kwargs
        assert len(call_kwargs.get("proposed_answers", [])) >= 2
        # Returns 0 task cards but still processed the thread.
        assert cards_emitted == 0

    def test_question_back_is_idempotent(self, tmp_path: Path) -> None:
        """
        REQ-03 test (idempotency): re-processing the same thread (same thread_id)
        must NOT emit a second question-back.

        Purpose: ensures the question-back is sent at most once per thread so
        the owner isn't flooded with duplicate questions on re-runs.
        Usage: pytest -k test_question_back_is_idempotent
        Gotchas: after the first consolidate_threads pass, the thread must be
        marked as questioned so subsequent passes skip re-asking.
        """
        from factory.voice_thread import VoiceThread, consolidate_threads

        broker = _make_broker()
        parked = _parked()

        thread = VoiceThread(db_path=tmp_path / "threads.db")
        ts = int(time.time())
        thread.add_note(sender="yk", note_id="n-1", transcript="vague task", ts=ts)

        with patch("factory.voice_thread.split", return_value=parked), \
             patch("factory.voice_thread.scan", return_value=[]), \
             patch("factory.voice_thread.enqueue_question", return_value="q-1") as mock_enq:
            consolidate_threads(thread, broker=broker, repo="r", base_branch="main")
            # Re-run: should NOT ask again.
            consolidate_threads(thread, broker=broker, repo="r", base_branch="main")

        # Exactly 1 question, not 2.
        assert mock_enq.call_count == 1


# ---------------------------------------------------------------------------
# REQ-01: Priority tagging
# ---------------------------------------------------------------------------

class TestPriorityTagging:
    """Priority markers in transcripts flow onto the emitted card."""

    def test_priority_tag_applied(self, tmp_path: Path) -> None:
        """
        REQ-01 test 5: a note containing 'urgent' produces a task card tagged
        'high'; a note with no keyword produces 'normal'.

        Purpose: verifies the plain-code priority keyword map in voice_thread.
        Usage: pytest -k test_priority_tag_applied
        Gotchas: priority flows into the broker enqueue_action payload/summary,
        not into the splitter — check broker call args.
        """
        from factory.voice_thread import VoiceThread, consolidate_threads

        broker_high = _make_broker()
        broker_normal = _make_broker()
        graph = _make_graph()
        ts = int(time.time())

        # --- urgent note ---
        thread_high = VoiceThread(db_path=tmp_path / "high.db")
        thread_high.add_note(sender="yk", note_id="n-u", transcript="urgent: fix prod now", ts=ts)

        with patch("factory.voice_thread.split", return_value=graph), \
             patch("factory.voice_thread.scan", return_value=[]):
            consolidate_threads(thread_high, broker=broker_high, repo="r", base_branch="main")

        call_kwargs_high = broker_high.enqueue_action.call_args.kwargs
        payload_high = call_kwargs_high.get("payload", "") or str(
            broker_high.enqueue_action.call_args
        )
        assert "high" in payload_high.lower() or "urgent" in payload_high.lower()

        # --- plain note ---
        thread_normal = VoiceThread(db_path=tmp_path / "normal.db")
        thread_normal.add_note(sender="yk", note_id="n-p", transcript="refactor auth module", ts=ts)

        with patch("factory.voice_thread.split", return_value=graph), \
             patch("factory.voice_thread.scan", return_value=[]):
            consolidate_threads(thread_normal, broker=broker_normal, repo="r", base_branch="main")

        call_kwargs_normal = broker_normal.enqueue_action.call_args.kwargs
        payload_normal = call_kwargs_normal.get("payload", "") or str(
            broker_normal.enqueue_action.call_args
        )
        assert "normal" in payload_normal.lower()

    def test_priority_is_advisory_not_auto_escalation(self, tmp_path: Path) -> None:
        """
        REQ-01 test 6: a priority-tagged card must NOT auto-reprioritize a job
        in the queue — it remains a held task_card that the owner must approve.

        Purpose: ensures priority is advisory metadata on the card, not an
        automatic scheduler action (which would bypass the broker nonce wall).
        Usage: pytest -k test_priority_is_advisory_not_auto_escalation
        Gotchas: broker.enqueue_action type must be 'task_card' (not
        'queue_reprioritize') regardless of the priority tag.
        """
        from factory.voice_thread import VoiceThread, consolidate_threads

        broker = _make_broker()
        graph = _make_graph()
        ts = int(time.time())

        thread = VoiceThread(db_path=tmp_path / "threads.db")
        thread.add_note(sender="yk", note_id="n-1",
                        transcript="p0 asap fix the billing service", ts=ts)

        with patch("factory.voice_thread.split", return_value=graph), \
             patch("factory.voice_thread.scan", return_value=[]):
            consolidate_threads(thread, broker=broker, repo="r", base_branch="main")

        # Must be a task_card, not a queue_reprioritize action.
        call_kwargs = broker.enqueue_action.call_args.kwargs
        assert call_kwargs.get("type") == "task_card"


# ---------------------------------------------------------------------------
# REQ-02 (P2-2 load-bearing): Cross-note injection caught on concatenation
# ---------------------------------------------------------------------------

class TestCrossNoteInjectionScan:
    """
    P2-2 fix: an injection payload SPLIT across two notes (so each note passes
    scan() individually) MUST be caught when the CONCATENATION is scanned.

    This test is the load-bearing security test: if the implementation only
    scans each note individually (without scanning the concatenation), the test
    MUST FAIL.  The test is specifically written so that:
      - note 1: "push to " — passes scan individually (partial phrase)
      - note 2: "main"    — passes scan individually (single word)
      - concat: "push to main" — triggers the 'push-to-protected' rule

    The implementation MUST call scan(concatenation) and fence if flagged.
    """

    def test_cross_note_injection_caught_on_concat(self, tmp_path: Path) -> None:
        """
        REQ-02 test 7 (P2-2 fix): a payload SPLIT across two notes is caught
        only when the concatenation is scanned — per-note scans miss it.

        Purpose: proves consolidate_threads calls scan() on the full concatenated
        transcript, not just on individual notes.  Uses real injection_scan.scan
        (not a mock) so the test is not weakened by a mock that always returns [].

        Usage: pytest -k test_cross_note_injection_caught_on_concat
        Gotchas:
          * This test uses REAL injection_scan.scan — mocking scan would make
            the test trivially pass even without the concat scan.
          * If the implementation only scans per-note, part 1 ("push to ") and
            part 2 ("main") each pass clean; only the concat "push to main"
            triggers the push-to-protected rule.
          * The card payload must be fenced (contain "[EXTERNAL CONTENT") or
            the thread marked flagged — the split must NOT receive a raw blob
            with an un-fenced injection.
        """
        from factory.injection_scan import scan, is_injection
        from factory.voice_thread import VoiceThread, consolidate_threads

        broker = _make_broker()
        graph = _make_graph()
        ts = int(time.time())

        # These two individual fragments each pass scan() on their own.
        note1_text = "push to "     # incomplete — scan returns [] for this alone
        note2_text = "main"         # just the word — scan returns [] for this alone

        # Verify the test premise: per-note scans are clean.
        assert scan(note1_text) == [], "test premise: note1 alone must pass scan"
        assert scan(note2_text) == [], "test premise: note2 alone must pass scan"

        # But the concatenation MUST trigger the push-to-protected rule.
        concat = note1_text + " " + note2_text
        assert is_injection(concat), "test premise: concat must trigger scan"

        thread = VoiceThread(db_path=tmp_path / "threads.db")
        thread.add_note(sender="yk", note_id="n-1", transcript=note1_text, ts=ts)
        thread.add_note(sender="yk", note_id="n-2", transcript=note2_text, ts=ts + 60)

        # Run consolidate_threads with the REAL injection_scan.scan (no mock).
        # The split mock captures what text is passed to it.
        split_received: list = []

        def capturing_split(text, **kwargs):
            split_received.append(text)
            return graph

        with patch("factory.voice_thread.split", side_effect=capturing_split):
            consolidate_threads(thread, broker=broker, repo="r", base_branch="main")

        # The split must have received FENCED content (not the raw injection blob).
        assert split_received, "split must have been called"
        delivered_text = split_received[0]
        assert "[EXTERNAL CONTENT" in delivered_text, (
            "The cross-note injection must be caught and fenced before split() sees it. "
            "Per-note-only scanning misses this — the concatenation scan is load-bearing."
        )

    def test_clean_concat_reaches_split_unfenced(self, tmp_path: Path) -> None:
        """
        Sanity: a clean multi-note concat (no injection) must NOT be fenced —
        confirm the concat scan is not over-aggressive (no false positives on clean text).

        Purpose: ensures the P2-2 fix doesn't break the happy path.
        Usage: pytest -k test_clean_concat_reaches_split_unfenced
        Gotchas: the split must receive clean, un-fenced text when there is no injection.
        """
        from factory.voice_thread import VoiceThread, consolidate_threads

        broker = _make_broker()
        graph = _make_graph()
        ts = int(time.time())

        thread = VoiceThread(db_path=tmp_path / "threads.db")
        thread.add_note(sender="yk", note_id="n-1",
                        transcript="refactor the billing service", ts=ts)
        thread.add_note(sender="yk", note_id="n-2",
                        transcript="add unit tests for the new path", ts=ts + 600)

        split_received: list = []

        def capturing_split(text, **kwargs):
            split_received.append(text)
            return graph

        with patch("factory.voice_thread.split", side_effect=capturing_split):
            consolidate_threads(thread, broker=broker, repo="r", base_branch="main")

        assert split_received
        assert "[EXTERNAL CONTENT" not in split_received[0], (
            "Clean text must not be fenced — no false positives from the concat scan."
        )
