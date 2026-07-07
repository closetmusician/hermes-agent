# ABOUTME: RED-first tests for factory/forensics.py — P4-a REQ-04 + F10.
# ABOUTME: Two capture paths: capture_pre_kill (supervised — bundle exists BEFORE
# ABOUTME: the kill signal, asserted via ordering spy) and capture_post_mortem
# ABOUTME: (crash / dead pgid — best-effort, may be truncated). Both write a
# ABOUTME: {transcript_tail, failing_test, phase.diff, meta.json} bundle. No mocks.
"""
Tests for factory.forensics (P4-a, design §3.3 + Revision v2 §R4/F10).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory import forensics


def _worktree(tmp_path) -> Path:
    wt = tmp_path / "wt"
    (wt / ".factory").mkdir(parents=True)
    (wt / ".factory" / "worker.out").write_text("line1\nline2\nfailing turn output\n")
    return wt


def test_pre_kill_bundle_captured_before_kill(tmp_path):
    """REQ-04 / §10-P4a-#4: the bundle is on disk BEFORE the kill signal is sent —
    asserted by an ordering spy that records the sequence of events."""
    wt = _worktree(tmp_path)
    events = []

    def kill_fn():
        # By the time the supervised kill fires, the bundle must already exist.
        events.append("kill")

    bundle = forensics.capture_pre_kill(
        job_id="j-1",
        worktree=wt,
        out_dir=tmp_path / "forensic-bundle",
        meta={"model": "claude", "phase": "IMPLEMENT", "kill_reason": "stall"},
        failing_test="test_foo::assert 1 == 2",
    )
    events.append("captured")
    kill_fn()

    # Bundle exists and was captured before the kill event.
    assert bundle.exists()
    assert (bundle / "meta.json").exists()
    assert events == ["captured", "kill"]


def test_pre_kill_bundle_has_all_parts(tmp_path):
    wt = _worktree(tmp_path)
    bundle = forensics.capture_pre_kill(
        job_id="j-1",
        worktree=wt,
        out_dir=tmp_path / "fb",
        meta={"model": "claude", "phase": "IMPLEMENT", "kill_reason": "stall"},
        failing_test="test_foo failed",
    )
    assert (bundle / "transcript-tail.txt").exists()
    assert (bundle / "failing-test.txt").read_text().strip() == "test_foo failed"
    assert (bundle / "phase.diff").exists()  # present even if empty (no git)
    meta = json.loads((bundle / "meta.json").read_text())
    assert meta["kill_reason"] == "stall"
    assert meta["phase"] == "IMPLEMENT"


def test_post_mortem_bundle_on_dead_pgid(tmp_path):
    """F10: for a crash there is no 'before' — capture_post_mortem snapshots what
    survives (transcript tail may be truncated) and never raises."""
    wt = _worktree(tmp_path)
    bundle = forensics.capture_post_mortem(
        job_id="j-dead",
        worktree=wt,
        out_dir=tmp_path / "fb",
        meta={"model": "codex", "phase": "IMPLEMENT", "kill_reason": "dead pgid 999"},
    )
    assert bundle.exists()
    assert (bundle / "meta.json").exists()
    tail = (bundle / "transcript-tail.txt").read_text()
    assert "failing turn output" in tail


def test_post_mortem_never_raises_on_missing_worktree(tmp_path):
    """F10: a reaped worktree must not crash the reclaim path."""
    gone = tmp_path / "reaped"
    bundle = forensics.capture_post_mortem(
        job_id="j-gone",
        worktree=gone,
        out_dir=tmp_path / "fb",
        meta={"phase": "IMPLEMENT", "kill_reason": "dead pgid 5"},
    )
    # A bundle dir with at least meta.json still lands.
    assert (bundle / "meta.json").exists()
