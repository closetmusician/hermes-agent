# merge-policy.md — factory per-repo merge flow

<!-- ABOUTME: Example per-repo merge policy consumed by factory/merge_policy.py -->
<!-- ABOUTME: (P2 design §4.2). One flow per repo; the broker merge executor reads -->
<!-- ABOUTME: this to decide HOW a cleared factory job merges. allow_openrouter -->
<!-- ABOUTME: defaults false so work/Diligent repos never route to 3rd-party model -->
<!-- ABOUTME: hosts. Copy this into a repo and edit the values; keep the YAML fence. -->

This file configures how the factory merges a job that has cleared the
test+review gauntlet. It is loaded by `factory.merge_policy.load_merge_policy`.
The safety wall is elsewhere (every merge is **held** for owner approval and the
diff must pass the immutable-ring gate); the fields below only choose the *flow*.

```yaml
repo: hermes
flow: local-merge            # local-merge = fetch + rebase + --ff-only merge;
                             # draft-pr    = gh pr create --draft then gh pr merge
base_branch: main
protected_branches: [main, master]   # merging here is only ever possible via the
                                     # held + nonce-approved broker path (belt; the
                                     # wall is held + ring, not this list)
allow_openrouter: false      # per-repo: work/Diligent repos NEVER route to a
                             # third-party model host. Present now; the P4 router
                             # enforces it. Default false is the safe default.
require_review: true         # run the codex review gate before building the
                             # approval card (default on)
```

## Field reference

| Field | Type | Default | Meaning |
|---|---|---|---|
| `repo` | string (required) | — | Repo slug this policy applies to. |
| `flow` | `local-merge` \| `draft-pr` | `local-merge` | How the executor merges. Unknown values are rejected (fail-closed). |
| `base_branch` | string | `main` | The branch the job's branch rebases onto and merges into. |
| `protected_branches` | list[string] | `[main, master]` | Branches for which an ungated merge is impossible. |
| `allow_openrouter` | bool | `false` | Whether this repo may route to a third-party model host (enforced P3/P4). |
| `require_review` | bool | `true` | Whether the codex review gate runs before the approval card. |

A repo with **no** `merge-policy.md` falls back to `factory.merge_policy.default_policy`
— the same conservative flow (`local-merge`, review on, openrouter **off**). The
absence of a policy file never grants extra reach; opting into `draft-pr` or
`allow_openrouter: true` requires an explicit file.
