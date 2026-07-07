# ABOUTME: RED-first tests for factory/trust_policy.py (P1b-b, REQ-02).
# ABOUTME: Covers compute_tier pure function with all three hard gates, the
# ABOUTME: trust-policy.md loader, ring-membership assertion, and injected-time
# ABOUTME: graduation thresholds. Each test maps to a done-when clause in
# ABOUTME: P1b-design.md §3 / REQ-02.
"""
Tests for factory.trust_policy (P1b-b, REQ-02).

Coverage:
  1. compute_tier: 10 clean + 30d + personal → tier-1 (graduate).
  2. compute_tier: 9 clean → tier-0 (streak short).
  3. compute_tier: a reverted row resets streak → tier-0 (demotion).
  4. compute_tier: a rejected row resets streak → tier-0 (demotion).
  5. HARD GATE 1: work repo → tier-0 regardless of 20 clean rows over 90 days.
  6. HARD GATE 2: never-graduates capability → tier-0 regardless of record.
  7. HARD GATE 3: task_type='other' or unknown → tier-0 regardless of record.
  8. 30-day window: 10 clean but only 29 days → tier-0.
  9. load_trust_policy parses the real docs/factory/trust-policy.md.
 10. AT-RING-1: trust-policy paths are in RING_PATHS (ring regression guard).
 11. Anti-weakening: removing hard gates makes the work-repo test FAIL (documented).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from factory.trust_policy import (
    TrustPolicy,
    compute_tier,
    load_trust_policy,
)
from factory.immutable_ring import RING_PATHS

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO_ROOT / "docs" / "factory" / "trust-policy.md"

# A pinned test policy so tests are independent of the on-disk file's thresholds.
_TEST_POLICY = TrustPolicy(
    consecutive_merged_clean=10,
    max_reverts=0,
    min_window_days=30,
    min_confidence=0.0,
    work_repo_hard_tier0=True,
)

DAY_MS = 24 * 60 * 60 * 1000


def _rows(n: int, *, outcome: str = "merged_clean", base_ts: int = 0) -> list:
    """Build n ledger-row dicts, evenly spaced 5 days apart from base_ts."""
    return [
        {
            "job_id": f"job-{i}",
            "repo": "personal-repo",
            "task_type": "feature",
            "outcome": outcome,
            "repo_class": "personal",
            "outcome_ts": base_ts + i * 5 * DAY_MS,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Graduation happy path
# ---------------------------------------------------------------------------


def test_ten_clean_thirty_days_graduates_to_tier1():
    """10 consecutive merged_clean spanning ≥30 days → tier-1."""
    base_ts = 0
    rows = _rows(10, base_ts=base_ts)
    # rows[0].outcome_ts = 0; rows[9].outcome_ts = 9*5 days = 45 days.
    now = base_ts + 46 * DAY_MS  # well past 30 days from the first
    result = compute_tier(
        repo="personal-repo",
        task_type="feature",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        repo_class="personal",
    )
    assert result == 1


def test_nine_clean_stays_tier0():
    """9 consecutive merged_clean → tier-0 (streak too short)."""
    base_ts = 0
    rows = _rows(9, base_ts=base_ts)
    now = base_ts + 50 * DAY_MS
    result = compute_tier(
        repo="personal-repo",
        task_type="feature",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        repo_class="personal",
    )
    assert result == 0


# ---------------------------------------------------------------------------
# Demotion
# ---------------------------------------------------------------------------


def test_reverted_row_resets_streak_to_tier0():
    """A reverted outcome in the trailing window resets the streak → tier-0."""
    base_ts = 0
    rows = _rows(9, base_ts=base_ts)
    # Append a reverted row after the 9 clean ones.
    rows.append({
        "job_id": "job-revert",
        "repo": "personal-repo",
        "task_type": "feature",
        "outcome": "reverted",
        "repo_class": "personal",
        "outcome_ts": base_ts + 10 * 5 * DAY_MS,
    })
    now = base_ts + 60 * DAY_MS
    result = compute_tier(
        repo="personal-repo",
        task_type="feature",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        repo_class="personal",
    )
    assert result == 0


def test_rejected_row_resets_streak_to_tier0():
    """A rejected outcome in the trailing window resets the streak → tier-0."""
    base_ts = 0
    rows = _rows(9, base_ts=base_ts)
    rows.append({
        "job_id": "job-reject",
        "repo": "personal-repo",
        "task_type": "feature",
        "outcome": "rejected",
        "repo_class": "personal",
        "outcome_ts": base_ts + 10 * 5 * DAY_MS,
    })
    now = base_ts + 60 * DAY_MS
    result = compute_tier(
        repo="personal-repo",
        task_type="feature",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        repo_class="personal",
    )
    assert result == 0


def test_merged_with_fix_breaks_streak():
    """A merged_with_fix outcome breaks the consecutive-clean streak → tier-0."""
    base_ts = 0
    # 5 clean, then merged_with_fix, then 5 more clean — streak resets.
    rows = _rows(5, base_ts=base_ts)
    rows.append({
        "job_id": "job-fix",
        "repo": "personal-repo",
        "task_type": "feature",
        "outcome": "merged_with_fix",
        "repo_class": "personal",
        "outcome_ts": base_ts + 6 * 5 * DAY_MS,
    })
    rows += [
        {
            "job_id": f"job-post-{i}",
            "repo": "personal-repo",
            "task_type": "feature",
            "outcome": "merged_clean",
            "repo_class": "personal",
            "outcome_ts": base_ts + (7 + i) * 5 * DAY_MS,
        }
        for i in range(5)
    ]
    now = base_ts + 70 * DAY_MS
    result = compute_tier(
        repo="personal-repo",
        task_type="feature",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        repo_class="personal",
    )
    assert result == 0


# ---------------------------------------------------------------------------
# HARD GATE 1: work repos
# ---------------------------------------------------------------------------


def test_work_repo_is_tier0_regardless_of_record():
    """HARD GATE 1: work repo stays tier-0 even with 20 clean rows over 90 days.

    Anti-weakening assertion: if this gate were removed, this test would FAIL.
    This is the AT-WORK-1 test from the design §5.2.
    """
    base_ts = 0
    rows = [
        {
            "job_id": f"job-{i}",
            "repo": "diligent-work-repo",
            "task_type": "feature",
            "outcome": "merged_clean",
            "repo_class": "work",
            "outcome_ts": base_ts + i * 4 * DAY_MS,
        }
        for i in range(20)
    ]
    now = base_ts + 91 * DAY_MS
    result = compute_tier(
        repo="diligent-work-repo",
        task_type="feature",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        repo_class="work",
    )
    assert result == 0, (
        "Work repo must be tier-0 regardless of history. "
        "If this fails, the work-repo hard gate has been removed — "
        "this is an anti-weakening test (AT-WORK-1)."
    )


def test_diligent_org_prefix_is_tier0():
    """A repo with a Diligent org prefix is treated as work-class → tier-0."""
    base_ts = 0
    rows = [
        {
            "job_id": f"job-{i}",
            "repo": "diligent-internal/platform",
            "task_type": "feature",
            "outcome": "merged_clean",
            "repo_class": "personal",  # even if mislabeled personal
            "outcome_ts": base_ts + i * 5 * DAY_MS,
        }
        for i in range(10)
    ]
    now = base_ts + 60 * DAY_MS
    result = compute_tier(
        repo="diligent-internal/platform",
        task_type="feature",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        repo_class="personal",  # mislabeled — the Diligent prefix hard gate catches it
    )
    assert result == 0


# ---------------------------------------------------------------------------
# HARD GATE 2: never-graduates capability
# ---------------------------------------------------------------------------


def test_never_graduates_capability_is_tier0(tmp_path, monkeypatch):
    """HARD GATE 2: a capability on the never-graduates list → tier-0 regardless of record.

    Anti-weakening: if this gate is removed, this test fails.
    """
    # Patch the never-graduates reader to return a known set.
    from factory import trust_policy as _tp
    monkeypatch.setattr(_tp, "_load_never_graduates_capabilities",
                        lambda: frozenset({"deploy", "self_approve"}))

    base_ts = 0
    rows = _rows(20, base_ts=base_ts)
    now = base_ts + 100 * DAY_MS
    result = compute_tier(
        repo="personal-repo",
        task_type="feature",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        repo_class="personal",
        capability="deploy",  # on the never-graduates list
    )
    assert result == 0, (
        "Never-graduates capability must be tier-0 regardless of history. "
        "If this fails, the never-graduates hard gate has been removed — "
        "this is an anti-weakening test."
    )


# ---------------------------------------------------------------------------
# HARD GATE 3: unknown / 'other' task_type
# ---------------------------------------------------------------------------


def test_other_task_type_is_tier0():
    """HARD GATE 3: task_type='other' → tier-0 regardless of record (AT-OTHER-1).

    Anti-weakening: if this gate is removed, this test fails.
    """
    base_ts = 0
    rows = [
        {
            "job_id": f"job-{i}",
            "repo": "personal-repo",
            "task_type": "other",
            "outcome": "merged_clean",
            "repo_class": "personal",
            "outcome_ts": base_ts + i * 5 * DAY_MS,
        }
        for i in range(20)
    ]
    now = base_ts + 100 * DAY_MS
    result = compute_tier(
        repo="personal-repo",
        task_type="other",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        repo_class="personal",
    )
    assert result == 0, (
        "task_type='other' must be tier-0 regardless of history. "
        "If this fails, the unknown-type hard gate has been removed — "
        "this is an anti-weakening test (AT-OTHER-1)."
    )


def test_non_graduatable_task_type_is_tier0():
    """An unrecognized task_type string (not in GRADUATABLE_TASK_TYPES) → tier-0."""
    base_ts = 0
    rows = [
        {
            "job_id": f"job-{i}",
            "repo": "personal-repo",
            "task_type": "deploy",
            "outcome": "merged_clean",
            "repo_class": "personal",
            "outcome_ts": base_ts + i * 5 * DAY_MS,
        }
        for i in range(20)
    ]
    now = base_ts + 100 * DAY_MS
    result = compute_tier(
        repo="personal-repo",
        task_type="deploy",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        repo_class="personal",
    )
    assert result == 0


# ---------------------------------------------------------------------------
# 30-day window
# ---------------------------------------------------------------------------


def test_ten_clean_but_only_29_days_is_tier0():
    """10 consecutive clean but spanning only 29 days → tier-0 (AT-GRAD-2)."""
    base_ts = 0
    # Pack all 10 rows into 29 days.
    rows = [
        {
            "job_id": f"job-{i}",
            "repo": "personal-repo",
            "task_type": "feature",
            "outcome": "merged_clean",
            "repo_class": "personal",
            "outcome_ts": base_ts + i * 3 * DAY_MS,  # 9*3 = 27 days span
        }
        for i in range(10)
    ]
    now = base_ts + 29 * DAY_MS  # exactly 29 days from first row
    result = compute_tier(
        repo="personal-repo",
        task_type="feature",
        rows=rows,
        now=now,
        policy=_TEST_POLICY,
        repo_class="personal",
    )
    assert result == 0


# ---------------------------------------------------------------------------
# Policy loader
# ---------------------------------------------------------------------------


def test_load_trust_policy_parses_real_file():
    """load_trust_policy parses the real docs/factory/trust-policy.md."""
    policy = load_trust_policy(POLICY_PATH)
    assert isinstance(policy, TrustPolicy)
    assert policy.consecutive_merged_clean >= 1
    assert policy.max_reverts >= 0
    assert policy.min_window_days >= 0
    assert policy.work_repo_hard_tier0 is True


def test_load_trust_policy_thresholds_match_design():
    """The pinned thresholds match the P1b-design §3.1 spec."""
    policy = load_trust_policy(POLICY_PATH)
    assert policy.consecutive_merged_clean == 10
    assert policy.max_reverts == 0
    assert policy.min_window_days == 30


# ---------------------------------------------------------------------------
# AT-RING-1: ring membership assertion
# ---------------------------------------------------------------------------


def test_ring_paths_include_trust_policy_files():
    """AT-RING-1: trust-policy.md paths are in RING_PATHS (guards against regression).

    If a refactor drops these paths from RING_PATHS, this test fails RED — the
    ring no longer protects the graduation policy.
    """
    ring_set = set(RING_PATHS)
    assert "trust-policy.md" in ring_set, (
        "trust-policy.md must be in RING_PATHS; dropping it would let a worker "
        "weaken the graduation thresholds. This is the AT-RING-1 guard."
    )
    assert "docs/factory/trust-policy.md" in ring_set, (
        "docs/factory/trust-policy.md must be in RING_PATHS (AT-RING-1)."
    )
    assert "docs/factory/never-graduates.md" in ring_set, (
        "docs/factory/never-graduates.md must be in RING_PATHS (AT-RING-1)."
    )
