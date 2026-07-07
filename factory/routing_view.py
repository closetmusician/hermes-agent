# ABOUTME: Routing view for the Fable factory (P5-a, REQ-02). Regenerates the
# ABOUTME: router's per-tier ordering FROM scorecard data — best-and-cheapest first
# ABOUTME: per task_type. The view ONLY REORDERS the catalog-confirmed set; it CANNOT
# ABOUTME: add a model not already in capabilities.json (residency-safe invariant).
# ABOUTME: model_router.admissible_ladder reads ranked_first_party/ranked_openrouter.
"""
Routing view — scorecard-driven ordering for the model router.

Design source: docs/plans/harness/fable/p5/P5-design.md §REQ-01 (routing_view.py),
§3.3 (the router-reads-scorecard design), §3.4 (regenerate-from-data flow).

The view regeneration contract (three invariants, all RED-tested):

  1. ORDER ONLY — ranked_first_party / ranked_openrouter intersect the incoming
     catalog entries with scorecard data before ranking.  A scorecard row for a
     model not in the catalog is SILENTLY DROPPED — the view never introduces a
     phantom entry that the admissible_ladder filter must later catch.  RED test SC-4
     asserts a since-removed model cannot reappear in any ranked output.

  2. COLD START — when a model has fewer than MIN_SAMPLES samples the scorecard has
     too little signal.  The hand-written _FIRST_PARTY_LADDER / _OPENROUTER_CANDIDATES
     order from model_router is used as the prior (deterministic, never crashes).
     RED test SC-3 asserts this.

  3. RESIDENCY UNTOUCHED — the view output feeds into admissible_ladder which still
     applies _rung_admissible AFTER ordering.  A false-policy repo that happens to
     get ranked openrouter-first still ends up with ZERO openrouter rungs once the
     residency pre-filter runs.  This module NEVER calls or bypasses assert_openrouter_
     allowed — residency is purely model_router's domain.

Scoring formula (score = merged_clean_rate - α * mean_cost_normalized - β * mean_findings_normalized):
  α = COST_WEIGHT, β = FINDINGS_WEIGHT.  Higher score is better.
  Normalization is per-group (within first-party / within openrouter) so the two tiers
  are ranked independently — an openrouter model with a high score does not displace a
  first-party model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from factory.scorecard import ScoreRow, Scorecard

# Minimum samples before scorecard data is trusted over the hand-written default.
MIN_SAMPLES: int = 5

# Scoring weights: merged_clean_rate is primary; cost and findings are secondary
# penalties.  Both are normalized within the ranking group (0..1 each).
COST_WEIGHT: float = 0.2
FINDINGS_WEIGHT: float = 0.1

# Fallback orderings (the hand-written defaults are the cold-start prior).
# These are imported at call time from model_router to avoid a circular import at
# module load — routing_view is imported by model_router.  Use _default_fp_order()
# and _default_or_order() helpers to access them.
_DEFAULT_FP_ORDER: Optional[Tuple[Tuple[str, str], ...]] = None
_DEFAULT_OR_ORDER: Optional[Tuple[str, ...]] = None


def _default_fp_order() -> Tuple[Tuple[str, str], ...]:
    """
    Return the hand-written first-party ladder order as the cold-start prior.

    Purpose: the fallback when scorecard data is insufficient — the existing
    (worker, model) tuples from _FIRST_PARTY_LADDER in model_router.
    Usage: order = _default_fp_order()
    Gotchas: imported lazily to avoid a circular import at module level.
    """
    from factory.model_router import _FIRST_PARTY_LADDER  # lazy import; avoid circular

    return _FIRST_PARTY_LADDER


def _default_or_order() -> Tuple[str, ...]:
    """
    Return the hand-written OpenRouter candidate order as the cold-start prior.

    Purpose: the fallback when scorecard data is insufficient for openrouter models.
    Usage: order = _default_or_order()
    Gotchas: imported lazily to avoid a circular import at module level.
    """
    from factory.model_router import _OPENROUTER_CANDIDATES  # lazy import; avoid circular

    return _OPENROUTER_CANDIDATES


def _score(row: ScoreRow, *, max_cost: float, max_findings: float) -> float:
    """
    Compute a ranking score for a ScoreRow (higher is better).

    Purpose: combined quality score = merged_clean_rate minus weighted cost and
    findings penalties, each normalized within the group so no absolute-scale bias.
    Usage: s = _score(row, max_cost=group_max, max_findings=group_max)
    Gotchas: max values of 0 (all rows are free or have zero findings) produce
    normalized penalty of 0 (no penalty), not a division error.
    """
    cost_norm = (row.mean_cost_usd / max_cost) if max_cost > 0 else 0.0
    find_norm = (row.mean_findings / max_findings) if max_findings > 0 else 0.0
    return row.merged_clean_rate - COST_WEIGHT * cost_norm - FINDINGS_WEIGHT * find_norm


def _rank_within_group(
    candidates: Sequence[Tuple[str, str]],
    score_map: Dict[Tuple[str, str], ScoreRow],
) -> List[Tuple[str, str]]:
    """
    Sort a list of (worker, model) pairs by scorecard score within the group.

    Purpose: stable sort of a candidate list using scorecard data, with cold-start
    fallback.  Only candidates that appear in score_map with n >= MIN_SAMPLES are
    scored; the rest keep their original order (cold-start prior) and are appended
    after the scored entries.
    Usage: ranked = _rank_within_group(fp_candidates, score_map)
    Gotchas:
      * Invariant 1 (ORDER ONLY): `candidates` is already the catalog-intersection —
        no model absent from the catalog can appear here.
      * Invariant 2 (COLD START): cold models (n < MIN_SAMPLES) are appended last in
        their original order; deterministic, never raises.
    """
    hot: List[Tuple[Tuple[str, str], float]] = []
    cold: List[Tuple[str, str]] = []

    # Normalization denominator computed over the scored (hot) subset.
    hot_rows = [
        score_map[c]
        for c in candidates
        if c in score_map and score_map[c].n >= MIN_SAMPLES
    ]
    max_cost = max((r.mean_cost_usd for r in hot_rows), default=0.0)
    max_findings = max((r.mean_findings for r in hot_rows), default=0.0)

    for cand in candidates:
        row = score_map.get(cand)
        if row is not None and row.n >= MIN_SAMPLES:
            hot.append((cand, _score(row, max_cost=max_cost, max_findings=max_findings)))
        else:
            cold.append(cand)

    hot.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in hot] + cold


def ranked_first_party(
    catalog: Optional[Dict[str, Any]],
    scorecard: Scorecard,
    task_type: Optional[str] = None,
) -> Tuple[Tuple[str, str], ...]:
    """
    Scorecard-ranked ordering of first-party (worker, model) pairs.

    Purpose: replaces the hand-written _FIRST_PARTY_LADDER order in admissible_ladder.
    Intersects the default FP order with the catalog (invariant 1: ORDER ONLY), then
    ranks by scorecard score for the given task_type (cold-start fallback to original
    order when n < MIN_SAMPLES).
    Usage: fp = ranked_first_party(catalog, scorecard, task_type="bugfix")
    Gotchas:
      * catalog may be None (e.g., first boot) — returns the default order unchanged.
      * task_type=None uses the global (all-task-type) scorecard rollup for ranking.
      * A model that was in _FIRST_PARTY_LADDER but has since been removed from the
        catalog is silently dropped — it cannot reappear via a stale scorecard row.
    """
    default = list(_default_fp_order())

    # Catalog intersection: keep only entries the catalog lists as present.
    if catalog is not None:
        cli_info = catalog.get("cli", {})
        default = [
            (worker, model)
            for (worker, model) in default
            if _fp_present(worker, cli_info)
        ]

    # Build scorecard map: (worker, model) → ScoreRow.
    score_rows = scorecard.rollup(task_type=task_type) if task_type else scorecard.rollup()
    score_map: Dict[Tuple[str, str], ScoreRow] = {
        (r.worker, r.model): r for r in score_rows
    }

    ranked = _rank_within_group(default, score_map)
    return tuple(ranked)


def ranked_openrouter(
    catalog: Optional[Dict[str, Any]],
    scorecard: Scorecard,
    task_type: Optional[str] = None,
) -> Tuple[str, ...]:
    """
    Scorecard-ranked ordering of OpenRouter candidate model names.

    Purpose: replaces the hand-written _OPENROUTER_CANDIDATES order.  Intersects
    the default OR candidates with catalog-confirmed (live_catalog AND ping_ok)
    entries, then ranks by scorecard score (invariants 1+2+3 all apply).
    Usage: or_order = ranked_openrouter(catalog, scorecard, task_type="feature")
    Gotchas:
      * Only models that pass the live_catalog+ping_ok predicate (already in the
        catalog) are considered — a model absent from catalog.openrouter.models or
        failing the predicate is silently excluded (phantom guard preserved).
      * Residency is NOT checked here — that remains admissible_ladder's job.
    """
    default_models = list(_default_or_order())

    # Catalog intersection: keep only probe-confirmed OR models.
    if catalog is not None:
        confirmed = _confirmed_or_models(catalog)
        default_models = [m for m in default_models if m in confirmed]

    # Build (worker, model) pairs for the ranker; worker is always "openrouter".
    candidates = [("openrouter", m) for m in default_models]

    score_rows = scorecard.rollup(task_type=task_type) if task_type else scorecard.rollup()
    score_map: Dict[Tuple[str, str], ScoreRow] = {
        (r.worker, r.model): r for r in score_rows
    }

    ranked = _rank_within_group(candidates, score_map)
    return tuple(model for (_, model) in ranked)


def task_type_table(
    catalog: Optional[Dict[str, Any]],
    scorecard: Scorecard,
) -> Dict[str, Tuple[Tuple[str, str], ...]]:
    """
    Per-task-type routing table: task_type → ranked (worker, model) order.

    Purpose: the learned routing table the plan calls "learned over time." For each
    known task_type, returns ranked_first_party (and appended ranked_openrouter entries)
    so select_tier can consult this before falling back to the generic ladder.
    Usage: table = task_type_table(catalog, scorecard); entry = table.get("bugfix")
    Gotchas:
      * Returns an empty dict when no samples exist (cold start).
      * OpenRouter entries are appended AFTER all first-party entries per task_type —
        the per-task table does not change the relative FP/OR ordering policy, only
        re-ranks within each group and then concatenates.
    """
    # Collect known task types from all scorecard samples.
    all_rows = scorecard.rollup()
    task_types = {r.task_type for r in all_rows}

    table: Dict[str, Tuple[Tuple[str, str], ...]] = {}
    for tt in task_types:
        fp = ranked_first_party(catalog, scorecard, task_type=tt)
        or_models = ranked_openrouter(catalog, scorecard, task_type=tt)
        or_tuples = tuple(("openrouter", m) for m in or_models)
        table[tt] = fp + or_tuples
    return table


# ---------------------------------------------------------------------------
# Catalog helpers (internal — not exported)
# ---------------------------------------------------------------------------


def _fp_present(worker: str, cli_info: Dict[str, Any]) -> bool:
    """
    Return True if the first-party CLI worker is listed as present in the catalog.

    Purpose: the catalog-intersection step for first-party entries — a worker not
    probed as present (cli.<worker>.present:true) is excluded from the ranked output.
    Usage: ok = _fp_present("claude", catalog["cli"])
    Gotchas: absent worker key → False (fail-closed toward exclusion).
    """
    info = cli_info.get(worker, {})
    return bool(info.get("present", False))


def _confirmed_or_models(catalog: Dict[str, Any]) -> frozenset:
    """
    Return the set of probe-confirmed (live_catalog AND ping_ok) OR model names.

    Purpose: the phantom guard for the routing view — only models that passed the
    capability probe enter the ranked output (mirrors _probe_confirmed_openrouter_models
    in model_router, but returns a set for O(1) membership checks).
    Usage: confirmed = _confirmed_or_models(catalog)
    Gotchas: an empty catalog or missing keys returns an empty frozenset safely.
    """
    models_info = catalog.get("openrouter", {}).get("models", {})
    return frozenset(
        name
        for name, flags in models_info.items()
        if flags and flags.get("live_catalog") and flags.get("ping_ok")
    )
