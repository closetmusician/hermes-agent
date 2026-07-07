# ABOUTME: RED-first tests for the router↔worker_runner integration (P4-b REQ-02).
# ABOUTME: The router's chosen tier populates the WorkerRunSpec; the existing
# ABOUTME: residency guard at worker_runner.py:362 stays the backstop wall. A 429
# ABOUTME: re-launch recomputes the rung from live policy (never a stale checkpoint
# ABOUTME: rung). Both walls active; wall-3 (no OR key unless policy true) held.
"""
Tests for the model_router ↔ worker_runner seam (P4-b REQ-02, F2/F7).

The router only SELECTS a tier and hands worker/model to a WorkerRunSpec. The
residency guard downstream (worker_runner.build_worker_env → assert_openrouter_allowed)
is the enforcement wall and must stay active. These tests pin:
  * apply_rung populates a run spec's worker/model from a RungSpec (additive).
  * a false-repo OR rung (if a bug injected one) → ResidencyViolation at the guard.
  * a 429 re-launch recomputes the rung from LIVE policy, ignoring a stale
    checkpoint rung (F2).
  * wall-3: no OpenRouter model key is placed unless policy.allow_openrouter (F7).
"""
from __future__ import annotations

import pytest

from factory.merge_policy import MergePolicy
from factory.residency_guard import ResidencyViolation
from factory.worker_runner import WorkerRunSpec, WorkerRunner


_LIVE_CATALOG = {
    "openrouter": {
        "models": {
            "zhipuai/glm-5": {"live_catalog": True, "ping_ok": True},
        }
    }
}


def _run_spec(repo: str, policy: MergePolicy, worker: str = "claude",
              model: str = "claude-sonnet") -> WorkerRunSpec:
    return WorkerRunSpec(
        job_id="j1", repo=repo, base_branch="main", spec_text="add x",
        worker=worker, model=model, budget_usd=2.0, timeout_min=20, policy=policy,
        model_key="sk-test",
    )


def test_apply_rung_populates_worker_and_model() -> None:
    """apply_rung sets the run spec's worker/model to the router's chosen rung
    (additive — it does not touch the residency guard)."""
    from factory.model_router import RungSpec, apply_rung

    policy = MergePolicy(repo="/repo/acme", allow_openrouter=True)
    spec = _run_spec("/repo/acme", policy)
    rung = RungSpec(worker="codex", model="gpt-5", tier=1)
    updated = apply_rung(spec, rung)
    assert updated.worker == "codex"
    assert updated.model == "gpt-5"


def test_false_repo_openrouter_rung_raises_at_runner_guard(tmp_path) -> None:
    """WALL 2 (backstop): even if the router buggily emits an OR rung for a false
    repo, build_worker_env raises ResidencyViolation before any env is built."""
    runner = WorkerRunner(repo_root=str(tmp_path / "repo"), worktrees_root=str(tmp_path / "wt"))
    false_policy = MergePolicy(repo="/repo/work", allow_openrouter=False)
    spec = _run_spec("/repo/work", false_policy, worker="openrouter", model="zhipuai/glm-5")
    with pytest.raises(ResidencyViolation):
        runner.build_worker_env(spec)


def test_true_repo_openrouter_key_placed_only_when_policy_allows(tmp_path) -> None:
    """WALL 3 (F7): an OpenRouter model key is placed only for a policy that permits
    it. A true-policy OR job builds an env carrying the OR key; a false-policy job
    never reaches this point (guard raises)."""
    runner = WorkerRunner(repo_root=str(tmp_path / "repo"), worktrees_root=str(tmp_path / "wt"))
    true_policy = MergePolicy(repo="/repo/acme", allow_openrouter=True)
    spec = _run_spec("/repo/acme", true_policy, worker="openrouter", model="zhipuai/glm-5")
    env = runner.build_worker_env(spec)
    # The OR key name must be present (the provider key map now includes openrouter).
    assert any("OPENROUTER" in k for k in env), f"no OR key placed: {sorted(env)}"


def test_relaunch_recomputes_rung_from_live_policy_not_stale_checkpoint() -> None:
    """F2: on 429 re-launch, the rung is recomputed via select_tier with the LIVE
    policy — a repo flipped true→false since the checkpoint gets a first-party rung,
    never the stale OR tier persisted in the checkpoint."""
    from factory.model_router import select_tier, RungSpec

    repo = "/repo/flipped"
    # Checkpoint persisted a true-era OR rung (forensics only).
    stale_rung = RungSpec(worker="openrouter", model="zhipuai/glm-5", tier=2)
    # But the live policy is now FALSE.
    live_policy = MergePolicy(repo=repo, allow_openrouter=False)
    recomputed = select_tier(repo, "feature", live_policy, _LIVE_CATALOG)
    assert recomputed.worker != "openrouter"
    assert recomputed != stale_rung
