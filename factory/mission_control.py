# ABOUTME: MissionControl — the factory-side command + notification surface for the
# ABOUTME: P6 mission-control panel (P6-a REQ-01/03). It holds a READ-ONLY job reader
# ABOUTME: (NO JobStore.transition, NO scheduler write handle) and PRODUCES broker held
# ABOUTME: cards via enqueue_action — it mutates NOTHING directly. A control card is
# ABOUTME: strictly weaker than a human tap: it can only REQUEST a state change; only a
# ABOUTME: broker-minted, out-of-band nonce approving the held card EFFECTS it.
"""
MissionControl — mission-control cards for live jobs.

Design authoritative source: docs/plans/harness/fable/p6/P6-design.md §1.1–1.4.

THE no-bypass boundary (why this layer holds no writable store):

  This module is the ONLY producer of control cards, and its sole mutating verb
  is emit_control_card, which goes through broker_client.enqueue_action. It
  deliberately does NOT hold a Scheduler or a writable JobStore — it takes a
  read-only reader (get / list_jobs only). This is the STRUCTURAL guarantee that
  a card cannot bypass the broker: the mutation methods (JobStore.transition,
  enqueue_job, scheduler.reserve_slot) are simply not in scope in this module, so
  there is no code path from a card to a direct state change. Capability is
  removed by construction, not by discipline.

  The effect of an approved card happens broker-side in control_executor, via a
  reconciler that lives ONLY in the broker process and is never referenced here.

  tail() and list_live() are read-only: they read job rows through the reader and
  NEVER call the broker and NEVER mutate. tail is deliberately NOT a broker action
  (a pure read has nothing to gate — making it one would be theatre).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from factory.injection_scan import fence, scan

# Control verb -> broker held-action type. job_approve is deliberately NOT here:
# approving a completed job's PR IS the existing type="merge" card (supervisor),
# reused verbatim — MissionControl adds only the reject/rescope/reprioritize
# siblings. Any verb not in this map is refused before any enqueue (fail-closed).
_VERB_TO_TYPE: Dict[str, str] = {
    "reject": "job_reject",
    "rescope": "job_rescope",
    "reprioritize": "queue_reprioritize",
}


def _is_writable_store(obj: Any) -> bool:
    """
    Purpose: detect a write-capable job store / scheduler handle (the no-bypass guard).
    Usage: internal — MissionControl refuses to be constructed with such an object.
    Gotchas: checks for the mutation surface (transition / enqueue_job /
    reserve_slot); a read-only reader exposes none of these, so it passes.
    """
    return any(hasattr(obj, m) for m in ("transition", "enqueue_job", "reserve_slot"))


class MissionControl:
    """
    Purpose: the factory-side mission-control surface — produce control cards and
    read fleet state for the panel; hold NO capability to mutate state except by
    producing a broker held card.
    Usage: mc = MissionControl(broker_client, job_store_reader);
           aid = mc.emit_control_card(job_id, "reject", {}, requested_by="owner").
    Gotchas:
      * emit_control_card is the ONLY mutating path and it goes through
        broker_client.enqueue_action (held, nonce-gated). It returns the held
        action_id — it changes NO job state itself.
      * The reader MUST be read-only. Construction with a writable JobStore /
        scheduler is REFUSED (fail-closed) so a card can never reach a direct
        transition — the no-bypass structural wall.
      * A rescope's new_spec is scanned + fenced HERE (factory side) before the
        card is produced, preserving the single-scan boundary; the broker-side
        executor imports nothing from factory and forwards it verbatim.
    """

    def __init__(self, broker_client: Any, job_store_reader: Any):
        """
        Wire MissionControl with the broker egress client and a READ-ONLY reader.

        Purpose: construct the surface with capability removed by construction.
        Usage: MissionControl(broker_client, job_store_reader).
        Gotchas: raises if job_store_reader is write-capable (has transition /
        enqueue_job / reserve_slot) — the no-bypass guard is enforced at build time.
        """
        if job_store_reader is None:
            raise ValueError("MissionControl requires a read-only job store reader")
        if _is_writable_store(job_store_reader):
            raise ValueError(
                "MissionControl must be given a READ-ONLY reader, not a writable "
                "JobStore/scheduler — a control card must not reach a direct mutation"
            )
        self._broker = broker_client
        self._reader = job_store_reader

    def emit_control_card(
        self, job_id: str, verb: str, args: Dict[str, Any], *, requested_by: str
    ) -> str:
        """
        Produce a held broker control card for a job-control intent.

        Purpose: the ONLY mutating path — enqueue a held broker action of the
        matching control type; it changes no job state (the effect happens broker
        -side on nonce approval, via control_executor + reconciler).
        Usage: aid = mc.emit_control_card("J1", "reject", {}, requested_by="owner").
        Gotchas: fail-closed on an unknown verb (raises before any enqueue). A
        rescope's new_spec is scanned + fenced here before it enters the card, so an
        injection marker is quarantined as external content, never surfaced as intent.
        Returns the held action_id; disposition is always 'held' (never auto).
        """
        action_type = _VERB_TO_TYPE.get(verb)
        if action_type is None:
            raise ValueError(f"unknown control verb {verb!r}; known: {sorted(_VERB_TO_TYPE)}")

        safe_args = dict(args)
        if verb == "rescope":
            raw_spec = safe_args.get("new_spec") or ""
            # Single-scan boundary: fence a re-scope spec here (factory side) before
            # it enters the card, so an injection payload is quarantined as external
            # content. The broker-side executor forwards it verbatim (no factory import).
            safe_args["new_spec"] = fence(raw_spec, scan(raw_spec))

        payload = json.dumps(
            {
                "job_id": job_id,
                "control_verb": verb,
                "args": safe_args,
                "requested_by": requested_by,
                "ts": int(time.time()),
            }
        )
        summary = f"control:{verb} job {job_id} (by {requested_by})"
        result = self._broker.enqueue_action(
            type=action_type,
            summary=summary,
            payload=payload,
            origin=f"mission_control:{job_id}",
        )
        return result["action_id"]

    def tail(self, job_id: str) -> str:
        """
        Read-only tail of a job's worker output (P6-a REQ-03).

        Purpose: surface the job's log_tail to the owner's panel — a PURE READ.
        Usage: text = mc.tail("J1").
        Gotchas: NEVER calls the broker and NEVER mutates — tail is deliberately NOT
        a broker action (nothing to gate). Returns "" for an unknown job or empty tail.
        """
        row = self._reader.get(job_id)
        if row is None:
            return ""
        return row.get("log_tail") or ""

    def list_live(self) -> List[Dict[str, Any]]:
        """
        Read-only fleet snapshot for the mission-control panel.

        Purpose: list current jobs for the panel display — a PURE READ.
        Usage: rows = mc.list_live().
        Gotchas: read-only via the reader; no broker call, no mutation. Returns a
        snapshot (the store may change between calls).
        """
        return list(self._reader.list_jobs())
