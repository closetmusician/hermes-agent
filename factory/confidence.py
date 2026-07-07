# ABOUTME: Per-job confidence score for P4-f (design §7, REQ-04).
# ABOUTME: compute_confidence(inputs, weights) is a PURE weighted sum, clamped
# ABOUTME: to [0,1]. Weights are owner-tunable via confidence-weights.yaml.
# ABOUTME: The input vector is stored alongside the score so recomputing from
# ABOUTME: stored inputs reproduces the exact score (reproducibility gate).
"""
Per-job confidence score — pure, deterministic, reproducible.

Design source: docs/plans/harness/fable/p4/P4-design.md §7 (REQ-04b / P4-6).

Formula:
    conf = w_test·test + w_review·(1 − severity) + w_blast·(1 − blast)
         + w_amb·(1 − amb) + w_hist·hist

All five input terms are normalised to [0, 1] before the caller supplies them.
The output is clamped to [0, 1].  Weights must sum to 1.0 (within a small
floating-point tolerance); load_weights() raises ValueError otherwise.

Reproducibility: every term in the input vector is stored in jobs.db alongside
the score (via transition(extra=…)).  Calling compute_confidence with the stored
vector + the same weights reproduces the exact score (Python float arithmetic is
deterministic for a given platform and the same sequence of ops).

Default weights live in confidence-weights.yaml at the repo root (or
factory/confidence-weights.yaml if the root file is absent).  They are
*owner-tunable* — changing a weight changes the score predictably with no code
edit needed.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

try:
    import yaml  # PyYAML — optional dep; fallback to hardcoded defaults on import error
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Default weights — design §7 + OQ-P4-1
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS: Dict[str, float] = {
    "w_test":   0.35,
    "w_review": 0.25,
    "w_blast":  0.15,
    "w_amb":    0.10,
    "w_hist":   0.15,
}
"""
Default confidence weights.  Must sum to 1.0.  Owner-tunable via
confidence-weights.yaml without any code change.

  w_test   — weight on the test-pass signal (highest: a passing suite is the
             single strongest quality indicator).
  w_review — weight on the inverse-severity signal (a P0 finding = severity 1
             means this term contributes 0; a clean review = 1.0).
  w_blast  — weight on the inverse blast-radius (large-blast diffs are riskier).
  w_amb    — weight on the inverse ambiguity (vague specs produce risky jobs).
  w_hist   — weight on the historical success rate for this repo×task_type.
"""

# Tolerance for the weights-sum-to-1 check.
_WEIGHT_SUM_TOLERANCE: float = 1e-6

# Neutral hist value used when no scorecard data is available (design §7).
HIST_NEUTRAL: float = 0.5

# Canonical term-to-result mapping for the 'test' input.
TEST_RESULT_TO_SCORE: Dict[Optional[str], float] = {
    "pass":  1.0,
    "flaky": 0.5,
    "fail":  0.0,
    None:    0.0,  # treat unknown as fail (fail-closed)
}


# ---------------------------------------------------------------------------
# ConfidenceInputs — the stored input vector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfidenceInputs:
    """
    Purpose: the full input vector for one confidence computation.  This
    dataclass is stored alongside the score in jobs.db so that recomputing
    from the stored vector with the same weights reproduces the exact score.
    Usage: inputs = ConfidenceInputs(test=1.0, severity=0.0, blast=0.2, amb=0.1, hist=0.5)
    Gotchas: all fields are normalised [0, 1]; the caller is responsible for
    normalisation.  test should be derived via test_score_from_result() rather
    than set directly.
    """

    test: float
    """Normalised test-pass signal: pass=1.0, flaky=0.5, fail/None=0.0."""

    severity: float
    """Normalised review severity: 0.0 = clean, 1.0 = P0 finding."""

    blast: float
    """Normalised blast radius: 0.0 = minimal impact, 1.0 = high impact."""

    amb: float
    """Normalised ambiguity score: 0.0 = crisp spec, 1.0 = vague."""

    hist: float
    """Historical success rate: 0.0 = never merges, 1.0 = always; 0.5 neutral."""


def score_from_test_result(result: Optional[str]) -> float:
    """
    Purpose: convert a gauntlet test_result string to the normalised [0,1]
    test score used in compute_confidence.
    Usage: score = score_from_test_result(gauntlet_result.test_result)
    Gotchas: unknown strings are treated as fail (0.0), not as an error — this
    is a fail-closed policy so a misconfigured gauntlet cannot inflate confidence.
    """
    return TEST_RESULT_TO_SCORE.get(result, 0.0)


# Keep the old name as an alias to avoid breaking any existing callers.
test_score_from_result = score_from_test_result


# ---------------------------------------------------------------------------
# Weights loading
# ---------------------------------------------------------------------------


def load_weights(path: Optional[Path] = None) -> Dict[str, float]:
    """
    Purpose: load confidence weights from confidence-weights.yaml (or the
    passed path).  Falls back to _DEFAULT_WEIGHTS when the file is absent.
    Usage: weights = load_weights()  # uses the repo-root or factory/ yaml
    Gotchas: raises ValueError if the loaded weights do not sum to 1.0 within
    _WEIGHT_SUM_TOLERANCE.  This is the owner-tunable config entry point —
    change a weight in the YAML, the score changes predictably on the next run.
    """
    if path is None:
        path = _find_weights_file()

    if path is not None and path.exists():
        raw = _read_yaml(path)
        weights = {k: float(v) for k, v in raw.items() if k.startswith("w_")}
    else:
        weights = dict(_DEFAULT_WEIGHTS)

    _validate_weights(weights)
    return weights


def _find_weights_file() -> Optional[Path]:
    """
    Purpose: locate confidence-weights.yaml by searching from this file's
    directory upward to the repo root, then checking factory/.
    Usage: internal; called by load_weights() when no explicit path is given.
    Gotchas: returns None if not found (caller falls back to hardcoded defaults).
    """
    here = Path(__file__).parent
    for candidate in (
        here / "confidence-weights.yaml",
        here.parent / "confidence-weights.yaml",
    ):
        if candidate.exists():
            return candidate
    return None


def _read_yaml(path: Path) -> dict:
    """
    Purpose: read a YAML file, returning an empty dict if yaml is unavailable.
    Usage: internal.
    Gotchas: if PyYAML is not installed the file is not parsed and defaults are
    used — intentional so the module works without the optional dep.
    """
    if yaml is None:
        return {}
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def _validate_weights(weights: Dict[str, float]) -> None:
    """
    Purpose: assert that the weights dict contains exactly the five expected
    keys and that they sum to 1.0 within tolerance.
    Usage: internal; called by load_weights().
    Gotchas: raises ValueError — not a soft warning — so a misconfigured YAML
    does not silently produce nonsense scores.
    """
    required = {"w_test", "w_review", "w_blast", "w_amb", "w_hist"}
    missing = required - weights.keys()
    if missing:
        raise ValueError(f"confidence weights missing keys: {sorted(missing)}")

    total = sum(weights[k] for k in required)
    if not math.isclose(total, 1.0, abs_tol=_WEIGHT_SUM_TOLERANCE):
        raise ValueError(
            f"confidence weights must sum to 1.0 (got {total:.8f}); "
            f"weights={weights}"
        )


# ---------------------------------------------------------------------------
# The core pure function
# ---------------------------------------------------------------------------


def compute_confidence(
    inputs: ConfidenceInputs,
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """
    Purpose: compute the per-job confidence score as a pure weighted sum,
    clamped to [0, 1].  Fully deterministic: same inputs + same weights always
    yields the same float.  No I/O, no side effects.

    Formula (design §7):
        conf = w_test·test + w_review·(1 − severity) + w_blast·(1 − blast)
             + w_amb·(1 − amb) + w_hist·hist

    Each term maps to a confidence contributor:
      - test:        higher = tests pass (good)
      - 1−severity:  higher = cleaner review (fewer/lower severity findings)
      - 1−blast:     higher = smaller blast radius (safer change)
      - 1−amb:       higher = crisper spec (less ambiguity)
      - hist:        higher = better historical success rate

    Usage:
        inputs = ConfidenceInputs(test=1.0, severity=0.0, blast=0.2, amb=0.0, hist=0.5)
        score = compute_confidence(inputs)          # uses default weights
        score = compute_confidence(inputs, weights) # uses caller-supplied weights

    Gotchas: weights must sum to 1.0 (validated); raises ValueError otherwise.
    The score is clamped to [0, 1] — pathological inputs (e.g. blast > 1) are
    silently saturated rather than producing out-of-range scores.
    """
    if weights is None:
        weights = load_weights()
    else:
        # Validate caller-supplied weights too — same invariant always holds.
        _validate_weights(weights)

    # Compute the weighted sum.  Each term is spelled out explicitly to make
    # the mapping to the design formula unambiguous and auditable.
    raw = (
        weights["w_test"]   * inputs.test
        + weights["w_review"] * (1.0 - inputs.severity)
        + weights["w_blast"]  * (1.0 - inputs.blast)
        + weights["w_amb"]    * (1.0 - inputs.amb)
        + weights["w_hist"]   * inputs.hist
    )

    # Clamp to [0, 1] — defensive against callers passing out-of-range inputs.
    return max(0.0, min(1.0, raw))


# ---------------------------------------------------------------------------
# Convenience: build inputs from gauntlet result + job metadata
# ---------------------------------------------------------------------------


def inputs_from_job(
    *,
    test_result: Optional[str],
    severity: float,
    blast: float,
    amb: float,
    hist: Optional[float] = None,
) -> ConfidenceInputs:
    """
    Purpose: construct a ConfidenceInputs from the raw gauntlet and risk fields
    that are available at AWAITING_APPROVAL time.
    Usage:
        inputs = inputs_from_job(
            test_result=gauntlet_result.test_result,
            severity=severity_score(gauntlet_result.review_findings),
            blast=blast_radius,
            amb=ambiguity_score,
        )
    Gotchas: hist defaults to HIST_NEUTRAL (0.5) when None — this is the
    documented behaviour before the P5 scorecard exists (design §7).
    """
    return ConfidenceInputs(
        test=score_from_test_result(test_result),
        severity=severity,
        blast=blast,
        amb=amb,
        hist=hist if hist is not None else HIST_NEUTRAL,
    )


def severity_from_findings(findings: list) -> float:
    """
    Purpose: derive a normalised severity score from a gauntlet review_findings
    list.  A P0 finding in the list produces severity=1.0 (worst); an empty list
    produces 0.0 (clean).
    Usage: sev = severity_from_findings(gauntlet_result.review_findings)
    Gotchas: only the first P0-classified finding is needed to pin severity at 1.0.
    Finding strings are expected to contain severity tags like 'P0', 'P1', 'P2'.
    When no tags are found each finding counts as P2.  This is a heuristic —
    the exact calibration does not affect the formula's correctness, only its
    numeric magnitude.
    """
    if not findings:
        return 0.0

    # P0 is the hardest finding — cap severity at 1.0 immediately.
    for f in findings:
        if "P0" in str(f):
            return 1.0

    # P1 → 0.7, P2 / unknown → 0.3.  Cap at 1.0 via clamp.
    worst = 0.0
    for f in findings:
        if "P1" in str(f):
            worst = max(worst, 0.7)
        else:
            worst = max(worst, 0.3)
    return min(1.0, worst)
