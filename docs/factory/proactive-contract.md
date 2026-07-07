# proactive-contract.md — quiet-by-default nudge contract

<!-- RING-PROTECTED: this file is a member of factory/immutable_ring.RING_PATHS.   -->
<!-- A worker diff that edits it is categorically rejected at merge-submission.     -->
<!-- Changes require a human-reviewed maintainer commit (never a worker-authored    -->
<!-- diff).  See factory/immutable_ring.py for details.                             -->

<!-- ABOUTME: Config data for the proactive-contract nudge engine (P6-c REQ-03).   -->
<!-- ABOUTME: Consumed by factory/proactive_contract.py as value-only key:value.   -->
<!-- ABOUTME: Content is DATA the engine reads, never instructions it executes.    -->
<!-- ABOUTME: Edit only via a human-reviewed maintainer commit to this ring member. -->
<!-- ABOUTME: Design authority: docs/plans/harness/fable/p6/P6-design.md §3.3.    -->

Design authority: `docs/plans/harness/fable/p6/P6-design.md` §3.3 (quiet-by-default semantics).

These values are **data** read by `factory/proactive_contract.py`.
Changing them requires a human-reviewed maintainer commit — a worker diff that
touches this file is rejected by the immutable-ring gate at merge-submission.

```yaml
# proactive-contract.md — quiet-by-default nudge contract
# (owner-tunable via REVIEWED maintainer commit only; no worker and no retro
# diff may alter these values — ring-protected, same posture as trust-policy.md).

nudge_max: 1          # at most 1 nudge per anomaly_key, ever
repeat: false         # once a nudge is sent, never send the same anomaly_key again
anomaly_window_min: 60  # lookback window for anomaly deduplication (minutes; advisory)
```

## Contract at a glance

| Parameter | Value | Note |
|---|---|---|
| nudge_max | 1 | Maximum nudges emitted for a single anomaly_key |
| repeat | false | Once a nudge is sent for a key, all further events for that key are suppressed |
| anomaly_window_min | 60 | Advisory window; the durable ledger is the actual enforcement mechanism |

## Quiet semantics (stated as a constraint, not a policy preference)

The factory is **quiet by default**. An anomaly event is notable the first time it
fires; repeated alerts for the same condition are noise, not information. The nudge
engine enforces this via a **durable on-disk ledger** (`nudges(anomaly_key,
first_sent_ts)`). The file is the record — process restarts do not reset the ledger.

A `nudge_max: 1` + `repeat: false` combination means:

1. First occurrence of `anomaly_key` → exactly 1 nudge is emitted.
2. All subsequent occurrences of the **same** `anomaly_key` → 0 nudges (suppressed).
3. A **different** `anomaly_key` → its own 1-nudge allowance, independently.

## Hard gates (code, not config)

The following behaviours are **code** inside `factory/proactive_contract.py` and
are NOT overridable by changing this file:

1. `should_nudge(key) -> bool` — checks the on-disk ledger; returns False if the key
   already exists.
2. `send_nudge(key, summary)` — calls `should_nudge` first; if False, is a no-op;
   if True, emits the nudge and records the key in the ledger atomically.
3. The ledger path is set at construction time; it is never in-memory-only.

Removing these gates breaks the anti-weakening tests in
`tests/factory/test_proactive_contract.py` (quiet_one_nudge + never_repeats_across_restart).
