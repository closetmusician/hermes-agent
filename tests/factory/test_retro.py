# ABOUTME: RED-first tests for the nightly retro orchestration (factory/retro.py).
# ABOUTME: The retro is a HELD-ACTION PRODUCER, never a silent editor: GATHER →
# ABOUTME: PROPOSE (AI diff text) → propose-gate (WALL 1) → HOLD as an owner-approved
# ABOUTME: broker action carrying {diff, diff_sha256, rationale, target_files}. A
# ABOUTME: ring-touching proposal is dropped at the gate BEFORE enqueue (ordering).
"""
Tests for the retro flow (P5-b REQ-01, closes review RT-1/RT-2/RT-6).

The retro drives an AI worker for the DIFF TEXT only; the diff's sole path to
disk is an owner tap on the held action. This suite proves:
  * RT-1 — a clean proposal becomes exactly one ``enqueue_action(type=...)`` held
    action and is NEVER written to disk directly (spy the apply path).
  * RT-6 — a ring-touching proposal drops at the gate: ``enqueue_action`` is
    NEVER called (gate runs BEFORE enqueue, the ordering contract).
  * the held payload carries a ``diff_sha256`` pinning the exact gated bytes (v2-C3).

Real ``retro_ring_gate`` throughout; the AI proposer and broker client are
injected seams (the diff TEXT source and the egress boundary), never the gate.
"""

import hashlib
import json

import pytest

from factory.retro import run_retro


class _SpyBrokerClient:
    """A stand-in broker client that records enqueue_action calls (no real socket).

    Mirrors broker_client.BrokerClient.enqueue_action's signature/return so the
    retro exercises the REAL held-action contract; the spy only records, never
    sends. disposition 'held' == queued for the owner tap.
    """

    def __init__(self):
        self.calls = []

    def enqueue_action(self, *, type, summary, payload, origin, channel=None, recipient=None):
        self.calls.append(
            {"type": type, "summary": summary, "payload": payload, "origin": origin}
        )
        return {"action_id": f"act-{len(self.calls)}", "disposition": "held"}


def _clean_proposal():
    """A benign skill-file diff a retro would propose (non-ring)."""
    return (
        "diff --git a/skills/qa/SKILL.md b/skills/qa/SKILL.md\n"
        "--- a/skills/qa/SKILL.md\n"
        "+++ b/skills/qa/SKILL.md\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+distilled failure-class fix\n"
    )


def _ring_proposal():
    """A diff proposing to weaken a cost stop — a ring path."""
    return (
        "diff --git a/factory/cost_stops.py b/factory/cost_stops.py\n"
        "--- a/factory/cost_stops.py\n"
        "+++ b/factory/cost_stops.py\n"
        "@@ -1 +1 @@\n"
        "-HARD_CAP = 5.0\n"
        "+HARD_CAP = 9999.0\n"
    )


# ---------------------------------------------------------------------------
# RT-1 — the retro produces a held action and NEVER writes the diff to disk.
# ---------------------------------------------------------------------------

def test_retro_produces_held_action_never_writes_disk(tmp_path, monkeypatch):
    """RT-1: a clean proposal → exactly one enqueue_action held action; ZERO direct
    disk writes of the diff (spy on open() write path)."""
    broker = _SpyBrokerClient()

    # Spy on the apply/disk path: any attempt to write the diff to disk is a bug.
    writes = []
    import builtins

    real_open = builtins.open

    def _spy_open(path, mode="r", *a, **k):
        if "w" in mode or "a" in mode or "+" in mode:
            writes.append(str(path))
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", _spy_open)

    result = run_retro(
        broker_client=broker,
        propose=lambda gathered: _clean_proposal(),
        gather=lambda: {"failure_class": "test_fail", "samples": []},
        worktree_root=str(tmp_path),
    )

    # Exactly one held action, of the retro_diff type.
    assert len(broker.calls) == 1, broker.calls
    assert broker.calls[0]["type"] == "retro_diff"
    assert result["disposition"] == "held"
    # The diff bytes were never written to a file by the retro flow itself.
    diff_writes = [w for w in writes if "cost_stops" in w or w.endswith(".diff")]
    assert diff_writes == [], f"retro wrote the diff to disk directly: {diff_writes}"


def test_held_payload_pins_diff_sha256(tmp_path):
    """v2-C3: the held payload carries diff_sha256 = sha256(diff bytes) — the hash-pin."""
    broker = _SpyBrokerClient()
    diff = _clean_proposal()
    run_retro(
        broker_client=broker,
        propose=lambda gathered: diff,
        gather=lambda: {"failure_class": "test_fail", "samples": []},
        worktree_root=str(tmp_path),
    )
    payload = json.loads(broker.calls[0]["payload"])
    assert payload["diff"] == diff
    assert payload["diff_sha256"] == hashlib.sha256(diff.encode("utf-8")).hexdigest()
    assert "rationale" in payload


# ---------------------------------------------------------------------------
# RT-6 — the propose gate runs BEFORE enqueue (ordering contract).
# ---------------------------------------------------------------------------

def test_ring_proposal_never_enqueued(tmp_path):
    """RT-6: a ring-touching proposal is dropped at the gate; enqueue_action is
    NEVER called (gate → enqueue ordering, never enqueue → gate)."""
    broker = _SpyBrokerClient()
    result = run_retro(
        broker_client=broker,
        propose=lambda gathered: _ring_proposal(),
        gather=lambda: {"failure_class": "cost", "samples": []},
        worktree_root=str(tmp_path),
    )
    assert broker.calls == [], "ring proposal reached enqueue_action (gate ran too late)"
    assert result["disposition"] == "rejected"
    assert result.get("reason") == "ring"


def test_empty_proposal_never_enqueued(tmp_path):
    """RT-5 (retro layer): an empty/unparseable proposal is dropped, no held action."""
    broker = _SpyBrokerClient()
    result = run_retro(
        broker_client=broker,
        propose=lambda gathered: "",
        gather=lambda: {"failure_class": "none", "samples": []},
        worktree_root=str(tmp_path),
    )
    assert broker.calls == []
    assert result["disposition"] == "rejected"
