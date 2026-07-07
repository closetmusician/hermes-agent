# ABOUTME: The Fable FLEET SCHEDULER (P3 design §4) — PLAIN CODE, no AI in the loop.
# ABOUTME: Fans out independent task nodes concurrently, serializes dependent ones
# ABOUTME: along depends_on edges, caps concurrency at 3 via ATOMIC admission
# ABOUTME: (QUEUED→ADMITTED reservation), throttles against the daily budget ceiling
# ABOUTME: with settlement on terminal state, and scans each rendered node at enqueue.
"""
Fleet scheduler — the plain-code control loop that drives MANY jobs.

Design authoritative source: docs/plans/harness/fable/p3/P3-design.md §4 +
§13.2 (N-threads-one-lock writer model), §13.3 (atomic admission / ADMITTED),
§13.4 (node-scan at enqueue), §13.5 (reservation settlement).

There is NO AI in this module. The tick is: check_integrity → count live →
try_reserve budget → reserve_slot (atomic CAS) → stagger → spawn. The only AI in
the P3 factory lives inside the splitter worker and the per-node workers the
scheduler spawns — never in this loop (test_no_ai_in_scheduler_module asserts it).

Writer model (§13.2): the scheduler thread + N per-node worker threads all share
ONE JobStore object (one sqlite connection, one threading.Lock). Every write path
acquires that lock, so writes serialize in-process — there is no lost update. A
worker SUBPROCESS never writes the store; it writes only its own worktree files
and its cost flows back through the spawning thread. Multi-PROCESS JobStore
writers are forbidden in P3 (a threading.Lock does not span processes).

Atomic admission (§13.3): the cap is enforced by reserving the concurrency slot
BEFORE spawn. reserve_slot CAS-moves a job QUEUED→ADMITTED under the store lock;
capacity counts {ADMITTED,RUNNING,TEST,REVIEW,AWAITING_APPROVAL,MERGING}. Because
the slot is taken the instant it is reserved, a second tick firing in the gap
between spawn and the child's RUNNING transition already sees the ADMITTED slot
and cannot double-admit past the cap.
"""
from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from factory import injection_scan
from factory.cost_stops import DailyBudgetLedger
from factory.job_store import JobStore
from factory.task_graph import TaskGraph

logger = logging.getLogger(__name__)

# Concurrency cap — min(measured host ceiling, 3) = 3 (compute.md §4, owner OQ6).
# This is the HARD concurrency bound; the daily-budget CAS is the independent
# SPEND bound. Both must pass before a node is admitted.
CONCURRENCY_CAP = 3

# Spawn stagger bounds (§4.6): at 3am the binding limit is tokens-per-minute, so
# a burst of simultaneous launches would spike the per-minute rate. Jitter in
# [30, 60] seconds spreads them.
STAGGER_MIN_S = 30
STAGGER_MAX_S = 60

# The state set that counts toward the concurrency cap. ADMITTED (§13.3) is what
# makes the count race-free: a slot reserved this tick is already in the set
# before the child's RUNNING transition lands.
_LIVE_STATES = (
    "ADMITTED",
    "RUNNING",
    "TEST",
    "REVIEW",
    "AWAITING_APPROVAL",
    "MERGING",
)


def _default_stagger() -> None:
    """
    Purpose: sleep a random 30–60s between admitted spawns in one tick (§4.6).
    Usage: passed as the default stagger_fn; overridden in tests with a no-op.
    Gotchas: plain time.sleep with jitter — it runs in the tick's spawn loop, not
    in any worker, and never blocks the store lock (it is called between spawns).
    """
    time.sleep(random.uniform(STAGGER_MIN_S, STAGGER_MAX_S))


class Scheduler:
    """
    The fleet scheduler — plain code, no AI.

    Purpose: drive many jobs from one queue. Fan out independent ready nodes up
    to the concurrency cap, serialize dependent nodes along depends_on edges,
    throttle against the daily budget ceiling, and scan each rendered node spec
    for injection at enqueue.

    Usage:
        sched = Scheduler(store=store, ledger=stopper.ledger, spawn_fn=spawn_node)
        sched.enqueue_graph(task_graph)   # one job row per node + dep edges
        sched.tick()                      # one fan-out pass
        sched.run_forever(cadence_s=60)   # the overnight loop

    Gotchas:
      * spawn_fn(job_row) is the ONLY injected seam — it launches the per-node
        worker (a supervisor/worker subprocess). Tests stub it to record spawns.
        A spawn MUST NOT be issued before reserve_slot succeeds (the cap guard).
      * enqueue_graph is the enqueue owner (§13.4): it scans+fences every rendered
        node spec BEFORE enqueue_job, since it bypasses the P2 supervisor fence.
      * The scheduler NEVER reads-then-writes the budget — it consults the atomic
        try_reserve CAS (§4.5) so parallel spawns cannot race past the ceiling.
    """

    def __init__(
        self,
        *,
        store: JobStore,
        ledger: DailyBudgetLedger,
        spawn_fn: Callable[[Dict[str, Any]], None],
        concurrency_cap: int = CONCURRENCY_CAP,
        stagger_fn: Callable[[], None] = _default_stagger,
        throttled_fn: Optional[Callable[[], bool]] = None,
        mirror_path: Optional[Path] = None,
        clock: Callable[[], float] = time.time,
        checkpoint_reader: Optional[
            Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]
        ] = None,
    ):
        self._store = store
        self._ledger = ledger
        self._spawn_fn = spawn_fn
        self._cap = concurrency_cap
        self._stagger_fn = stagger_fn
        self._throttled_fn = throttled_fn
        self._mirror_path = Path(mirror_path) if mirror_path else None
        self._clock = clock
        # P4-a (§R1): the checkpoint reader that lets check_integrity route a
        # crashed job WITH a valid checkpoint to RESUMABLE, and that the tick uses
        # to re-admit RESUMABLE/WAITING_CAPACITY jobs. None ⇒ P3 behaviour (a
        # crashed job parks terminal; no resume candidates surface).
        self._checkpoint_reader = checkpoint_reader

    # ------------------------------------------------------------------
    # Enqueue — the node-scan-at-enqueue owner (§13.4)
    # ------------------------------------------------------------------

    def enqueue_graph(self, graph: TaskGraph) -> Dict[str, str]:
        """
        Purpose: enqueue one job row per graph node and record its depends_on
        edges, returning a {node_id: job_id} map. Each rendered node spec (title +
        acceptance[]) is injection-scanned and fenced BEFORE enqueue_job — the
        second injection surface the review flagged (§13.4): the scheduler enqueues
        nodes directly, bypassing the P2 supervisor intake fence, so it MUST own
        the scan on the derived surface, not just the original spec.
        Usage: ids = sched.enqueue_graph(task_graph); ids["T1"] → job id.
        Gotchas:
          * A node whose rendered spec scans dirty is FENCED (wrapped in a visible
            marker), not silently dropped — the injection is neutralized but the
            job is still enqueued and visible for owner review.
          * Edges are added AFTER all nodes are enqueued so a depends_on referring
            to a later-listed node still resolves to a real job id.
        """
        node_ids: Dict[str, str] = {}
        # First pass: enqueue every node (scan + fence the rendered spec).
        for node in graph.nodes:
            rendered = self._render_node_spec(node, graph)
            findings = injection_scan.scan(rendered)
            safe_spec = injection_scan.fence(rendered, findings)
            spec_dict = {
                "repo": graph.repo,
                "spec": safe_spec,
                "kind": "feature",
                "intake_source_hash": f"{graph.spec_hash}:{node.id}",
            }
            jid = self._store.enqueue_job(spec_dict)
            node_ids[node.id] = jid

        # Second pass: record dependency edges (all job ids now exist).
        for node in graph.nodes:
            for dep_node_id in node.depends_on:
                self._store.add_dep(
                    node_ids[node.id], node_ids[dep_node_id], graph.spec_hash
                )
        return node_ids

    @staticmethod
    def _render_node_spec(node, graph: TaskGraph) -> str:
        """
        Purpose: render the concatenated node surface (title + acceptance[]) that
        BECOMES the enqueued job spec — the exact text the scan must cover (§13.4).
        Usage: rendered = Scheduler._render_node_spec(node, graph).
        Gotchas: this is the derived surface, distinct from the original spec text;
        an injection can survive the input scan yet appear here in AI-emitted
        acceptance criteria.
        """
        parts = [node.title, *node.acceptance]
        return "\n".join(p for p in parts if p)

    # ------------------------------------------------------------------
    # The tick loop (§4.3) — plain code
    # ------------------------------------------------------------------

    def tick(self) -> List[str]:
        """
        Purpose: run ONE fan-out pass. Reconcile dead jobs, count live slots, then
        admit ready nodes up to remaining capacity — each behind the atomic budget
        reservation AND the atomic slot reservation, staggered between spawns.
        Returns the list of job ids admitted this tick.
        Usage: admitted = sched.tick()  # call from the 60s cron tick.
        Gotchas:
          * ORDER MATTERS: try_reserve (budget) THEN reserve_slot (concurrency).
            If reserve_slot loses the race after budget was reserved, the budget
            reservation is released immediately (settlement §13.5) so no leak.
          * The cap can never be exceeded because reserve_slot moves the job to
            ADMITTED under the store lock, and capacity counts ADMITTED — a
            concurrent tick sees the slot before the child transitions to RUNNING.
          * A spawn is issued ONLY after reserve_slot succeeds; the spawn itself is
            outside the lock so a slow launch does not stall other writers.
        """
        # P4-a (§R1): snapshot the RESUMABLE jobs that existed BEFORE this tick's
        # integrity sweep. A job the sweep newly routes to RESUMABLE is re-admitted
        # on the NEXT tick, not the same one — so the RESUMABLE park is observable
        # (mirror + forensics) for one cadence and the crash→resume path is two
        # distinct steps (design §R1's "run one tick … run the next tick").
        resume_candidates = (
            self._store.resumable_jobs() if self._checkpoint_reader is not None else []
        )

        # 0/1. Reconcile: park dead-pgid jobs and release their reservations.
        #      A crashed job WITH a valid checkpoint is routed to RESUMABLE (not
        #      terminal) when a checkpoint_reader is configured.
        self._store.check_integrity(
            ledger=self._ledger, checkpoint_reader=self._checkpoint_reader
        )

        # 2/3. Count live slots; bail if at the cap.
        capacity = self._cap - self._count_live()
        if capacity <= 0:
            self._write_mirror()
            return []

        # 4. Quota throttle — admit nothing new when near a provider wall.
        if self._throttled_fn is not None and self._throttled_fn():
            self._write_mirror()
            return []

        admitted: List[str] = []

        # 5a. RESUME first (P4-a §R1): a crashed job re-enters the pipeline from its
        #     last cleared phase BEFORE fresh QUEUED work, so overnight progress is
        #     recovered ahead of starting new jobs. Re-admission holds the SAME
        #     atomic slot (reserve_resume_slot CAS) and counts toward the cap. The
        #     resume budget is already reserved (never released on the RESUMABLE
        #     reclaim), so there is no try_reserve here.
        if self._checkpoint_reader is not None:
            for job in resume_candidates:
                if len(admitted) >= capacity:
                    break
                if not self._store.reserve_resume_slot(job["id"]):
                    continue  # lost the race this tick — try next tick
                if admitted:
                    self._stagger_fn()
                # Re-read so the spawn seam sees state='ADMITTED'.
                self._spawn_fn(self._store.get(job["id"]))
                admitted.append(job["id"])

        # 5b. Ready = QUEUED & all deps DONE, oldest first.
        ready = self._store.ready_jobs()

        # 6. Admit up to remaining capacity, each behind both atomic gates.
        for node in ready:
            if len(admitted) >= capacity:
                break
            cap_usd = node.get("budget_usd") or 0.0

            # Budget gate (atomic CAS) — deferred if the ceiling would breach.
            if not self._ledger.try_reserve(cap_usd):
                continue  # try again next tick; a later cheaper node may still fit

            # Concurrency gate (atomic CAS) — claim the slot BEFORE spawn.
            if not self._store.reserve_slot(node["id"]):
                # Lost the race this tick — settle the budget we just reserved.
                self._ledger.release_reservation(cap_usd, node["id"])
                continue

            # Stagger between spawns (jitter spreads the per-minute rate).
            if admitted:
                self._stagger_fn()

            # Spawn the per-node worker (the ONLY injected seam; outside the lock).
            self._spawn_fn(node)
            admitted.append(node["id"])

        # 7. Refresh the human-readable mirror.
        self._write_mirror()
        return admitted

    def run_forever(self, cadence_s: float = 60.0) -> None:
        """
        Purpose: the overnight entry loop — tick, sleep cadence_s, repeat. Fires on
        the same cadence as the hermes 60s scheduler tick so two ticks never
        overlap (the caller holds the file-locked tick; this loop assumes it).
        Usage: sched.run_forever(cadence_s=60)  # runs until the process is stopped.
        Gotchas: this method never returns under normal operation; a KeyboardInterrupt
        or process signal is the intended exit. Each tick is crash-safe (all state
        on disk, WAL+FULL) so an interrupted tick loses nothing.
        """
        while True:
            try:
                self.tick()
            except Exception:  # a bad tick must not kill the loop
                logger.exception("scheduler tick failed; continuing")
            time.sleep(cadence_s)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _count_live(self) -> int:
        """
        Purpose: count jobs occupying a concurrency slot (§4.4). ADMITTED is
        included so a slot reserved this tick is visible to the next tick before
        the child's RUNNING transition — the race-free count.
        Usage: capacity = self._cap - self._count_live().
        Gotchas: each state is a separate indexed query; the sum is the live count.
        """
        return sum(len(self._store.list_jobs(state=s)) for s in _LIVE_STATES)

    def _write_mirror(self) -> None:
        """
        Purpose: refresh the human-readable build-jobs.md mirror at tick end when a
        mirror path was configured.
        Usage: internal; called at every tick exit.
        Gotchas: a no-op when no mirror_path was given (tests do not need it).
        """
        if self._mirror_path is not None:
            self._store.write_build_jobs_mirror(self._mirror_path)
