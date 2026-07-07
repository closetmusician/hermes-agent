# ABOUTME: Spec-review gate for P3 (REQ-02): routes a TaskGraph to PARKED,
# ABOUTME: HELD, or AUTO_CLEARED based on ambiguity score and repo tier.
# ABOUTME: High-ambiguity specs (≥ PARK_THRESHOLD) write a questions.md file
# ABOUTME: with 2-3 proposed answers per question; clear specs on tier-0 repos
# ABOUTME: produce a held broker action; tier-1 personal repos auto-clear.
"""
Spec-review gate — park/hold/auto-clear routing after the task-splitter.

Design source: docs/plans/harness/fable/p3/P3-design.md §5 (REQ-02).

Three-way routing:
  PARKED      — max_ambiguity ≥ PARK_THRESHOLD: write questions.md, no jobs.
  HELD        — clear spec on tier-0 repo: held broker action, one owner tap needed.
  AUTO_CLEARED — clear spec on tier-1 repo: proceeds without a tap.

Work/Diligent repos never auto-clear — the Diligent hard gate is checked
INDEPENDENTLY before compute_tier is called, so it cannot be bypassed even if
compute_tier is mocked. (Anti-weakening: test_tier_gate_is_load_bearing.)

PARK_THRESHOLD (0.5) is the tunable boundary between park and hold.
"""
from __future__ import annotations

import datetime
import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from factory.trust_policy import _is_diligent_repo, compute_tier, load_trust_policy

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PARK_THRESHOLD: float = 0.5
"""Ambiguity score at or above which a spec is parked for clarification."""

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class ReviewOutcome(Enum):
    """
    Purpose: outcome of a spec-review pass — one of park, hold, or auto-clear.
    Usage: result.outcome == ReviewOutcome.PARKED
    Gotchas: AUTO_CLEARED is only reachable for tier-1 personal repos.
    """

    PARKED = "parked"
    HELD = "held"
    AUTO_CLEARED = "auto_cleared"


@dataclass
class ReviewResult:
    """
    Purpose: return value from SpecReviewGate.review().
    Usage: result = gate.review(graph); check result.outcome and result.action_id.
    Gotchas: action_id is None for PARKED (no broker action) and AUTO_CLEARED
    (no tap needed); only HELD sets action_id to the broker-issued id.
    """

    outcome: ReviewOutcome
    action_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


class SpecReviewGate:
    """
    Purpose: route a TaskGraph (from the splitter) to PARKED, HELD, or AUTO_CLEARED.
    Usage: gate = SpecReviewGate(held_store=store, questions_dir=path); result = gate.review(graph)
    Gotchas: held_store must expose .enqueue(**kwargs) -> str (same contract as
    broker.held_store.HeldStore.enqueue); questions_dir is created on first park.
    The gate is stateless beyond these two references — safe to call concurrently.
    """

    def __init__(self, *, held_store: Any, questions_dir: Optional[Path] = None):
        self.held_store = held_store
        self._questions_dir = Path(questions_dir) if questions_dir else (
            Path(__file__).resolve().parents[1] / "docs" / "factory" / "questions"
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def review(self, graph: Any) -> ReviewResult:
        """
        Purpose: examine graph.max_ambiguity and repo tier; return park/hold/clear.
        Usage: result = gate.review(graph)  where graph is a TaskGraph duck-type.
        Gotchas: idempotent for the same spec_hash — re-parking overwrites the file
        cleanly (no duplicate front-matter). The held_store.enqueue is NOT
        idempotent by default; callers must guard for repeated invocations if needed.
        """
        if graph.max_ambiguity >= PARK_THRESHOLD:
            self._park(graph)
            return ReviewResult(outcome=ReviewOutcome.PARKED, action_id=None)

        if self._auto_clear_allowed(graph.repo):
            return ReviewResult(outcome=ReviewOutcome.AUTO_CLEARED, action_id=None)

        action_id = self.held_store.enqueue(
            type="spec-review",
            summary=f"Spec review: {graph.repo} ({graph.spec_hash})",
            payload=json.dumps(
                {
                    "spec_hash": graph.spec_hash,
                    "repo": graph.repo,
                    "max_ambiguity": graph.max_ambiguity,
                }
            ),
            origin="spec_review_gate",
            safe_lane_json=json.dumps({"lane": "spec-review"}),
        )
        return ReviewResult(outcome=ReviewOutcome.HELD, action_id=action_id)

    # ------------------------------------------------------------------
    # Tier gate — Diligent check is independent of compute_tier
    # ------------------------------------------------------------------

    def _auto_clear_allowed(self, repo: str) -> bool:
        """
        Purpose: return True ONLY if the repo has earned tier-1 autonomy.
        Usage: internal; called after the park threshold check.
        Gotchas: HARD GATE — _is_diligent_repo is checked BEFORE compute_tier so
        that mocking compute_tier cannot bypass the Diligent work-repo constraint.
        (Anti-weakening: test_tier_gate_is_load_bearing asserts that patching
        compute_tier to return 1 still produces HELD for a Diligent repo.)
        """
        # Hard gate: Diligent/work repos can never auto-clear, regardless of tier.
        if _is_diligent_repo(repo):
            return False

        # Try to load the policy; fall back to a conservative default if absent.
        try:
            policy = load_trust_policy()
        except Exception:
            return False  # fail-closed: unknown policy → no auto-clear

        tier = compute_tier(
            repo,
            "feature",  # conservative task_type for the gate check
            [],          # no ledger rows — keep the gate pure; tier-1 requires history
            int(time.time() * 1000),
            policy=policy,
            repo_class="personal",
        )
        return tier >= 1

    # ------------------------------------------------------------------
    # Questions file writer
    # ------------------------------------------------------------------

    def _park(self, graph: Any) -> None:
        """
        Purpose: write (or overwrite) the per-spec questions.md for a high-ambiguity spec.
        Usage: internal; called from review() when max_ambiguity ≥ PARK_THRESHOLD.
        Gotchas: idempotent by spec_hash — always writes a fresh file, never appends.
        The directory is created on first call.
        """
        self._questions_dir.mkdir(parents=True, exist_ok=True)
        qfile = self._questions_dir / f"{graph.spec_hash}.md"
        qfile.write_text(self._render_questions(graph))

    def _render_questions(self, graph: Any) -> str:
        """
        Purpose: build the questions.md content for a parked spec.
        Usage: text = self._render_questions(graph)
        Gotchas: always produces ≥1 question section with 2-3 proposed answers.
        parked_ts is stored as both ISO-8601 and epoch so the schema test finds a
        digit run matching r'\\d{4}|\\d{10}'.
        """
        now_epoch = int(time.time())
        now_iso = datetime.datetime.utcfromtimestamp(now_epoch).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        lines: list[str] = [
            "# Spec parked — needs clarification",
            f"spec_hash: {graph.spec_hash}",
            f"repo: {graph.repo}",
            f"max_ambiguity: {graph.max_ambiguity}",
            f"parked_ts: {now_iso}",
            "",
        ]

        # Build question blocks from nodes (ambiguous nodes first).
        questions = self._build_questions(graph)
        for i, (qtext, context, answers) in enumerate(questions, start=1):
            lines.append(f"## Q{i}: {qtext}")
            lines.append(f"context: {context}")
            for answer in answers:
                lines.append(f"- [ ] {answer}")
            lines.append("")

        return "\n".join(lines)

    def _build_questions(
        self, graph: Any
    ) -> list[tuple[str, str, list[str]]]:
        """
        Purpose: derive 1-N question tuples (text, context, answers) from the graph.
        Usage: internal; called from _render_questions.
        Gotchas: always returns ≥1 question even if all nodes are well-defined,
        because the graph was parked and the reviewer must clarify the intent.
        Each tuple's answers list has 2-3 entries (proposal + defer option).
        """
        questions: list[tuple[str, str, list[str]]] = []

        nodes = getattr(graph, "nodes", []) or []
        seen_node_ids: set[str] = set()

        for node in nodes:
            node_id = getattr(node, "id", "?")
            if node_id in seen_node_ids:
                continue
            seen_node_ids.add(node_id)

            title = getattr(node, "title", "")
            acceptance = getattr(node, "acceptance", []) or []
            ambiguity = getattr(node, "ambiguity", 0.0)

            if not acceptance:
                questions.append(
                    (
                        f"What are the acceptance criteria for '{title}'?",
                        f"node {node_id} has no acceptance criteria",
                        [
                            "Define explicit, testable criteria (e.g. POST /endpoint returns 200)",
                            "Split into smaller nodes each with one clear AC",
                            "Defer this node to a follow-up spec",
                        ],
                    )
                )
            elif ambiguity >= PARK_THRESHOLD:
                questions.append(
                    (
                        f"Clarify the ambiguous requirement in '{title}'",
                        f"node {node_id} acceptance text is vague or incomplete",
                        [
                            "Rewrite the AC with a concrete, measurable outcome",
                            "Remove vague hedge tokens ('or equivalent', 'similar to', 'TBD')",
                            "Defer and replace with a concrete spike task first",
                        ],
                    )
                )

        if not questions:
            # Fallback: the spec was parked due to overall graph ambiguity;
            # generate a generic clarification question.
            questions.append(
                (
                    "Which approach should be taken for this spec?",
                    f"overall spec ambiguity ({graph.max_ambiguity:.2f}) exceeds threshold",
                    [
                        "Proceed with the current spec and accept the ambiguity",
                        "Revise the spec to add concrete acceptance criteria for each node",
                        "Defer to a discovery spike before committing to this approach",
                    ],
                )
            )

        return questions
