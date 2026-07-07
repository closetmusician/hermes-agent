# ABOUTME: Owner profile switch for the Fable factory (P1b-c, REQ-03). Implements
# ABOUTME: the tier-ceiling cap: ask-for-everything (default, OQ1) caps all to
# ABOUTME: tier-0; auto-merge-personal-non-prod lets a computed tier-1 through.
# ABOUTME: A profile ONLY lowers the effective tier — it can never raise tier-0
# ABOUTME: to tier-1.  merge_disposition wires profile + compute_tier together.
"""
Owner trust profiles — tier ceiling cap and merge disposition.

Design authoritative source: docs/plans/harness/fable/p1b/P1b-design.md §4.1/§4.2

A profile sets an effective tier CEILING: effective_tier = min(compute_tier(...), ceiling).
The profile can only lower the effective tier, never raise it.  The hard gates inside
compute_tier (work repo / never-graduates / unknown task type) still fire before the
profile ceiling is applied — a work repo stays tier-0 under any profile.

Owner profile config is read from ~/.hermes/factory/trust-profile.txt (one line).
Default (missing or unrecognized) is ask-for-everything (OQ1 launch posture).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from factory.trust_policy import TrustPolicy, compute_tier

# ---------------------------------------------------------------------------
# Profile constants
# ---------------------------------------------------------------------------

PROFILE_ASK_EVERYTHING = "ask-for-everything"
PROFILE_AUTO_MERGE_PERSONAL = "auto-merge-personal-non-prod"

_VALID_PROFILES = frozenset({PROFILE_ASK_EVERYTHING, PROFILE_AUTO_MERGE_PERSONAL})

# Default config path (readable at runtime; not ring-protected — the owner controls it).
_DEFAULT_PROFILE_PATH = Path.home() / ".hermes" / "factory" / "trust-profile.txt"


# ---------------------------------------------------------------------------
# TrustProfile dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrustProfile:
    """
    Purpose: wraps a profile name and its effective tier ceiling for downstream use.
    Usage: p = TrustProfile(name=PROFILE_ASK_EVERYTHING, ceiling=0)
    Gotchas: ceiling is purely advisory here; profile_ceiling() is the canonical
    ceiling lookup — always derive ceiling from the profile name via profile_ceiling().
    """
    name: str
    ceiling: int


# ---------------------------------------------------------------------------
# profile_ceiling
# ---------------------------------------------------------------------------


def profile_ceiling(profile: str) -> int:
    """
    Purpose: return the tier ceiling for the given profile name.
    ask-for-everything → 0 (nothing auto-merges, regardless of record).
    auto-merge-personal-non-prod → 1 (earned tier-1 is honored).
    Any unrecognized profile → 0 (fail-safe, same as ask-for-everything).
    Usage: ceiling = profile_ceiling(PROFILE_ASK_EVERYTHING)  # → 0
    Gotchas: the ceiling CAPS the tier; it can never lift a tier-0 computed tier.
    """
    if profile == PROFILE_AUTO_MERGE_PERSONAL:
        return 1
    # Default (ask-for-everything) and any unknown profile → 0.
    return 0


# ---------------------------------------------------------------------------
# effective_tier
# ---------------------------------------------------------------------------


def effective_tier(computed: int, ceiling: int) -> int:
    """
    Purpose: apply the profile ceiling to a computed tier: min(computed, ceiling).
    A ceiling of 0 caps everything to tier-0; a ceiling of 1 lets a genuine tier-1
    through.  A ceiling of 1 cannot lift a computed tier-0 to tier-1 (min semantics).
    Usage: t = effective_tier(compute_tier(...), profile_ceiling(profile))
    Gotchas: this is intentionally the trivial min() so the semantics are obvious.
    """
    return min(computed, ceiling)


# ---------------------------------------------------------------------------
# load_trust_profile
# ---------------------------------------------------------------------------


def load_trust_profile(path: Optional[Path] = None) -> str:
    """
    Purpose: read the owner profile from disk and return the profile name string.
    Defaults to ask-for-everything when the file does not exist, is empty, or
    contains an unrecognized value (fail-safe: unknown → most restrictive).
    Usage: profile = load_trust_profile()  # uses ~/.hermes/factory/trust-profile.txt
    Gotchas: the file holds a single profile name on the first line; trailing
    whitespace and newlines are stripped.  A missing file is not an error.
    """
    if path is None:
        path = _DEFAULT_PROFILE_PATH

    try:
        raw = Path(path).read_text().strip().splitlines()
        value = raw[0].strip() if raw else ""
    except (OSError, IndexError):
        value = ""

    if value in _VALID_PROFILES:
        return value
    # Unknown or missing → fail-safe default.
    return PROFILE_ASK_EVERYTHING


# ---------------------------------------------------------------------------
# merge_disposition
# ---------------------------------------------------------------------------


def merge_disposition(
    repo: str,
    task_type: str,
    rows: list,
    now: int,
    *,
    policy: TrustPolicy,
    profile: str,
    repo_class: str,
    capability: Optional[str] = None,
) -> Tuple[str, int]:
    """
    Purpose: compute the merge disposition and effective tier for a (repo × task_type).
    Returns ("auto", tier) when the effective tier is ≥ 1, ("held", tier) otherwise.
    This is the single decision function described in P1b-design.md §4.2.
    Usage: disp, tier = merge_disposition("my-repo", "feature", rows, now_ms(),
               policy=policy, profile=PROFILE_AUTO_MERGE_PERSONAL, repo_class="personal")
    Gotchas: rows must be sorted ascending by outcome_ts (TrustLedger.read_rows
    guarantees this). `capability` is forwarded to compute_tier's HARD GATE 2.
    The profile ceiling is applied AFTER compute_tier, so the hard gates inside
    compute_tier (work repo / never-graduates / unknown task type) still fire first.
    """
    computed = compute_tier(
        repo=repo,
        task_type=task_type,
        rows=rows,
        now=now,
        policy=policy,
        repo_class=repo_class,
        capability=capability,
    )
    ceiling = profile_ceiling(profile)
    tier = effective_tier(computed, ceiling)
    disposition = "auto" if tier >= 1 else "held"
    return disposition, tier
