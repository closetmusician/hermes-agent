# ABOUTME: RED-first tests for factory/overnight_queue.py (P4-c).
# ABOUTME: REQ-01: night-window stops new spawns outside the window; in-flight drains.
# ABOUTME: REQ-02/REQ-05: concurrency cap=3 honoured; evening graph reaches terminal
# ABOUTME: states by morning; WAITING_CAPACITY job resumes when first-party frees.
# ABOUTME: Real JobStore+DailyBudgetLedger (real sqlite); mock clock + spawn_fn.
"""
Tests for factory.overnight_queue (P4-c).

Design: docs/plans/harness/fable/p4/P4-design.md §5.1 + Revision v2 §R1/§R5.

REQ-01  Night window stops new spawns outside window; in-flight finishes.
REQ-02  concurrency cap=3 not exceeded.
REQ-03  Evening graph → by window-end jobs reach terminal/AWAITING_APPROVAL states.
REQ-04  WAITING_CAPACITY job resumes on first-party free (tick re-check).
REQ-05  TDD: tests are RED before implementation exists.

Anti-weakening:
  * test_night_window_stops_new_spawns_outside_window — FAILS if the window guard is
    dropped (no new jobs outside the window = the core night-queue invariant).
  * test_concurrency_never_exceeds_three — FAILS if cap guard is removed.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from factory.cost_stops import DailyBudgetLedger
from factory.job_store import JobStore
from factory.scheduler import Scheduler
from factory.task_graph import TaskGraph, TaskNode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    return JobStore(tmp_path / "jobs.db")


@pytest.fixture()
def ledger(tmp_path):
    return DailyBudgetLedger(tmp_path / "jobs.db", ceiling_usd=1000.0)


class SpawnRecorder:
    """Records spawn_fn calls; does NOT launch real workers."""

    def __init__(self):
        self.spawned: List[str] = []
        self.lock = threading.Lock()

    def __call__(self, job_row):
        with self.lock:
            self.spawned.append(job_row["id"])


def _node(nid, deps=None):
    return TaskNode(
        id=nid,
        title=f"task {nid}",
        task_type="feature",
        acceptance=[f"AC for {nid}"],
        depends_on=deps or [],
        est_files=[],
        ambiguity=0.0,
    )


def _graph(nodes, repo="test-repo", spec_hash="hash-1"):
    return TaskGraph(spec_hash=spec_hash, repo=repo, nodes=nodes, max_ambiguity=0.0)


def _drive_to_awaiting_approval(store, job_id):
    """Walk a job ADMITTED → AWAITING_APPROVAL (terminal for morning packet)."""
    store.transition(job_id, "ADMITTED", "RUNNING")
    store.transition(job_id, "RUNNING", "TEST")
    store.transition(job_id, "TEST", "REVIEW")
    store.transition(job_id, "REVIEW", "AWAITING_APPROVAL")


def _drive_to_done(store, job_id):
    """Walk a job ADMITTED → DONE."""
    _drive_to_awaiting_approval(store, job_id)
    store.transition(job_id, "AWAITING_APPROVAL", "MERGING")
    store.transition(job_id, "MERGING", "DONE")


# ---------------------------------------------------------------------------
# REQ-01: night-window stops new spawns outside window (anti-weakening)
# ---------------------------------------------------------------------------


def test_night_window_stops_new_spawns_outside_window(store, ledger, tmp_path):
    """
    Purpose: Outside the night window the queue stops launching NEW jobs but lets
    in-flight jobs drain. The 'inside_window' predicate gates new spawns only.
    Anti-weakening: FAILS if the inside_window check is removed from run_night.
    """
    from factory.overnight_queue import NightWindow, run_night

    spawn = SpawnRecorder()
    graph = _graph([_node("T1"), _node("T2"), _node("T3")])

    # A window that is NEVER active (always outside).
    window_never = NightWindow(start_hour=2, end_hour=2)  # zero-width window

    # Tick-limited: we call tick manually with a capped iteration count so the test
    # does not block. run_night honours the window; outside = no new spawns.
    ticks_fired = []

    def stubbed_tick():
        ticks_fired.append(1)
        return []

    sched = Scheduler(
        store=store,
        ledger=ledger,
        spawn_fn=spawn,
        concurrency_cap=3,
        stagger_fn=lambda: None,
    )
    # Patch the scheduler.tick so we observe calls while honouring the window logic.
    with patch.object(sched, "tick", side_effect=stubbed_tick):
        # run_night with a never-active window and max_ticks=3 should still complete
        # without spawning — the tick is only called when inside the window.
        run_night(
            graph=graph,
            store=store,
            ledger=ledger,
            spawn_fn=spawn,
            window=window_never,
            stagger_fn=lambda: None,
            max_ticks=3,
            cadence_s=0,
        )

    # No new spawns since window was never active.
    assert len(spawn.spawned) == 0, (
        "run_night spawned jobs outside the night window — the window guard is missing"
    )


def test_inside_window_jobs_are_spawned(store, ledger, tmp_path):
    """
    Purpose: Inside the night window, ticks run normally and jobs are admitted.
    """
    from factory.overnight_queue import NightWindow, run_night

    spawn = SpawnRecorder()
    graph = _graph([_node("T1")])

    # Always-active window (covers all 24 hours).
    window_always = NightWindow(start_hour=0, end_hour=24)

    # Run for 1 tick — T1 should be admitted.
    run_night(
        graph=graph,
        store=store,
        ledger=ledger,
        spawn_fn=spawn,
        window=window_always,
        stagger_fn=lambda: None,
        max_ticks=1,
        cadence_s=0,
    )

    assert len(spawn.spawned) == 1


# ---------------------------------------------------------------------------
# REQ-02: concurrency cap=3 never exceeded (anti-weakening)
# ---------------------------------------------------------------------------


def test_concurrency_never_exceeds_three(store, ledger):
    """
    Purpose: A graph with 6 independent nodes never has more than 3 live at once
    (cap=3 is the overnight queue's CONCURRENCY_CAP, enforced by the scheduler).
    Anti-weakening: FAILS if the cap guard is removed from the scheduler.
    """
    from factory.overnight_queue import NightWindow, run_night

    spawn = SpawnRecorder()
    graph = _graph([_node(f"T{i}") for i in range(6)])
    window = NightWindow(start_hour=0, end_hour=24)

    run_night(
        graph=graph,
        store=store,
        ledger=ledger,
        spawn_fn=spawn,
        window=window,
        stagger_fn=lambda: None,
        max_ticks=2,
        cadence_s=0,
    )

    # At most 3 jobs should have been admitted (cap=3).
    live_states = ("ADMITTED", "RUNNING", "TEST", "REVIEW", "AWAITING_APPROVAL", "MERGING")
    live = sum(len(store.list_jobs(state=s)) for s in live_states)
    assert live <= 3, f"cap=3 exceeded: {live} live jobs after 2 ticks"
    assert len(spawn.spawned) <= 3


# ---------------------------------------------------------------------------
# REQ-03: Evening graph → terminal states by morning (clock mocked)
# ---------------------------------------------------------------------------


def test_evening_graph_runs_to_morning_packet(store, ledger, tmp_path):
    """
    Purpose: Enqueue a graph; the overnight queue runs; jobs reach terminal or
    AWAITING_APPROVAL states. The morning packet then has cards to render.
    Clock is mocked so this runs instantly in CI.
    """
    from factory.morning_packet import build
    from factory.overnight_queue import NightWindow, run_night

    spawn = SpawnRecorder()
    graph = _graph([_node("T1"), _node("T2")])
    window = NightWindow(start_hour=0, end_hour=24)

    run_night(
        graph=graph,
        store=store,
        ledger=ledger,
        spawn_fn=spawn,
        window=window,
        stagger_fn=lambda: None,
        max_ticks=1,
        cadence_s=0,
    )

    # Drive spawned jobs to AWAITING_APPROVAL so morning_packet has material.
    for jid in spawn.spawned:
        _drive_to_awaiting_approval(store, jid)

    # Build the morning packet — it must succeed and return at least one card.
    packet = build(store=store, held_store=None)
    assert len(packet.cards) >= 1, "morning packet empty after overnight run"


# ---------------------------------------------------------------------------
# REQ-04: WAITING_CAPACITY job resumes when first-party rung frees (tick re-check)
# ---------------------------------------------------------------------------


def test_waiting_capacity_job_resumes_when_first_party_free(store, ledger):
    """
    Purpose: A job parked at WAITING_CAPACITY (residency wait) is re-admitted on the
    next tick once a first-party rung is admissible. The scheduler's reserve_resume_slot
    handles WAITING_CAPACITY → ADMITTED; overnight_queue wraps that tick loop.
    """
    from factory.overnight_queue import NightWindow, run_night

    spawn = SpawnRecorder()
    window = NightWindow(start_hour=0, end_hour=24)

    # Enqueue a single job and manually move it to WAITING_CAPACITY to simulate
    # a residency-park (the router's WAIT_FOR_CAPACITY path).
    graph = _graph([_node("T1")])
    run_night(
        graph=graph,
        store=store,
        ledger=ledger,
        spawn_fn=spawn,
        window=window,
        stagger_fn=lambda: None,
        max_ticks=1,
        cadence_s=0,
    )
    # T1 was admitted; move it back to WAITING_CAPACITY (simulate residency park).
    jid = spawn.spawned[0]
    store.transition(jid, "ADMITTED", "RUNNING")
    store.transition(jid, "RUNNING", "WAITING_CAPACITY")
    spawn.spawned.clear()

    # Another tick should re-admit the WAITING_CAPACITY job.
    run_night(
        graph=_graph([]),  # no new work; just the parked job
        store=store,
        ledger=ledger,
        spawn_fn=spawn,
        window=window,
        stagger_fn=lambda: None,
        max_ticks=1,
        cadence_s=0,
    )

    # The WAITING_CAPACITY job should be re-admitted (reserve_resume_slot handles it).
    state_after = store.get(jid)["state"]
    assert state_after == "ADMITTED", (
        f"WAITING_CAPACITY job not re-admitted on tick; state={state_after}"
    )
