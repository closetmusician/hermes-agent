# ABOUTME: Tests for the P1b-d composition root (factory/broker_launch.py) — the ONE
# ABOUTME: module that injects the REAL factory compute_tier + trust-ledger reader + ring
# ABOUTME: into the broker's MergeGate and hands it to a BrokerServer. Proves: a tier-1
# ABOUTME: clean card returns AUTO-OK using the real compute_tier; a ring-touching or
# ABOUTME: tier-0 card stays held server-side; and the broker CORE imports nothing from factory.
"""
REQ-01 + REQ-03 evidence for P1b-d.

These tests drive the REAL broker over a socketpair (production serve_connection
path — not a mock) with the gate the composition root built from the REAL
factory-side compute_tier and a REAL TrustLedger. The only stubbed boundary is
git-network (the git_diff runner returns a canned diff, and the merge executor is
a recorder) — everything on the trust decision path is real.
"""
from __future__ import annotations

import ast
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from broker.credentials import BrokerCredentials
from broker.merge_gate import MergeGate
from broker.server import BrokerServer
from broker_client import BrokerClient
from factory.broker_launch import build_merge_gate
from factory.trust_ledger import TrustLedger


DAY_MS = 24 * 60 * 60 * 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_graduated_ledger(ledger: TrustLedger, repo: str, task_type: str,
                           now_ms: int, *, n: int = 10, repo_class: str = "personal") -> None:
    """Seed n consecutive merged_clean rows spanning >=30 days ending well before now."""
    # Oldest row is 40 days back; newest ~31 days back — the whole streak is >=30d old.
    for i in range(n):
        ts = now_ms - (40 - i) * DAY_MS
        ledger.record_outcome(
            f"job-{repo}-{task_type}-{i}",
            repo=repo,
            task_type=task_type,
            outcome="merged_clean",
            repo_class=repo_class,
            outcome_ts=ts,
        )


def _merge_card(repo: str, task_type: str, *, capability=None,
                worktree: str = "/tmp/wt", branch: str = "feature/x",
                base: str = "main") -> dict:
    return {
        "repo": repo,
        "branch": branch,
        "base": base,
        "worktree": worktree,
        "remote": "origin",
        "task_type": task_type,
        "capability": capability,
        "summary": "factory job: add X",
    }


class _RecordingExecutor:
    """A merge executor that records calls instead of touching git (network boundary)."""

    def __init__(self):
        self.calls = []

    def __call__(self, row):
        self.calls.append(row)
        return {"status": "merged", "sha": "deadbeef"}


def _run_server(server: BrokerServer, conn: socket.socket) -> threading.Thread:
    t = threading.Thread(target=server.serve_connection, args=(conn,), daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# REQ-01 — the composition root builds a REAL gate; broker core stays clean.
# ---------------------------------------------------------------------------

def test_broker_launch_does_not_import_factory_into_broker_core():
    """REQ-01: the broker CORE still imports nothing from factory. The composition
    root (factory.broker_launch) is allowed to import both; the broker package is not."""
    broker_dir = Path(__file__).resolve().parents[2] / "broker"
    offenders = []
    for py in list(broker_dir.rglob("*.py")):
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("factory"):
                offenders.append(f"{py}: from {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("factory"):
                        offenders.append(f"{py}: import {alias.name}")
    assert offenders == [], f"broker core must not import factory: {offenders}"


def test_composed_gate_returns_auto_for_graduated_clean_tier1_card(tmp_path):
    """REQ-01/REQ-03: the composed gate, given a tier-1 clean card, returns AUTO-OK
    using the REAL factory compute_tier over a REAL ledger. This is the load-bearing
    proof that the composition root wired the real trust computation."""
    now_ms = int(time.time() * 1000)
    ledger = TrustLedger(tmp_path / "jobs.db")
    _seed_graduated_ledger(ledger, "myrepo", "feature", now_ms)

    # A clean (app-only) diff that touches NO ring path.
    clean_diff = (
        "diff --git a/app/foo.py b/app/foo.py\n"
        "--- a/app/foo.py\n"
        "+++ b/app/foo.py\n"
        "@@ -1 +1 @@\n"
        "-old\n+new\n"
    )
    gate = build_merge_gate(
        ledger=ledger,
        repo_class_for=lambda repo: "personal",
        profile="auto-merge-personal-non-prod",
        git_diff=lambda base, branch, worktree: clean_diff,
        worktree_root_override=str(tmp_path),
    )
    assert isinstance(gate, MergeGate)
    disp, tier, reason = gate.evaluate(
        _merge_card("myrepo", "feature", worktree=str(tmp_path)), now_ms=now_ms
    )
    assert disp == "auto", f"expected auto, got {disp}/{reason}"
    assert tier == 1


def test_composed_gate_holds_tier0_card(tmp_path):
    """REQ-03: a repo×task-type with NO graduated record → the real compute_tier
    returns tier 0 → the composed gate holds."""
    now_ms = int(time.time() * 1000)
    ledger = TrustLedger(tmp_path / "jobs.db")
    # No rows seeded → tier 0.
    clean_diff = (
        "diff --git a/app/foo.py b/app/foo.py\n--- a/app/foo.py\n+++ b/app/foo.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    gate = build_merge_gate(
        ledger=ledger,
        repo_class_for=lambda repo: "personal",
        profile="auto-merge-personal-non-prod",
        git_diff=lambda base, branch, worktree: clean_diff,
        worktree_root_override=str(tmp_path),
    )
    disp, tier, reason = gate.evaluate(
        _merge_card("myrepo", "feature", worktree=str(tmp_path)), now_ms=now_ms
    )
    assert disp == "held"
    assert tier == 0
    assert reason == "tier"


def test_composed_gate_holds_ring_touching_tier1_card(tmp_path):
    """REQ-03 server-side catch: a tier-1-eligible card whose DIFF touches a ring
    path is STILL held by the composed gate — the ring re-check runs before tier."""
    now_ms = int(time.time() * 1000)
    ledger = TrustLedger(tmp_path / "jobs.db")
    _seed_graduated_ledger(ledger, "myrepo", "feature", now_ms)

    ring_diff = (
        "diff --git a/broker/server.py b/broker/server.py\n"
        "--- a/broker/server.py\n+++ b/broker/server.py\n"
        "@@ -1 +1 @@\n-x\n+y\n"
    )
    gate = build_merge_gate(
        ledger=ledger,
        repo_class_for=lambda repo: "personal",
        profile="auto-merge-personal-non-prod",
        git_diff=lambda base, branch, worktree: ring_diff,
        worktree_root_override=str(tmp_path),
    )
    disp, tier, reason = gate.evaluate(
        _merge_card("myrepo", "feature", worktree=str(tmp_path)), now_ms=now_ms
    )
    assert disp == "held"
    assert reason == "ring"


def test_composed_gate_holds_under_ask_for_everything_profile(tmp_path):
    """REQ-03: the ask-for-everything profile (default launch posture) caps every
    computed tier to 0 — even a fully graduated card is held."""
    now_ms = int(time.time() * 1000)
    ledger = TrustLedger(tmp_path / "jobs.db")
    _seed_graduated_ledger(ledger, "myrepo", "feature", now_ms)
    clean_diff = (
        "diff --git a/app/foo.py b/app/foo.py\n--- a/app/foo.py\n+++ b/app/foo.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    gate = build_merge_gate(
        ledger=ledger,
        repo_class_for=lambda repo: "personal",
        profile="ask-for-everything",
        git_diff=lambda base, branch, worktree: clean_diff,
        worktree_root_override=str(tmp_path),
    )
    disp, tier, reason = gate.evaluate(
        _merge_card("myrepo", "feature", worktree=str(tmp_path)), now_ms=now_ms
    )
    assert disp == "held"
    assert tier == 0


def test_composed_gate_holds_work_repo_regardless_of_record(tmp_path):
    """REQ-03: a work repo_class is a hard gate in compute_tier — held even with a
    full clean record under the permissive profile."""
    now_ms = int(time.time() * 1000)
    ledger = TrustLedger(tmp_path / "jobs.db")
    _seed_graduated_ledger(ledger, "workrepo", "feature", now_ms, repo_class="work")
    clean_diff = (
        "diff --git a/app/foo.py b/app/foo.py\n--- a/app/foo.py\n+++ b/app/foo.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    gate = build_merge_gate(
        ledger=ledger,
        repo_class_for=lambda repo: "work",
        profile="auto-merge-personal-non-prod",
        git_diff=lambda base, branch, worktree: clean_diff,
        worktree_root_override=str(tmp_path),
    )
    disp, tier, reason = gate.evaluate(
        _merge_card("workrepo", "feature", worktree=str(tmp_path)), now_ms=now_ms
    )
    assert disp == "held"
    assert tier == 0


# ---------------------------------------------------------------------------
# REQ-03 — end-to-end over the REAL socket: graduated clean job auto-merges;
# ring-touching + tier-0 jobs stay held.
# ---------------------------------------------------------------------------

def _make_server_with_gate(tmp_path, ledger, *, git_diff, repo_class_for,
                           profile="auto-merge-personal-non-prod", executor=None):
    executor = executor or _RecordingExecutor()
    gate = build_merge_gate(
        ledger=ledger,
        repo_class_for=repo_class_for,
        profile=profile,
        git_diff=git_diff,
        worktree_root_override=str(tmp_path),
    )
    creds = BrokerCredentials(secret_dir=tmp_path / "secret")
    server = BrokerServer(
        socket_path=tmp_path / "b.sock",
        db_path=tmp_path / "held.db",
        credentials=creds,
        executors={"merge": executor},
        merge_gate=gate,
    )
    return server, executor


def test_end_to_end_graduated_clean_job_auto_merges_over_socket(tmp_path):
    """REQ-03 crown-jewel: a graduated (repo×task_type) clean job, requested over the
    REAL socket, auto-merges WITHOUT a human — the broker recomputed tier-1 from the
    real ledger and executed. The caller (supervisor stand-in) sent NO nonce."""
    now_ms = int(time.time() * 1000)
    ledger = TrustLedger(tmp_path / "jobs.db")
    _seed_graduated_ledger(ledger, "myrepo", "feature", now_ms)
    clean_diff = (
        "diff --git a/app/foo.py b/app/foo.py\n--- a/app/foo.py\n+++ b/app/foo.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    server, executor = _make_server_with_gate(
        tmp_path, ledger,
        git_diff=lambda base, branch, worktree: clean_diff,
        repo_class_for=lambda repo: "personal",
    )
    card = _merge_card("myrepo", "feature", worktree=str(tmp_path))
    aid = server._store.enqueue(
        type="merge", summary="s", payload=json.dumps(card),
        origin="factory:job-1", safe_lane_json=json.dumps({"disposition": "held"}),
        state="held",
    )

    srv_sock, cli_sock = socket.socketpair()
    _run_server(server, srv_sock)
    client = BrokerClient(tmp_path / "b.sock", connector=lambda: cli_sock)

    res = client.auto_merge(aid)
    assert res["disposition"] == "auto", res
    assert res.get("tier") == 1
    assert len(executor.calls) == 1, "the merge executor ran exactly once"
    assert server._store.get(aid)["state"] == "executed"
    assert server._store.get(aid)["decided_by"] == "trust:auto"


def test_end_to_end_ring_touching_tier1_job_stays_held_over_socket(tmp_path):
    """REQ-03 server-side catch over the socket: a tier-1-eligible job whose diff
    touches a ring path is held by the broker — the merge executor NEVER runs."""
    now_ms = int(time.time() * 1000)
    ledger = TrustLedger(tmp_path / "jobs.db")
    _seed_graduated_ledger(ledger, "myrepo", "feature", now_ms)
    ring_diff = (
        "diff --git a/broker/approval.py b/broker/approval.py\n"
        "--- a/broker/approval.py\n+++ b/broker/approval.py\n@@ -1 +1 @@\n-x\n+y\n"
    )
    server, executor = _make_server_with_gate(
        tmp_path, ledger,
        git_diff=lambda base, branch, worktree: ring_diff,
        repo_class_for=lambda repo: "personal",
    )
    card = _merge_card("myrepo", "feature", worktree=str(tmp_path))
    aid = server._store.enqueue(
        type="merge", summary="s", payload=json.dumps(card),
        origin="factory:job-1", safe_lane_json=json.dumps({"disposition": "held"}),
        state="held",
    )
    srv_sock, cli_sock = socket.socketpair()
    _run_server(server, srv_sock)
    client = BrokerClient(tmp_path / "b.sock", connector=lambda: cli_sock)

    res = client.auto_merge(aid)
    assert res["disposition"] == "held", res
    assert res.get("reason") == "ring"
    assert executor.calls == [], "the merge must NOT execute on a ring-touching diff"
    assert server._store.get(aid)["state"] == "held"


def test_end_to_end_tier0_job_stays_held_over_socket(tmp_path):
    """REQ-03: a job in a repo×task_type with no graduated record → broker recomputes
    tier-0 → held over the socket; executor never runs."""
    now_ms = int(time.time() * 1000)
    ledger = TrustLedger(tmp_path / "jobs.db")  # empty → tier 0
    clean_diff = (
        "diff --git a/app/foo.py b/app/foo.py\n--- a/app/foo.py\n+++ b/app/foo.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    server, executor = _make_server_with_gate(
        tmp_path, ledger,
        git_diff=lambda base, branch, worktree: clean_diff,
        repo_class_for=lambda repo: "personal",
    )
    card = _merge_card("myrepo", "feature", worktree=str(tmp_path))
    aid = server._store.enqueue(
        type="merge", summary="s", payload=json.dumps(card),
        origin="factory:job-1", safe_lane_json=json.dumps({"disposition": "held"}),
        state="held",
    )
    srv_sock, cli_sock = socket.socketpair()
    _run_server(server, srv_sock)
    client = BrokerClient(tmp_path / "b.sock", connector=lambda: cli_sock)

    res = client.auto_merge(aid)
    assert res["disposition"] == "held", res
    assert res.get("reason") == "tier"
    assert executor.calls == []


# ---------------------------------------------------------------------------
# ANTI-WEAKENING — if the composition root injected a constant tier-1 instead of
# the real compute_tier, the tier-0 hold test above would flip to auto. This test
# documents that the REAL compute_tier is what makes the tier-0 case hold.
# ---------------------------------------------------------------------------

def test_real_compute_tier_is_load_bearing_in_composition(tmp_path):
    """Anti-weakening: swap the real ledger-driven tier for a constant-1 stub and the
    same tier-0 (empty-ledger) card flips to auto — proving build_merge_gate's real
    compute_tier is what holds it. If broker_launch hardcoded tier=1, the production
    gate would auto-merge an ungraduated repo."""
    now_ms = int(time.time() * 1000)
    ledger = TrustLedger(tmp_path / "jobs.db")  # empty → real tier 0
    clean_diff = (
        "diff --git a/app/foo.py b/app/foo.py\n--- a/app/foo.py\n+++ b/app/foo.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    real_gate = build_merge_gate(
        ledger=ledger, repo_class_for=lambda r: "personal",
        profile="auto-merge-personal-non-prod",
        git_diff=lambda b, br, w: clean_diff, worktree_root_override=str(tmp_path),
    )
    card = _merge_card("myrepo", "feature", worktree=str(tmp_path))
    assert real_gate.evaluate(card, now_ms=now_ms)[0] == "held"

    # A neutered gate with a constant tier-1 would AUTO on the exact same card.
    neutered = MergeGate(
        git_diff=lambda b, br, w: clean_diff,
        compute_tier=lambda repo, task_type, capability, now_ms: 1,
    )
    assert neutered.evaluate(card, now_ms=now_ms)[0] == "auto"
