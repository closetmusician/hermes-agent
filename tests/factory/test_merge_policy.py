# ABOUTME: RED-first tests for factory.merge_policy (P2 design §4.2, REQ-05). A repo's
# ABOUTME: merge-policy.md drives the merge flow; allow_openrouter defaults false so
# ABOUTME: work repos never route to 3rd-party model hosts. Fail-closed: unknown flow
# ABOUTME: and missing repo are rejected, never coerced to the most permissive option.
"""
Tests for the per-repo merge-policy schema + loader (REQ-05).

Covers: the shipped docs/factory/merge-policy.md parses; allow_openrouter is
present and defaults false; an unknown flow is rejected (fail-closed); optional
fields fall back to safe defaults; the loader reads a file; the default_policy
fallback is conservative.
"""

from pathlib import Path

import pytest

from factory.merge_policy import (
    FLOW_DRAFT_PR,
    FLOW_LOCAL_MERGE,
    MergePolicy,
    MergePolicyInvalid,
    default_policy,
    load_merge_policy,
    parse_merge_policy,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_shipped_example_policy_parses_with_allow_openrouter():
    """The seeded docs/factory/merge-policy.md parses and carries allow_openrouter."""
    policy = load_merge_policy(REPO_ROOT / "docs" / "factory" / "merge-policy.md")
    assert policy.repo == "hermes"
    assert policy.flow == FLOW_LOCAL_MERGE
    assert policy.base_branch == "main"
    # allow_openrouter is PRESENT and false for the work repo (REQ-05 done-when).
    assert policy.allow_openrouter is False
    assert policy.require_review is True


def test_allow_openrouter_defaults_false_when_omitted():
    """A policy omitting allow_openrouter defaults to false (safe default)."""
    policy = parse_merge_policy("repo: acme\nflow: local-merge\n")
    assert policy.allow_openrouter is False


def test_allow_openrouter_true_when_opted_in():
    policy = parse_merge_policy("repo: oss\nallow_openrouter: true\n")
    assert policy.allow_openrouter is True


def test_draft_pr_flow_accepted():
    policy = parse_merge_policy("repo: acme\nflow: draft-pr\n")
    assert policy.flow == FLOW_DRAFT_PR


def test_unknown_flow_rejected_fail_closed():
    """An unknown flow is rejected — never coerced to local-merge."""
    with pytest.raises(MergePolicyInvalid):
        parse_merge_policy("repo: acme\nflow: yolo-merge\n")


def test_missing_repo_rejected():
    with pytest.raises(MergePolicyInvalid):
        parse_merge_policy("flow: local-merge\n")


def test_non_mapping_rejected():
    with pytest.raises(MergePolicyInvalid):
        parse_merge_policy("- just\n- a\n- list\n")


def test_fenced_yaml_block_extracted():
    """A fenced ```yaml block inside prose is extracted and parsed."""
    md = "# policy\n\nsome prose\n\n```yaml\nrepo: acme\nflow: draft-pr\n```\n\nmore prose\n"
    policy = parse_merge_policy(md)
    assert policy.repo == "acme"
    assert policy.flow == FLOW_DRAFT_PR


def test_default_policy_is_conservative():
    """The no-file fallback is local-merge + review-on + openrouter-off."""
    policy = default_policy("acme")
    assert policy.flow == FLOW_LOCAL_MERGE
    assert policy.require_review is True
    assert policy.allow_openrouter is False
    assert policy.is_protected("main") is True
    assert policy.is_protected("feature/x") is False


def test_protected_branches_default_main_master():
    policy = parse_merge_policy("repo: acme\n")
    assert "main" in policy.protected_branches
    assert "master" in policy.protected_branches


def test_protected_branches_must_be_list_of_strings():
    with pytest.raises(MergePolicyInvalid):
        parse_merge_policy("repo: acme\nprotected_branches: 42\n")
