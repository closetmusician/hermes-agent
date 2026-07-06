# ABOUTME: RED-first tests for the spec-first two-stage flow (P2-e). Feature jobs
# ABOUTME: produce a schema'd stage-1 spec BEFORE any implement launch; quick jobs
# ABOUTME: SKIP stage-1. The stage-1 spec is persisted (reviewable/held) and its
# ABOUTME: budget is the small tier, not the implement budget. Plain-code sequencing,
# ABOUTME: no worker stub needed for the plan/persist logic.
"""
Tests for factory.two_stage.

Design source: docs/plans/harness/fable/p2/P2-design.md §2.4 + §5 (P2-e RED tests).
"""

import json

import pytest

from factory.two_stage import (
    STAGE1_BUDGET_USD,
    SpecArtifactInvalid,
    Stage,
    implement_run_spec,
    persist_spec_artifact,
    plan_stages,
    spec_artifact_path,
    spec_run_spec,
    validate_spec_artifact,
)
from factory.worker_runner import WorkerRunSpec


def _base_spec(**over):
    base = dict(
        job_id="job7",
        repo="acme",
        base_branch="main",
        spec_text="build feature Z",
        worker="claude",
        model="claude-x",
        budget_usd=5.0,
        timeout_min=30,
        slug="feat-z",
        model_key="sk-ant-fake",
    )
    base.update(over)
    return WorkerRunSpec(**base)


# P2-e #1 (★): feature → stage-1 SPEC precedes IMPLEMENT.
def test_feature_plans_spec_before_implement():
    plan = plan_stages("feature")
    assert plan.stages == [Stage.SPEC, Stage.IMPLEMENT]
    assert plan.stages.index(Stage.SPEC) < plan.stages.index(Stage.IMPLEMENT)
    assert plan.skips_stage1 is False


# P2-e #2 (★): quick → SKIPS stage-1; first launch is implement.
def test_quick_skips_stage1():
    plan = plan_stages("quick")
    assert plan.stages == [Stage.IMPLEMENT]
    assert Stage.SPEC not in plan.stages
    assert plan.skips_stage1 is True
    assert plan.stages[0] == Stage.IMPLEMENT


def test_unknown_kind_defaults_to_feature_spec_first():
    """Fail safe toward MORE review: an unknown kind gets stage-1."""
    plan = plan_stages("")
    assert Stage.SPEC in plan.stages


# P2-e #4: stage-1 budget is the small tier, not the implement budget.
def test_stage1_uses_small_budget():
    base = _base_spec(budget_usd=5.0)
    s1 = spec_run_spec(base)
    assert s1.budget_usd == STAGE1_BUDGET_USD
    assert s1.budget_usd < base.budget_usd
    assert "spec" in s1.slug  # distinct worktree/branch


def test_implement_keeps_full_budget():
    base = _base_spec(budget_usd=5.0)
    s2 = implement_run_spec(base)
    assert s2.budget_usd == 5.0


def test_implement_binds_approved_spec():
    base = _base_spec()
    s2 = implement_run_spec(base, approved_spec_text='{"title":"Z"}')
    assert s2.extra_prompt is not None
    assert "Z" in s2.extra_prompt


# P2-e #3: stage-1 spec is a persisted, validated artifact (reviewable/held).
def test_persist_stage1_spec_writes_artifact(tmp_path):
    raw = json.dumps(
        {"title": "Z", "approach": "do it", "acceptance": ["works"]}
    )
    path = persist_spec_artifact(str(tmp_path), "job7", raw)
    assert path == spec_artifact_path(str(tmp_path), "job7")
    on_disk = json.loads(open(path).read())
    assert on_disk["title"] == "Z"


def test_persist_rejects_malformed_spec(tmp_path):
    raw = json.dumps({"title": "Z"})  # missing approach + acceptance
    with pytest.raises(SpecArtifactInvalid):
        persist_spec_artifact(str(tmp_path), "job7", raw)


def test_persist_rejects_non_json(tmp_path):
    from factory.job_schema import WorkerResultInvalid

    with pytest.raises(WorkerResultInvalid):
        persist_spec_artifact(str(tmp_path), "job7", "not json")


def test_validate_spec_artifact_happy():
    obj = {"title": "T", "approach": "A", "acceptance": ["x"]}
    assert validate_spec_artifact(obj) == obj
