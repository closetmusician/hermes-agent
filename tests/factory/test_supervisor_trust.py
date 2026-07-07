# ABOUTME: Tests for P1b-d REQ-02 — the production supervisor holds NO mint authority.
# ABOUTME: A prod Supervisor is built with authority=None (request-only broker client);
# ABOUTME: it cannot self-approve/self-mint. The human held-merge path and the tier-1
# ABOUTME: auto-merge path both go over the socket via BrokerClient (approve / auto_merge).
# ABOUTME: AT-NOMINT-1: getattr(sup,"_authority") cannot mint. Fails RED against pre-P1b-d wiring.
"""
REQ-02 evidence for P1b-d: the prod supervisor cannot mint or self-approve.

The old wiring handed the Supervisor a live ApprovalAuthority(held, broker_secret)
— the same object exposes .approve AND .mint_nonce, so any holder could self-mint +
self-approve (review P0-2). This test suite pins the new contract: production passes
authority=None and a request-only BrokerClient; the supervisor routes both the human
approval and the auto-merge over the socket and never holds a mint-capable object.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from broker.held_store import HeldStore
from factory.job_store import JobStore
from factory.supervisor import Supervisor, build_production_supervisor


def _min_supervisor(tmp_path: Path, **overrides) -> Supervisor:
    """Construct a Supervisor with the minimum wiring for a no-mint assertion."""
    store = JobStore(tmp_path / "jobs.db")
    held = HeldStore(tmp_path / "held.db")
    kwargs = dict(
        store=store,
        repo_root=str(tmp_path / "repo"),
        worktrees_root=str(tmp_path / "wt"),
        held_store=held,
        merge_executor=lambda row: {"status": "merged"},
        worker_impl=None,
    )
    kwargs.update(overrides)
    return Supervisor(**kwargs)


class _MintCapableAuthority:
    """Stand-in for the old ApprovalAuthority — has BOTH approve and mint_nonce."""

    def approve(self, action_id, nonce, *, executor, decided_by="telegram"):
        return {"status": "merged"}

    def mint_nonce(self, action_id):
        return "forged-nonce"


# ---------------------------------------------------------------------------
# AT-NOMINT-1 — the production supervisor holds no mint-capable authority.
# ---------------------------------------------------------------------------

def test_supervisor_accepts_authority_none(tmp_path):
    """The prod constructor path must accept authority=None (request-only) without
    crashing. Fails RED today: __init__ requires a real ApprovalAuthority."""
    sup = _min_supervisor(tmp_path, authority=None)
    assert getattr(sup, "_authority", "__missing__") is None


def test_production_supervisor_has_no_mint_capable_authority(tmp_path):
    """AT-NOMINT-1: build_production_supervisor wires the prod Supervisor with NO
    mint-capable authority. A test may inject a fake; production must not hold one."""
    class _FakeBrokerClient:
        def approve(self, action_id, *, nonce):
            return {"status": "merged"}

        def auto_merge(self, action_id):
            return {"disposition": "held"}

        def reject(self, action_id, **kw):
            return {"status": "rejected"}

    store = JobStore(tmp_path / "jobs.db")
    held = HeldStore(tmp_path / "held.db")
    sup = build_production_supervisor(
        store=store,
        repo_root=str(tmp_path / "repo"),
        worktrees_root=str(tmp_path / "wt"),
        held_store=held,
        broker_client=_FakeBrokerClient(),
        merge_executor=lambda row: {"status": "merged"},
        worker_impl=None,
    )
    authority = getattr(sup, "_authority", None)
    assert authority is None, "prod supervisor must NOT hold an ApprovalAuthority"
    assert not hasattr(sup, "_broker_secret"), "prod supervisor must NOT hold the broker secret"
    # It also must not expose a way to mint a nonce.
    assert not hasattr(sup, "mint_nonce")


def test_supervisor_with_real_authority_cannot_be_prod(tmp_path):
    """Anti-weakening: if a mint-capable authority is present, it is the TEST path.
    A supervisor constructed with a real mint-capable authority still has .approve on
    that object (the old surface) — this test documents that production must route
    around it. The production builder never passes such an object (asserted above)."""
    sup = _min_supervisor(tmp_path, authority=_MintCapableAuthority())
    # The injected fake IS mint-capable — this is the surface prod must never expose.
    assert hasattr(sup._authority, "mint_nonce")


# ---------------------------------------------------------------------------
# REQ-02 — the P2 held-merge path still works over the socket (human approval).
# ---------------------------------------------------------------------------

def test_prod_held_merge_approval_routes_over_socket(tmp_path):
    """REQ-02: with authority=None, approve_merge routes the human approval over the
    socket via broker_client.approve(action_id, nonce) — never an in-process mint."""
    calls = {}

    class _RecordingBrokerClient:
        def approve(self, action_id, *, nonce):
            calls["approve"] = (action_id, nonce)
            return {"status": "merged"}

        def auto_merge(self, action_id):
            calls["auto_merge"] = action_id
            return {"disposition": "held"}

    store = JobStore(tmp_path / "jobs.db")
    held = HeldStore(tmp_path / "held.db")
    sup = build_production_supervisor(
        store=store,
        repo_root=str(tmp_path / "repo"),
        worktrees_root=str(tmp_path / "wt"),
        held_store=held,
        broker_client=_RecordingBrokerClient(),
        merge_executor=lambda row: {"status": "merged"},
        worker_impl=None,
    )

    # Drive a synthetic job into AWAITING_APPROVAL so approve_merge can transition it.
    job_id = _seed_awaiting_job(store)
    result = sup.approve_merge(job_id, "action-123", nonce="broker-minted-nonce")

    assert calls.get("approve") == ("action-123", "broker-minted-nonce"), \
        "the human approval must go over the socket, not an in-process authority"
    assert result.get("status") == "merged"
    assert store.get(job_id)["state"] == "DONE"


def test_prod_supervisor_request_auto_merge_over_socket(tmp_path):
    """REQ-02/REQ-03: the supervisor REQUESTS auto-merge via broker_client.auto_merge
    (a no-nonce request verb) — it never mints. The broker decides server-side."""
    calls = {}

    class _RecordingBrokerClient:
        def approve(self, action_id, *, nonce):
            return {"status": "merged"}

        def auto_merge(self, action_id):
            calls["auto_merge"] = action_id
            return {"disposition": "auto", "tier": 1}

    store = JobStore(tmp_path / "jobs.db")
    held = HeldStore(tmp_path / "held.db")
    sup = build_production_supervisor(
        store=store,
        repo_root=str(tmp_path / "repo"),
        worktrees_root=str(tmp_path / "wt"),
        held_store=held,
        broker_client=_RecordingBrokerClient(),
        merge_executor=lambda row: {"status": "merged"},
        worker_impl=None,
    )
    res = sup.request_auto_merge("action-xyz")
    assert calls.get("auto_merge") == "action-xyz"
    assert res["disposition"] == "auto"


def test_prod_approve_merge_without_broker_client_fails_closed(tmp_path):
    """Fail-closed: a prod supervisor (authority=None) with no broker_client cannot
    approve — the merge never runs and the remote never moves."""
    sup = _min_supervisor(tmp_path, authority=None)
    job_id = _seed_awaiting_job(sup._store)
    with pytest.raises(Exception):
        sup.approve_merge(job_id, "action-1", nonce="n")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _seed_awaiting_job(store: JobStore) -> str:
    """Insert a job and walk it to AWAITING_APPROVAL so approve_merge can act on it."""
    spec = {
        "repo": "myrepo",
        "spec": "add a feature",
        "kind": "quick",
        "base_branch": "main",
        "worker": "claude",
        "model": "claude-opus-4-5",
        "budget_usd": 1.0,
        "timeout_min": 30,
    }
    job_id = store.enqueue_job(spec)
    store.transition(job_id, "QUEUED", "RUNNING",
                     extra={"worktree_path": "/tmp/wt", "branch": "feature/x", "pgid": 0})
    store.transition(job_id, "RUNNING", "TEST")
    store.transition(job_id, "TEST", "REVIEW")
    store.transition(job_id, "REVIEW", "AWAITING_APPROVAL")
    return job_id
