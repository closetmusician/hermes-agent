# ABOUTME: THE model router (P4-b, the crown task). The ONE place a worker tier is
# ABOUTME: chosen at launch, and the 429 step-down ladder. Its residency PRE-FILTER
# ABOUTME: removes EVERY third-party (openrouter) rung from an allow_openrouter:false
# ABOUTME: OR unknown-policy job's ladder — at every depth — so a 429 that exhausts
# ABOUTME: the subscription tiers WAITS for capacity and NEVER reaches OpenRouter.
"""
Model router + 429 failover ladder + residency pre-filter.

Design authoritative source: docs/plans/harness/fable/p4/P4-design.md §4 (router,
ladder, residency) + §13 (the two enforcement walls) + Revision v2 §R4 (F1/F2/F3).
Review closures: docs/plans/harness/fable/p4/P4-review.md F1/F2/F3/F12.

The crown invariant, made structurally true (not merely policy-labelled):

  * ``admissible_ladder(repo, policy, catalog)`` filters the probe-confirmed ladder
    so a false/unknown-policy repo's ladder contains ZERO openrouter rungs — the
    residency pre-filter, wall 1 of two (wall 2 is the runner guard at
    worker_runner.py:362, already in production).
  * ``next_rung`` re-derives that admissible ladder on EVERY call (F1) — it never
    trusts a cached list or the passed-in rung's tier to index a stale ladder that
    still holds OR rungs.
  * When no admissible rung remains below the current one, ``next_rung`` /
    ``select_tier`` return the ``WAIT_FOR_CAPACITY`` sentinel — NOT an OpenRouter
    rung, NOT a hard failure. The scheduler parks the job WAITING_CAPACITY.
  * A "phantom" model (F12) is any name failing ``live_catalog && ping_ok`` — NOT a
    name absent from capabilities.json. GLM-5.2 IS in the file but fails the ping,
    so it is excluded on the predicate, and a genuinely-absent name is excluded too.

The router only SELECTS. The enforcement wall (assert_openrouter_allowed) sits
downstream in worker_runner.build_worker_env and fires unconditionally — so even a
buggy router that emitted an OR rung for a false repo cannot leak a key.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from factory.merge_policy import (
    MergePolicy,
    MergePolicyInvalid,
    default_policy,
    load_merge_policy,
)
from factory.residency_guard import THIRD_PARTY_WORKERS, assert_openrouter_allowed

# The first-party subscription tiers, in ladder order (tier 0 = top). These are
# never gated by residency — claude/codex are first-party inference. Their models
# are the current subscription defaults; the supervisor may override per-repo.
_FIRST_PARTY_LADDER = (
    ("claude", "claude-sonnet"),
    ("codex", "gpt-5"),
)

# The candidate OpenRouter overflow order (design §4.1). A candidate becomes a rung
# ONLY if capabilities.json probe-confirms it (live_catalog && ping_ok). The order
# here is the preference; unconfirmed candidates are silently skipped (phantom guard).
_OPENROUTER_CANDIDATES = (
    "zhipuai/glm-5",
    "moonshot/kimi-k2",
    "deepseek/deepseek-r1",
    "deepseek/deepseek-v3",
)


@dataclass(frozen=True)
class RungSpec:
    """
    One admissible rung of the failover ladder: a (worker, model, tier) triple.

    Purpose: the router's unit of selection — worker/model the runner will spawn,
    tier is the ladder depth (0 = top) used only to order the step-down.
    Usage: rung = select_tier(repo, task_type, policy, catalog); spec.worker = rung.worker
    Gotchas: tier is a ladder position, NOT a residency signal — residency is decided
    by ``worker`` (openrouter is third-party) against the policy at filter time.
    """

    worker: str
    model: str
    tier: int


class _WaitForCapacity:
    """
    The sentinel returned when no admissible rung remains (residency-safe park).

    Purpose: a false/unknown-policy job that exhausts its first-party tiers must WAIT
    for subscription capacity, never spill to OpenRouter. This singleton is that
    "wait" signal — distinct from both a valid RungSpec and a hard error.
    Usage: if next_rung(...) is WAIT_FOR_CAPACITY: park the job WAITING_CAPACITY.
    Gotchas: identity-compared (``is WAIT_FOR_CAPACITY``); there is exactly one.
    """

    _instance: Optional["_WaitForCapacity"] = None

    def __new__(cls) -> "_WaitForCapacity":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "WAIT_FOR_CAPACITY"


WAIT_FOR_CAPACITY = _WaitForCapacity()

RungOrWait = Union[RungSpec, _WaitForCapacity]


def _probe_confirmed_openrouter_models(catalog: Optional[Dict[str, Any]]) -> List[str]:
    """
    Purpose: return the OpenRouter candidate models the catalog probe-confirmed
    (live_catalog AND ping_ok), preserving the preference order — the phantom guard.
    Usage: live = _probe_confirmed_openrouter_models(catalog)
    Gotchas: a name absent from the catalog OR present-but-failing-the-predicate is
    excluded identically (F12); an empty/None catalog yields no OR rungs.
    """
    if not catalog:
        return []
    models = catalog.get("openrouter", {}).get("models", {})
    confirmed = []
    for name in _OPENROUTER_CANDIDATES:
        flags = models.get(name)
        if flags and flags.get("live_catalog") and flags.get("ping_ok"):
            confirmed.append(name)
    return confirmed


def _rung_admissible(worker: str, repo: str, policy: Optional[MergePolicy]) -> bool:
    """
    Purpose: the residency pre-flight — True iff ``worker`` may run for ``repo`` under
    ``policy``. Uses the SAME predicate as the runner's hard wall
    (assert_openrouter_allowed), so the pre-filter and the wall can never disagree.
    Usage: [r for r in ladder if _rung_admissible(r.worker, repo, policy)]
    Gotchas: fail-closed — a None/unknown policy denies every third-party worker.
    First-party workers always pass (the guard returns without raising).
    """
    if worker not in THIRD_PARTY_WORKERS:
        return True
    try:
        assert_openrouter_allowed(repo, worker, policy)
        return True
    except Exception:
        return False


def admissible_ladder(
    repo: str,
    policy: Optional[MergePolicy],
    catalog: Optional[Dict[str, Any]],
) -> List[RungSpec]:
    """
    Build the residency-filtered failover ladder for a repo, top rung first.

    Purpose: THE pre-filter (wall 1). Compose first-party tiers + probe-confirmed
    OpenRouter overflow, then drop every rung the residency policy forbids. For a
    false/unknown-policy repo the returned ladder contains ZERO openrouter rungs at
    ANY depth — there is no OR rung to fall to.
    Usage: ladder = admissible_ladder(repo, policy, catalog)
    Gotchas:
      * Called fresh by ``select_tier`` and by ``next_rung`` on EVERY step (F1) — no
        caller may cache and re-index it, or a stale list could reintroduce an OR rung.
      * Tier numbers are assigned across the FULL candidate ladder before filtering,
        so a rung's tier is a stable ladder position independent of what was filtered.
    """
    full: List[RungSpec] = []
    tier = 0
    for worker, model in _FIRST_PARTY_LADDER:
        full.append(RungSpec(worker=worker, model=model, tier=tier))
        tier += 1
    for model in _probe_confirmed_openrouter_models(catalog):
        full.append(RungSpec(worker="openrouter", model=model, tier=tier))
        tier += 1
    return [r for r in full if _rung_admissible(r.worker, repo, policy)]


def select_tier(
    repo: str,
    task_type: str,
    policy: Optional[MergePolicy],
    catalog: Optional[Dict[str, Any]],
) -> RungOrWait:
    """
    Pick the top admissible (probe-confirmed, residency-allowed) rung for a job.

    Purpose: the launch-time tier choice — the ONE place worker/model is selected.
    For a false/unknown-policy repo this is always a first-party rung; if somehow no
    admissible rung exists, returns WAIT_FOR_CAPACITY (never OpenRouter).
    Usage: rung = select_tier(repo, "feature", policy, catalog)
    Gotchas: task_type is accepted for future per-task routing (feature vs quick) and
    is currently informational — the ladder order is task-independent. Residency is
    enforced by the admissible_ladder filter, not by task_type.
    """
    ladder = admissible_ladder(repo, policy, catalog)
    if not ladder:
        return WAIT_FOR_CAPACITY
    return ladder[0]


def next_rung(
    current: RungSpec,
    repo: str,
    policy: Optional[MergePolicy],
    catalog: Optional[Dict[str, Any]],
) -> RungOrWait:
    """
    The 429 step-down: the next admissible rung strictly below ``current``, or WAIT.

    Purpose: descend the ladder one probe-confirmed rung on a 429. F1 anti-weakening:
    it RE-DERIVES the admissible ladder from (repo, policy, catalog) on EVERY call —
    it does NOT reuse a select_tier-cached list, and it does NOT trust ``current`` to
    be admissible. So a false repo whose ``current`` is (buggily) an OR rung still
    only sees first-party rungs when choosing the next one → WAIT_FOR_CAPACITY once
    the first-party tiers are exhausted.
    Usage: nxt = next_rung(current_rung, repo, policy, catalog)
    Gotchas:
      * "strictly below" is by tier number: the first admissible rung whose tier >
        current.tier. A false repo has no admissible OR rung, so once past the last
        first-party tier there is nothing below → WAIT.
      * Returns WAIT_FOR_CAPACITY (not None, not an error) so the scheduler parks the
        job WAITING_CAPACITY rather than dispatching a residency-violating fallback.
    """
    ladder = admissible_ladder(repo, policy, catalog)
    for rung in ladder:
        if rung.tier > current.tier:
            return rung
    return WAIT_FOR_CAPACITY


def resolve_policy(repo: str) -> MergePolicy:
    """
    Load a repo's merge policy fail-closed: missing/unparseable → default (OR OFF).

    Purpose: F3 — the router's policy read must NEVER let a missing or malformed
    merge-policy.md leave OpenRouter rungs in the ladder. ``load_merge_policy`` raises
    FileNotFoundError on absence and MergePolicyInvalid on a bad body; both are
    caught here and substituted with ``default_policy(repo)`` (allow_openrouter=False).
    Usage: policy = resolve_policy(repo); ladder = admissible_ladder(repo, policy, cat)
    Gotchas: this is a deliberate fail-CLOSED catch, not a naive ``except: pass`` —
    the substituted default denies third-party egress, so an unknown-residency repo
    gets a first-party-only ladder → WAIT on exhaustion, never a leak.
    """
    policy_path = Path(repo) / "docs" / "factory" / "merge-policy.md"
    try:
        return load_merge_policy(policy_path)
    except (FileNotFoundError, MergePolicyInvalid, OSError):
        return default_policy(repo)


def apply_rung(spec: Any, rung: RungSpec) -> Any:
    """
    Return a copy of a WorkerRunSpec with worker/model set to the chosen rung.

    Purpose: the additive router→runner seam — populate worker/model without touching
    the residency guard (which stays the downstream wall). Used at launch and on a
    429 re-launch so the injected tier reflects the router's live choice.
    Usage: spec = apply_rung(base_spec, select_tier(...))
    Gotchas: spec must be a dataclass (WorkerRunSpec); ``replace`` yields a new frozen
    copy so the caller's original is untouched. The policy on the spec is preserved —
    the guard re-checks it downstream regardless of the rung.
    """
    return replace(spec, worker=rung.worker, model=rung.model)
