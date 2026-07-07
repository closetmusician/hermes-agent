# trust-policy.md — pinned graduation thresholds

<!-- RING-PROTECTED: this file is a member of factory/immutable_ring.RING_PATHS.    -->
<!-- A worker diff that edits it is categorically rejected at merge-submission.      -->
<!-- Changes require a human-reviewed maintainer commit (never a worker-authored     -->
<!-- diff).  See factory/immutable_ring.py and P1b-design.md §3.3 for details.       -->

<!-- ABOUTME: Pinned thresholds for the earned-trust autonomy boundary (P1b-b REQ-02). -->
<!-- ABOUTME: Consumed by factory/trust_policy.py:load_trust_policy(). Values are    -->
<!-- ABOUTME: read as DATA by the policy engine — not as instructions to any agent.  -->
<!-- ABOUTME: Edit only via a human-reviewed maintainer commit to this ring member.  -->
<!-- ABOUTME: Design authority: docs/plans/harness/fable/p1b/P1b-design.md §3.1.    -->

Design authority: `docs/plans/harness/fable/p1b/P1b-design.md` §3.1 (pinned thresholds).

These values are **data** read by `factory/trust_policy.py:load_trust_policy()`.
Changing them requires a human-reviewed maintainer commit — a worker diff that
touches this file is rejected by the immutable-ring gate at merge-submission.

```yaml
# trust-policy.md — pinned graduation thresholds (owner-tunable via REVIEWED
# maintainer commit only; no worker and no retro diff may alter these values).

tiers:
  tier1:                            # auto-merge, personal non-production only
    consecutive_merged_clean: 10    # ≥10 consecutive merged_clean, per repo × task_type
    max_reverts: 0                  # 0 reverts ever in the window
    min_window_days: 30             # ≥30 days wall-clock (STAGED — see STAGED-GATES.md SG-P1b-1)
    min_confidence: 0.0             # AND-condition once P4.6 lands; 0.0 = inert until then

  # tier2 (broader) intentionally ABSENT at launch — L1/OQ1 = ask-for-everything.
  # tier-1 is the ceiling at launch.

revoke:
  on_rejected: true                 # first human-rejected outcome → tier-0 (streak reset)
  on_reverted: true                 # first post-merge regression → tier-0 (streak reset)

# Reference to the capability denylist (read by the policy engine, never re-authored here).
never_graduates_ref: docs/factory/never-graduates.md

# Hard-coded tier-0 for work/Diligent repos (cannot be overridden by profile or record).
work_repo_hard_tier0: true
```

## Thresholds at a glance

| Threshold | Value | Note |
|---|---|---|
| consecutive_merged_clean | 10 | Trailing run of clean merges; any non-clean resets to 0 |
| max_reverts | 0 | Any revert in the window → tier-0 immediately |
| min_window_days | 30 | Wall-clock span from first streak row to now (STAGED) |
| min_confidence | 0.0 | Inert until P4.6 confidence scores land |

## Hard gates (code, not config)

The following gates are **code** inside `factory/trust_policy.py:compute_tier` and
are NOT overridable by changing this file:

1. **Work / Diligent repos** — `repo_class == "work"` or any known Diligent org prefix
   → tier-0 forever, regardless of record.
2. **Never-graduates capabilities** — any capability in `docs/factory/never-graduates.md`
   → tier-0 regardless of record.
3. **Unknown / `other` task type** — any `task_type` not in `{bugfix, feature, refactor,
   test, docs}` → tier-0 (fail-safe for misclassification).

Removing any of these gates breaks the anti-weakening tests in
`tests/factory/test_trust_policy.py` (AT-WORK-1 and its siblings).
