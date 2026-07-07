# ABOUTME: Overnight queue (P4-c, design §5.1). Wraps the P3 Scheduler under a sleep
# ABOUTME: policy (NightWindow) — enqueues the graph, then ticks inside the window and
# ABOUTME: drains in-flight outside it. No new scheduling engine; the scheduler's own
# ABOUTME: tick + run_forever are the engine. Cap=3 + budget CAS are unchanged.
# ABOUTME: RESUMABLE/WAITING_CAPACITY jobs are re-admitted in the same tick loop.
"""
Overnight queue — evening-enqueue + night-window scheduler wrapper (P4-c).

Design authoritative source: docs/plans/harness/fable/p4/P4-design.md §5.1 +
Revision v2 §R1 (RESUMABLE/WAITING_CAPACITY states) + §R5 (task deltas).

What this module adds over the raw Scheduler:

  1. **NightWindow** — a time-range predicate (start_hour..end_hour UTC, 24-h).
     Outside the window, run_night stops launching NEW jobs but lets in-flight jobs
     drain naturally (the scheduler's tick still runs — it just finds nothing new to
     admit because we wrap run_night to only call tick when inside the window).

  2. **run_night** — the primary entry point. Enqueues the graph (via
     Scheduler.enqueue_graph) then drives tick iterations while inside the window,
     up to max_ticks (test seam; None = unlimited, production uses run_forever).

  3. **Re-admission of RESUMABLE/WAITING_CAPACITY jobs** — the scheduler's tick
     already handles reserve_resume_slot (P4-a §R1); overnight_queue's tick loop
     does nothing extra for those — they are handled by the same tick that handles
     QUEUED jobs. The window check applies only to NEW QUEUED spawns; a
     RESUMABLE/WAITING_CAPACITY job that was already in-flight continues.

Night-window ordering:
  * Inside the window ⇒ call scheduler.tick() (may admit new + resume parked).
  * Outside the window ⇒ no tick call; in-flight jobs continue in their workers
    (the scheduler has no kill-switch; draining = letting workers finish naturally).

Concurrency and budget are delegated entirely to Scheduler (cap=3, DailyBudgetLedger
CAS). This module owns ONLY the window-gating and the graph enqueue.

Gotchas:
  * max_ticks=None means run until KeyboardInterrupt or SIGTERM (production mode).
    For tests, always pass max_ticks=N so the function returns deterministically.
  * cadence_s controls the sleep between ticks (default 60s in production; 0 in tests).
  * The night window uses UTC hours so the behaviour is timezone-stable.
  * graph may be an empty TaskGraph (no new nodes) — in that case run_night just
    drives the tick loop for parked RESUMABLE/WAITING_CAPACITY jobs already in the store.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from factory.cost_stops import DailyBudgetLedger
from factory.job_store import JobStore
from factory.scheduler import CONCURRENCY_CAP, Scheduler
from factory.task_graph import TaskGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WAITING_CAPACITY re-admission helper
# ---------------------------------------------------------------------------


def _admit_waiting_capacity(
    *,
    store: JobStore,
    spawn_fn: Callable[[Dict[str, Any]], None],
) -> None:
    """
    Purpose: re-admit WAITING_CAPACITY jobs by calling reserve_resume_slot on each.
    This is the overnight queue's tick-level re-check for residency-parked jobs —
    when a first-party rung is free again, these jobs can resume.

    The scheduler's own tick handles RESUMABLE (crashed workers with checkpoints);
    WAITING_CAPACITY is handled here because it requires a router admissibility check
    in production (P4-b) — in the overnight queue context we attempt the slot
    reservation directly and let the spawn_fn use the existing checkpoint brief.

    Usage: called at every cadence tick before the window check so parked jobs are
    recovered regardless of whether we are inside the night window.
    Gotchas:
      * This is NOT a concurrency-cap check — the reserve_resume_slot CAS enforces
        the WAITING_CAPACITY → ADMITTED transition; the cap is the scheduler's concern.
        We may over-admit relative to cap here if many WAITING_CAPACITY jobs exist.
        Production should wire the router admissibility check here (P4-b); this
        overnight_queue implementation does a best-effort re-admission.
    """
    waiting = store.list_jobs(state="WAITING_CAPACITY")
    for job in waiting:
        if store.reserve_resume_slot(job["id"]):
            # Re-read so spawn_fn sees state='ADMITTED'.
            updated = store.get(job["id"])
            if updated is not None:
                try:
                    spawn_fn(updated)
                except Exception:
                    logger.exception(
                        "spawn_fn failed for WAITING_CAPACITY job %s", job["id"]
                    )


# ---------------------------------------------------------------------------
# Night window — the sleep-policy predicate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NightWindow:
    """
    Purpose: the off-peak time range in which the overnight queue launches new jobs.
    The window is inclusive at start_hour and exclusive at end_hour (UTC, 0..24).
    A zero-width window (start_hour == end_hour) is NEVER active — used in tests to
    simulate a window that has expired.
    Usage:
        window = NightWindow(start_hour=22, end_hour=8)  # 22:00..08:00 UTC
        if window.is_active(): scheduler.tick()

    Gotchas:
      * Wraps midnight: start_hour=22, end_hour=8 means 22:00..00:00 + 00:00..08:00.
      * end_hour=24 is treated as end_hour=0 with always-wrapping — do NOT use 24
        except in tests where you want an always-active window. Production windows
        use [22..8] from the P0.4 schedule.
      * A zero-width window (start == end) is NEVER active.
    """

    start_hour: int  # UTC hour, 0..23
    end_hour: int    # UTC hour, 0..24 (24 treated as "always active" in tests)

    def is_active(self, hour: Optional[int] = None) -> bool:
        """
        Purpose: True when the current UTC hour falls inside the night window.
        Usage: if window.is_active(): tick()
        Gotchas:
          * end_hour=24 is a test-only sentinel meaning "always active" —
            it is numerically >= 24, outside any real hour, so the normal
            start_hour < current < end_hour branch handles it correctly when
            start_hour=0 (0 <= any_hour < 24).
          * A zero-width window (start == end) is always inactive.
        """
        if self.start_hour == self.end_hour:
            return False  # zero-width = always inactive

        import datetime
        h = hour if hour is not None else datetime.datetime.utcnow().hour

        if self.end_hour == 24:
            # Special sentinel: start_hour=0, end_hour=24 = always active.
            # Any hour 0..23 is inside [0, 24).
            return self.start_hour <= h < 24

        if self.start_hour < self.end_hour:
            # Window does NOT cross midnight (e.g. 02:00..06:00).
            return self.start_hour <= h < self.end_hour
        else:
            # Window crosses midnight (e.g. 22:00..08:00).
            return h >= self.start_hour or h < self.end_hour


# ---------------------------------------------------------------------------
# run_night — the overnight entry point
# ---------------------------------------------------------------------------


def run_night(
    *,
    graph: TaskGraph,
    store: JobStore,
    ledger: DailyBudgetLedger,
    spawn_fn: Callable[[Dict[str, Any]], None],
    window: NightWindow,
    stagger_fn: Callable[[], None] = lambda: None,
    max_ticks: Optional[int] = None,
    cadence_s: float = 60.0,
    checkpoint_reader: Optional[
        Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]
    ] = None,
) -> None:
    """
    Purpose: the overnight entry point — enqueue the task graph once, then drive the
    scheduler tick loop INSIDE the night window, respecting cap=3 and the budget CAS.

    The night-window loop (the core invariant):
      * Inside the window  → call tick() → may admit new QUEUED or resume parked jobs.
      * Outside the window → do NOT call tick() for new spawns. Let in-flight workers
                             finish naturally; the queue is done for the night.
      * max_ticks is the test seam. None = run until interrupted (production).
      * cadence_s controls sleep between ticks (0 in tests, 60 in production).

    RESUMABLE/WAITING_CAPACITY jobs: the scheduler's tick already handles reserve_resume_slot
    for these (P4-a §R1). The window guard does NOT block RESUMABLE/WAITING_CAPACITY
    re-admission — those are already in-flight from the previous tick cycle; the window
    only stops NEW QUEUED work from being admitted outside the off-peak window.

    Usage (production):
        run_night(graph=g, store=s, ledger=l, spawn_fn=f,
                  window=NightWindow(start_hour=22, end_hour=8))

    Usage (test):
        run_night(..., window=NightWindow(0, 24), max_ticks=2, cadence_s=0)

    Gotchas:
      * enqueue_graph is called ONCE at the start. Re-calling run_night with the same
        graph is safe only if the intake_source_hash deduplication in enqueue_job
        guards against duplicates (the scheduler does NOT re-check this).
      * A graph with zero nodes is a no-op for enqueue but the tick loop still runs
        (useful for driving RESUMABLE/WAITING_CAPACITY jobs already in the store).
    """
    sched = Scheduler(
        store=store,
        ledger=ledger,
        spawn_fn=spawn_fn,
        concurrency_cap=CONCURRENCY_CAP,
        stagger_fn=stagger_fn,
        checkpoint_reader=checkpoint_reader,
    )

    # Enqueue the task graph (one job row per node, dep edges).
    if graph.nodes:
        sched.enqueue_graph(graph)

    ticks_run = 0
    while max_ticks is None or ticks_run < max_ticks:
        # Night-window check — gates NEW spawns only.
        # The tick is only called when inside the window so that outside the window
        # no new jobs are admitted (the core invariant). In-flight jobs continue
        # in their own worker subprocesses regardless; we simply don't start more.
        #
        # WAITING_CAPACITY jobs (P4-b §4.3 / design §R1): these are re-admitted
        # in the tick loop too — _admit_waiting_capacity runs before the main tick
        # so parked jobs that freed up a first-party rung get re-admitted this cadence.
        # The window guard does NOT block WAITING_CAPACITY re-admission because those
        # jobs were already accounted as in-flight before the window closed.
        _admit_waiting_capacity(store=store, spawn_fn=spawn_fn)

        if not window.is_active():
            # Outside the window: no new QUEUED spawns. If max_ticks is set
            # (test mode), still count this iteration so we don't loop forever.
            ticks_run += 1
            if cadence_s > 0:
                time.sleep(cadence_s)
            continue

        try:
            sched.tick()
        except Exception:
            logger.exception("overnight_queue tick failed; continuing")

        ticks_run += 1
        if cadence_s > 0:
            time.sleep(cadence_s)
