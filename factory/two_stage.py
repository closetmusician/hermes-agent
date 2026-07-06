# ABOUTME: The spec-first two-stage flow (P2 design §2.4, task P2-e). A feature-class
# ABOUTME: job runs Stage 1 (a cheap, small-budget spec-draft worker producing a
# ABOUTME: schema'd spec that is PERSISTED and held for one owner tap) BEFORE Stage 2
# ABOUTME: (the implement worker runs against the approved spec). kind:quick skips
# ABOUTME: Stage 1 entirely and goes straight to implement.
"""
Spec-first two-stage flow.

Design authoritative source: docs/plans/harness/fable/p2/P2-design.md §2.4

Two job kinds drive the plan:
  * ``feature`` — Stage 1 spec-draft (small model, tiny budget) → persisted,
    reviewable/held → Stage 2 implement against the approved spec.
  * ``quick``   — Stage 1 SKIPPED; the first launch is the implement worker.

This module is plain code (no AI in the loop). It produces the *plan* of stages
and persists the Stage-1 artifact; the supervisor drives the actual launches via
the WorkerRunner. Keeping the launch mechanics in the runner (and the sequencing
here) keeps files disjoint and the two-stage logic unit-testable in isolation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from factory.job_schema import WorkerResultInvalid
from factory.worker_runner import WorkerRunSpec

# Stage-1 spec-draft budget tier (design §2.4: ~$0.20–0.50, small model). This is
# the CHEAP cap — distinct from the (larger) implement budget on the job row.
STAGE1_BUDGET_USD = 0.50
STAGE1_TIMEOUT_MIN = 5

# The Stage-1 spec artifact schema — a minimal, schema'd spec draft. Kept small
# and separate from the worker-RESULT schema (job_schema.py): this validates the
# spec the owner reviews, not the implement worker's outcome.
_SPEC_REQUIRED_FIELDS = ("title", "approach", "acceptance")


class Stage(Enum):
    """
    The two stages of a feature job (plus the single stage of a quick job).

    SPEC      — Stage 1: cheap spec-draft worker; output persisted + held.
    IMPLEMENT — Stage 2 (or the sole stage for quick): the implement worker.
    """

    SPEC = "spec"
    IMPLEMENT = "implement"


class SpecArtifactInvalid(Exception):
    """
    Raised when a persisted Stage-1 spec draft is missing required fields.

    Purpose: a spec the owner is asked to approve must be well-formed; a malformed
    draft parks the job rather than presenting garbage for approval.
    Usage: raised by validate_spec_artifact / persist_spec_artifact.
    """


@dataclass
class StagePlan:
    """
    The ordered plan of stages for one job, decided from its kind.

    Purpose: tell the supervisor exactly which worker launches to sequence and
    whether a Stage-1 spec must be produced+approved first.
    Usage: plan = plan_stages(kind="feature"); for stage in plan.stages: ...
    Gotchas: for ``quick`` the stages list is [IMPLEMENT] only (no SPEC) — the
    ``skips_stage1`` flag is the falsifiable proof the design's §2.4 test checks.
    """

    kind: str
    stages: List[Stage] = field(default_factory=list)

    @property
    def skips_stage1(self) -> bool:
        """True iff this plan has NO Stage-1 spec draft (quick jobs)."""
        return Stage.SPEC not in self.stages


def plan_stages(kind: str) -> StagePlan:
    """
    Decide the stage plan from the job kind (design §2.4).

    Purpose: feature-class → [SPEC, IMPLEMENT]; quick → [IMPLEMENT] (skip Stage 1).
    Usage: plan = plan_stages(job.kind)
    Gotchas: any kind other than 'quick' is treated as feature-class (default is
    spec-first) — the design's default for feature-class is Stage-1-first, so an
    unknown/blank kind fails safe toward MORE review, not less.
    """
    normalized = (kind or "").strip().lower()
    if normalized == "quick":
        return StagePlan(kind="quick", stages=[Stage.IMPLEMENT])
    return StagePlan(kind="feature", stages=[Stage.SPEC, Stage.IMPLEMENT])


def spec_run_spec(base: WorkerRunSpec) -> WorkerRunSpec:
    """
    Derive the Stage-1 (spec-draft) WorkerRunSpec from the job's base run spec.

    Purpose: the spec-draft worker runs with the SMALL budget/timeout tier and a
    spec-drafting prompt — NOT the implement budget. Same repo/worktree/model
    plumbing, cheaper caps.
    Usage: s1 = spec_run_spec(base_run_spec); runner.launch(s1)
    Gotchas: the returned spec carries STAGE1_BUDGET_USD (not base.budget_usd) —
    asserting this is the design §2.4 test #4 (stage-1 budget is the small tier).
    The slug is suffixed '-spec' so the Stage-1 worktree/branch is distinct.
    """
    prompt = (
        "Produce ONLY a short implementation spec as JSON with keys "
        '"title", "approach", "acceptance" (a list of acceptance checks). '
        "Do NOT write code. The task:\n\n"
        f"{base.spec_text}"
    )
    return WorkerRunSpec(
        job_id=base.job_id,
        repo=base.repo,
        base_branch=base.base_branch,
        spec_text=prompt,
        worker=base.worker,
        model=base.model,
        budget_usd=STAGE1_BUDGET_USD,
        timeout_min=STAGE1_TIMEOUT_MIN,
        slug=f"{base.slug}-spec",
        model_key=base.model_key,
        allowed_tools=base.allowed_tools,
    )


def implement_run_spec(
    base: WorkerRunSpec, *, approved_spec_text: Optional[str] = None
) -> WorkerRunSpec:
    """
    Derive the Stage-2 (implement) WorkerRunSpec, optionally binding the approved spec.

    Purpose: the implement worker runs with the job's FULL budget; for a feature
    job it receives the approved Stage-1 spec as extra prompt context.
    Usage: s2 = implement_run_spec(base, approved_spec_text=spec_json)
    Gotchas: for quick jobs approved_spec_text is None (there was no Stage 1). The
    implement worker keeps the base budget/timeout (the real, larger caps).
    """
    extra = None
    if approved_spec_text:
        extra = f"Approved spec:\n{approved_spec_text}\n\nImplement it."
    return WorkerRunSpec(
        job_id=base.job_id,
        repo=base.repo,
        base_branch=base.base_branch,
        spec_text=base.spec_text,
        worker=base.worker,
        model=base.model,
        budget_usd=base.budget_usd,
        timeout_min=base.timeout_min,
        slug=base.slug,
        model_key=base.model_key,
        allowed_tools=base.allowed_tools,
        extra_prompt=extra,
    )


def validate_spec_artifact(obj: dict) -> dict:
    """
    Purpose: validate a Stage-1 spec draft has the required review fields.
    Usage: clean = validate_spec_artifact(json.loads(worker_output))
    Gotchas: raises SpecArtifactInvalid listing missing fields; a draft the owner
    cannot meaningfully review is parked, not presented.
    """
    missing = [f for f in _SPEC_REQUIRED_FIELDS if f not in obj]
    if missing:
        raise SpecArtifactInvalid(f"stage-1 spec missing required fields: {missing}")
    return obj


def spec_artifact_path(worktrees_root: str, job_id: str) -> str:
    """
    Purpose: the on-disk location of a job's persisted Stage-1 spec artifact.
    Usage: path = spec_artifact_path(worktrees_root, job.id)
    Gotchas: lives under the worktrees root (not the worktree itself) so it
    survives worktree teardown — it is the reviewable record of what was approved.
    """
    return os.path.join(worktrees_root, "specs", f"{job_id}-stage1-spec.json")


def persist_spec_artifact(worktrees_root: str, job_id: str, raw_output: str) -> str:
    """
    Validate + persist a Stage-1 spec draft BEFORE Stage 2 may run (design §2.4).

    Purpose: parse the spec-draft worker's output, validate it, and write it to a
    durable artifact path. The persisted file is the reviewable/held record; Stage
    2 must not launch until it exists (the supervisor gates on this).
    Usage: path = persist_spec_artifact(worktrees_root, job.id, worker_output)
    Gotchas:
      * raises SpecArtifactInvalid / WorkerResultInvalid on unparseable or
        malformed output — Stage 2 is then NOT reached (fail-closed).
      * atomic write (temp + os.replace) so a crash mid-write never leaves a
        half-spec that Stage 2 might read as approved.
    """
    text = (raw_output or "").strip()
    if not text:
        raise WorkerResultInvalid("stage-1 spec worker produced no output")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkerResultInvalid(f"stage-1 spec is not valid JSON: {exc}") from exc
    validate_spec_artifact(obj)

    path = spec_artifact_path(worktrees_root, job_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)  # atomic — the artifact appears whole or not at all
    return path
