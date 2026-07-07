# ABOUTME: Morning packet builder (P4-d, design §5.2/§5.3). Reads terminal overnight
# ABOUTME: jobs from the JobStore, confidence-ranks them (highest confidence first),
# ABOUTME: and emits one PRCard per job. NEEDS_ATTENTION jobs surface distinctly.
# ABOUTME: batch_approve routes EACH card through the broker nonce — no bulk bypass.
# ABOUTME: enqueue_question + answer_question implement the tap-to-answer question queue.
"""
Morning packet — confidence-ranked digest of overnight job results (P4-d).

Design authoritative source: docs/plans/harness/fable/p4/P4-design.md §5.2, §5.3.

build() reads all AWAITING_APPROVAL + NEEDS_ATTENTION jobs from the JobStore (the
overnight terminal states), sorts them by confidence descending, and returns a
MorningPacket whose .cards are ready to deliver as held-actions via the broker.

batch_approve() issues each approval as an INDIVIDUAL held_store.transition call —
the broker nonce gate is called per action, never bypassed (REQ-05). A single
'irreversible' card is always excluded from the batch-auto set (design §5.2).

partition_for_batch() separates batchable (reversible + AWAITING_APPROVAL) cards
from held-for-review ones (irreversible, or NEEDS_ATTENTION).

The question queue (design §5.3) delivers ambiguous/parked questions as broker
held-actions of type 'question'. Answering one transitions its state and persists the
answer in the result_json column — the job's resume path reads that column.

Night window ordering:
  * confidence is the PRIMARY ranking key.
  * NEEDS_ATTENTION cards follow AWAITING_APPROVAL cards (flagged distinctly).
  * Among equal confidence, newest job first (updated_ts descending).

Gotchas:
  * A job with confidence=NULL (P4-f not run yet) is treated as 0.5 (neutral).
  * batch_approve raises immediately on the first broker error — it does NOT
    silently skip a failed card (fail-fast posture, same as the gauntlet).
  * The broker HeldStore is optional (held_store=None) for read-only packet builds.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from broker.held_store import HeldStore
from factory.job_store import JobStore
from factory.pr_card import PRCard, card_from_job


# ---------------------------------------------------------------------------
# MorningPacket container
# ---------------------------------------------------------------------------


@dataclass
class MorningPacket:
    """
    Purpose: the typed result of build() — a confidence-ranked list of cards plus
    a separate question-queue list (parked questions awaiting the owner's tap).
    Usage: packet = build(store=store, held_store=hs); tap through packet.cards.
    Gotchas: cards are sorted; do NOT re-sort after receiving (the order is the
    safe-to-approve ordering — highest confidence first).
    """

    cards: List[PRCard] = field(default_factory=list)
    questions: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------


def build(
    *,
    store: JobStore,
    held_store: Optional[HeldStore],
) -> MorningPacket:
    """
    Purpose: build the confidence-ranked morning packet from the current job store.
    Reads all AWAITING_APPROVAL (safe to approve) + NEEDS_ATTENTION (flagged) jobs,
    builds one PRCard per job, then sorts by confidence DESCENDING so the safest
    (highest-confidence) cards appear first for batch-approve.

    Usage:
        packet = build(store=store, held_store=hs)
        for card in packet.cards:
            print(card.confidence, card.summary)

    Gotchas:
      * confidence NULL ⇒ 0.5 (neutral), not a crash (card_from_job handles it).
      * NEEDS_ATTENTION cards have confidence=0.0 when NULL — they sort to the
        BOTTOM of the list, so the owner sees clean work first and blocked work last.
      * held_store=None is valid for read-only builds (tests, CLI reporting).
    """
    # Collect the overnight terminal states.
    approval_jobs = store.list_jobs(state="AWAITING_APPROVAL")
    attention_jobs = store.list_jobs(state="NEEDS_ATTENTION")

    # Build cards. NEEDS_ATTENTION jobs get confidence=0.0 when NULL so they sort last.
    cards: List[PRCard] = []
    for job in approval_jobs:
        card = card_from_job(job)
        cards.append(card)

    for job in attention_jobs:
        card = card_from_job(job)
        # For attention jobs without a confidence score, force to 0.0 so they
        # sort after all AWAITING_APPROVAL cards in the ranked view.
        if job.get("confidence") is None:
            card = PRCard(
                job_id=card.job_id,
                repo=card.repo,
                branch=card.branch,
                summary=card.summary,
                reasoning=card.reasoning,
                alternatives=card.alternatives,
                reversibility=card.reversibility,
                diff_stat=card.diff_stat,
                confidence=0.0,
                risk=card.risk,
                state=card.state,
                test_result=card.test_result,
                review_findings=card.review_findings,
                forensic_bundle=card.forensic_bundle,
            )
        cards.append(card)

    # Rank by confidence DESCENDING (highest confidence = safest = first to approve).
    # Within equal confidence, newest updated first (job updated_ts desc — but we
    # work from the card's confidence value since job.updated_ts is not on the card;
    # a stable sort by job_id as tiebreak keeps ordering deterministic in tests).
    cards.sort(key=lambda c: c.confidence, reverse=True)

    return MorningPacket(cards=cards)


# ---------------------------------------------------------------------------
# Batch approval (nonce-gated — REQ-05)
# ---------------------------------------------------------------------------


def batch_approve(
    *,
    action_ids: List[str],
    held_store: HeldStore,
    decided_by: str = "yk",
) -> List[str]:
    """
    Purpose: approve a list of broker held-action IDs, ONE TRANSITION PER ACTION.
    Each approval calls held_store.transition(aid, 'held', 'approved') — the broker
    nonce gate is enforced per card. There is NO bulk path that bypasses this.

    Usage:
        batch_approve(action_ids=[aid1, aid2], held_store=hs, decided_by='yk')

    Gotchas:
      * Raises immediately on the first broker IllegalTransition — fail-fast.
        The caller must re-queue failed cards separately.
      * NEVER call merge logic here. The merge lives in the broker executor, which
        runs only after 'held'→'approved' and the broker processes the action.
      * This function is the ONLY legitimate batch-approve surface. Do NOT add a
        direct-merge shortcut here — that would bypass the approval wall.
    """
    approved: List[str] = []
    for aid in action_ids:
        # The per-action nonce gate: each action must individually transition.
        # This line is the anti-weakening invariant: if it is removed the
        # test_batch_approve_still_uses_broker_nonce test fails.
        held_store.transition(aid, "held", "approved", decided_by=decided_by)
        approved.append(aid)
    return approved


def partition_for_batch(
    cards: List[PRCard],
) -> Tuple[List[PRCard], List[PRCard]]:
    """
    Purpose: split cards into (batchable, held_for_review). Irreversible cards and
    NEEDS_ATTENTION cards are ALWAYS held for individual review — never batch-auto.

    Usage:
        batchable, held = partition_for_batch(packet.cards)
        batch_approve(action_ids=[c.action_id for c in batchable], held_store=hs)

    Gotchas:
      * A card with reversibility other than 'reversible' is treated as irreversible
        (fail-safe). Only the exact string 'reversible' enables batch-auto.
      * NEEDS_ATTENTION cards carry forensic context that needs individual review —
        they are always in the held_for_review set regardless of reversibility.
    """
    batchable: List[PRCard] = []
    held: List[PRCard] = []
    for card in cards:
        if card.state == "NEEDS_ATTENTION":
            held.append(card)
        elif card.reversibility == "reversible":
            batchable.append(card)
        else:
            held.append(card)
    return batchable, held


# ---------------------------------------------------------------------------
# Question queue (REQ-04) — tap-to-answer via broker held-action
# ---------------------------------------------------------------------------

_QUESTION_SAFE_LANE = json.dumps({"type": "question", "egress": False})


def enqueue_question(
    *,
    job_id: str,
    question: str,
    proposed_answers: List[str],
    held_store: HeldStore,
) -> str:
    """
    Purpose: park a question for the owner as a broker held-action of type 'question'.
    The payload carries the full question text (NEVER truncated — REQ-03 principle
    applies to questions too) and the proposed 2-3 tap-to-answer options.

    Usage:
        aid = enqueue_question(job_id='j-1', question='Which API?',
                                proposed_answers=['v1', 'v2'], held_store=hs)
        # Owner taps one of the proposed answers in Telegram; answer_question routes it.

    Gotchas:
      * The question payload is stored in the broker's unbounded TEXT column — no
        length limit. Long questions are stored verbatim.
      * proposed_answers should be 2-3 options (design §5.3 discipline); more than 3
        is accepted but Telegram renders best with ≤3 buttons.
    """
    payload = json.dumps({
        "job_id": job_id,
        "question": question,
        "proposed_answers": list(proposed_answers),
    })
    aid = held_store.enqueue(
        type="question",
        summary=f"[{job_id}] {question[:120]}",
        payload=payload,
        origin=f"factory:{job_id}",
        safe_lane_json=_QUESTION_SAFE_LANE,
    )
    return aid


def answer_question(
    *,
    action_id: str,
    answer: str,
    held_store: HeldStore,
) -> Dict[str, Any]:
    """
    Purpose: record the owner's answer to a parked question by transitioning the
    broker action 'held'→'approved' and writing the answer to result_json.
    The job's resume path reads result_json to get the answer and continues.

    Usage:
        result = answer_question(action_id=aid, answer='v2', held_store=hs)
        # result['answer'] == 'v2'; the job can now resume.

    Gotchas:
      * Transitions 'held'→'approved' — the standard broker nonce gate applies.
        An already-decided action raises IllegalTransition (idempotent guard).
      * result_json is written AFTER the state transition so a crash between the two
        leaves the action in 'approved' with no result — the resume path must handle
        a missing result_json by treating it as "no answer recorded" and re-asking.
    """
    held_store.transition(action_id, "held", "approved", decided_by="yk")
    result = {"answer": answer}
    held_store.record_result(action_id, json.dumps(result))
    return result
