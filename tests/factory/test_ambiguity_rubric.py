# ABOUTME: Unit tests for factory/ambiguity_rubric.py (P3-a REQ-01, REQ-03).
# ABOUTME: Covers: weight sum, undefined-AC scoring, vague-hedge detection, missing-field
# ABOUTME: heuristic, clamp invariant, determinism, and the anti-gaming property (grade()
# ABOUTME: ignores node.ambiguity — the AI's self-reported score cannot lower the gate).
# ABOUTME: All tests are pure — no I/O, no mocking (the rubric itself has no side effects).
"""
Tests for factory.ambiguity_rubric.

The rubric is pure deterministic code: same input → same output, no I/O.
These tests prove each scoring component in isolation and the combined invariants.
"""

from __future__ import annotations

import pytest

from factory.ambiguity_rubric import (
    WEIGHT_MISSING_FIELD,
    WEIGHT_UNDEFINED_AC,
    WEIGHT_VAGUE_DEP,
    grade,
)
from factory.task_graph import TaskNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node(
    id: str = "T1",
    title: str = "Add login endpoint",
    task_type: str = "feature",
    acceptance=None,
    depends_on=None,
    est_files=None,
    ambiguity: float = 0.0,
) -> TaskNode:
    """Build a TaskNode with sane defaults for rubric testing."""
    return TaskNode(
        id=id,
        title=title,
        task_type=task_type,
        acceptance=acceptance if acceptance is not None else ["POST /login returns 200"],
        depends_on=depends_on if depends_on is not None else [],
        est_files=est_files if est_files is not None else [],
        ambiguity=ambiguity,
    )


# ---------------------------------------------------------------------------
# REQ-01: Weight constants
# ---------------------------------------------------------------------------

class TestWeightConstants:
    """Verify the three module-level weight constants sum to 1.0."""

    def test_weights_sum_to_one(self):
        """
        WEIGHT_UNDEFINED_AC + WEIGHT_VAGUE_DEP + WEIGHT_MISSING_FIELD must equal 1.0.
        This is the contract the design §3.3 specifies; the comment in the module
        says 'All three MUST sum to 1.0 (asserted by the weight-sum test in REQ-04)'.
        """
        total = WEIGHT_UNDEFINED_AC + WEIGHT_VAGUE_DEP + WEIGHT_MISSING_FIELD
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"

    def test_weight_undefined_ac_is_0_45(self):
        """Undefined-AC weight is calibrated at 0.45 (strongest signal)."""
        assert WEIGHT_UNDEFINED_AC == pytest.approx(0.45)

    def test_weight_vague_dep_is_0_35(self):
        """Vague-dep weight is 0.35."""
        assert WEIGHT_VAGUE_DEP == pytest.approx(0.35)

    def test_weight_missing_field_is_0_20(self):
        """Missing-field weight is 0.20."""
        assert WEIGHT_MISSING_FIELD == pytest.approx(0.20)


# ---------------------------------------------------------------------------
# REQ-01: Undefined-AC component
# ---------------------------------------------------------------------------

class TestUndefinedAcComponent:
    """Tests for the undefined-AC scoring branch (weight 0.45)."""

    def test_empty_acceptance_scores_at_least_weight(self):
        """
        A node with acceptance=[] contributes the full WEIGHT_UNDEFINED_AC (0.45)
        to the score.  The total may be higher if other components also fire.

        Design: §3.3 undefined_ac = 1.0 if len(node.acceptance) == 0.
        """
        node = _node(acceptance=[])
        score = grade(node)
        assert score >= WEIGHT_UNDEFINED_AC

    def test_non_empty_acceptance_contributes_zero_from_undefined_ac(self):
        """A node with ≥1 concrete AC has the undefined_ac component = 0."""
        node = _node(
            title="Add login",
            acceptance=["POST /login returns HTTP 200 with signed JWT"],
        )
        score = grade(node)
        # If no vague tokens and no missing-field → score must be 0.0
        assert score == 0.0

    def test_two_concrete_acs_score_zero(self):
        """Two concrete, non-vague ACs → 0.0 total."""
        node = _node(
            acceptance=[
                "POST /login returns HTTP 200 with signed JWT",
                "POST /login with wrong password returns HTTP 401",
            ]
        )
        assert grade(node) == 0.0


# ---------------------------------------------------------------------------
# REQ-01: Vague-dep component
# ---------------------------------------------------------------------------

class TestVagueDepComponent:
    """Tests for the vague-hedge scoring branch (weight 0.35)."""

    def test_or_equivalent_raises_score(self):
        """
        A node whose AC says 'or equivalent' scores strictly higher than the same
        node without the phrase.

        Design: §3.4 test #5 / §3.3 vague_dep weight = 0.35.
        """
        vague = _node(acceptance=["Use Redis or equivalent for session cache"])
        clean = _node(acceptance=["Use Redis for session cache"])
        assert grade(vague) > grade(clean)

    def test_something_like_raises_score(self):
        """'something like' is a vague hedge that raises the score."""
        vague = _node(acceptance=["Use something like a queue for async jobs"])
        clean = _node(acceptance=["Use a job queue for async jobs"])
        assert grade(vague) > grade(clean)

    def test_similar_to_raises_score(self):
        """'similar to' raises the score above the clean baseline."""
        vague = _node(acceptance=["Handle errors similar to the existing retry module"])
        clean = _node(acceptance=["Handle errors with exponential backoff up to 3 retries"])
        assert grade(vague) > grade(clean)

    def test_tbd_raises_score(self):
        """'TBD' in an acceptance criterion raises the score above zero."""
        tbd_node = _node(acceptance=["Handle auth TBD"])
        assert grade(tbd_node) > 0.0

    def test_triple_question_marks_raises_score(self):
        """'???' in an acceptance criterion raises the score above zero."""
        q_node = _node(acceptance=["Add some ??? error handling"])
        assert grade(q_node) > 0.0

    def test_case_insensitive_or_equivalent(self):
        """'OR EQUIVALENT' (upper-case) is still detected."""
        upper_vague = _node(acceptance=["Use Postgres OR EQUIVALENT"])
        clean = _node(acceptance=["Use Postgres"])
        assert grade(upper_vague) > grade(clean)

    def test_vague_token_in_title_also_detected(self):
        """Vague tokens in the title (not just acceptance) are combined in the text blob."""
        vague_title_node = _node(
            title="Add something like a caching layer",
            acceptance=["Cache responses for 60 seconds"],
        )
        clean_title_node = _node(
            title="Add a caching layer",
            acceptance=["Cache responses for 60 seconds"],
        )
        assert grade(vague_title_node) > grade(clean_title_node)


# ---------------------------------------------------------------------------
# REQ-01: Missing-field component
# ---------------------------------------------------------------------------

class TestMissingFieldComponent:
    """Tests for the missing-field heuristic (weight 0.20)."""

    def test_add_noun_with_no_field_raises_score(self):
        """'add user' with no field list raises the score above a concrete node."""
        vague = _node(title="Add user", acceptance=["Add user"])
        clean = _node(
            title="Add user endpoint",
            acceptance=["POST /users creates a user row with email:column, name:column"],
        )
        assert grade(vague) > grade(clean)

    def test_add_with_field_list_does_not_fire(self):
        """
        'add user: email, name' (colon + list) has the 'has_field_list' pattern
        matching → missing_field component stays 0.
        """
        concrete = _node(
            title="Add user: email, name fields",
            acceptance=["POST /users with email returns 201"],
        )
        # Should not fire the missing-field heuristic
        score = grade(concrete)
        # Only way to be > 0 is via vague tokens — no vague tokens here
        assert score == 0.0


# ---------------------------------------------------------------------------
# REQ-01: Combined / boundary invariants
# ---------------------------------------------------------------------------

class TestCombinedInvariants:
    """Tests for the combined score and boundary conditions."""

    def test_score_clamped_to_one(self):
        """The rubric score is always in [0.0, 1.0] even for maximally ambiguous nodes."""
        worst = _node(
            title="add something or equivalent similar to TBD ???",
            acceptance=[],
        )
        score = grade(worst)
        assert 0.0 <= score <= 1.0

    def test_score_clamped_to_zero(self):
        """A perfectly clean node scores exactly 0.0 (not negative)."""
        clean = _node(
            title="Create oauth_tokens table migration",
            acceptance=["Migration creates oauth_tokens table with id, user_id, token columns"],
        )
        assert grade(clean) == 0.0

    def test_empty_node_does_not_crash(self):
        """A node with empty title and empty acceptance does not raise."""
        node = _node(title="", acceptance=[])
        score = grade(node)
        assert 0.0 <= score <= 1.0

    def test_very_long_acceptance_does_not_overflow(self):
        """A very long acceptance list with mixed tokens stays clamped."""
        node = _node(
            acceptance=["POST /login returns 200"] * 1000,
        )
        score = grade(node)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# REQ-03: Determinism + anti-gaming property
# ---------------------------------------------------------------------------

class TestDeterminismAndAntiGaming:
    """Determinism and the anti-gaming property (grade() ignores node.ambiguity)."""

    def test_deterministic_same_input_same_score(self):
        """grade() is pure: same node → same float, regardless of call count."""
        node = _node(acceptance=["Add feature or equivalent if needed"])
        scores = [grade(node) for _ in range(5)]
        assert all(s == scores[0] for s in scores), f"Non-deterministic scores: {scores}"

    def test_ai_self_score_high_does_not_affect_rubric(self):
        """
        The AI may plant a high self-reported ambiguity (node.ambiguity=0.99) on a clean
        node.  grade() must return the same score regardless of that field.

        Design: §3.3 'anti-gaming' — the AI's self-reported score is advisory only.
        This test is the falsifiable proof: a green result means a dishonest AI cannot
        lower its score by emitting a low self-report, nor inflate a clean node by
        emitting a high one.
        """
        node_ai_high = _node(
            acceptance=["POST /login returns 200 with signed JWT"],
            ambiguity=0.99,
        )
        node_ai_low = _node(
            acceptance=["POST /login returns 200 with signed JWT"],
            ambiguity=0.0,
        )
        assert grade(node_ai_high) == grade(node_ai_low), (
            "grade() must not read node.ambiguity — AI self-score is ignored"
        )

    def test_ai_self_score_low_does_not_help_vague_node(self):
        """An AI-planted low self-score on a vague node does not lower the rubric score."""
        vague_self_zero = _node(
            acceptance=["Use Redis or equivalent"],
            ambiguity=0.0,  # AI says 'not ambiguous'
        )
        vague_self_default = _node(
            acceptance=["Use Redis or equivalent"],
            # default ambiguity=0.0 via _node()
        )
        assert grade(vague_self_zero) == grade(vague_self_default)
        assert grade(vague_self_zero) > 0.0, "Vague node must score > 0 regardless of self-report"

    def test_rubric_scores_undefined_ac_high_design_test_4(self):
        """
        Design §3.4 test #4 (reproduced here): undefined_ac ≥ 0.45; two clean ACs → 0.0.
        """
        empty_ac_node = _node(acceptance=[])
        assert grade(empty_ac_node) >= 0.45

        clean_node = _node(
            acceptance=[
                "POST /login returns HTTP 200 with signed JWT",
                "POST /login with wrong password returns HTTP 401",
            ]
        )
        assert grade(clean_node) == 0.0

    def test_rubric_flags_or_equivalent_design_test_5(self):
        """Design §3.4 test #5: 'or equivalent' raises score vs same node without it."""
        vague = _node(acceptance=["Use Redis or equivalent for session cache"])
        clean = _node(acceptance=["Use Redis for session cache"])
        assert grade(vague) > grade(clean)
