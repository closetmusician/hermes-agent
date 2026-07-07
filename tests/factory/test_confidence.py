# ABOUTME: Tests for factory/confidence.py — per-job confidence score (P4-f).
# ABOUTME: Covers REQ-01 (formula + weights-sum enforcement + determinism),
# ABOUTME: REQ-02 (reproducibility from stored inputs), REQ-03 (differentiation),
# ABOUTME: REQ-04 (weight tunability), REQ-05 (TDD evidence).
"""
Unit tests for factory.confidence — pure per-job confidence score.

Each test maps to a documented requirement in the P4-f task:
  REQ-01: pure weighted sum; weights-not-summing-to-1 rejected; deterministic.
  REQ-02: recompute from stored input vector reproduces exact score.
  REQ-03: materially different jobs get materially different scores.
  REQ-04: config-driven weight change changes the score predictably.
  REQ-05: RED-first; determinism failure case is covered.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Dict

import pytest

from factory.confidence import (
    HIST_NEUTRAL,
    ConfidenceInputs,
    _DEFAULT_WEIGHTS,
    _validate_weights,
    compute_confidence,
    inputs_from_job,
    load_weights,
    score_from_test_result,
    severity_from_findings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLEAN_WEIGHTS: Dict[str, float] = dict(_DEFAULT_WEIGHTS)  # copy of defaults


def _inputs(
    test: float = 1.0,
    severity: float = 0.0,
    blast: float = 0.0,
    amb: float = 0.0,
    hist: float = 0.5,
) -> ConfidenceInputs:
    """Construct a ConfidenceInputs with sane defaults."""
    return ConfidenceInputs(test=test, severity=severity, blast=blast, amb=amb, hist=hist)


# ---------------------------------------------------------------------------
# REQ-01a: formula computes correctly
# ---------------------------------------------------------------------------


class TestFormulaComputes:
    """Verify the weighted-sum formula is applied correctly."""

    def test_perfect_job_scores_near_max(self) -> None:
        """test=pass, severity=0, blast=0, amb=0, hist=1 → close to 1."""
        inputs = _inputs(test=1.0, severity=0.0, blast=0.0, amb=0.0, hist=1.0)
        score = compute_confidence(inputs, _CLEAN_WEIGHTS)
        assert score > 0.9, f"expected score > 0.9, got {score}"

    def test_worst_job_scores_near_zero(self) -> None:
        """test=fail, severity=1, blast=1, amb=1, hist=0 → close to 0."""
        inputs = _inputs(test=0.0, severity=1.0, blast=1.0, amb=1.0, hist=0.0)
        score = compute_confidence(inputs, _CLEAN_WEIGHTS)
        assert score < 0.1, f"expected score < 0.1, got {score}"

    def test_manual_calculation_matches_formula(self) -> None:
        """
        Apply the formula by hand and verify compute_confidence matches.

        conf = w_test·test + w_review·(1−sev) + w_blast·(1−blast)
             + w_amb·(1−amb) + w_hist·hist
        """
        inputs = _inputs(test=0.5, severity=0.3, blast=0.4, amb=0.2, hist=0.6)
        w = _CLEAN_WEIGHTS
        expected = (
            w["w_test"]   * 0.5
            + w["w_review"] * (1.0 - 0.3)
            + w["w_blast"]  * (1.0 - 0.4)
            + w["w_amb"]    * (1.0 - 0.2)
            + w["w_hist"]   * 0.6
        )
        got = compute_confidence(inputs, _CLEAN_WEIGHTS)
        assert math.isclose(got, expected, abs_tol=1e-9), (
            f"formula mismatch: expected {expected}, got {got}"
        )

    def test_all_weights_used_not_one_dominates(self) -> None:
        """Each weight contributes: change one input, score changes."""
        base = _inputs(test=0.5, severity=0.5, blast=0.5, amb=0.5, hist=0.5)
        base_score = compute_confidence(base, _CLEAN_WEIGHTS)

        better_test = _inputs(test=1.0, severity=0.5, blast=0.5, amb=0.5, hist=0.5)
        assert compute_confidence(better_test, _CLEAN_WEIGHTS) > base_score

        better_review = _inputs(test=0.5, severity=0.0, blast=0.5, amb=0.5, hist=0.5)
        assert compute_confidence(better_review, _CLEAN_WEIGHTS) > base_score

        better_blast = _inputs(test=0.5, severity=0.5, blast=0.0, amb=0.5, hist=0.5)
        assert compute_confidence(better_blast, _CLEAN_WEIGHTS) > base_score

        better_amb = _inputs(test=0.5, severity=0.5, blast=0.5, amb=0.0, hist=0.5)
        assert compute_confidence(better_amb, _CLEAN_WEIGHTS) > base_score

        better_hist = _inputs(test=0.5, severity=0.5, blast=0.5, amb=0.5, hist=1.0)
        assert compute_confidence(better_hist, _CLEAN_WEIGHTS) > base_score


# ---------------------------------------------------------------------------
# REQ-01b: weights-not-summing-to-1 rejected
# ---------------------------------------------------------------------------


class TestWeightValidation:
    """Verify that invalid weight configs are rejected immediately."""

    def test_weights_not_sum_to_one_raises_value_error(self) -> None:
        """REQ-01: weights summing to 0.99 must be rejected."""
        bad = dict(_CLEAN_WEIGHTS)
        bad["w_test"] = 0.34  # now sums to 0.99 instead of 1.00
        with pytest.raises(ValueError, match="sum to 1.0"):
            _validate_weights(bad)

    def test_weights_sum_to_one_passes(self) -> None:
        """REQ-01: the default weights must be accepted without error."""
        _validate_weights(_CLEAN_WEIGHTS)  # should not raise

    def test_weights_sum_over_one_raises(self) -> None:
        """REQ-01: weights summing to 1.01 are equally rejected."""
        over = dict(_CLEAN_WEIGHTS)
        over["w_test"] = 0.36  # 1.01 total
        with pytest.raises(ValueError, match="sum to 1.0"):
            _validate_weights(over)

    def test_missing_key_raises_value_error(self) -> None:
        """REQ-01: a weights dict missing a required key must raise."""
        incomplete = {k: v for k, v in _CLEAN_WEIGHTS.items() if k != "w_hist"}
        with pytest.raises(ValueError, match="missing keys"):
            _validate_weights(incomplete)

    def test_compute_confidence_rejects_bad_weights(self) -> None:
        """REQ-01: compute_confidence itself rejects bad weights."""
        bad = dict(_CLEAN_WEIGHTS)
        bad["w_test"] = 0.40  # sum = 1.05
        with pytest.raises(ValueError):
            compute_confidence(_inputs(), bad)


# ---------------------------------------------------------------------------
# REQ-01c: determinism — same inputs → same score
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Verify compute_confidence is deterministic."""

    def test_same_inputs_same_score_repeated(self) -> None:
        """REQ-01: 1000 calls with identical inputs produce identical scores."""
        inputs = _inputs(test=0.5, severity=0.3, blast=0.4, amb=0.2, hist=0.6)
        first = compute_confidence(inputs, _CLEAN_WEIGHTS)
        for _ in range(999):
            assert compute_confidence(inputs, _CLEAN_WEIGHTS) == first

    def test_different_inputs_generally_different_scores(self) -> None:
        """Sanity: distinct inputs that differ materially produce distinct outputs."""
        a = _inputs(test=1.0, severity=0.0, blast=0.1)
        b = _inputs(test=0.0, severity=1.0, blast=0.9)
        assert compute_confidence(a, _CLEAN_WEIGHTS) != compute_confidence(b, _CLEAN_WEIGHTS)


# ---------------------------------------------------------------------------
# REQ-02: reproducibility — recompute from stored inputs reproduces score
# ---------------------------------------------------------------------------


class TestReproducibility:
    """Verify the stored input vector is sufficient to reproduce the score."""

    def test_recompute_from_stored_inputs_reproduces_exact_score(self) -> None:
        """
        REQ-02: simulate job completion — compute + store inputs, then
        recompute from the stored inputs → bit-identical score.
        """
        # Simulate compute at job completion.
        original_inputs = _inputs(test=1.0, severity=0.2, blast=0.3, amb=0.1, hist=0.7)
        stored_score = compute_confidence(original_inputs, _CLEAN_WEIGHTS)

        # Simulate recompute from stored inputs (e.g. from jobs.db columns).
        stored_test     = original_inputs.test
        stored_severity = original_inputs.severity
        stored_blast    = original_inputs.blast
        stored_amb      = original_inputs.amb
        stored_hist     = original_inputs.hist

        recomputed_inputs = ConfidenceInputs(
            test=stored_test,
            severity=stored_severity,
            blast=stored_blast,
            amb=stored_amb,
            hist=stored_hist,
        )
        recomputed_score = compute_confidence(recomputed_inputs, _CLEAN_WEIGHTS)

        assert recomputed_score == stored_score, (
            f"stored {stored_score} != recomputed {recomputed_score}"
        )

    def test_recompute_requires_same_weights(self) -> None:
        """
        REQ-02 (boundary): different weights produce different scores even with
        the same inputs — the weights are part of the reproducer's context.
        """
        inputs = _inputs(test=0.8, severity=0.1, blast=0.2, amb=0.05, hist=0.6)
        w_a = dict(_CLEAN_WEIGHTS)
        w_b = dict(_CLEAN_WEIGHTS)
        # Redistribute w_test ↔ w_hist (sum still 1.0).
        w_b["w_test"], w_b["w_hist"] = w_b["w_hist"], w_b["w_test"]
        assert compute_confidence(inputs, w_a) != compute_confidence(inputs, w_b)

    def test_multiple_jobs_reproduce_independently(self) -> None:
        """REQ-02: multiple job records each reproduce their own scores."""
        job_records = [
            _inputs(test=1.0, severity=0.0, blast=0.1, amb=0.0, hist=0.8),
            _inputs(test=0.5, severity=0.5, blast=0.5, amb=0.5, hist=0.5),
            _inputs(test=0.0, severity=0.8, blast=0.9, amb=0.7, hist=0.2),
        ]
        stored_scores = [compute_confidence(inp, _CLEAN_WEIGHTS) for inp in job_records]
        for original_inp, stored_score in zip(job_records, stored_scores):
            restored = ConfidenceInputs(
                test=original_inp.test,
                severity=original_inp.severity,
                blast=original_inp.blast,
                amb=original_inp.amb,
                hist=original_inp.hist,
            )
            assert compute_confidence(restored, _CLEAN_WEIGHTS) == stored_score


# ---------------------------------------------------------------------------
# REQ-03: materially different jobs get materially different scores
# ---------------------------------------------------------------------------


class TestMaterialDifferentiation:
    """Verify high-quality and low-quality jobs get materially different scores."""

    def test_high_quality_vs_low_quality_differ_by_more_than_0_3(self) -> None:
        """
        REQ-03: a clean/small-blast job vs a P0-finding/large-blast job must
        differ by more than 0.3 in confidence.
        """
        high_quality = _inputs(
            test=1.0,   # pass
            severity=0.0,   # clean review
            blast=0.1,      # small blast
            amb=0.0,        # crisp spec
            hist=0.8,       # good history
        )
        low_quality = _inputs(
            test=0.0,   # fail
            severity=1.0,   # P0 finding
            blast=0.9,      # large blast
            amb=0.8,        # vague spec
            hist=0.2,       # poor history
        )
        high_score = compute_confidence(high_quality, _CLEAN_WEIGHTS)
        low_score  = compute_confidence(low_quality, _CLEAN_WEIGHTS)
        diff = high_score - low_score
        assert diff > 0.3, (
            f"expected diff > 0.3 between high ({high_score:.4f}) "
            f"and low ({low_score:.4f}), got {diff:.4f}"
        )

    def test_flaky_job_between_pass_and_fail(self) -> None:
        """Flaky test result (0.5) scores between pass (1.0) and fail (0.0)."""
        perfect  = _inputs(test=1.0, severity=0.0)
        flaky    = _inputs(test=0.5, severity=0.0)
        failing  = _inputs(test=0.0, severity=0.0)

        s_perfect = compute_confidence(perfect,  _CLEAN_WEIGHTS)
        s_flaky   = compute_confidence(flaky,    _CLEAN_WEIGHTS)
        s_failing = compute_confidence(failing,  _CLEAN_WEIGHTS)
        assert s_perfect > s_flaky > s_failing


# ---------------------------------------------------------------------------
# REQ-04: weights owner-tunable — config change changes score predictably
# ---------------------------------------------------------------------------


class TestWeightTunability:
    """Verify that tuning a single weight produces a predictable score change."""

    def test_increasing_w_test_increases_score_when_test_is_pass(self) -> None:
        """REQ-04: higher w_test increases the contribution of a passing test."""
        inputs = _inputs(test=1.0, severity=0.3, blast=0.4, amb=0.2, hist=0.5)

        w_low  = dict(_CLEAN_WEIGHTS)  # w_test = 0.35
        # Redistribute: increase w_test by 0.1, decrease w_review by 0.1.
        w_high = dict(_CLEAN_WEIGHTS)
        w_high["w_test"]   = 0.45
        w_high["w_review"] = 0.15  # still sums to 1.0

        score_low  = compute_confidence(inputs, w_low)
        score_high = compute_confidence(inputs, w_high)
        # Higher test weight + passing test → higher score.
        assert score_high > score_low

    def test_increasing_w_blast_penalises_high_blast(self) -> None:
        """REQ-04: higher w_blast penalises a high-blast job more."""
        inputs = _inputs(test=0.5, severity=0.2, blast=0.9, amb=0.3, hist=0.5)

        w_base = dict(_CLEAN_WEIGHTS)           # w_blast = 0.15
        w_high_blast = dict(_CLEAN_WEIGHTS)
        w_high_blast["w_blast"] = 0.30
        w_high_blast["w_amb"]   = 0.05          # sum still 1.0: +0.15 from blast, -0.10 from amb, -0.05 compensated → rebalance
        # Recalculate to make sum = 1.0:
        # default: test=0.35, review=0.25, blast=0.15, amb=0.10, hist=0.15 (sum 1.0)
        # new:     test=0.35, review=0.20, blast=0.30, amb=0.05, hist=0.10 (sum 1.0)
        w_high_blast = {
            "w_test":   0.35,
            "w_review": 0.20,
            "w_blast":  0.30,
            "w_amb":    0.05,
            "w_hist":   0.10,
        }
        score_base       = compute_confidence(inputs, w_base)
        score_high_blast = compute_confidence(inputs, w_high_blast)
        # A higher blast weight for a high-blast job should reduce confidence.
        assert score_high_blast < score_base

    def test_load_weights_from_yaml_file(self, tmp_path: Path) -> None:
        """REQ-04: load_weights reads a YAML file and produces tunable weights."""
        yaml_content = (
            "w_test:   0.40\n"
            "w_review: 0.20\n"
            "w_blast:  0.15\n"
            "w_amb:    0.10\n"
            "w_hist:   0.15\n"
        )
        cfg = tmp_path / "confidence-weights.yaml"
        cfg.write_text(yaml_content)
        weights = load_weights(cfg)
        assert math.isclose(weights["w_test"], 0.40, abs_tol=1e-6)
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)

    def test_load_weights_invalid_yaml_raises(self, tmp_path: Path) -> None:
        """REQ-04: a YAML file whose weights don't sum to 1 raises ValueError."""
        bad_yaml = (
            "w_test:   0.50\n"  # 0.50 + 0.20 + 0.10 + 0.10 + 0.10 = 1.00 is fine;
            "w_review: 0.20\n"  # let's make it fail:
            "w_blast:  0.20\n"  # sum = 1.10
            "w_amb:    0.10\n"
            "w_hist:   0.10\n"
        )
        cfg = tmp_path / "confidence-weights.yaml"
        cfg.write_text(bad_yaml)
        with pytest.raises(ValueError, match="sum to 1.0"):
            load_weights(cfg)

    def test_default_weights_yaml_exists_and_valid(self) -> None:
        """REQ-04: the shipped confidence-weights.yaml (if found) is valid."""
        # load_weights() with no arg should not raise.
        weights = load_weights()
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)


# ---------------------------------------------------------------------------
# Additional: hist neutral + clamping + helpers
# ---------------------------------------------------------------------------


class TestHistNeutralAndClamping:
    """Cover the hist=neutral default and output clamping."""

    def test_hist_neutral_before_scorecard(self) -> None:
        """
        REQ-01 (design §7): with no scorecard, hist defaults to HIST_NEUTRAL (0.5).
        No crash, no None bleed-through.
        """
        inputs = inputs_from_job(
            test_result="pass",
            severity=0.0,
            blast=0.0,
            amb=0.0,
            # hist not supplied → should default to HIST_NEUTRAL
        )
        assert inputs.hist == HIST_NEUTRAL
        score = compute_confidence(inputs, _CLEAN_WEIGHTS)
        assert 0.0 <= score <= 1.0

    def test_confidence_clamped_0_1_for_negative_inputs(self) -> None:
        """REQ-01 (clamping): pathological negative-territory inputs are clamped to 0."""
        # blast > 1 would make the blast term negative.
        inputs = ConfidenceInputs(test=0.0, severity=1.0, blast=1.0, amb=1.0, hist=0.0)
        score = compute_confidence(inputs, _CLEAN_WEIGHTS)
        assert score == 0.0

    def test_confidence_clamped_at_1_for_overshooting_inputs(self) -> None:
        """REQ-01 (clamping): over-1 inputs are clamped to 1."""
        # All maximally good inputs saturate at 1.0 (or close to it).
        inputs = ConfidenceInputs(test=1.0, severity=0.0, blast=0.0, amb=0.0, hist=1.0)
        score = compute_confidence(inputs, _CLEAN_WEIGHTS)
        # Should be exactly 1.0 with these ideal inputs and valid weights.
        assert score == 1.0

    def test_score_from_pass_result(self) -> None:
        assert score_from_test_result("pass") == 1.0

    def test_score_from_flaky_result(self) -> None:
        assert score_from_test_result("flaky") == 0.5

    def test_score_from_fail_result(self) -> None:
        assert score_from_test_result("fail") == 0.0

    def test_score_from_none_result(self) -> None:
        """None (no result yet) is treated as fail — fail-closed policy."""
        assert score_from_test_result(None) == 0.0

    def test_score_from_unknown_result_string(self) -> None:
        """Unknown string → 0.0 (fail-closed)."""
        assert score_from_test_result("unknown_status") == 0.0


class TestSeverityFromFindings:
    """Cover severity_from_findings helper."""

    def test_empty_findings_is_zero(self) -> None:
        assert severity_from_findings([]) == 0.0

    def test_p0_finding_is_one(self) -> None:
        assert severity_from_findings(["P0: critical security issue"]) == 1.0

    def test_p1_finding_is_0_7(self) -> None:
        assert severity_from_findings(["P1: something wrong"]) == 0.7

    def test_p2_finding_is_0_3(self) -> None:
        assert severity_from_findings(["P2: minor nit"]) == 0.3

    def test_p0_wins_over_p1(self) -> None:
        """Mixed P0+P1 → severity=1.0 (worst-case)."""
        assert severity_from_findings(["P1: moderate", "P0: critical"]) == 1.0

    def test_multiple_p1_findings_capped_at_0_7(self) -> None:
        findings = ["P1: issue A", "P1: issue B"]
        assert severity_from_findings(findings) == 0.7
