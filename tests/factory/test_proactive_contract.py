# ABOUTME: RED-first tests for proactive_contract.py — quiet-by-default nudge engine (P6-c).
# ABOUTME: Core properties: ≤1 nudge per anomaly_key (never repeats), durable ledger survives
# ABOUTME: restart, different keys still nudge once each, anti-weakening: tests FAIL if the
# ABOUTME: ledger check is removed. Uses real SQLite on tmp paths — no mocks.
"""
Tests for factory.proactive_contract (P6-c design §REQ-01 + §REQ-04).

Quiet-by-default semantics:
  * The same anomaly_key fired N times → exactly 1 nudge, zero more.
  * The ledger is durable: a new ProactiveContract instance reading the same ledger
    file suppresses repeats (restart-durable).
  * A distinct anomaly_key nudges once independently.

Anti-weakening: if should_nudge() always returns True (ledger check removed),
test_proactive_contract_quiet_one_nudge FAILS because enqueue_action would be
called 5 times, not 1. That is the falsifiable property.
"""

import pathlib
import pytest

from factory.proactive_contract import ProactiveContract
from factory.immutable_ring import RING_PATHS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_enqueue():
    """Return a list and a callable that appends to it (real-call recorder)."""
    calls = []

    def enqueue(anomaly_key: str, summary: str) -> None:
        calls.append(anomaly_key)

    return calls, enqueue


# ---------------------------------------------------------------------------
# REQ-01: quiet-by-default, ≤1 nudge per anomaly_key
# ---------------------------------------------------------------------------

class TestQuietOnNudge:
    """
    Purpose: verify that the nudge engine emits AT MOST 1 nudge for a given
    anomaly_key regardless of how many times the event fires.
    """

    def test_proactive_contract_quiet_one_nudge(self, tmp_path: pathlib.Path):
        """
        Purpose: fire the same anomaly_key 5× and assert exactly 1 nudge is
        emitted; the other 4 are suppressed.  Anti-weakening: if should_nudge
        always returns True this test asserts 5 calls and FAILS.
        Usage: call send_nudge 5× with the same key; assert len(calls) == 1.
        Gotchas: a new ProactiveContract shares the ledger file path, not
        in-memory state — this test uses one instance for simplicity.
        """
        calls, enqueue = _mock_enqueue()
        ledger_path = tmp_path / "nudges.db"
        pc = ProactiveContract(ledger_path=ledger_path, enqueue_fn=enqueue)

        for i in range(5):
            pc.send_nudge("drift:job-42", f"anomaly event {i}")

        assert len(calls) == 1, (
            f"Expected 1 nudge, got {len(calls)}. "
            "If this fails, the ledger dedup check was removed (anti-weakening)."
        )
        assert calls[0] == "drift:job-42"

    def test_different_anomaly_key_nudges(self, tmp_path: pathlib.Path):
        """
        Purpose: a distinct anomaly_key produces its own nudge independently.
        Usage: send_nudge for two different keys; each should emit once.
        Gotchas: keys must be fully independent (separate ledger rows).
        """
        calls, enqueue = _mock_enqueue()
        pc = ProactiveContract(ledger_path=tmp_path / "nudges.db", enqueue_fn=enqueue)

        pc.send_nudge("drift:job-1", "job 1 drifted")
        pc.send_nudge("drift:job-1", "job 1 drifted again")  # suppressed
        pc.send_nudge("regression:job-2", "job 2 regressed")
        pc.send_nudge("regression:job-2", "job 2 regressed again")  # suppressed

        assert len(calls) == 2
        assert "drift:job-1" in calls
        assert "regression:job-2" in calls

    def test_should_nudge_returns_true_first_time(self, tmp_path: pathlib.Path):
        """
        Purpose: should_nudge returns True on first call for a fresh key.
        Usage: directly call should_nudge before any send_nudge.
        Gotchas: should_nudge does NOT record the nudge — only send_nudge does.
        """
        pc = ProactiveContract(ledger_path=tmp_path / "nudges.db", enqueue_fn=lambda k, s: None)
        assert pc.should_nudge("new:key-99") is True

    def test_should_nudge_returns_false_after_send(self, tmp_path: pathlib.Path):
        """
        Purpose: after send_nudge records the key, should_nudge returns False.
        Usage: send once, then check should_nudge again.
        Gotchas: the ledger insert is committed atomically during send_nudge.
        """
        pc = ProactiveContract(ledger_path=tmp_path / "nudges.db", enqueue_fn=lambda k, s: None)
        pc.send_nudge("fleet:done", "all jobs complete")
        assert pc.should_nudge("fleet:done") is False


# ---------------------------------------------------------------------------
# REQ-02: ledger survives restart (durable, on-disk)
# ---------------------------------------------------------------------------

class TestDurableLedger:
    """
    Purpose: verify the never-repeat property holds across a ProactiveContract
    instance restart — the ledger file on disk preserves the nudge record.
    Anti-weakening: if the ledger is in-memory only, the restart test FAILS.
    """

    def test_proactive_contract_never_repeats_across_restart(self, tmp_path: pathlib.Path):
        """
        Purpose: nudge sent on instance A is suppressed when a NEW instance B
        reads the same ledger file.  Models a process restart.
        Usage: send on pc1; create pc2 from same ledger; assert send_nudge suppressed.
        Gotchas: both instances must share the exact same ledger_path, NOT tmp dirs.
        """
        ledger_path = tmp_path / "nudges.db"
        calls_a, enqueue_a = _mock_enqueue()
        calls_b, enqueue_b = _mock_enqueue()

        # First instance: nudge once
        pc1 = ProactiveContract(ledger_path=ledger_path, enqueue_fn=enqueue_a)
        pc1.send_nudge("drift:job-77", "first encounter")
        assert len(calls_a) == 1

        # Simulate restart: new instance, same ledger
        pc2 = ProactiveContract(ledger_path=ledger_path, enqueue_fn=enqueue_b)
        pc2.send_nudge("drift:job-77", "same anomaly after restart")

        assert len(calls_b) == 0, (
            "Nudge was emitted again after restart — ledger must be on-disk "
            "(anti-weakening: in-memory state does not survive restart)."
        )

    def test_new_key_after_restart_still_nudges(self, tmp_path: pathlib.Path):
        """
        Purpose: a brand-new key (not in ledger) on the restarted instance still
        emits one nudge — the ledger only blocks known keys.
        Usage: restart instance, use a key that was never sent on pc1.
        Gotchas: verify the restart correctly distinguishes seen from unseen keys.
        """
        ledger_path = tmp_path / "nudges.db"
        calls_b, enqueue_b = _mock_enqueue()

        pc1 = ProactiveContract(ledger_path=ledger_path, enqueue_fn=lambda k, s: None)
        pc1.send_nudge("drift:job-77", "known key")

        pc2 = ProactiveContract(ledger_path=ledger_path, enqueue_fn=enqueue_b)
        pc2.send_nudge("regression:job-88", "brand new key after restart")

        assert len(calls_b) == 1
        assert calls_b[0] == "regression:job-88"


# ---------------------------------------------------------------------------
# REQ-04 (design §5, test 6): proactive-contract.md is ring-protected
# ---------------------------------------------------------------------------

class TestRingProtection:
    """
    Purpose: verify that docs/factory/proactive-contract.md is listed in
    RING_PATHS so a worker diff touching it is rejected at merge-submission.
    Design reference: P6-design.md §3.3 + immutable_ring.RING_PATHS.
    """

    def test_proactive_contract_is_ring_protected(self):
        """
        Purpose: docs/factory/proactive-contract.md must appear in RING_PATHS so
        the immutable-ring gate rejects any worker diff that edits it.
        Usage: assert the path string is in RING_PATHS.
        Gotchas: the path must match exactly (the ring uses worktree-relative paths
        with forward slashes); case matters on case-sensitive filesystems.
        """
        assert "docs/factory/proactive-contract.md" in RING_PATHS, (
            "docs/factory/proactive-contract.md is NOT in RING_PATHS. "
            "A worker could weaken the quiet-by-default contract without review. "
            "Add it to factory/immutable_ring.RING_PATHS."
        )
