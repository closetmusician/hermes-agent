# ABOUTME: The data-residency wall (P3 design §6.2 / §13.1). One chokepoint
# ABOUTME: function — assert_openrouter_allowed — that makes it structurally
# ABOUTME: impossible for a work/Diligent/allow_openrouter:false job to receive a
# ABOUTME: third-party (OpenRouter) model key. Called UNCONDITIONALLY at the top of
# ABOUTME: worker_runner.build_worker_env, before any model key is looked up.
"""
Data-residency guard — the one third-party-egress chokepoint.

Design authoritative source: docs/plans/harness/fable/p3/P3-design.md §6.2, §13.1.
Closes: docs/plans/harness/fable/p3/P3-review.md finding P0-1 (the residency guard
was specified in v1 but nothing called it; work code stayed off OpenRouter only by
the accident that ``_PROVIDER_MODEL_KEY`` lacked an ``openrouter`` entry).

The wall this module builds:

  * ``assert_openrouter_allowed(repo, worker, policy)`` raises ``ResidencyViolation``
    when a third-party-hosted worker (OpenRouter) is requested for a job whose
    residency policy does NOT permit it. ``claude``/``codex`` are first-party
    subscription workers and are never gated — only a host that egresses a job's
    code to a third party is.
  * FAIL-CLOSED: a ``None``/unknown policy is treated as ``allow_openrouter=false``.
    An unmapped/unknown repo therefore never reaches a third-party host.

The guard is intentionally a pure predicate over (repo, worker, policy) with zero
I/O so it is deterministic and unit-testable. Its SECURITY value comes from being
wired UNCONDITIONALLY into ``worker_runner.build_worker_env`` before the model-key
lookup — see §13.1: the code path that would inject an OpenRouter key is
unreachable past a raising guard.
"""

from __future__ import annotations

from typing import Optional

from factory.merge_policy import MergePolicy

# Workers whose model host egresses a job's code/prompt to a THIRD PARTY. These
# are the only workers the residency wall gates. claude/codex are first-party
# subscription inference (no third-party egress) and are always admitted. Extend
# this set when a new hosted tier is added — never widen it silently.
THIRD_PARTY_WORKERS = frozenset({"openrouter"})


class ResidencyViolation(Exception):
    """
    Raised when a job would route to a third-party model host it is not allowed to.

    Purpose: a single catchable, fail-closed error at the model-key injection seam
    so a work/Diligent/allow_openrouter:false job is refused a third-party key
    BEFORE any env is built or worker spawned.
    Usage: caught (or allowed to propagate → NEEDS_ATTENTION) by the supervisor.
    Gotchas: this is the confidentiality wall — it fires on unknown residency too
    (policy=None → treated as disallowed), never defaulting toward permissive.
    """


def assert_openrouter_allowed(
    repo: str,
    worker: str,
    policy: Optional[MergePolicy],
) -> None:
    """
    Deny a third-party-host worker unless the job's policy explicitly permits it.

    Purpose: the ONE residency chokepoint. Called unconditionally at the top of
    ``worker_runner.build_worker_env`` (before the model-key lookup) so an
    OpenRouter key cannot be injected for a work/Diligent job even if a future
    ``_PROVIDER_MODEL_KEY`` entry maps openrouter.
    Usage: assert_openrouter_allowed(run_spec.repo, run_spec.worker, run_spec.policy)
    Gotchas:
      * FAIL-CLOSED — a ``None`` or missing policy is treated as
        ``allow_openrouter=false`` (unknown residency → no third-party host).
      * Only ``THIRD_PARTY_WORKERS`` are gated; ``claude``/``codex`` return
        without raising (first-party inference is always in-residency).
      * Pure predicate: no I/O, so the SAME (repo, worker, policy) always yields
        the same allow/deny — the falsifiable core the design §13.1 mandates.
    """
    if worker not in THIRD_PARTY_WORKERS:
        return  # first-party inference — never gated.

    # Fail-closed: a None/unknown policy means allow_openrouter is NOT granted.
    if policy is not None and policy.allow_openrouter:
        return

    raise ResidencyViolation(
        f"{repo}: allow_openrouter is false (or residency unknown) — worker "
        f"{worker!r} egresses to a third-party host and is refused"
    )
