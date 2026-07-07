# ABOUTME: RED-first tests for factory/morning_packet.py + factory/pr_card.py (P4-d).
# ABOUTME: REQ-02: packet is CONFIDENCE-RANKED (highest first); batch-approve routes
# ABOUTME: each card through the broker nonce (no bulk bypass). REQ-03: PR card has
# ABOUTME: all 6 required fields; long payloads are not truncated (action-id pattern).
# ABOUTME: REQ-04: a queued question is tap-to-answer; the answer routes back.
"""
Tests for factory.morning_packet + factory.pr_card (P4-d).

Design: docs/plans/harness/fable/p4/P4-design.md §5.2, §5.3.

REQ-02  Packet is confidence-ranked (desc); batch-approve still uses broker nonce.
REQ-03  PRCard carries all 6 required fields; long payload not truncated.
REQ-04  A parked question is tap-to-answer via broker held-action; answer routes back.
REQ-05  batch-approve MUST NOT bypass the per-action nonce (anti-weakening).

Anti-weakening:
  * test_batch_approve_still_uses_broker_nonce — FAILS if batch_approve skips the
    broker's approve call and merges directly (nonce bypass = P0 security defect).
  * test_packet_ranked_by_confidence_desc — FAILS if ranking order is wrong/missing.
  * test_irreversible_card_never_batch_auto — FAILS if irreversible cards are included.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import MagicMock, call, patch

import pytest

from factory.job_store import JobStore
from factory.cost_stops import DailyBudgetLedger
from broker.held_store import HeldStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(store: JobStore, *, confidence: Optional[float] = None,
              state: str = "AWAITING_APPROVAL",
              repo: str = "test-repo",
              spec: str = "add feature") -> str:
    """Insert a minimal job row and optionally set its confidence."""
    jid = store.enqueue_job({
        "repo": repo,
        "spec": spec,
        "kind": "feature",
        "worker": "claude",
        "model": "claude-sonnet",
        "budget_usd": 1.0,
        "timeout_min": 30,
    })
    # Drive it to the desired state.
    state_seq = ["QUEUED", "ADMITTED", "RUNNING", "TEST", "REVIEW", "AWAITING_APPROVAL",
                 "MERGING", "DONE"]
    idx = state_seq.index(state)
    transitions = list(zip(state_seq, state_seq[1:]))[:idx]
    for frm, to in transitions:
        store.transition(jid, frm, to)
    # Set confidence via direct UPDATE (mirrors what P4-f does at AWAITING_APPROVAL).
    if confidence is not None:
        with store._lock:
            store._conn.execute(
                "UPDATE jobs SET confidence=? WHERE id=?", (confidence, jid)
            )
            store._conn.commit()
    return jid


# ---------------------------------------------------------------------------
# REQ-02: Packet ranked by confidence descending (anti-weakening)
# ---------------------------------------------------------------------------


def test_packet_ranked_by_confidence_desc(tmp_path):
    """
    Purpose: 3 jobs with confidences 0.9 / 0.5 / 0.2 → cards ordered 0.9, 0.5, 0.2.
    Anti-weakening: FAILS if ranking is missing or ascending.
    """
    from factory.morning_packet import build

    store = JobStore(tmp_path / "jobs.db")
    confs = [0.5, 0.9, 0.2]
    jids = [_make_job(store, confidence=c) for c in confs]

    packet = build(store=store, held_store=None)
    card_confs = [c.confidence for c in packet.cards]

    assert card_confs == sorted(card_confs, reverse=True), (
        f"Packet not ranked by confidence desc: {card_confs}"
    )
    # Exact order: 0.9 first.
    assert card_confs[0] == 0.9


def test_needs_attention_jobs_surface_distinctly(tmp_path):
    """
    Purpose: A job in NEEDS_ATTENTION surfaces in the packet distinctly (flagged).
    """
    from factory.morning_packet import build

    store = JobStore(tmp_path / "jobs.db")
    jid_ok = _make_job(store, confidence=0.8, state="AWAITING_APPROVAL")
    # Enqueue + drive to NEEDS_ATTENTION.
    jid_bad = store.enqueue_job({"repo": "r", "spec": "s", "kind": "feature",
                                  "worker": "claude", "model": "m",
                                  "budget_usd": 1.0, "timeout_min": 30})
    store.transition(jid_bad, "QUEUED", "ADMITTED")
    store.transition(jid_bad, "ADMITTED", "RUNNING")
    store.transition(jid_bad, "RUNNING", "NEEDS_ATTENTION",
                     fail_reason="self-heal exhausted")

    packet = build(store=store, held_store=None)
    attention_ids = {c.job_id for c in packet.cards if c.state == "NEEDS_ATTENTION"}
    assert jid_bad in attention_ids, "NEEDS_ATTENTION job not surfaced distinctly"


# ---------------------------------------------------------------------------
# REQ-03: PR card has all required fields; long payload not truncated
# ---------------------------------------------------------------------------


def test_pr_card_has_all_fields(tmp_path):
    """
    Purpose: A card built from a completed job carries all 6 required fields:
    summary, reasoning, alternatives, reversibility, diff_stat, confidence, risk.
    """
    from factory.pr_card import PRCard, build_card

    card = build_card(
        job_id="j-abc",
        repo="acme",
        branch="factory/acme/j-abc-add-x",
        summary="Add rate-limit header",
        reasoning="Prevents 429 storms from downstream callers",
        alternatives=["Option A: header only", "Option B: middleware"],
        reversibility="reversible",
        diff_stat={"files": 3, "insertions": 84, "deletions": 12},
        confidence=0.83,
        risk={"blast_radius": 0.2, "source": "code-review-graph"},
        state="AWAITING_APPROVAL",
    )

    required = ["summary", "reasoning", "alternatives", "reversibility",
                "diff_stat", "confidence", "risk"]
    for field in required:
        assert hasattr(card, field), f"PRCard missing field: {field}"
        assert getattr(card, field) is not None, f"PRCard field {field!r} is None"

    # Serializable to dict.
    d = card.to_dict()
    for field in required:
        assert field in d


def test_long_payload_not_truncated(tmp_path):
    """
    Purpose: A card with a very long reasoning string (>2000 chars) is stored
    in the broker held-action without truncation (action-id → full-payload pattern).
    """
    from factory.pr_card import PRCard, build_card

    long_reasoning = "x" * 5000
    card = build_card(
        job_id="j-long",
        repo="big-repo",
        branch="factory/big-repo/j-long-thing",
        summary="Long card",
        reasoning=long_reasoning,
        alternatives=[],
        reversibility="reversible",
        diff_stat={"files": 1, "insertions": 1, "deletions": 0},
        confidence=0.5,
        risk={"blast_radius": 0.1, "source": "placeholder"},
        state="AWAITING_APPROVAL",
    )

    payload_str = json.dumps(card.to_dict())
    assert long_reasoning in payload_str, (
        "Long reasoning was truncated in the serialized card payload"
    )
    assert len(card.reasoning) == 5000


# ---------------------------------------------------------------------------
# REQ-02 + REQ-05: Batch-approve still routes through broker nonce (anti-weakening)
# ---------------------------------------------------------------------------


def test_batch_approve_still_uses_broker_nonce(tmp_path):
    """
    Purpose: batch_approve(card_ids, held_store) approves EACH card by calling
    held_store.transition(aid, 'held', 'approved') — the broker nonce gate.
    It MUST NOT bypass the per-action transition and merge directly.
    Anti-weakening: FAILS if batch_approve calls merge directly without transition.
    """
    from factory.morning_packet import batch_approve

    # Two mock held-action IDs.
    mock_held = MagicMock()
    action_ids = ["act-1", "act-2"]

    batch_approve(action_ids=action_ids, held_store=mock_held, decided_by="yk")

    # Each card must have had its broker transition called — nonce gate honoured.
    expected_calls = [
        call.transition("act-1", "held", "approved", decided_by="yk"),
        call.transition("act-2", "held", "approved", decided_by="yk"),
    ]
    mock_held.transition.assert_has_calls(expected_calls, any_order=False)
    # Total transition calls == len(action_ids) — no extras, no bulk bypass.
    assert mock_held.transition.call_count == 2, (
        "batch_approve called transition a wrong number of times — possible bulk bypass"
    )


def test_irreversible_card_never_batch_auto(tmp_path):
    """
    Purpose: A card with reversibility='irreversible' is EXCLUDED from
    batch_approve and returned in the 'held_for_review' list.
    Anti-weakening: FAILS if irreversible cards are auto-approved.
    """
    from factory.morning_packet import partition_for_batch

    from factory.pr_card import build_card

    card_rev = build_card(
        job_id="j-rev", repo="r", branch="b", summary="s", reasoning="r",
        alternatives=[], reversibility="reversible",
        diff_stat={"files": 1, "insertions": 1, "deletions": 0},
        confidence=0.9,
        risk={"blast_radius": 0.1, "source": "placeholder"},
        state="AWAITING_APPROVAL",
    )
    card_irrev = build_card(
        job_id="j-irrev", repo="r", branch="b", summary="s2", reasoning="r2",
        alternatives=[], reversibility="irreversible",
        diff_stat={"files": 2, "insertions": 10, "deletions": 5},
        confidence=0.8,
        risk={"blast_radius": 0.4, "source": "placeholder"},
        state="AWAITING_APPROVAL",
    )

    batchable, held = partition_for_batch([card_rev, card_irrev])
    assert card_rev in batchable
    assert card_irrev not in batchable
    assert card_irrev in held


# ---------------------------------------------------------------------------
# REQ-04: Question queue — tap-to-answer resumes the job
# ---------------------------------------------------------------------------


def test_question_queue_tap_answers_resume_job(tmp_path):
    """
    Purpose: A parked question (from spec-review / ambiguity) is delivered via the
    broker as a held action; answering it (transition 'held'→'approved') routes the
    answer back and makes the job resumable.
    """
    from factory.morning_packet import enqueue_question, answer_question

    held_store = HeldStore(tmp_path / "held.db")

    # Enqueue a question for a parked job.
    aid = enqueue_question(
        job_id="j-parked",
        question="Which API to use: v1 or v2?",
        proposed_answers=["v1", "v2", "defer"],
        held_store=held_store,
    )
    assert aid is not None, "enqueue_question returned no action_id"

    # Verify the action is in the held store as 'held'.
    action = held_store.get(aid)
    assert action is not None
    assert action["state"] == "held"
    assert action["type"] == "question"

    # Tap-to-answer: answering routes back (held → approved).
    result = answer_question(
        action_id=aid,
        answer="v2",
        held_store=held_store,
    )

    # After answer, the action should be approved (answer routed).
    action_after = held_store.get(aid)
    assert action_after["state"] == "approved", (
        f"Answer did not route back via broker; state={action_after['state']}"
    )
    assert result["answer"] == "v2"


def test_question_payload_not_truncated(tmp_path):
    """
    Purpose: A question with a long body is stored untruncated in the broker.
    """
    from factory.morning_packet import enqueue_question

    held_store = HeldStore(tmp_path / "held.db")
    long_q = "What should we do about " + "x" * 3000 + "?"
    aid = enqueue_question(
        job_id="j-q2",
        question=long_q,
        proposed_answers=["yes", "no"],
        held_store=held_store,
    )
    action = held_store.get(aid)
    payload = json.loads(action["payload"])
    assert long_q in payload.get("question", ""), (
        "Question was truncated in the broker payload"
    )
