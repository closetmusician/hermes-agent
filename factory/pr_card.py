# ABOUTME: PRCard schema for the P4-d morning packet (P4 design §5.2 + pr_card.py row).
# ABOUTME: Carries 6 required fields: summary, reasoning, alternatives, reversibility,
# ABOUTME: diff_stat, confidence, risk. Long payloads are NEVER truncated — the broker
# ABOUTME: action-id → full-payload pattern (HeldStore.payload column is unbounded TEXT).
# ABOUTME: build_card is a pure constructor; to_dict() serializes for broker delivery.
"""
PRCard schema + constructor (P4-d, design §5.2).

PRCard is a plain dataclass — no I/O, no AI. The morning packet builds one per job and
delivers it as a HeldStore held-action whose payload is the full JSON-serialized card.
Long payloads (reasoning, review findings) are never truncated: the broker stores them
in an unbounded TEXT column, and the summary is the visible Telegram surface while the
full payload rides the action_id reference.

Fields (all required unless noted):
  job_id, repo, branch             — identity
  summary                          — one-line visible in Telegram
  reasoning                        — why this approach
  alternatives                     — list of considered-but-rejected alternatives
  reversibility                    — 'reversible' | 'irreversible'
  diff_stat                        — {files, insertions, deletions}
  confidence                       — float 0..1 (from P4-f; 0.5 placeholder if absent)
  risk                             — {blast_radius: float, source: str}
  state                            — 'AWAITING_APPROVAL' | 'NEEDS_ATTENTION'
  test_result (optional)           — 'pass'|'fail'|'flaky'|None
  review_findings (optional)       — list of finding strings from gauntlet
  forensic_bundle (optional)       — path string when state=NEEDS_ATTENTION

Usage:
  card = build_card(job_id="j-1", repo="acme", branch="factory/acme/j-1-add-x",
                    summary="Add rate-limit header", ...)
  payload_str = json.dumps(card.to_dict())   # never truncated

Gotchas:
  * reversibility='irreversible' ⇒ the card is EXCLUDED from batch-auto and always
    held for individual human review (morning_packet.partition_for_batch enforces this).
  * risk.source should be 'code-review-graph' when available; use 'placeholder' when
    the code-review-graph plugin is not available (documented placeholder per REQ-03).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PRCard:
    """
    One morning-packet card representing a job that completed overnight.

    Purpose: the unit the owner taps through in the morning digest — each card maps
    to one held-action in the broker (action_id → full JSON payload).
    Usage: card = build_card(...); held_store.enqueue(type='pr_card', payload=json.dumps(card.to_dict()))
    Gotchas: confidence defaults to 0.5 (neutral) when P4-f has not populated it yet.
    """

    job_id: str
    repo: str
    branch: str
    summary: str
    reasoning: str
    alternatives: List[str]
    reversibility: str  # 'reversible' | 'irreversible'
    diff_stat: Dict[str, int]
    confidence: float
    risk: Dict[str, Any]
    state: str  # 'AWAITING_APPROVAL' | 'NEEDS_ATTENTION'
    test_result: Optional[str] = None
    review_findings: List[str] = field(default_factory=list)
    forensic_bundle: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        Purpose: serialize all fields to a plain dict suitable for json.dumps.
        Usage: payload = json.dumps(card.to_dict())
        Gotchas: long strings (reasoning, alternatives) are included VERBATIM —
        never truncated — so the broker's TEXT column carries the full payload.
        """
        return {
            "job_id": self.job_id,
            "repo": self.repo,
            "branch": self.branch,
            "summary": self.summary,
            "reasoning": self.reasoning,
            "alternatives": list(self.alternatives),
            "reversibility": self.reversibility,
            "diff_stat": dict(self.diff_stat),
            "confidence": self.confidence,
            "risk": dict(self.risk),
            "state": self.state,
            "test_result": self.test_result,
            "review_findings": list(self.review_findings),
            "forensic_bundle": self.forensic_bundle,
        }


def build_card(
    *,
    job_id: str,
    repo: str,
    branch: str,
    summary: str,
    reasoning: str,
    alternatives: List[str],
    reversibility: str,
    diff_stat: Dict[str, int],
    confidence: float,
    risk: Dict[str, Any],
    state: str,
    test_result: Optional[str] = None,
    review_findings: Optional[List[str]] = None,
    forensic_bundle: Optional[str] = None,
) -> PRCard:
    """
    Purpose: construct a PRCard from named arguments; the canonical factory for tests
    and the morning_packet builder.
    Usage: card = build_card(job_id=..., repo=..., ...)
    Gotchas: reversibility must be 'reversible' or 'irreversible'; any other value
    is accepted but will behave as 'irreversible' in partition_for_batch (fail-safe).
    """
    return PRCard(
        job_id=job_id,
        repo=repo,
        branch=branch,
        summary=summary,
        reasoning=reasoning,
        alternatives=list(alternatives),
        reversibility=reversibility,
        diff_stat=dict(diff_stat),
        confidence=float(confidence),
        risk=dict(risk),
        state=state,
        test_result=test_result,
        review_findings=list(review_findings) if review_findings is not None else [],
        forensic_bundle=forensic_bundle,
    )


def card_from_job(job: Dict[str, Any]) -> PRCard:
    """
    Purpose: build a minimal PRCard from a raw job dict (as returned by JobStore.get).
    Derives summary from spec[:80]; fills reasoning/risk with placeholder values when
    P4-f/code-review-graph has not populated the job yet.
    Usage: card = card_from_job(store.get(jid))
    Gotchas: confidence defaults to 0.5 when the jobs.confidence column is NULL
    (P4-f has not run yet, or the job pre-dates P4-f). The caller may override fields
    by calling build_card directly.
    """
    confidence = job.get("confidence")
    if confidence is None:
        confidence = 0.5  # neutral placeholder per P4 design §7

    fail_reason = job.get("fail_reason")
    state = job.get("state", "AWAITING_APPROVAL")

    return PRCard(
        job_id=job["id"],
        repo=job.get("repo", ""),
        branch=job.get("branch") or "",
        summary=(job.get("spec") or "")[:80],
        reasoning="(generated from job spec — full reasoning available in job log)",
        alternatives=[],
        reversibility="reversible",  # safe default; owner should override per-card
        diff_stat={"files": 0, "insertions": 0, "deletions": 0},
        confidence=float(confidence),
        risk={"blast_radius": 0.0, "source": "placeholder"},
        state=state,
        test_result=job.get("test_result"),
        review_findings=[],
        forensic_bundle=fail_reason if state == "NEEDS_ATTENTION" else None,
    )
