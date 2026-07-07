# ABOUTME: Mission-control panel wiring for P6-d — surface-level glue connecting
# ABOUTME: the Jira intake pipeline (factory/jira_poller.py) and the morning
# ABOUTME: briefing pipeline (factory/morning_packet.py) into the factory panel.
# ABOUTME: PARKED pipelines (pulse, weekly, exec-narrative) are NOT wired here.
# ABOUTME: Any action that mutates state goes through broker_client.enqueue_action.
"""
Panel wiring — surface glue that connects pm_os-used factory pipelines to the panel.

Design authoritative source: docs/plans/harness/fable/p6/P6-design.md §4 (REQ-04).

Two pipelines are wired (factory-used):
  * Jira intake  — JiraPanelView wraps job_store to surface Jira-sourced jobs in the
                   panel's live view; broker-gated write-back for status transitions.
  * Briefing     — BriefingPanelView wraps morning_packet.build() so the panel's
                   standup view reads the P4 morning packet.

Three pipelines are explicitly PARKED (NOT wired, per design §4):
  * pulse
  * weekly
  * exec-narrative

No-bypass invariant (REQ-03): any panel action that mutates state is submitted via
broker_client.enqueue_action(), never as a direct mutation from the panel layer.
The panel holds a READ-ONLY reference to the JobStore (read via list_jobs/get_job);
the only writable path is the broker call.

Gotchas:
  * Both views accept broker_client=None for read-only usage (listing, display).
    The write-back methods raise BrokerRequired if broker_client is None.
  * This module does NOT import mission_control (P6-a, may not be merged yet).
    It is a standalone surface-glue module; mission_control.py will import this.
  * Do NOT add pulse/weekly/exec-narrative — they are explicitly parked per §4.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from factory.job_store import JobStore
from factory.morning_packet import MorningPacket, build as _build_morning_packet

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BrokerRequired(RuntimeError):
    """
    Raised when a write-back action is requested but no broker_client was provided.

    Purpose: fail-closed sentinel so a read-only panel view cannot accidentally
    attempt a broker call with a None client.
    Usage: raised automatically by write-path methods when broker_client is None.
    Gotchas: callers that only read (live_jira_jobs, standup_packet) never hit this.
    """


# ---------------------------------------------------------------------------
# JiraPanelView
# ---------------------------------------------------------------------------


class JiraPanelView:
    """
    Surface the Jira intake pipeline in the mission-control panel.

    Purpose: provides a read-only live view of Jira-sourced jobs (from
    jira_poller intake) and a broker-gated write-back for Jira status updates.
    Usage:
        view = JiraPanelView(store=store, broker_client=client)
        jobs = view.live_jira_jobs()        # read-only
        aid  = view.trigger_jira_writeback(job_id=..., ticket_key=..., status=..., ...)
    Gotchas:
      * broker_client=None is valid for read-only use; trigger_jira_writeback raises
        BrokerRequired if called with no client.
      * The store reference is used READ-ONLY here (list_jobs). No transition/enqueue
        is called — the no-bypass invariant is maintained structurally.
    """

    def __init__(
        self,
        *,
        store: JobStore,
        broker_client: Optional[Any],
    ) -> None:
        """
        Purpose: construct the view with the job store and optional broker client.
        Usage: JiraPanelView(store=store, broker_client=client_or_None).
        Gotchas: store is used read-only; broker_client may be None for display-only.
        """
        self._store = store
        self._broker_client = broker_client

    def live_jira_jobs(self) -> List[Dict[str, Any]]:
        """
        Return all jobs whose intake_source is 'jira', across all states.

        Purpose: the panel's Jira intake surface — shows which jobs originated
        from Jira polling so the owner sees the intake queue at a glance.
        Usage: jobs = view.live_jira_jobs()
        Gotchas:
          * Returns jobs in all states (QUEUED, RUNNING, AWAITING_APPROVAL, DONE…).
            The panel renders state; filtering by state is the caller's responsibility.
          * Returns an empty list if no Jira-sourced jobs exist (never raises).
        """
        all_jobs = self._store.list_jobs()
        return [j for j in all_jobs if j.get("intake_source") == "jira"]

    def trigger_jira_writeback(
        self,
        *,
        job_id: str,
        ticket_key: str,
        status: str,
        requested_by: str = "yk",
    ) -> str:
        """
        Enqueue a broker-gated Jira status write-back action for the given job.

        Purpose: the panel's only mutating path — routes a Jira status transition
        through broker_client.enqueue_action() so it goes through the approval
        surface (P6 no-bypass invariant). NEVER writes to Jira directly.
        Usage:
            aid = view.trigger_jira_writeback(
                job_id='j-1', ticket_key='SAND-42',
                status='In Progress', requested_by='yk')
        Gotchas:
          * Raises BrokerRequired if broker_client is None.
          * The returned string is the broker action_id; disposition is always
            'held' (no SafeLane auto-approval for Jira write-backs).
          * The caller does NOT own the nonce — broker mints it and delivers it
            out-of-band (Telegram button); this is the same wall as a merge card.
        """
        if self._broker_client is None:
            raise BrokerRequired(
                "trigger_jira_writeback requires a broker_client; "
                "construct JiraPanelView with broker_client=<client>"
            )

        payload = json.dumps({
            "job_id": job_id,
            "ticket_key": ticket_key,
            "status": status,
            "requested_by": requested_by,
        })
        result = self._broker_client.enqueue_action(
            type="jira_writeback",
            summary=f"[{ticket_key}] Jira status → {status} (job {job_id})",
            payload=payload,
            origin="panel_wiring",
        )
        logger.info(
            "panel_wiring: jira_writeback enqueued — action_id=%s job=%s ticket=%s",
            result.get("action_id"),
            job_id,
            ticket_key,
        )
        return result["action_id"]


# ---------------------------------------------------------------------------
# BriefingPanelView
# ---------------------------------------------------------------------------


class BriefingPanelView:
    """
    Surface the morning briefing pipeline in the mission-control panel.

    Purpose: the panel's standup view — wraps morning_packet.build() so the
    panel reads the P4 morning packet (confidence-ranked overnight job results).
    Usage:
        view = BriefingPanelView(store=store, held_store=hs_or_None)
        packet = view.standup_packet()   # returns MorningPacket
    Gotchas:
      * held_store=None is valid for read-only packet builds (tests, display).
        The MorningPacket.cards will be present; batch_approve will need a real
        held_store if called separately.
      * This view does NOT call batch_approve — batch approval is a separate
        panel action that the panel triggers after owner review.
    """

    def __init__(
        self,
        *,
        store: JobStore,
        held_store: Optional[Any],
    ) -> None:
        """
        Purpose: construct the briefing view with the job store + optional held_store.
        Usage: BriefingPanelView(store=store, held_store=held_store_or_None).
        Gotchas: held_store=None is valid for read-only display use.
        """
        self._store = store
        self._held_store = held_store

    def standup_packet(self) -> MorningPacket:
        """
        Build and return the current morning packet for the panel's standup view.

        Purpose: exposes the P4 morning_packet.build() result as the panel's
        briefing surface, giving the owner a confidence-ranked digest of overnight
        job results directly from the factory panel.
        Usage:
            packet = view.standup_packet()
            for card in packet.cards:
                print(card.job_id, card.confidence, card.summary)
        Gotchas:
          * Returns a MorningPacket with cards=[] if there are no terminal-state
            jobs — always safe to call, never raises on an empty store.
          * The packet is a READ snapshot; it does not approve/reject anything.
            Batch approval is a separate step the owner triggers from the panel.
        """
        return _build_morning_packet(
            store=self._store,
            held_store=self._held_store,
        )
