# ABOUTME: The COMPOSITION ROOT (P1b-d, REQ-01) — the one module allowed to import
# ABOUTME: both factory and broker. It injects the REAL factory compute_tier + trust-
# ABOUTME: ledger reader + profile ceiling into the broker's MergeGate so tier-1 auto-
# ABOUTME: merge decides on real earned trust. The broker CORE still imports nothing
# ABOUTME: from factory (AST-verified in tests); only this root wires the two together.
"""
Broker launch — the composition root that wires the factory trust layer into the
broker's server-side auto-merge gate (design §V2.1).

Why this module exists (REQ-01 escalate_if resolution):
  The broker's MergeGate needs a tier recompute that lives factory-side
  (compute_tier over the trust ledger). The broker CORE must NOT import factory —
  the layering is factory→broker and the broker is the lower, credential-holding
  trust root (design §V2.1). MergeGate resolves this by taking compute_tier as an
  INJECTED callable. This module is the ONE place that closes the loop: it imports
  BOTH factory.trust_policy (compute_tier) and broker.merge_gate (MergeGate), and
  builds the 4-arg adapter the gate expects — (repo, task_type, capability, now_ms)
  -> int — by closing over the real ledger reader, the pinned policy, the owner
  profile ceiling, and the per-repo class lookup. The broker package never sees this.

The gate's compute_tier contract (broker/merge_gate.py) is deliberately narrow:
  (repo, task_type, capability, now_ms) -> int
This root adapts it onto the factory's richer signature:
  compute_tier(repo, task_type, rows, now, *, policy, repo_class, capability)
then caps the result with the owner profile ceiling (min semantics — a profile can
only LOWER the tier, never lift it). Everything on the trust decision path is real;
the ONLY injected boundary is git_diff (the network/subprocess seam).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from broker.merge_gate import GitDiff, MergeGate
from factory.trust_ledger import TrustLedger
from factory.trust_policy import TrustPolicy, compute_tier, load_trust_policy
from factory.trust_profile import load_trust_profile, profile_ceiling

# The factory-side git-diff runner type mirrors the gate's GitDiff:
# (base, branch, worktree) -> unified diff text. The composition root either
# receives one (tests) or builds a real subprocess runner (production).
RepoClassFor = Callable[[str], str]  # repo -> 'personal' | 'work'


def _real_git_diff(base: str, branch: str, worktree: str) -> str:
    """
    Purpose: the production git-diff runner — compute the diff the merge would land
    (``git diff base...branch``) inside the merge worktree, for the broker's ring
    re-check. This is the ONLY subprocess boundary in the gate path.
    Usage: passed as git_diff to build_merge_gate when no test runner is supplied.
    Gotchas: on ANY git failure this raises; the MergeGate catches it and fails
    CLOSED to held — the broker never auto-merges on a diff it could not read.
    """
    import subprocess

    proc = subprocess.run(
        ["git", "diff", f"{base}...{branch}"],
        cwd=worktree, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git diff {base}...{branch} failed (rc={proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    return proc.stdout


def build_merge_gate(
    *,
    ledger: TrustLedger,
    repo_class_for: RepoClassFor,
    profile: Optional[str] = None,
    policy: Optional[TrustPolicy] = None,
    git_diff: Optional[GitDiff] = None,
    worktree_root_override: Optional[str] = None,
) -> MergeGate:
    """
    Build the broker's server-side MergeGate with the REAL factory trust layer.

    Purpose: the composition root (design §V2.1) — injects compute_tier (real,
    ledger-driven) + a never-graduates reader (the gate's own default) + a git_diff
    runner into a MergeGate. The returned gate is what BrokerServer(merge_gate=...)
    consumes; the broker package never imports factory to get it.
    Usage:
        gate = build_merge_gate(ledger=TrustLedger(jobs_db),
                                repo_class_for=lambda r: classify(r),
                                profile=load_trust_profile())
        server = BrokerServer(..., merge_gate=gate)
    Gotchas:
      * profile defaults to the on-disk owner profile (ask-for-everything unless the
        owner opted in). The profile CAPS the tier (min) — it can never lift tier-0.
      * policy defaults to the ring-protected trust-policy.md thresholds.
      * git_diff defaults to the real subprocess runner; tests inject a canned diff.
      * the adapter is a CLOSURE over ledger/policy/profile/repo_class — the gate
        calls it with only (repo, task_type, capability, now_ms), the narrow contract
        the broker owns. worktree_root_override is a test seam (the never-graduates
        default path resolves off the broker module; tests may point elsewhere).
    """
    resolved_policy = policy if policy is not None else load_trust_policy()
    resolved_profile = profile if profile is not None else load_trust_profile()
    resolved_git_diff = git_diff if git_diff is not None else _real_git_diff
    ceiling = profile_ceiling(resolved_profile)

    def _compute_tier_adapter(repo: str, task_type: Optional[str],
                              capability: Optional[str], now_ms: int) -> int:
        """
        Adapt the broker's narrow 4-arg tier contract onto the factory compute_tier.

        Purpose: the injected boundary — reads the REAL ledger rows for (repo,
        task_type), computes the earned tier with the pinned policy + repo class, then
        caps it with the owner profile ceiling. This is what makes the broker's auto
        decision depend on genuine earned trust rather than a stamped card value.
        Gotchas: a missing/None task_type or an unreadable ledger yields tier 0 (the
        gate then holds) — the fail-closed posture propagates here too. repo_class is
        looked up per-repo so a work repo hits compute_tier's hard gate.
        """
        if not task_type:
            return 0
        try:
            rows = ledger.read_rows(repo, task_type)
            repo_class = repo_class_for(repo)
            computed = compute_tier(
                repo, task_type, rows, now_ms,
                policy=resolved_policy, repo_class=repo_class, capability=capability,
            )
        except Exception:
            # Fail closed: any ledger/policy error → tier 0 → the gate holds.
            return 0
        # The owner profile ceiling caps the tier (min) — never lifts it.
        return min(computed, ceiling)

    return MergeGate(
        git_diff=resolved_git_diff,
        compute_tier=_compute_tier_adapter,
        # never_graduates defaults to the gate's own ring-protected doc reader.
    )
