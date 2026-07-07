# ABOUTME: Per-repo merge-policy schema + loader (P2 design §4.2, task P2-g). One
# ABOUTME: flow per repo — how the broker merge executor merges a cleared factory
# ABOUTME: job. Carries allow_openrouter (default False so work/Diligent repos never
# ABOUTME: route to third-party model hosts), the merge flow, the base + protected
# ABOUTME: branches, and whether the codex review gate is required.
"""
Merge-policy schema and loader.

Design authoritative source: docs/plans/harness/fable/p2/P2-design.md §4.2

A repo's ``merge-policy.md`` (a YAML block; the ``.md`` extension keeps it human
-browsable alongside the other docs/factory/*.md) drives the broker merge flow:

    repo: <slug>
    flow: local-merge | draft-pr   # local-merge = fetch+rebase+ff-only;
                                    # draft-pr = gh pr create --draft then merge
    base_branch: main
    protected_branches: [main, master]  # ungated merge here is impossible
                                        # (the wall is held + ring, not this field)
    allow_openrouter: false             # per-repo: work repos never route to
                                        # 3rd-party model hosts (enforced P3/P4)
    require_review: true                # codex review gate on/off (default on)

``load_merge_policy`` parses that block and returns a validated ``MergePolicy``.
Unknown/missing keys fail-closed toward the SAFE default (local-merge, review on,
openrouter OFF) so a malformed policy never silently widens the merge flow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# The two merge flows the executor understands (design §4.2).  An unknown flow
# is rejected at load time — the merge path must never guess a flow.
FLOW_LOCAL_MERGE = "local-merge"
FLOW_DRAFT_PR = "draft-pr"
_KNOWN_FLOWS = frozenset({FLOW_LOCAL_MERGE, FLOW_DRAFT_PR})

# Safe defaults for optional fields.  These bias toward MORE gating / LESS reach:
# review on, openrouter off, main+master protected.
_DEFAULT_PROTECTED = ("main", "master")


class MergePolicyInvalid(Exception):
    """
    Raised when a merge-policy block is malformed or names an unknown flow.

    Purpose: give the merge path a single catchable error so a bad policy parks
    the job rather than merging with a guessed flow.
    Usage: raised by load_merge_policy / parse_merge_policy.
    Gotchas: this is fail-closed — a missing required key or an unknown flow is
    an error, never silently defaulted to the most permissive option.
    """


@dataclass(frozen=True)
class MergePolicy:
    """
    A validated per-repo merge policy.

    Purpose: the immutable config the broker merge executor consults to decide
    HOW to merge a cleared factory job (design §4.2).
    Usage: policy = load_merge_policy(path); if policy.require_review: ...
    Gotchas: allow_openrouter defaults False — work/Diligent repos must never
    route to a third-party model host; the field is present now but only the P4
    router enforces it. protected_branches is a belt (the wall is held + ring).
    """

    repo: str
    flow: str = FLOW_LOCAL_MERGE
    base_branch: str = "main"
    protected_branches: List[str] = field(default_factory=lambda: list(_DEFAULT_PROTECTED))
    allow_openrouter: bool = False
    require_review: bool = True

    def is_protected(self, branch: str) -> bool:
        """
        Purpose: test whether a branch is protected under this policy.
        Usage: if policy.is_protected(target): the merge must be held + gated.
        Gotchas: comparison is on the plain branch name; a caller passing
        'origin/main' should strip the remote prefix first.
        """
        return branch in self.protected_branches


def _extract_yaml_block(text: str) -> str:
    """
    Purpose: pull the YAML config out of a merge-policy.md file, tolerating a
    fenced ```yaml block or a bare YAML body.
    Usage: block = _extract_yaml_block(md_text)
    Gotchas: returns the whole text when no fence is present (a bare-YAML .md is
    valid); a fenced block wins when present so prose around it is ignored.
    """
    fence = re.search(r"```(?:ya?ml)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    return text


def parse_merge_policy(text: str) -> MergePolicy:
    """
    Parse + validate a merge-policy YAML/markdown body into a MergePolicy.

    Purpose: the pure-string entry point (no filesystem) so it is unit-testable
    from an inline block; load_merge_policy wraps this over a file.
    Usage: policy = parse_merge_policy(open(path).read())
    Gotchas:
      * ``repo`` is REQUIRED — a policy with no repo cannot be matched to a job.
      * ``flow`` must be one of _KNOWN_FLOWS — an unknown flow is fail-closed
        (MergePolicyInvalid), never coerced to local-merge.
      * optional fields fall back to SAFE defaults (review on, openrouter off).
    """
    block = _extract_yaml_block(text)
    try:
        data: Any = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise MergePolicyInvalid(f"merge-policy is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise MergePolicyInvalid("merge-policy must be a YAML mapping")

    repo = data.get("repo")
    if not repo or not isinstance(repo, str):
        raise MergePolicyInvalid("merge-policy is missing a non-empty 'repo'")

    flow = data.get("flow", FLOW_LOCAL_MERGE)
    if flow not in _KNOWN_FLOWS:
        raise MergePolicyInvalid(
            f"unknown merge flow {flow!r}; known: {sorted(_KNOWN_FLOWS)}"
        )

    protected = data.get("protected_branches")
    if protected is None:
        protected = list(_DEFAULT_PROTECTED)
    elif not isinstance(protected, list) or not all(isinstance(b, str) for b in protected):
        raise MergePolicyInvalid("'protected_branches' must be a list of strings")

    return MergePolicy(
        repo=repo,
        flow=flow,
        base_branch=str(data.get("base_branch", "main")),
        protected_branches=list(protected),
        # allow_openrouter defaults False — the SAFE default for work repos.
        allow_openrouter=bool(data.get("allow_openrouter", False)),
        require_review=bool(data.get("require_review", True)),
    )


def load_merge_policy(path: Path) -> MergePolicy:
    """
    Load + validate a repo's merge-policy.md from disk.

    Purpose: the filesystem entry point the merge path calls to learn a repo's
    flow before merging (design §4.2).
    Usage: policy = load_merge_policy(Path("docs/factory/merge-policy.md"))
    Gotchas: raises FileNotFoundError if the policy is absent (the caller decides
    whether to fall back to a conservative default or park the job); raises
    MergePolicyInvalid on a malformed body.
    """
    text = Path(path).read_text(encoding="utf-8")
    return parse_merge_policy(text)


def default_policy(repo: str) -> MergePolicy:
    """
    Purpose: the conservative fallback policy when a repo has no merge-policy.md.
    Usage: policy = default_policy(repo) when load_merge_policy raises FileNotFound.
    Gotchas: local-merge + review-on + openrouter-OFF + main/master protected —
    the safest flow. A repo that wants draft-pr or openrouter must OPT IN via an
    explicit policy file; the absence of a file never grants extra reach.
    """
    return MergePolicy(repo=repo)
