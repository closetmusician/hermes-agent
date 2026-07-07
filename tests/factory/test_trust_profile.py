# ABOUTME: RED-first tests for factory/trust_profile.py (P1b-c, REQ-03).
# ABOUTME: Covers the owner profile switch (ask-for-everything vs auto-merge-
# ABOUTME: personal-non-prod), the tier ceiling cap, and merge_disposition.
# ABOUTME: Each test maps to a done-when clause in P1b-design.md §4 / REQ-03.
"""
Tests for factory.trust_profile (P1b-c, REQ-03).

Coverage:
  1. ask-for-everything profile caps effective tier to 0 (AT-PROFILE-1 held half).
  2. auto-merge-personal-non-prod profile lets a computed tier-1 through.
  3. A profile never RAISES a tier-0 to tier-1 (ceiling only, never lift).
  4. merge_disposition: returns ("held", 0) for ask-for-everything regardless of ledger.
  5. merge_disposition: returns ("auto", 1) for permissive profile + tier-1 ledger.
  6. merge_disposition: returns ("held", 0) for permissive profile + work repo.
  7. load_trust_profile reads the config from disk; default is ask-for-everything.
  8. repo_class from merge_policy trust block: defaults to 'work' (fail-safe).
  9. effective_tier = min(compute_tier, profile_ceiling) — profile never lifts.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from factory.trust_profile import (
    PROFILE_ASK_EVERYTHING,
    PROFILE_AUTO_MERGE_PERSONAL,
    TrustProfile,
    effective_tier,
    load_trust_profile,
    merge_disposition,
    profile_ceiling,
)
from factory.trust_policy import TrustPolicy

REPO_ROOT = Path(__file__).resolve().parents[2]

# A pinned test policy.
_TEST_POLICY = TrustPolicy(
    consecutive_merged_clean=10,
    max_reverts=0,
    min_window_days=30,
    min_confidence=0.0,
    work_repo_hard_tier0=True,
)

DAY_MS = 24 * 60 * 60 * 1000


def _clean_rows(n: int, *, repo: str = "personal-repo", task_type: str = "feature",
                base_ts: int = 0) -> list:
    """Build n merged_clean rows, 5 days apart."""
    return [
        {
            "job_id": f"job-{i}",
            "repo": repo,
            "task_type": task_type,
            "outcome": "merged_clean",
            "repo_class": "personal",
            "outcome_ts": base_ts + i * 5 * DAY_MS,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# profile_ceiling
# ---------------------------------------------------------------------------


def test_ask_everything_profile_ceiling_is_zero():
    """ask-for-everything profile has tier ceiling = 0."""
    assert profile_ceiling(PROFILE_ASK_EVERYTHING) == 0


def test_auto_merge_personal_profile_ceiling_is_one():
    """auto-merge-personal-non-prod profile has tier ceiling = 1."""
    assert profile_ceiling(PROFILE_AUTO_MERGE_PERSONAL) == 1


# ---------------------------------------------------------------------------
# effective_tier
# ---------------------------------------------------------------------------


def test_effective_tier_caps_to_zero_under_ask_everything():
    """effective_tier = min(computed, ceiling): ask-for-everything caps tier-1 → 0."""
    assert effective_tier(computed=1, ceiling=0) == 0


def test_effective_tier_passes_through_under_permissive_profile():
    """effective_tier passes tier-1 through when ceiling = 1."""
    assert effective_tier(computed=1, ceiling=1) == 1


def test_effective_tier_never_lifts_tier0():
    """A profile ceiling of 1 cannot lift a computed tier-0 to tier-1."""
    assert effective_tier(computed=0, ceiling=1) == 0


def test_effective_tier_capped_by_ceiling():
    """effective_tier always returns min(computed, ceiling)."""
    assert effective_tier(computed=2, ceiling=1) == 1
    assert effective_tier(computed=0, ceiling=0) == 0


# ---------------------------------------------------------------------------
# merge_disposition (AT-PROFILE-1, AT-GRAD-1)
# ---------------------------------------------------------------------------


def test_merge_disposition_ask_everything_always_held():
    """AT-PROFILE-1 (held half): ask-for-everything returns ('held', 0) even with tier-1 ledger."""
    rows = _clean_rows(10, base_ts=0)
    now = 46 * DAY_MS

    result = merge_disposition(
        repo="personal-repo",
        task_type="feature",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        profile=PROFILE_ASK_EVERYTHING,
        repo_class="personal",
    )
    assert result == ("held", 0)


def test_merge_disposition_permissive_profile_tier1_auto():
    """AT-PROFILE-1 (auto half) + AT-GRAD-1: permissive profile + tier-1 record → ('auto', 1)."""
    rows = _clean_rows(10, base_ts=0)
    now = 46 * DAY_MS

    result = merge_disposition(
        repo="personal-repo",
        task_type="feature",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        profile=PROFILE_AUTO_MERGE_PERSONAL,
        repo_class="personal",
    )
    assert result == ("auto", 1)


def test_merge_disposition_permissive_profile_work_repo_held():
    """Permissive profile + work repo → ('held', 0): hard gate wins over profile."""
    rows = _clean_rows(20, repo="work-repo", base_ts=0)
    now = 100 * DAY_MS

    result = merge_disposition(
        repo="work-repo",
        task_type="feature",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        profile=PROFILE_AUTO_MERGE_PERSONAL,
        repo_class="work",
    )
    assert result == ("held", 0)


def test_merge_disposition_short_streak_held():
    """Only 9 clean rows → ('held', 0) regardless of profile."""
    rows = _clean_rows(9, base_ts=0)
    now = 50 * DAY_MS

    result = merge_disposition(
        repo="personal-repo",
        task_type="feature",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        profile=PROFILE_AUTO_MERGE_PERSONAL,
        repo_class="personal",
    )
    assert result == ("held", 0)


def test_merge_disposition_profile_does_not_lift_tier0():
    """A profile can only lower a tier, never raise tier-0 to tier-1."""
    # Empty rows → compute_tier = 0; permissive ceiling = 1; result must still be 0.
    result = merge_disposition(
        repo="personal-repo",
        task_type="feature",
        rows=[],
        now=100 * DAY_MS,
        policy=_TEST_POLICY,
        profile=PROFILE_AUTO_MERGE_PERSONAL,
        repo_class="personal",
    )
    assert result == ("held", 0)


# ---------------------------------------------------------------------------
# TrustProfile dataclass + load_trust_profile
# ---------------------------------------------------------------------------


def test_trust_profile_constants_are_valid():
    """The two valid profile strings are non-empty and distinct."""
    assert PROFILE_ASK_EVERYTHING != PROFILE_AUTO_MERGE_PERSONAL
    assert isinstance(PROFILE_ASK_EVERYTHING, str)
    assert isinstance(PROFILE_AUTO_MERGE_PERSONAL, str)


def test_load_trust_profile_default_is_ask_everything(tmp_path):
    """load_trust_profile returns ask-for-everything when no config file exists."""
    result = load_trust_profile(tmp_path / "trust-profile.txt")
    assert result == PROFILE_ASK_EVERYTHING


def test_load_trust_profile_reads_from_file(tmp_path):
    """load_trust_profile reads the profile from the config file."""
    config = tmp_path / "trust-profile.txt"
    config.write_text(PROFILE_AUTO_MERGE_PERSONAL + "\n")
    result = load_trust_profile(config)
    assert result == PROFILE_AUTO_MERGE_PERSONAL


def test_load_trust_profile_unknown_value_returns_ask_everything(tmp_path):
    """An unrecognized profile value falls back to ask-for-everything (fail-safe)."""
    config = tmp_path / "trust-profile.txt"
    config.write_text("yolo-merge-everything\n")
    result = load_trust_profile(config)
    assert result == PROFILE_ASK_EVERYTHING


def test_trust_profile_dataclass():
    """TrustProfile wraps a name and ceiling cleanly."""
    p = TrustProfile(name=PROFILE_ASK_EVERYTHING, ceiling=0)
    assert p.name == PROFILE_ASK_EVERYTHING
    assert p.ceiling == 0
