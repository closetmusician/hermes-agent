# ABOUTME: RED-first test suite for factory/model_router.py (P4-b, the crown task).
# ABOUTME: The router is the ONE place a worker tier is chosen. These tests pin the
# ABOUTME: residency pre-filter (every rung), the 429 step-down ladder, and the
# ABOUTME: WAIT_FOR_CAPACITY sentinel that a false-policy job hits instead of ever
# ABOUTME: reaching OpenRouter. Real merge_policy + real residency_guard, no mocks.
"""
Tests for factory/model_router.py — router + 429 ladder + residency pre-filter.

RED → GREEN protocol: this file is committed before model_router.py exists; the
tests drive the implementation.

The crown invariant under test (design §4 R4 + §13): an allow_openrouter:false OR
unknown-policy job's admissible ladder contains ZERO third-party (openrouter) rungs
at ANY depth. On a 429 that exhausts the first-party tiers, next_rung returns the
WAIT_FOR_CAPACITY sentinel — never an OpenRouter rung. Anti-weakening tests FAIL if
the pre-filter is removed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.merge_policy import MergePolicy, default_policy


# A synthetic catalog where the openrouter tiers ARE probe-confirmed (live), so the
# residency pre-filter — not an empty catalog — is what removes them for a false repo.
# This is essential: capabilities.json ships all-false today, which would make the
# ladder first-party-only for the WRONG reason (no live OR rungs), hiding a broken
# pre-filter. We inject a live catalog so the pre-filter is genuinely exercised.
_LIVE_CATALOG = {
    "openrouter": {
        "models": {
            "zhipuai/glm-5": {"live_catalog": True, "ping_ok": True},
            "moonshot/kimi-k2": {"live_catalog": True, "ping_ok": True},
            "deepseek/deepseek-r1": {"live_catalog": True, "ping_ok": True},
            # A probe-FAILED model: live_catalog true but ping_ok false → excluded on
            # the predicate, NOT on absence (F12: GLM-5.2 is IN the file, fails ping).
            "zhipuai/GLM-5.2": {"live_catalog": True, "ping_ok": False},
        }
    }
}


def _true_policy(repo: str) -> MergePolicy:
    """A repo that explicitly permits third-party (openrouter) egress."""
    return MergePolicy(repo=repo, allow_openrouter=True)


def _false_policy(repo: str) -> MergePolicy:
    """A work repo: openrouter OFF (the default, restated for clarity)."""
    return MergePolicy(repo=repo, allow_openrouter=False)


# ---------------------------------------------------------------------------
# REQ-01 — residency pre-filter on EVERY rung; WAIT_FOR_CAPACITY, never OpenRouter
# ---------------------------------------------------------------------------


def test_true_repo_ladder_includes_openrouter_rungs() -> None:
    """A true-policy repo's ladder DOES contain the probe-confirmed OR rungs.

    This is the control: it proves the OR rungs are genuinely present in the
    catalog, so their ABSENCE for a false repo (next test) is the pre-filter's
    doing, not an empty catalog artifact.
    """
    from factory.model_router import admissible_ladder

    ladder = admissible_ladder("/repo/acme", _true_policy("/repo/acme"), _LIVE_CATALOG)
    workers = {r.worker for r in ladder}
    assert "openrouter" in workers, "true repo must be able to overflow to OpenRouter"
    # The probe-failed GLM-5.2 is never a rung even for a true repo (phantom guard).
    models = {r.model for r in ladder}
    assert "zhipuai/GLM-5.2" not in models


def test_false_repo_ladder_has_no_openrouter_rung_at_any_depth() -> None:
    """CROWN: a false-policy repo's ladder contains ZERO openrouter rungs, any depth."""
    from factory.model_router import admissible_ladder

    ladder = admissible_ladder("/repo/work", _false_policy("/repo/work"), _LIVE_CATALOG)
    workers = [r.worker for r in ladder]
    assert "openrouter" not in workers, f"false repo leaked an OR rung: {workers}"
    # Only first-party rungs remain.
    assert set(workers) <= {"claude", "codex"}


def test_unknown_policy_ladder_fails_closed_no_openrouter() -> None:
    """CROWN: policy=None (unknown residency) is treated as false → no OR rungs."""
    from factory.model_router import admissible_ladder

    ladder = admissible_ladder("/repo/mystery", None, _LIVE_CATALOG)
    assert "openrouter" not in {r.worker for r in ladder}


def test_select_tier_picks_top_first_party_rung_for_false_repo() -> None:
    """select_tier on a false repo returns the top-of-ladder FIRST-PARTY rung."""
    from factory.model_router import select_tier

    rung = select_tier("/repo/work", "feature", _false_policy("/repo/work"), _LIVE_CATALOG)
    assert rung.worker in ("claude", "codex")
    assert rung.worker != "openrouter"


def test_429_failover_steps_down_one_probe_confirmed_rung() -> None:
    """A 429 on tier-0 → next_rung returns the next probe-confirmed rung below it."""
    from factory.model_router import select_tier, next_rung, WAIT_FOR_CAPACITY

    repo, policy = "/repo/acme", _true_policy("/repo/acme")
    top = select_tier(repo, "feature", policy, _LIVE_CATALOG)
    step = next_rung(top, repo, policy, _LIVE_CATALOG)
    assert step is not WAIT_FOR_CAPACITY
    assert step.tier > top.tier, "next_rung must descend strictly below current"


def test_false_repo_429_exhaustion_waits_never_openrouter() -> None:
    """CROWN: a false repo that exhausts every first-party rung gets WAIT_FOR_CAPACITY.

    Walk the whole ladder via next_rung; assert we NEVER get an openrouter rung and
    that the terminal result is the WAIT_FOR_CAPACITY sentinel (not a hard error,
    not an OR rung).
    """
    from factory.model_router import select_tier, next_rung, WAIT_FOR_CAPACITY, RungSpec

    repo, policy = "/repo/work", _false_policy("/repo/work")
    cur = select_tier(repo, "feature", policy, _LIVE_CATALOG)
    seen_workers = []
    # Bounded walk — the ladder is finite; guard against an infinite loop bug.
    for _ in range(20):
        assert isinstance(cur, RungSpec)
        assert cur.worker != "openrouter"
        seen_workers.append(cur.worker)
        cur = next_rung(cur, repo, policy, _LIVE_CATALOG)
        if cur is WAIT_FOR_CAPACITY:
            break
    assert cur is WAIT_FOR_CAPACITY, "first-party exhaustion must yield WAIT, not OR"
    assert "openrouter" not in seen_workers


def test_next_rung_refilters_every_step(tmp_path: Path) -> None:
    """F1 anti-weakening: next_rung re-derives the admissible ladder EVERY call.

    We start next_rung from a (hypothetical) openrouter rung on a FALSE repo. A
    correct implementation re-filters from (repo, policy, catalog) on every call and
    therefore returns WAIT_FOR_CAPACITY — it must NOT trust the passed-in rung's tier
    to index a stale cached admissible list that still contains OR rungs.
    """
    from factory.model_router import next_rung, WAIT_FOR_CAPACITY, RungSpec

    repo, policy = "/repo/work", _false_policy("/repo/work")
    # An OR rung handed in as 'current' (as if a stale checkpoint replayed it).
    injected = RungSpec(worker="openrouter", model="zhipuai/glm-5", tier=2)
    result = next_rung(injected, repo, policy, _LIVE_CATALOG)
    # Re-filtering from the false policy leaves only first-party rungs, all ABOVE
    # tier 2 — so there is no admissible rung strictly below → WAIT.
    assert result is WAIT_FOR_CAPACITY


def test_phantom_model_never_enters_ladder() -> None:
    """F12: 'phantom' = fails live_catalog && ping_ok, NOT name-absence.

    Two cases: (a) GLM-5.2 is IN the catalog but ping_ok:false → excluded on the
    predicate; (b) a genuinely-absent fabricated name → also never a rung.
    """
    from factory.model_router import admissible_ladder

    ladder = admissible_ladder("/repo/acme", _true_policy("/repo/acme"), _LIVE_CATALOG)
    models = {r.model for r in ladder}
    assert "zhipuai/GLM-5.2" not in models  # in-file but ping-failed
    assert "acme/totally-made-up-9000" not in models  # genuinely absent


# ---------------------------------------------------------------------------
# REQ-01 / F3 — missing / unparseable policy fails closed to WAIT
# ---------------------------------------------------------------------------


def test_resolve_policy_missing_file_fails_closed(tmp_path: Path) -> None:
    """A repo with no merge-policy.md → default_policy (openrouter OFF), never a raise."""
    from factory.model_router import resolve_policy

    repo = str(tmp_path / "no-policy-repo")
    Path(repo).mkdir()
    policy = resolve_policy(repo)
    assert policy.allow_openrouter is False


def test_missing_policy_fails_closed_to_wait(tmp_path: Path) -> None:
    """F3: a missing policy yields a first-party-only ladder → WAIT on exhaustion,
    NEVER an OpenRouter rung."""
    from factory.model_router import resolve_policy, admissible_ladder

    repo = str(tmp_path / "bare-repo")
    Path(repo).mkdir()
    policy = resolve_policy(repo)
    ladder = admissible_ladder(repo, policy, _LIVE_CATALOG)
    assert "openrouter" not in {r.worker for r in ladder}


def test_unparseable_policy_fails_closed(tmp_path: Path) -> None:
    """A malformed merge-policy.md → default_policy (fail-closed), not a leak."""
    from factory.model_router import resolve_policy

    repo = tmp_path / "bad-repo"
    (repo / "docs" / "factory").mkdir(parents=True)
    (repo / "docs" / "factory" / "merge-policy.md").write_text("::: not yaml :::")
    policy = resolve_policy(str(repo))
    assert policy.allow_openrouter is False
