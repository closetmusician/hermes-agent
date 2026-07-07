# ABOUTME: RED-first tests for the BROKER-SIDE auto-merge gate (P1b-e) — the load-bearing
# ABOUTME: fix for the crown-jewel auto-merge risk (review P0-1 server-side re-check, P0-2
# ABOUTME: self-mint). Drives the REAL BrokerServer over a socketpair (not a mock) to prove:
# ABOUTME: (1) auto_merge carries NO nonce and the broker mints/burns INTERNALLY; (2) a
# ABOUTME: ring-touching / never-graduates / tier-0 diff is held server-side, fail-closed;
# ABOUTME: (3) no client-reachable mint verb exists. Only the git/subprocess boundary is stubbed.
import json
import socket
import threading

import pytest

from broker.server import BrokerServer, _CLIENT_METHODS
from broker.credentials import BrokerCredentials
import broker_client
from broker_client import BrokerClient, BrokerError
from broker import merge_gate
from broker.merge_gate import evaluate_auto_merge, MergeGate, BROKER_RING_PATHS
from factory.immutable_ring import RING_PATHS


# --------------------------------------------------------------------------
# Fakes: the ONLY stub is the git-diff/subprocess boundary + injected policy.
# The broker objects (server, store, approval, executor dispatch) are real.
# --------------------------------------------------------------------------

def _card(*, repo="userrepo", branch="job/x", base="main", worktree="/tmp/wt",
          capability="feature", task_type="feature", requested_tier=1):
    """Build a merge-card payload dict as the supervisor would enqueue it."""
    return {
        "repo": repo,
        "branch": branch,
        "base": base,
        "worktree": worktree,
        "remote": "origin",
        "capability": capability,
        "task_type": task_type,
        "requested_tier": requested_tier,
    }


def _clean_diff():
    # An app-only diff — touches nothing in the ring.
    return (
        "diff --git a/app/feature.py b/app/feature.py\n"
        "--- a/app/feature.py\n+++ b/app/feature.py\n"
        "@@ -1 +1,2 @@\n line\n+added\n"
    )


def _ring_diff():
    # A diff that edits a ring path (broker/) — must be caught server-side.
    return (
        "diff --git a/broker/server.py b/broker/server.py\n"
        "--- a/broker/server.py\n+++ b/broker/server.py\n"
        "@@ -1 +1,2 @@\n line\n+backdoor\n"
    )


def _policy_ring_diff():
    # A diff that edits the trust policy doc — also a ring path.
    return (
        "diff --git a/docs/factory/trust-policy.md b/docs/factory/trust-policy.md\n"
        "--- a/docs/factory/trust-policy.md\n+++ b/docs/factory/trust-policy.md\n"
        "@@ -1 +1,2 @@\n line\n+consecutive_merged_clean: 1\n"
    )


def _tier1_gate(diff_text, *, tier=1, never=frozenset()):
    """A MergeGate wired with injected boundaries: a fixed diff, a tier function
    returning `tier`, and a never-graduates capability set `never`."""
    return MergeGate(
        git_diff=lambda base, branch, worktree: diff_text,
        compute_tier=lambda repo, task_type, capability, now_ms: tier,
        never_graduates=lambda: set(never),
    )


class _RecordingMergeExec:
    """Captures whether the real merge executor was invoked (proves no execution)."""

    def __init__(self):
        self.calls = []

    def __call__(self, row):
        self.calls.append(row["action_id"])
        return {"status": "merged", "forced": False}


@pytest.fixture()
def broker_with_gate(tmp_path):
    """A real BrokerServer wired with a merge executor + a merge_gate factory.

    The gate is supplied per-test via server._merge_gate so each test controls the
    diff / tier / never-graduates boundary. Returns (server, merge_exec)."""
    creds = BrokerCredentials(secret_dir=tmp_path)
    merge_exec = _RecordingMergeExec()
    server = BrokerServer(
        socket_path=tmp_path / "broker.sock",
        db_path=tmp_path / "held_actions.db",
        credentials=creds,
        executors={
            "message": lambda row: {"status": "sent"},
            "merge": merge_exec,
        },
    )
    return server, merge_exec


def _client_for(server) -> BrokerClient:
    def connector():
        cli_end, srv_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        threading.Thread(target=server.serve_connection, args=(srv_end,), daemon=True).start()
        return cli_end

    return BrokerClient(socket_path="/unused", connector=connector)


def _enqueue_held_merge(server, card):
    """Enqueue a held merge card directly on the store (as the supervisor would)."""
    return server._store.enqueue(
        type="merge",
        summary="merge job",
        payload=json.dumps(card),
        origin=f"factory:{card['repo']}",
        safe_lane_json=json.dumps({"disposition": "held", "allowed_origins": []}),
        state="held",
    )


# ==========================================================================
# REQ-01 — evaluate_auto_merge: server-side ring + never-graduates + tier
# ==========================================================================

def test_ring_touching_diff_returns_held():
    """AT-BROKER-GATE-1 (unit): a tier-1 card whose diff touches a ring path → HELD."""
    gate = _tier1_gate(_ring_diff(), tier=1)
    disp, tier, reason = gate.evaluate(_card(), now_ms=0)
    assert disp == "held"
    assert reason == "ring"


def test_policy_doc_diff_returns_held():
    """Editing the trust-policy doc itself must be caught server-side → HELD."""
    gate = _tier1_gate(_policy_ring_diff(), tier=1)
    disp, tier, reason = gate.evaluate(_card(), now_ms=0)
    assert disp == "held"
    assert reason == "ring"


def test_never_graduates_capability_returns_held():
    """A never-graduates capability (e.g. prod-deploy) → HELD even at tier-1, clean diff."""
    gate = _tier1_gate(_clean_diff(), tier=1, never={"prod_deploy"})
    disp, tier, reason = gate.evaluate(_card(capability="prod_deploy"), now_ms=0)
    assert disp == "held"
    assert reason == "never"


def test_clean_tier1_card_returns_auto_ok():
    """A clean tier-1 card with a graduatable capability → AUTO-OK."""
    gate = _tier1_gate(_clean_diff(), tier=1)
    disp, tier, reason = gate.evaluate(_card(), now_ms=0)
    assert disp == "auto"
    assert tier == 1
    assert reason == "ok"


def test_tier_zero_recompute_returns_held():
    """AT-BROKER-GATE-2: broker recomputes tier-0 (streak broken) → HELD, ignores card tier."""
    gate = _tier1_gate(_clean_diff(), tier=0)
    disp, tier, reason = gate.evaluate(_card(requested_tier=1), now_ms=0)
    assert disp == "held"
    assert reason == "tier"


def test_any_error_fails_closed_to_held():
    """Fail-closed: any exception inside the gate (unreadable diff/policy) → HELD."""
    def boom(base, branch, worktree):
        raise RuntimeError("git unavailable")

    gate = MergeGate(
        git_diff=boom,
        compute_tier=lambda *a, **k: 1,
        never_graduates=lambda: set(),
    )
    disp, tier, reason = gate.evaluate(_card(), now_ms=0)
    assert disp == "held"
    assert tier == 0


def test_unparseable_diff_fails_closed():
    """A diff that check_ring cannot parse (fail-closed in the ring port) → HELD."""
    gate = _tier1_gate("diff --git garbage-no-paths\n", tier=1)
    disp, tier, reason = gate.evaluate(_card(), now_ms=0)
    assert disp == "held"
    assert reason == "ring"


def test_module_level_evaluate_auto_merge_helper():
    """The design's function-shaped entry point exists and fails closed on error."""
    disp, tier, reason = evaluate_auto_merge(
        _card(),
        git_diff=lambda *a: (_ for _ in ()).throw(RuntimeError("x")),
        compute_tier=lambda *a, **k: 1,
        never_graduates=lambda: set(),
        now_ms=0,
    )
    assert disp == "held"


# ==========================================================================
# REQ-01 — broker owns its ring list; parity with factory (AT-RING-2)
# ==========================================================================

def test_broker_ring_list_is_superset_of_factory():
    """AT-RING-2: broker's ring list ⊇ factory.immutable_ring.RING_PATHS (never checks less)."""
    assert set(RING_PATHS).issubset(set(BROKER_RING_PATHS))


def test_broker_does_not_import_factory():
    """The broker gate must NOT import from factory.* (layering: factory→broker only).

    Checks actual import STATEMENTS via the AST, not prose in comments/docstrings."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(merge_gate))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert "factory" not in imported_roots, f"broker gate imports factory: {imported_roots}"


# ==========================================================================
# REQ-02 — auto_merge over the SOCKET; broker mints/burns internally
# ==========================================================================

def test_auto_merge_is_a_client_method():
    """'auto_merge' is reachable over the socket; 'mint'/'mint_approval_nonce' are NOT."""
    assert "auto_merge" in _CLIENT_METHODS
    assert "mint" not in _CLIENT_METHODS
    assert "mint_approval_nonce" not in _CLIENT_METHODS
    assert "mint_nonce" not in _CLIENT_METHODS


def test_broker_client_has_auto_merge_no_mint():
    """AT-NONCE-1 (client): BrokerClient exposes auto_merge(action_id) and NO mint verb."""
    assert hasattr(BrokerClient, "auto_merge")
    assert not hasattr(BrokerClient, "mint_nonce")
    assert not hasattr(BrokerClient, "mint")
    assert not hasattr(broker_client, "mint_nonce")


def test_auto_merge_signature_carries_no_nonce():
    """The auto_merge client verb takes an action_id and NO nonce parameter."""
    import inspect

    sig = inspect.signature(BrokerClient.auto_merge)
    assert "nonce" not in sig.parameters


def test_auto_merge_clean_tier1_executes_over_socket(broker_with_gate):
    """AT-BROKER-GATE / REQ-02: auto_merge on a clean tier-1 card executes the merge —
    the broker mints+burns the nonce INTERNALLY (the caller sends none)."""
    server, merge_exec = broker_with_gate
    server._merge_gate = _tier1_gate(_clean_diff(), tier=1)
    aid = _enqueue_held_merge(server, _card())

    client = _client_for(server)
    out = client.auto_merge(aid)

    assert out["disposition"] == "auto"
    assert merge_exec.calls == [aid], "clean tier-1 auto_merge must execute the merge"
    row = server._store.get(aid)
    assert row["state"] == "executed"
    assert row["decided_by"] == "trust:auto"


def test_auto_merge_ring_diff_leaves_held(broker_with_gate):
    """AT-BROKER-GATE-1 (socket): a ring-touching diff over the socket → stays held,
    merge does NOT execute. THE load-bearing server-side catch."""
    server, merge_exec = broker_with_gate
    server._merge_gate = _tier1_gate(_ring_diff(), tier=1)
    aid = _enqueue_held_merge(server, _card())

    client = _client_for(server)
    out = client.auto_merge(aid)

    assert out["disposition"] == "held"
    assert out["reason"] == "ring"
    assert merge_exec.calls == [], "ring-touching card must NOT merge"
    assert server._store.get(aid)["state"] == "held"


def test_auto_merge_never_graduates_leaves_held(broker_with_gate):
    """AT-NEVER-1 (socket): a never-graduates capability over the socket → held, no merge."""
    server, merge_exec = broker_with_gate
    server._merge_gate = _tier1_gate(_clean_diff(), tier=1, never={"prod_deploy"})
    aid = _enqueue_held_merge(server, _card(capability="prod_deploy"))

    client = _client_for(server)
    out = client.auto_merge(aid)

    assert out["disposition"] == "held"
    assert merge_exec.calls == []
    assert server._store.get(aid)["state"] == "held"


def test_auto_merge_tier0_recompute_leaves_held(broker_with_gate):
    """AT-BROKER-GATE-2 (socket): broker recomputes tier-0 → held, never trusts card tier."""
    server, merge_exec = broker_with_gate
    server._merge_gate = _tier1_gate(_clean_diff(), tier=0)
    aid = _enqueue_held_merge(server, _card(requested_tier=1))

    client = _client_for(server)
    out = client.auto_merge(aid)

    assert out["disposition"] == "held"
    assert out["reason"] == "tier"
    assert merge_exec.calls == []


def test_auto_merge_non_merge_action_rejected(broker_with_gate):
    """auto_merge only operates on a held 'merge' card; a message action is rejected."""
    server, merge_exec = broker_with_gate
    server._merge_gate = _tier1_gate(_clean_diff(), tier=1)
    aid = server._store.enqueue(
        type="message", summary="s", payload="b", origin="x",
        safe_lane_json=json.dumps({"disposition": "held", "allowed_origins": []}),
        state="held",
    )
    client = _client_for(server)
    with pytest.raises(BrokerError):
        client.auto_merge(aid)


def test_auto_merge_unknown_action_rejected(broker_with_gate):
    """auto_merge on an unknown action_id is rejected (fail-closed)."""
    server, _ = broker_with_gate
    server._merge_gate = _tier1_gate(_clean_diff(), tier=1)
    client = _client_for(server)
    with pytest.raises(BrokerError):
        client.auto_merge("does-not-exist")


# ==========================================================================
# REQ-03 — no regression: the human nonce path still requires a nonce
# ==========================================================================

def test_human_approve_still_requires_nonce(broker_with_gate):
    """The existing nonce wall is intact: approve without a nonce is still rejected,
    and auto_merge is the ONLY no-nonce release path (and it is gated)."""
    server, merge_exec = broker_with_gate
    server._merge_gate = _tier1_gate(_clean_diff(), tier=1)
    aid = _enqueue_held_merge(server, _card())

    client = _client_for(server)
    with pytest.raises(BrokerError):
        client.approve(aid, nonce=None)
    with pytest.raises(BrokerError):
        client.approve(aid, nonce="forged")
    assert merge_exec.calls == []


def test_ring_hit_diff_executes_when_gate_neutered():
    """REQ-04 anti-theater: if the gate is neutered to always-AUTO, the ring-touching
    card would (wrongly) pass. This test asserts the REAL gate does NOT do that —
    i.e. neutering evaluate() → always 'auto' makes test_ring_touching_diff_returns_held
    FAIL. Here we prove the ring check is load-bearing by showing a neutered gate
    returns 'auto' on the same ring diff the real gate holds."""
    neutered = MergeGate(
        git_diff=lambda *a: _ring_diff(),
        compute_tier=lambda *a, **k: 1,
        never_graduates=lambda: set(),
        _skip_ring_for_anti_theater_proof=True,  # simulate a neutered gate
    )
    disp, _, _ = neutered.evaluate(_card(), now_ms=0)
    assert disp == "auto", "neutered gate returns auto — proving the real ring check is what holds it"
    # And the REAL gate holds it:
    real = _tier1_gate(_ring_diff(), tier=1)
    assert real.evaluate(_card(), now_ms=0)[0] == "held"
