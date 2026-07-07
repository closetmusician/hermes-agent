# ABOUTME: Pure-code ambiguity rubric for P3-a task-splitter (design §3.3, REQ-02).
# ABOUTME: grade() scores one TaskNode deterministically — zero I/O, no AI in the gate.
# ABOUTME: The AI's self-reported ambiguity field is IGNORED; the rubric is the only
# ABOUTME: authoritative score (anti-gaming property). Weights are module constants,
# ABOUTME: owner-tunable without touching test logic.
"""
Ambiguity rubric — pure, deterministic, un-hallucinable.

Design authoritative source:
  docs/plans/harness/fable/p3/P3-design.md §3.3

The rubric grades exactly three failure kinds, each with a calibrated weight:

  undefined_ac  — node has no acceptance criteria (acceptance=[]).
                  Weight WEIGHT_UNDEFINED_AC = 0.45
  vague_dep     — acceptance text or title contains vague hedge tokens
                  ("or equivalent", "similar to", "something like", "TBD", "???").
                  Weight WEIGHT_VAGUE_DEP = 0.35
  missing_field — title or acceptance text says "add <noun>" with no concrete
                  field/identifier (heuristic: vague field reference).
                  Weight WEIGHT_MISSING_FIELD = 0.20

Weights sum to 1.0.  Final score is clamped to [0.0, 1.0].

The AI's self-reported ``node.ambiguity`` field is NOT read here (it is advisory
only, set for documentation purposes; the splitter overwrites it with grade()).
This is the anti-gaming property: a dishonest AI cannot lower its own ambiguity
score by emitting a low self-report.

PARK_THRESHOLD (0.5) lives in spec_review.py so it is tunable without changing
this module.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.task_graph import TaskNode

# ---------------------------------------------------------------------------
# Weights — module constants so the owner can tune without touching test logic.
# All three MUST sum to 1.0 (asserted by the weight-sum test in REQ-04).
# ---------------------------------------------------------------------------

WEIGHT_UNDEFINED_AC: float = 0.45
"""Weight applied when a node has zero acceptance criteria."""

WEIGHT_VAGUE_DEP: float = 0.35
"""
Weight applied proportionally to the fraction of acceptance/title tokens that
match the hedge-word pattern.  Full weight = all tokens are vague; 0 = none are.
"""

WEIGHT_MISSING_FIELD: float = 0.20
"""
Weight applied when the title/acceptance text suggests 'add <noun>' but names
no concrete field, column, or identifier.
"""

# ---------------------------------------------------------------------------
# Vague-hedge pattern — matches the hedge tokens the design specifies.
# Applied over lower-cased text so casing does not matter.
# Keeping as a compiled regex so the test can assert it is deterministic and
# so the definition is explicit (not hidden in a closure).
# ---------------------------------------------------------------------------

_VAGUE_TOKENS_PATTERN: re.Pattern = re.compile(
    r"or\s+equivalent|something\s+like|similar\s+to|\bTBD\b|\?\?\?",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Missing-field heuristic pattern — "add <noun> …" with NO following colon or
# parenthesis (which would indicate a field/identifier list follows).
# This is intentionally conservative: we only fire on a bare "add <noun> at end
# of phrase" (no colon, no paren, no comma with field list).
# ---------------------------------------------------------------------------

_ADD_NO_FIELD_PATTERN: re.Pattern = re.compile(
    r"\badd\s+(?:[a-z]+\s+){0,3}[a-z]+\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_HAS_FIELD_LIST_PATTERN: re.Pattern = re.compile(
    r"\badd\s+[^:]+(?::\s*\S|\([^)]+\)|\b(?:column|field|table|endpoint|route|method|param|arg)\b)",
    re.IGNORECASE,
)


def _vague_score(text: str) -> float:
    """
    Purpose: count vague-hedge matches in text as a fraction of whitespace-delimited
    tokens, clamped to [0.0, 1.0].  Returns 0.0 for empty text.
    Usage: score = _vague_score(combined_acceptance_and_title_text)
    Gotchas: single-match in a long text gives a near-zero fraction, not a binary
    flag — this is intentional: a hint among concrete criteria is mild, not fatal.
    """
    if not text:
        return 0.0
    tokens = text.split()
    if not tokens:
        return 0.0
    # Count how many hedge-matches fire in the whole text (not per-token — the
    # patterns span multiple words so a token count would undercount).
    matches = _VAGUE_TOKENS_PATTERN.findall(text)
    # Express as a fraction of tokens, with 1 match per token counted.  Clamp ≤1.
    raw_fraction = len(matches) / max(len(tokens), 1)
    return min(raw_fraction, 1.0)


def _missing_field_score(text: str) -> float:
    """
    Purpose: 1.0 if the text says 'add <noun>' with no concrete field identifier;
    0.0 otherwise.  A rough but deterministic heuristic for underspecified work items.
    Usage: score = _missing_field_score(combined_title_and_acceptance_text)
    Gotchas: false positives are possible for natural-language titles; the low
    weight (0.20) means a false positive nudges the score only mildly.
    """
    if not text:
        return 0.0
    has_add_no_field = bool(_ADD_NO_FIELD_PATTERN.search(text))
    has_field_list = bool(_HAS_FIELD_LIST_PATTERN.search(text))
    if has_add_no_field and not has_field_list:
        return 1.0
    return 0.0


def grade(node: "TaskNode") -> float:
    """
    Compute the authoritative ambiguity score for one TaskNode.

    Purpose: deterministic, pure scoring of a node's ambiguity.  Called by the
    splitter to overwrite any AI self-reported ambiguity with this score.
    The spec-review gate (spec_review.py) uses max(node.ambiguity) across the
    graph to decide park vs hold.

    Usage:
        from factory.ambiguity_rubric import grade
        score = grade(node)   # float in [0.0, 1.0]

    Gotchas:
      * DOES NOT read node.ambiguity — the AI's self-score is ignored.  This is
        the anti-gaming property: the same node always produces the same score
        regardless of what the AI reported.  To avoid confusion, the caller
        (task_splitter.split) REPLACES node.ambiguity with this return value.
      * Pure function — no I/O, no random, no AI.  Same input → same output.
      * Clamps to [0.0, 1.0] even if the weighted sum would exceed 1.0 (which
        can happen if the vague_dep fraction is large for a very short text).
    """
    # --- Component 1: undefined acceptance criteria ---
    # Weight 0.45 — the strongest signal.  A node with zero ACs is maximally
    # underspecified: the coder has no falsifiable exit condition.
    undefined_ac: float = 1.0 if len(node.acceptance) == 0 else 0.0

    # --- Component 2: vague hedge tokens in acceptance text + title ---
    # Weight 0.35.  Combine title + all acceptance strings into one text blob
    # so "or equivalent" in either the title OR an AC is captured.
    combined_text = node.title + " " + " ".join(node.acceptance)
    vague_dep: float = _vague_score(combined_text)

    # --- Component 3: missing concrete field/identifier reference ---
    # Weight 0.20.  A title like "add user" with no field list suggests the
    # coder must guess what "user" means in this context.
    missing_field: float = _missing_field_score(combined_text)

    # Weighted sum, clamped to [0.0, 1.0].
    raw = (
        WEIGHT_UNDEFINED_AC * undefined_ac
        + WEIGHT_VAGUE_DEP * vague_dep
        + WEIGHT_MISSING_FIELD * missing_field
    )
    return max(0.0, min(1.0, raw))
