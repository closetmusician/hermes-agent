# ABOUTME: The broker-side EFFECTOR for P6 mission-control cards (P6-a REQ-02/03).
# ABOUTME: On an owner nonce-approval, this executor performs a job-control effect
# ABOUTME: (reject / rescope / reprioritize) through an INJECTED reconciler callback.
# ABOUTME: The broker imports NOTHING from factory — the reconciler is a plain object
# ABOUTME: passed in at server construction, exactly mirroring the merge_gate injection.
# ABOUTME: This is the ONLY path a control card can mutate the fleet, and it runs ONLY
# ABOUTME: after ApprovalAuthority validated a broker-minted, out-of-band nonce.
"""
Control executor — where a mission-control card's effect actually happens.

Design authoritative source: docs/plans/harness/fable/p6/P6-design.md §1.1–1.2.

THE no-bypass boundary (why this module exists):

  A mission-control card is strictly WEAKER than a human tap. The factory-side
  MissionControl layer can only PRODUCE a held broker action (via enqueue_action);
  it holds no writable JobStore and no reference to this reconciler. The reconciler
  lives ONLY in the broker process, injected at construction. So a control card
  cannot reach JobStore.transition / the scheduler directly — the sole path is:

      card -> broker held action -> owner taps Telegram button (out-of-band nonce)
           -> ApprovalAuthority.approve validates the nonce -> THIS executor runs
           -> reconciler.<verb>(...) performs the one state change.

  Remove the nonce gate (in ApprovalAuthority) and the no-bypass control test
  fails; give MissionControl a writable store and its structural test fails.
  This executor never mints a nonce and never approves — it is only the effector.

Fail-closed everywhere: an unknown control_verb, a malformed payload, a missing
reconciler, or a reconciler that raises (an illegal job-state transition) all
result in an EXCEPTION — which ApprovalAuthority.approve turns into a 'failed'
action with NO state change. The fleet is never mutated on a bad control card.

The P2-3 review fix (QUEUED reject) lives in the reconciler, not here: this
executor calls reconciler.reject(job_id) and the factory-side reconciler maps a
QUEUED job to FAILED (the allowed edge) rather than the illegal NEEDS_ATTENTION.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict

# NOTE: this module imports NOTHING from factory — the factory→broker layering is
# preserved exactly as merge_gate / retro_diff_executor preserve it. The reconciler
# is a plain object injected at construction; the scan+fence of a re-scope spec
# happens on the factory side (MissionControl produces an already-fenced new_spec,
# and the reconciler re-enqueues through the scan-fenced intake seam).

# The reconciler is a narrow factory-side object injected at broker construction.
# The broker imports NOTHING from factory (this type is structural only): the
# reconciler must expose reject(job_id), rescope(job_id, new_spec) and
# reprioritize(job_id, delta). It is the ONLY thing that mutates job state.
Reconciler = Any

# Map from control_verb -> the reconciler method + how to call it. Any verb not
# in this map fails closed (no reconciler method is invoked).
_KNOWN_VERBS = frozenset({"reject", "rescope", "reprioritize"})


def build_control_executor(reconciler: Reconciler) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """
    Build the broker-side control executor bound to an injected reconciler.

    Purpose: construct the callable the broker runs on owner approval of a held
    job_reject / job_rescope / queue_reprioritize card. It validates the control
    intent, then calls the ONE matching reconciler method — the effect happens
    there, never here and never in the assistant layer.
    Usage: executor = build_control_executor(reconciler); broker executors dict maps
    "job_reject"/"job_rescope"/"queue_reprioritize" -> executor.
    Gotchas:
      * FAIL-CLOSED: no reconciler => refuse to build (a broker with no effector
        can never mutate the fleet). Unknown verb / bad payload / a reconciler
        raise => this executor raises => ApprovalAuthority marks the action failed
        with NO state change.
      * The nonce wall lives in ApprovalAuthority, NOT here — this executor only
        runs AFTER a valid broker-minted nonce approved the action. It never mints
        or approves. Removing the nonce check is what the no-bypass test catches.
      * rescope re-scans + fences the new_spec BEFORE handing it to the reconciler
        so an injection marker in a re-scope spec is quarantined, preserving the
        single-scan boundary on the control path.
    """
    if reconciler is None:
        raise ValueError("control_executor requires a reconciler (fail-closed: no effector)")

    def _execute(row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Effect one owner-approved control card through the injected reconciler.

        Purpose: the broker-side effector. Parses the control intent, dispatches to
        the single matching reconciler method, returns a result dict.
        Usage: called by ApprovalAuthority.approve after nonce validation.
        Gotchas: raises on unknown verb / bad payload / reconciler error — the
        approval path turns that into a 'failed' action, leaving the fleet unchanged.
        A rescope spec is injection-scanned + fenced before re-enqueue.
        """
        payload = row.get("payload") or "{}"
        try:
            intent = json.loads(payload) if isinstance(payload, str) else dict(payload)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"control payload is not valid JSON: {exc}") from exc

        job_id = intent.get("job_id")
        verb = intent.get("control_verb")
        args = intent.get("args") or {}

        if not job_id:
            raise ValueError("control intent missing job_id")
        if verb not in _KNOWN_VERBS:
            raise ValueError(f"unknown control_verb {verb!r}; known: {sorted(_KNOWN_VERBS)}")

        if verb == "reject":
            reconciler.reject(job_id)
            return {"status": "controlled", "verb": "reject", "job_id": job_id}

        if verb == "rescope":
            # The new_spec was scanned + fenced on the factory side (MissionControl)
            # before the card was produced; the reconciler additionally re-enqueues
            # through the scan-fenced intake seam. This executor imports nothing from
            # factory, so it forwards the (already-fenced) spec verbatim.
            new_spec = args.get("new_spec") or ""
            parked = reconciler.rescope(job_id, new_spec)
            return {"status": "controlled", "verb": "rescope", "job_id": job_id, "parked": parked}

        # verb == "reprioritize"
        delta = int(args.get("delta", 0))
        reconciler.reprioritize(job_id, delta)
        return {"status": "controlled", "verb": "reprioritize", "job_id": job_id, "delta": delta}

    return _execute
