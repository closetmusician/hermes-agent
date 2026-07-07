# ABOUTME: RED-first tests for factory/watchdogs (P5-d WD-1..6).
# ABOUTME: Tests use real JobStore / TrustLedger (SQLite temp); process-kill and
# ABOUTME: broker_client are injected (mocked at the boundary only).  Anti-weakening:
# ABOUTME: tests assert state changes, not just log lines — log-only implementations
# ABOUTME: FAIL.  Design source: P5-design.md §5.1–5.5 + WD-1..6.
"""
Tests for factory/watchdogs/ (P5-d WD-1..6).

WD-1: drift watchdog pauses a synthetically-churning worker
      (RUNNING→PAUSED_DRIFT + capture_pre_kill ran + DRIFT alert)
WD-2: scope-creep worker (diff outside footprint) → paused + DRIFT
WD-3: regression sentinel opens revert + pauses branched tasks + trust ledger reverted
WD-4: memory-zone RED → throttled()=True → scheduler tick admits nothing
WD-5: watchdog-supervisor respawns killed watchdog; dead+unrespawnable → fail-safe
WD-6: worktree GC blocks new spawns below disk floor + reaps terminal-state worktree
      preserving forensics bundle; never reaps a live-pgid worktree
"""
from __future__ import annotations

import signal
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from factory.job_store import JobStore, IllegalTransition
from factory.trust_ledger import TrustLedger
from factory.watchdogs.drift import DriftWatchdog
from factory.watchdogs.memory_zone import MemoryZone, ZONE_RED, ZONE_GREEN, ZONE_YELLOW
from factory.watchdogs.regression_sentinel import RegressionSentinel
from factory.watchdogs.watchdog_supervisor import WatchdogSupervisor, REQUIRED_WATCHDOGS
from factory.watchdogs.worktree_gc import WorktreeGC


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    """Real JobStore backed by a temp SQLite file."""
    return JobStore(tmp_path / "jobs.db")


@pytest.fixture()
def ledger(tmp_path):
    """Real TrustLedger co-located in the jobs DB."""
    return TrustLedger(tmp_path / "jobs.db")


def _job_spec(**overrides) -> dict:
    base = {
        "repo": "test-repo",
        "base_branch": "main",
        "spec": "add a test feature",
        "kind": "feature",
        "worker": "claude",
        "model": "claude-haiku-4-5",
        "budget_usd": 0.5,
        "timeout_min": 10,
        "intake_source_hash": None,
    }
    base.update(overrides)
    return base


def _running_job(store: JobStore, *, pgid: int = 12345, worktree: str = "") -> str:
    """Create a job and transition it to RUNNING, returning its id."""
    jid = store.enqueue_job(_job_spec())
    store.transition(jid, "QUEUED", "RUNNING", extra={"pgid": pgid, "worktree_path": worktree})
    return jid


def _queued_job(store: JobStore, *, base_branch: str = "main") -> str:
    """Create a QUEUED job with given base_branch."""
    return store.enqueue_job(_job_spec(base_branch=base_branch))


# ---------------------------------------------------------------------------
# WD-1: drift watchdog pauses a churning worker
# ---------------------------------------------------------------------------


class TestDriftWatchdogChurning:
    """WD-1: cost climbs N ticks, no progress → PAUSED_DRIFT + forensics + DRIFT alert."""

    def test_churning_worker_paused(self, store, tmp_path):
        """
        Anti-weakening: if DriftWatchdog only logs instead of transitioning the job
        to PAUSED_DRIFT, this test FAILS (job["state"] != "PAUSED_DRIFT").
        """
        killed_pgids: List[int] = []

        def mock_kill(pgid, sig):
            killed_pgids.append(pgid)

        jid = _running_job(store, pgid=9999, worktree=str(tmp_path / "wt"))

        # Build a drift watchdog with a 2-tick window.
        wd = DriftWatchdog(
            store=store,
            out_dir=tmp_path / "forensics",
            kill_fn=mock_kill,
            cost_climb_ticks=2,
        )

        # Simulate N ticks where cost climbs but updated_ts stays the same.
        # We directly manipulate the cost_history to simulate multiple ticks
        # without actually needing to wait real time or update the DB.
        # Tick 1: seed cost history with a lower cost at a fixed ts.
        base_ts = 1000000
        wd._cost_history[jid] = [(0.01, base_ts), (0.02, base_ts)]

        # Pre-tick: job should be RUNNING.
        assert store.get(jid)["state"] == "RUNNING"

        # Now do a tick — the job's current row has cost=0 and the same updated_ts,
        # but our history has two entries with climbing costs.  We need the current
        # row's cost to be > the last history entry to trip the climb check.
        # Manually update the row's cost to simulate cost climbing.
        store._conn.execute(
            "UPDATE jobs SET cost_so_far_usd=0.03, updated_ts=? WHERE id=?",
            (base_ts, jid),
        )
        store._conn.commit()

        # Tick — should detect tokens-without-progress and pause.
        alerts = wd.tick()

        job = store.get(jid)
        assert job["state"] == "PAUSED_DRIFT", (
            "Anti-weakening: DriftWatchdog MUST transition to PAUSED_DRIFT, not just log"
        )
        assert job["fail_reason"] is not None
        assert "drift_watchdog" in job["fail_reason"]
        assert len(alerts) == 1
        assert alerts[0]["job_id"] == jid
        assert alerts[0]["alert"] == "DRIFT"

        # SIGTERM must have been sent (os.kill is called with -pgid).
        assert -9999 in killed_pgids

    def test_forensics_captured_before_kill(self, store, tmp_path):
        """
        capture_pre_kill must be called BEFORE the kill signal — ordering contract
        from forensics.py:9-13.
        """
        call_order: List[str] = []

        def ordered_kill(pgid, sig):
            call_order.append(f"kill:{pgid}")

        jid = _running_job(store, pgid=7777, worktree=str(tmp_path / "wt"))
        (tmp_path / "wt").mkdir(exist_ok=True)

        wd = DriftWatchdog(
            store=store,
            out_dir=tmp_path / "forensics",
            kill_fn=ordered_kill,
            cost_climb_ticks=2,
        )

        # Seed cost history for immediate trigger.
        base_ts = 1000000
        wd._cost_history[jid] = [(0.01, base_ts), (0.02, base_ts)]
        store._conn.execute(
            "UPDATE jobs SET cost_so_far_usd=0.03, updated_ts=? WHERE id=?",
            (base_ts, jid),
        )
        store._conn.commit()

        # Patch capture_pre_kill and _git_diff_files to record order and avoid real FS.
        with patch("factory.watchdogs.drift.capture_pre_kill") as mock_capture, \
             patch("factory.watchdogs.drift._git_diff_files") as mock_diff:
            mock_diff.return_value = []  # no scope creep / ring edits
            def side_effect(*args, **kwargs):
                call_order.append("capture")
                return tmp_path / "forensics" / jid
            mock_capture.side_effect = side_effect
            wd.tick()

        # capture must appear BEFORE kill in the call sequence.
        assert "capture" in call_order
        # os.kill is called as os.kill(-pgid, signal) so the key is "kill:-7777".
        assert any(s.startswith("kill:") for s in call_order)
        capture_idx = call_order.index("capture")
        kill_idx = next(i for i, s in enumerate(call_order) if s.startswith("kill:"))
        assert capture_idx < kill_idx, "capture_pre_kill MUST run before SIGTERM"


# ---------------------------------------------------------------------------
# WD-2: scope-creep detection
# ---------------------------------------------------------------------------


class TestDriftWatchdogScopeCreep:
    """WD-2: a job whose diff touches an out-of-footprint file is paused."""

    def test_scope_creep_triggers_pause(self, store, tmp_path):
        """
        Anti-weakening: if scope_creep only logs, job["state"] != "PAUSED_DRIFT" → FAIL.
        """
        killed: List[int] = []
        jid = store.enqueue_job(_job_spec(spec="footprint: src/foo.py\nadd feature"))
        wt = tmp_path / "wt"
        wt.mkdir()
        store.transition(jid, "QUEUED", "RUNNING", extra={"pgid": 5555, "worktree_path": str(wt)})

        wd = DriftWatchdog(
            store=store,
            out_dir=tmp_path / "forensics",
            kill_fn=lambda pg, sig: killed.append(pg),
        )

        # Simulate the git diff returning a file outside the footprint.
        with patch("factory.watchdogs.drift._git_diff_files") as mock_diff:
            mock_diff.return_value = ["src/other_module.py"]
            alerts = wd.tick()

        job = store.get(jid)
        assert job["state"] == "PAUSED_DRIFT", "scope_creep MUST pause the job"
        assert any(a["trigger"] == "scope_creep" for a in alerts)

    def test_no_pause_when_within_footprint(self, store, tmp_path):
        """No trigger when the diff stays within the declared footprint."""
        jid = store.enqueue_job(_job_spec(spec="footprint: src/foo.py\nadd feature"))
        wt = tmp_path / "wt"
        wt.mkdir()
        store.transition(jid, "QUEUED", "RUNNING", extra={"pgid": 4444, "worktree_path": str(wt)})

        wd = DriftWatchdog(store=store, out_dir=tmp_path / "forensics",
                           kill_fn=lambda *a: None)

        with patch("factory.watchdogs.drift._git_diff_files") as mock_diff:
            mock_diff.return_value = ["src/foo.py"]
            alerts = wd.tick()

        assert store.get(jid)["state"] == "RUNNING"
        assert alerts == []


# ---------------------------------------------------------------------------
# WD-3: regression sentinel
# ---------------------------------------------------------------------------


class TestRegressionSentinel:
    """WD-3: injected post-merge break → revert action + branched tasks paused + reverted ledger."""

    def _make_broker_mock(self) -> MagicMock:
        mock = MagicMock()
        mock.enqueue_action.return_value = {"action_id": "ACT-001", "disposition": "held"}
        return mock

    def test_regression_opens_revert_pauses_branches_and_records_ledger(
        self, store, ledger, tmp_path
    ):
        """
        Anti-weakening:
          (a) broker_client.enqueue_action MUST be called (revert action opened).
          (b) branched jobs MUST be in PAUSED_DRIFT (not just logged).
          (c) trust ledger MUST have a 'reverted' row.
        Any one of these being a log-only no-op MUST make this test fail.
        """
        merge_sha = "deadbeef1234"
        merged_job_id = store.enqueue_job(_job_spec())
        # Mark it as DONE (the merge completed).
        store.transition(merged_job_id, "QUEUED", "RUNNING")
        for st in ("TEST", "REVIEW", "AWAITING_APPROVAL", "MERGING", "DONE"):
            prev = {"TEST": "RUNNING", "REVIEW": "TEST", "AWAITING_APPROVAL": "REVIEW",
                    "MERGING": "AWAITING_APPROVAL", "DONE": "MERGING"}[st]
            store.transition(merged_job_id, prev, st)

        # Create some branched jobs (QUEUED, base_branch=main).
        branched1 = _queued_job(store, base_branch="main")
        branched2 = _queued_job(store, base_branch="main")

        broker_mock = self._make_broker_mock()

        sentinel = RegressionSentinel(
            store=store,
            ledger=ledger,
            broker_client=broker_mock,
            repo_path=str(tmp_path),
            test_fn=lambda: False,  # inject a failing test suite
        )

        result = sentinel.on_merge(
            merge_sha=merge_sha,
            merged_job_id=merged_job_id,
            repo="test-repo",
        )

        # (a) Revert held action must be enqueued.
        assert result["passed"] is False
        assert result["action_taken"] == "revert_opened"
        broker_mock.enqueue_action.assert_called_once()
        call_kwargs = broker_mock.enqueue_action.call_args.kwargs
        assert call_kwargs["type"] == "merge"
        assert merge_sha[:8] in call_kwargs["summary"]

        # (b) Branched tasks must be paused (not just logged).
        assert branched1 in result["paused_jobs"] or branched2 in result["paused_jobs"], (
            "Anti-weakening: branched jobs MUST be transitioned to PAUSED_DRIFT"
        )
        for jid in [branched1, branched2]:
            j = store.get(jid)
            assert j["state"] == "PAUSED_DRIFT", (
                f"Anti-weakening: job {jid} must be PAUSED_DRIFT, not {j['state']}"
            )

        # (c) Trust ledger must have a 'reverted' outcome.
        rows = ledger.read_rows("test-repo", "feature")
        reverted_rows = [r for r in rows if r["outcome"] == "reverted"]
        assert len(reverted_rows) >= 1, (
            "Anti-weakening: 'reverted' trust-ledger row MUST be written"
        )

    def test_no_action_on_passing_suite(self, store, ledger, tmp_path):
        """When post-merge tests pass, no action is taken."""
        broker_mock = self._make_broker_mock()
        merged_jid = store.enqueue_job(_job_spec())

        sentinel = RegressionSentinel(
            store=store, ledger=ledger, broker_client=broker_mock,
            repo_path=str(tmp_path),
            test_fn=lambda: True,
        )
        result = sentinel.on_merge(
            merge_sha="good123",
            merged_job_id=merged_jid,
            repo="test-repo",
        )

        assert result["passed"] is True
        assert result["action_taken"] == "none"
        broker_mock.enqueue_action.assert_not_called()


# ---------------------------------------------------------------------------
# WD-4: memory-zone RED throttles scheduler tick
# ---------------------------------------------------------------------------


class TestMemoryZone:
    """WD-4: RED zone makes throttled()=True; scheduler tick admits nothing."""

    def test_red_zone_throttled(self):
        """
        Anti-weakening: MemoryZone.throttled() MUST return True in RED zone.
        If it only logs, the scheduler still admits new jobs → test fails.
        """
        mz = MemoryZone(memory_reader=lambda: 100.0)  # well below yellow floor (512 MB)
        assert mz.zone() == ZONE_RED
        assert mz.throttled() is True

    def test_green_zone_not_throttled(self):
        mz = MemoryZone(memory_reader=lambda: 2048.0)  # 2 GB, above green floor
        assert mz.zone() == ZONE_GREEN
        assert mz.throttled() is False

    def test_yellow_zone_not_throttled_but_identified(self):
        mz = MemoryZone(memory_reader=lambda: 600.0)  # between 512 and 1024
        assert mz.zone() == ZONE_YELLOW
        assert mz.throttled() is False  # YELLOW caps concurrency but doesn't block outright

    def test_scheduler_tick_admits_nothing_in_red_zone(self, store, tmp_path):
        """
        Wire MemoryZone.throttled into a real Scheduler and assert zero admissions
        when the zone is RED.
        """
        from factory.scheduler import Scheduler
        from factory.cost_stops import DailyBudgetLedger

        mz = MemoryZone(memory_reader=lambda: 50.0)  # RED

        spawned: List[str] = []
        sched = Scheduler(
            store=store,
            ledger=DailyBudgetLedger(db_path=tmp_path / "budget.db", ceiling_usd=100.0),
            spawn_fn=lambda job: spawned.append(job["id"]),
            stagger_fn=lambda: None,
            throttled_fn=mz.throttled,
        )

        # Enqueue a ready job.
        jid = store.enqueue_job(_job_spec())

        admitted = sched.tick()

        assert admitted == [], "Anti-weakening: RED zone MUST block all admissions"
        assert spawned == [], "No spawns when throttled"
        assert store.get(jid)["state"] == "QUEUED", "Job must remain QUEUED"


# ---------------------------------------------------------------------------
# WD-5: watchdog-supervisor respawns a killed watchdog + fail-safe
# ---------------------------------------------------------------------------


class _FakeWatchdog:
    """Minimal watchdog stand-in for supervisor tests."""
    def __init__(self, name: str = "test"):
        self._last_run_ts: float = time.time()
        self.name = name
        self.tick_count = 0

    def tick(self):
        self._last_run_ts = time.time()
        self.tick_count += 1
        return []


class TestWatchdogSupervisor:
    """WD-5: supervisor respawns dead watchdog; dead+unrespawnable → fail-safe."""

    def test_respawn_on_stale_watchdog(self):
        """A stale watchdog is replaced by the respawn_fn; fail-safe stays False."""
        ws = WatchdogSupervisor(stale_s=1)

        original_wd = _FakeWatchdog("drift")
        # Set last_run_ts to very old so it's stale from the start.
        original_wd._last_run_ts = 0.0

        new_wd = _FakeWatchdog("drift")
        new_wd._last_run_ts = time.time()  # freshly spawned

        respawned: List[bool] = []

        def respawn_fn():
            respawned.append(True)
            return new_wd

        ws.register("drift", original_wd, respawn_fn=respawn_fn)
        ws.register("sentinel", _FakeWatchdog("sentinel"))  # keep alive

        # Force sentinel to be alive (set recent ts).
        ws.get_watchdog("sentinel")._last_run_ts = time.time()

        events = ws.tick()

        assert respawned == [True], "respawn_fn must be called for a stale watchdog"
        respawn_events = [e for e in events if e["event"] == "respawned"]
        assert respawn_events, "respawned event must be emitted"
        assert ws.get_watchdog("drift") is new_wd, "The watchdog must be replaced"

    def test_fail_safe_when_required_watchdog_dead_and_unrespawnable(self):
        """
        Anti-weakening: when a REQUIRED watchdog is stale and no respawn_fn,
        ws.throttled() MUST return True (fail-safe). A log-only response FAILS.
        """
        ws = WatchdogSupervisor(stale_s=1)

        dead_wd = _FakeWatchdog("drift")
        dead_wd._last_run_ts = 0.0  # stale

        # Register with NO respawn function.
        ws.register("drift", dead_wd, respawn_fn=None)
        ws.register("sentinel", _FakeWatchdog("sentinel"))
        ws.get_watchdog("sentinel")._last_run_ts = time.time()

        # Must not be throttled yet.
        assert ws.throttled() is False

        ws.tick()

        assert ws.throttled() is True, (
            "Anti-weakening: dead required watchdog MUST engage fail-safe throttle"
        )

    def test_fail_safe_cleared_after_respawn_and_tick(self):
        """Once a respawned watchdog proves itself alive, fail-safe clears."""
        ws = WatchdogSupervisor(stale_s=1)

        dead_wd = _FakeWatchdog("drift")
        dead_wd._last_run_ts = 0.0

        new_wd = _FakeWatchdog("drift")

        def respawn_fn():
            return new_wd

        ws.register("drift", dead_wd, respawn_fn=respawn_fn)
        ws.register("sentinel", _FakeWatchdog("sentinel"))
        ws.get_watchdog("sentinel")._last_run_ts = time.time()

        ws.tick()  # triggers respawn; new_wd._last_run_ts = 0 initially → still stale
        # The fail-safe may or may not be set here depending on respawn timing.
        # Now simulate the new watchdog running (proving itself alive).
        new_wd._last_run_ts = time.time()

        ws.tick()  # now drift + sentinel both alive → fail-safe clears

        assert ws.throttled() is False, "Fail-safe must clear once all required watchdogs are alive"

    def test_no_fail_safe_for_non_required_watchdog(self):
        """A non-required watchdog dying does not engage the fail-safe."""
        ws = WatchdogSupervisor(stale_s=1)

        stale_wd = _FakeWatchdog("gc")  # 'gc' not in REQUIRED_WATCHDOGS
        stale_wd._last_run_ts = 0.0

        # Keep required watchdogs alive.
        drift_wd = _FakeWatchdog("drift")
        drift_wd._last_run_ts = time.time()
        sentinel_wd = _FakeWatchdog("sentinel")
        sentinel_wd._last_run_ts = time.time()

        ws.register("gc", stale_wd)
        ws.register("drift", drift_wd)
        ws.register("sentinel", sentinel_wd)

        ws.tick()

        assert ws.throttled() is False, "Non-required watchdog death must NOT engage fail-safe"


# ---------------------------------------------------------------------------
# WD-6: worktree GC
# ---------------------------------------------------------------------------


class TestWorktreeGC:
    """WD-6: disk floor blocks spawns; terminal-state worktrees reaped; live pgid skipped."""

    def test_disk_full_blocks_spawns(self, store, tmp_path):
        """
        Anti-weakening: disk_full() MUST return True below floor → scheduler admits nothing.
        """
        reaped: List[str] = []
        gc = WorktreeGC(
            store=store,
            repo_path=str(tmp_path),
            out_dir=tmp_path / "forensics",
            disk_floor_bytes=10 * 1024 ** 3,  # 10 GB floor — always tripped in tests
            free_bytes_fn=lambda path: 1024,   # only 1 KB free
        )

        assert gc.disk_full() is True, "disk_full MUST be True below floor"
        assert gc.disk_ok() is False

    def test_disk_ok_above_floor(self, store, tmp_path):
        gc = WorktreeGC(
            store=store,
            repo_path=str(tmp_path),
            out_dir=tmp_path / "forensics",
            disk_floor_bytes=1024,
            free_bytes_fn=lambda path: 10 * 1024 ** 3,
        )
        assert gc.disk_ok() is True
        assert gc.disk_full() is False

    def test_reaps_terminal_worktree_preserves_forensics(self, store, tmp_path):
        """
        A DONE job with an old worktree is reaped.  Forensics bundle in out_dir is
        NOT touched (it's a separate directory).
        """
        # Create a fake worktree directory.
        wt = tmp_path / "worktrees" / "job-wt"
        wt.mkdir(parents=True)
        forensics_dir = tmp_path / "forensics"
        forensics_dir.mkdir()
        (forensics_dir / "bundle.txt").write_text("forensics data")

        # Create a DONE job with old updated_ts and the worktree path.
        jid = store.enqueue_job(_job_spec())
        for tr in [("QUEUED", "RUNNING"), ("RUNNING", "TEST"), ("TEST", "REVIEW"),
                   ("REVIEW", "AWAITING_APPROVAL"), ("AWAITING_APPROVAL", "MERGING"),
                   ("MERGING", "DONE")]:
            store.transition(jid, tr[0], tr[1])
        # Set updated_ts to 10 days ago.
        old_ts = int((time.time() - 10 * 86400) * 1000)
        store._conn.execute(
            "UPDATE jobs SET worktree_path=?, updated_ts=?, pgid=NULL WHERE id=?",
            (str(wt), old_ts, jid),
        )
        store._conn.commit()

        reaped_calls: List[tuple] = []

        def fake_remove(repo, wt_path):
            reaped_calls.append((repo, wt_path))
            return True

        gc = WorktreeGC(
            store=store,
            repo_path=str(tmp_path),
            out_dir=forensics_dir,
            retention_s=86400,  # 1 day
            free_bytes_fn=lambda p: 100 * 1024 ** 3,
            worktree_remove_fn=fake_remove,
        )

        results = gc.reap()

        assert any(r["action"] == "reaped" and r["job_id"] == jid for r in results), (
            "Terminal worktree MUST be reaped"
        )
        assert len(reaped_calls) == 1
        assert reaped_calls[0][1] == str(wt)

        # Forensics bundle must still exist.
        assert (forensics_dir / "bundle.txt").exists(), "Forensics bundle MUST be preserved"

    def test_never_reaps_live_pgid_worktree(self, store, tmp_path):
        """
        A DONE job whose pgid is still alive (simulated) must NOT be reaped.
        """
        wt = tmp_path / "live-wt"
        wt.mkdir()

        jid = store.enqueue_job(_job_spec())
        for tr in [("QUEUED", "RUNNING"), ("RUNNING", "TEST"), ("TEST", "REVIEW"),
                   ("REVIEW", "AWAITING_APPROVAL"), ("AWAITING_APPROVAL", "MERGING"),
                   ("MERGING", "DONE")]:
            store.transition(jid, tr[0], tr[1])
        old_ts = int((time.time() - 10 * 86400) * 1000)
        store._conn.execute(
            "UPDATE jobs SET worktree_path=?, updated_ts=?, pgid=42424 WHERE id=?",
            (str(wt), old_ts, jid),
        )
        store._conn.commit()

        reaped_calls: List[tuple] = []

        # Simulate pgid 42424 as alive.
        with patch("factory.watchdogs.worktree_gc._is_pgid_alive") as mock_alive:
            mock_alive.return_value = True
            gc = WorktreeGC(
                store=store,
                repo_path=str(tmp_path),
                out_dir=tmp_path / "forensics",
                retention_s=86400,
                free_bytes_fn=lambda p: 100 * 1024 ** 3,
                worktree_remove_fn=lambda r, w: reaped_calls.append(w) or True,
            )
            results = gc.reap()

        skipped = [r for r in results if r["action"] == "skipped_live_pgid"]
        assert skipped, "Live-pgid worktree MUST be skipped, not reaped"
        assert reaped_calls == [], "MUST NOT call worktree_remove on a live-pgid job"

    def test_disk_full_via_scheduler(self, store, tmp_path):
        """
        WorktreeGC.disk_full wired into scheduler.throttled_fn blocks admissions
        when disk is below floor.
        """
        from factory.scheduler import Scheduler
        from factory.cost_stops import DailyBudgetLedger

        gc = WorktreeGC(
            store=store,
            repo_path=str(tmp_path),
            out_dir=tmp_path / "forensics",
            disk_floor_bytes=10 * 1024 ** 3,
            free_bytes_fn=lambda p: 512,  # way below floor
        )

        spawned: List[str] = []
        sched = Scheduler(
            store=store,
            ledger=DailyBudgetLedger(db_path=tmp_path / "budget.db", ceiling_usd=100.0),
            spawn_fn=lambda job: spawned.append(job["id"]),
            stagger_fn=lambda: None,
            throttled_fn=gc.disk_full,
        )

        store.enqueue_job(_job_spec())
        admitted = sched.tick()

        assert admitted == [], "Disk-full MUST block all admissions (anti-weakening)"
        assert spawned == []


# ---------------------------------------------------------------------------
# Integration: PAUSED_DRIFT state added to job_store
# ---------------------------------------------------------------------------


class TestPausedDriftState:
    """PAUSED_DRIFT is a valid state reachable from RUNNING."""

    def test_running_to_paused_drift_allowed(self, store):
        jid = store.enqueue_job(_job_spec())
        store.transition(jid, "QUEUED", "RUNNING")
        store.transition(jid, "RUNNING", "PAUSED_DRIFT", fail_reason="drift_watchdog:test")
        assert store.get(jid)["state"] == "PAUSED_DRIFT"

    def test_paused_drift_to_admitted_allowed(self, store):
        jid = store.enqueue_job(_job_spec())
        store.transition(jid, "QUEUED", "RUNNING")
        store.transition(jid, "RUNNING", "PAUSED_DRIFT")
        store.transition(jid, "PAUSED_DRIFT", "ADMITTED")
        assert store.get(jid)["state"] == "ADMITTED"

    def test_paused_drift_to_needs_attention_allowed(self, store):
        jid = store.enqueue_job(_job_spec())
        store.transition(jid, "QUEUED", "RUNNING")
        store.transition(jid, "RUNNING", "PAUSED_DRIFT")
        store.transition(jid, "PAUSED_DRIFT", "NEEDS_ATTENTION")
        assert store.get(jid)["state"] == "NEEDS_ATTENTION"

    def test_queued_to_paused_drift_allowed_for_sentinel(self, store):
        """
        QUEUED→PAUSED_DRIFT is allowed so the regression sentinel can pause
        branched jobs that haven't started yet (design §5.2).
        """
        jid = store.enqueue_job(_job_spec())
        # Should not raise — sentinel needs this path.
        store.transition(jid, "QUEUED", "PAUSED_DRIFT", fail_reason="regression_sentinel:test")
        assert store.get(jid)["state"] == "PAUSED_DRIFT"
