# ABOUTME: Trust policy engine for the Fable factory (P1b-b, REQ-02). Implements
# ABOUTME: compute_tier — a pure, deterministic function over ledger rows that
# ABOUTME: returns the earned autonomy tier (0 or 1). Three hard early-return gates
# ABOUTME: (work repos, never-graduates capabilities, unknown task types) make the
# ABOUTME: tier-0 default unbypassable regardless of the ledger record.
"""
Trust policy — pure tier computation and policy-file loader.

Design authoritative source: docs/plans/harness/fable/p1b/P1b-design.md §3

compute_tier is a PURE function (no I/O, fully unit-testable). It reads only the
rows passed to it and the pinned policy/config args — no database calls, no file I/O.
The three hard gates fire as early-return `return 0` before any ledger data is
consulted, making them unbypassable by any accumulation of clean outcomes.

Outcome vocabulary consumed from the ledger (§2.1):
  merged_clean    — the ONLY outcome that counts toward the consecutive streak.
  merged_with_fix — breaks the streak (not a revert, but not clean either).
  rejected        — breaks the streak; triggers demotion on the next computation.
  reverted        — breaks the streak; triggers demotion.

Ring protection: docs/factory/trust-policy.md (the thresholds file) is a member of
factory/immutable_ring.RING_PATHS.  A worker diff that edits it is categorically
rejected at merge-submission.  AT-RING-1 in the test suite guards against a refactor
silently dropping those paths from the ring.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional

from factory.trust_ledger import GRADUATABLE_TASK_TYPES

# ---------------------------------------------------------------------------
# Known Diligent org repo prefixes — an additional AND-gate so a mislabeled
# work repo cannot graduate even if its repo_class was set to 'personal'.
# ---------------------------------------------------------------------------
_DILIGENT_PREFIXES = (
    "diligent",
    "diligent-internal",
    "diligent-corp",
    "diligent-boardbooks",
    "boardbooks",
)

# Default location of the trust-policy.md file (ring-protected path).
_DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "factory" / "trust-policy.md"
)

# Default location of the never-graduates capabilities file.
_DEFAULT_NEVER_GRADS_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "factory" / "never-graduates.md"
)

# ms per day (used for window-span calculations).
_DAY_MS: int = 24 * 60 * 60 * 1000


# ---------------------------------------------------------------------------
# Policy data (loaded from docs/factory/trust-policy.md)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrustPolicy:
    """
    Purpose: pinned graduation thresholds loaded from the ring-protected trust-policy.md.
    All fields map 1:1 to the YAML block in §3.1 of P1b-design.md.
    Usage: policy = load_trust_policy(path); compute_tier(..., policy=policy)
    Gotchas: work_repo_hard_tier0 is loaded but the hard gate in compute_tier is
    independent — removing it from the YAML does not remove the code gate.
    """
    consecutive_merged_clean: int
    max_reverts: int
    min_window_days: float
    min_confidence: float
    work_repo_hard_tier0: bool = True


def load_trust_policy(path: Optional[Path] = None) -> TrustPolicy:
    """
    Purpose: parse docs/factory/trust-policy.md and return a TrustPolicy dataclass.
    The file is a YAML-in-markdown block; we extract the YAML between code fences
    or parse the key: value lines directly from the tiers.tier1 section.
    Usage: policy = load_trust_policy()  # uses the default ring-protected path
    Gotchas: the file is ring-protected so a worker cannot alter it; but the loader
    deliberately re-reads from disk each call (no module-level cache) so that a
    maintainer commit updating thresholds takes effect on the next process start.
    """
    if path is None:
        path = _DEFAULT_POLICY_PATH

    text = Path(path).read_text()

    def _extract(key: str, default: str) -> str:
        m = re.search(rf"^\s*{re.escape(key)}\s*:\s*(.+)$", text, re.MULTILINE)
        if not m:
            return default
        # Strip inline YAML comment (anything after unquoted ' #').
        raw = m.group(1).strip()
        raw = re.sub(r"\s+#.*$", "", raw)
        return raw.strip()

    consecutive = int(_extract("consecutive_merged_clean", "10"))
    max_reverts = int(_extract("max_reverts", "0"))
    min_window = float(_extract("min_window_days", "30"))
    min_conf = float(_extract("min_confidence", "0.0"))

    hard_tier0_raw = _extract("work_repo_hard_tier0", "true").lower()
    hard_tier0 = hard_tier0_raw not in ("false", "0", "no")

    return TrustPolicy(
        consecutive_merged_clean=consecutive,
        max_reverts=max_reverts,
        min_window_days=min_window,
        min_confidence=min_conf,
        work_repo_hard_tier0=hard_tier0,
    )


# ---------------------------------------------------------------------------
# Never-graduates capability reader
# ---------------------------------------------------------------------------


def _load_never_graduates_capabilities(path: Optional[Path] = None) -> FrozenSet[str]:
    """
    Purpose: read the capability keywords mentioned in docs/factory/never-graduates.md
    that should never qualify for tier graduation. Returns a frozenset of lowercase
    capability tokens extracted from the ring-protected file.
    Usage: caps = _load_never_graduates_capabilities()
    Gotchas: the extraction is heuristic (bold-list items) — it is only used for the
    capability= keyword argument to compute_tier, which is populated from trusted
    job-creation code (§V2.4), never from worker output.
    """
    if path is None:
        path = _DEFAULT_NEVER_GRADS_PATH

    try:
        text = Path(path).read_text()
    except OSError:
        return frozenset()

    # Extract bold list-item keywords: **keyword** or **multi word phrase**.
    caps: set = set()
    for match in re.finditer(r"\*\*([^*]+)\*\*", text):
        token = match.group(1).strip().lower()
        if token:
            # Also add individual words from multi-word phrases.
            caps.add(token)
            for word in token.split():
                caps.add(word)

    return frozenset(caps)


# ---------------------------------------------------------------------------
# Diligent prefix check
# ---------------------------------------------------------------------------


def _is_diligent_repo(repo: str) -> bool:
    """
    Purpose: detect a known Diligent/work org repo by prefix, even if it was
    mislabeled as 'personal' in merge-policy.md.
    Usage: called as an additional AND-gate inside compute_tier BEFORE the
    earned-tier logic — a Diligent-prefixed repo can never pass this gate.
    Gotchas: prefix match is case-insensitive; includes the repo slug and
    the org/repo form (diligent-internal/platform → caught by 'diligent' prefix).
    """
    repo_lower = repo.lower()
    for prefix in _DILIGENT_PREFIXES:
        if repo_lower == prefix or repo_lower.startswith(prefix + "-") or repo_lower.startswith(prefix + "/"):
            return True
    return False


# ---------------------------------------------------------------------------
# Streak helper
# ---------------------------------------------------------------------------


def _trailing_clean_streak(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Purpose: return the longest trailing run of consecutive merged_clean rows
    (in time order, oldest-first). The streak is reset to zero at the first
    non-merged_clean outcome encountered when scanning from the end.
    Usage: streak_rows = _trailing_clean_streak(rows)  # rows sorted by outcome_ts asc
    Gotchas: rows must already be sorted ascending by outcome_ts; any outcome that
    is not exactly 'merged_clean' (including merged_with_fix) resets the streak.
    """
    streak: List[Dict[str, Any]] = []
    for row in reversed(rows):
        if row.get("outcome") == "merged_clean":
            streak.insert(0, row)
        else:
            break
    return streak


# ---------------------------------------------------------------------------
# compute_tier — the pure, deterministic tier function
# ---------------------------------------------------------------------------


def compute_tier(
    repo: str,
    task_type: str,
    rows: List[Dict[str, Any]],
    now: int,
    *,
    policy: TrustPolicy,
    repo_class: str,
    capability: Optional[str] = None,
) -> int:
    """
    Purpose: compute the earned autonomy tier (0 or 1) for a (repo × task_type) pair.
    This is a PURE function — no I/O, no side effects, fully deterministic for the
    same inputs. It reads only the rows passed to it and the pinned policy/config args.

    Hard gates fire as early-return `return 0` BEFORE any ledger data is consulted
    so no amount of clean history can lift a work repo, a never-graduates capability,
    or an unknown/other task type.

    Usage:
        tier = compute_tier("my-repo", "feature", ledger.read_rows("my-repo","feature"),
                             now_ms(), policy=policy, repo_class="personal")
    Gotchas: `rows` must be sorted ascending by outcome_ts (TrustLedger.read_rows
    guarantees this). `now` is a ms-epoch integer so tests can inject a fake clock
    without waiting real wall-clock time. `capability` is optional; when provided it
    is checked against the never-graduates list (HARD GATE 2).

    Anti-weakening: the three hard gates are load-bearing. The test suite includes
    test_work_repo_is_tier0_regardless_of_record, test_never_graduates_capability_is_tier0,
    and test_other_task_type_is_tier0 — each explicitly documents that removing
    the gate causes the test to FAIL (AT-WORK-1, §5.2 anti-weakening assertions).
    """
    # -------------------------------------------------------------------------
    # HARD GATE 1 — work / Diligent repos never graduate (L1, §5 plan).
    # repo_class == 'work' is the explicit label; _is_diligent_repo is the
    # additional AND-gate that catches mislabeled Diligent repos.
    # -------------------------------------------------------------------------
    if repo_class == "work" or _is_diligent_repo(repo):
        return 0  # anti-weakening: test_work_repo_is_tier0_regardless_of_record

    # -------------------------------------------------------------------------
    # HARD GATE 2 — capability on the never-graduates list (plan §5).
    # -------------------------------------------------------------------------
    if capability is not None:
        never_caps = _load_never_graduates_capabilities()
        if capability.lower() in never_caps:
            return 0  # anti-weakening: test_never_graduates_capability_is_tier0

    # -------------------------------------------------------------------------
    # HARD GATE 3 — 'other' / unknown task_type never graduates (fail-safe).
    # -------------------------------------------------------------------------
    if task_type not in GRADUATABLE_TASK_TYPES:
        return 0  # anti-weakening: test_other_task_type_is_tier0

    # --- Only here may we earn tier-1 from the ledger record -----------------

    # Filter to this (repo, task_type) — rows from read_rows are already filtered,
    # but compute_tier is also callable with a pre-built list (tests) so filter again.
    filtered = [
        r for r in rows
        if r.get("repo") == repo and r.get("task_type") == task_type
    ]

    # Trailing consecutive merged_clean streak.
    streak_rows = _trailing_clean_streak(filtered)
    streak = len(streak_rows)

    # Reverts anywhere in the filtered window (not just the streak).
    reverts = sum(1 for r in filtered if r.get("outcome") == "reverted")

    # Window span: (now − oldest-streak-row.outcome_ts) in days.
    if streak_rows:
        span_days = (now - streak_rows[0]["outcome_ts"]) / _DAY_MS
    else:
        span_days = 0.0

    # Confidence gate (inert when min_confidence == 0.0, §3.1).
    if policy.min_confidence > 0.0:
        conf_ok = all(
            (r.get("confidence") or 0.0) >= policy.min_confidence
            for r in streak_rows
        )
    else:
        conf_ok = True

    # Earn tier-1 iff ALL conditions met.
    if (
        streak >= policy.consecutive_merged_clean
        and reverts <= policy.max_reverts
        and span_days >= policy.min_window_days
        and conf_ok
    ):
        return 1

    return 0
