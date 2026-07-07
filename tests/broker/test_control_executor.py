# ABOUTME: RED-first tests for the broker-side control_executor (P6-a REQ-02/03).
# ABOUTME: The control_executor is the ONLY effector for job_reject/job_rescope/
# ABOUTME: queue_reprioritize control cards: on owner nonce-approval it performs the
# ABOUTME: effect through an INJECTED reconciler (broker imports nothing from factory,
# ABOUTME: mirroring merge_gate). Drives the REAL BrokerServer over a socketpair — an
# ABOUTME: approved card effects the change; an UNAPPROVED (no-nonce) card effects NOTHING.
from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from typing import Any, Dict

import pytest

from broker.credentials import BrokerCredentials
from broker.executors.control_executor import build_control_executor
from broker.server import BrokerServer
from broker_client import BrokerClient, BrokerError


# ---------------------------------------------------------------------------
# A REAL job-state reconciler backed by a tiny in-memory state machine that
# mirrors JobStore._ALLOWED. It is the ONLY thing that mutates job state; the
# control_executor calls it. This is a real object (not a mock) exercising the
# real executor validation + dispatch path — only the persistence backend is
# a dict rather than SQLite (the executor never touches SQLite directly).
# ---------------------------------------------------------------------------


class _Reconciler:
    """
    Purpose: the injected factory-side effector the broker calls on approval.
    Usage: reconciler.reject(job_id); the executor invokes exactly one method.
    Gotchas: raises on an illegal transition (fail-closed) exactly as JobStore
    would — the executor must surface that as a failed action, never a state change.
    """

    def __init__(self, states: Dict[str, str]):
        self.states = states
        self.priority: Dict[str, int] = {}
        self.rescoped: Dict[str, str] = {}
        self.calls: list = []

    def reject(self, job_id: str) -> None:
        self.calls.append(("reject", job_id))
        state = self.states.get(job_id)
        # QUEUED reject → FAILED (P2-3 fix: QUEUED→NEEDS_ATTENTION is illegal).
        if state == "QUEUED":
            self.states[job_id] = "FAILED"
        elif state in ("ADMITTED", "RUNNING", "TEST", "REVIEW", "AWAITING_APPROVAL", "MERGING"):
            self.states[job_id] = "NEEDS_ATTENTION"
        else:
            raise ValueError(f"cannot reject job in state {state!r}")

    def rescope(self, job_id: str, new_spec: str) -> str:
        self.calls.append(("rescope", job_id, new_spec))
        self.rescoped[job_id] = new_spec
        return f"reparked:{job_id}"

    def reprioritize(self, job_id: str, delta: int) -> None:
        self.calls.append(("reprioritize", job_id, delta))
        self.priority[job_id] = self.priority.get(job_id, 0) + delta


def _make_server(tmp_path: Path, creds: BrokerCredentials, reconciler: _Reconciler) -> BrokerServer:
    """Build a real BrokerServer whose control types dispatch to the control_executor."""
    control_exec = build_control_executor(reconciler)

    def _noop(row):
        return {"status": "sent"}

    return BrokerServer(
        socket_path=tmp_path / "broker.sock",
        db_path=tmp_path / "held_actions.db",
        credentials=creds,
        executors={
            "message": _noop,
            "job_reject": control_exec,
            "job_rescope": control_exec,
            "queue_reprioritize": control_exec,
        },
    )


def _client_for(server: BrokerServer) -> BrokerClient:
    def connector():
        cli_end, srv_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        threading.Thread(target=server.serve_connection, args=(srv_end,), daemon=True).start()
        return cli_end

    return BrokerClient(socket_path="/unused", connector=connector)


@pytest.fixture()
def creds(tmp_path) -> BrokerCredentials:
    return BrokerCredentials(secret_dir=tmp_path)


def _control_payload(job_id: str, verb: str, args: Dict[str, Any]) -> str:
    return json.dumps(
        {"job_id": job_id, "control_verb": verb, "args": args, "requested_by": "owner", "ts": 1}
    )


# ---------------------------------------------------------------------------
# REQ-02: an APPROVED control card effects the change via the reconciler.
# ---------------------------------------------------------------------------


def test_approved_job_reject_effects_change_via_reconciler(tmp_path, creds):
    """A nonce-approved job_reject moves a RUNNING job to NEEDS_ATTENTION through
    the injected reconciler — the effect happens ONLY on the broker approval path."""
    reconciler = _Reconciler({"J1": "RUNNING"})
    server = _make_server(tmp_path, creds, reconciler)
    client = _client_for(server)

    res = client.enqueue_action(
        type="job_reject",
        summary="reject J1",
        payload=_control_payload("J1", "reject", {}),
        origin="mission_control",
    )
    assert res["disposition"] == "held"
    aid = res["action_id"]
    assert reconciler.states["J1"] == "RUNNING", "no mutation before approval"

    nonce = server.mint_approval_nonce(aid)
    out = client.approve(aid, nonce=nonce)
    assert out["status"] == "executed"
    assert reconciler.states["J1"] == "NEEDS_ATTENTION"
    assert reconciler.calls == [("reject", "J1")]


def test_approved_queued_reject_maps_to_failed(tmp_path, creds):
    """P2-3 fix: a job_reject on a QUEUED job maps to FAILED (allowed), NOT the
    illegal QUEUED→NEEDS_ATTENTION edge."""
    reconciler = _Reconciler({"J2": "QUEUED"})
    server = _make_server(tmp_path, creds, reconciler)
    client = _client_for(server)

    res = client.enqueue_action(
        type="job_reject", summary="reject queued J2",
        payload=_control_payload("J2", "reject", {}), origin="mission_control",
    )
    aid = res["action_id"]
    nonce = server.mint_approval_nonce(aid)
    client.approve(aid, nonce=nonce)
    assert reconciler.states["J2"] == "FAILED"


def test_approved_reprioritize_effects_priority(tmp_path, creds):
    """A nonce-approved queue_reprioritize sets the priority delta via the reconciler."""
    reconciler = _Reconciler({"J3": "QUEUED"})
    server = _make_server(tmp_path, creds, reconciler)
    client = _client_for(server)

    res = client.enqueue_action(
        type="queue_reprioritize", summary="bump J3",
        payload=_control_payload("J3", "reprioritize", {"delta": 5}), origin="mission_control",
    )
    aid = res["action_id"]
    nonce = server.mint_approval_nonce(aid)
    client.approve(aid, nonce=nonce)
    assert reconciler.priority["J3"] == 5


# ---------------------------------------------------------------------------
# REQ-02/03: the NO-BYPASS gate — an UNAPPROVED (no-nonce) card effects NOTHING.
# ---------------------------------------------------------------------------


def test_control_card_cannot_mutate_job_without_nonce(tmp_path, creds):
    """The crown no-bypass proof (broker side): a held control card cannot effect a
    state change without a broker-minted nonce. An approve() with NO nonce and with a
    FORGED nonce both raise and leave the job untouched — the reconciler is never called."""
    reconciler = _Reconciler({"J4": "RUNNING"})
    server = _make_server(tmp_path, creds, reconciler)
    client = _client_for(server)

    res = client.enqueue_action(
        type="job_reject", summary="reject J4",
        payload=_control_payload("J4", "reject", {}), origin="mission_control",
    )
    aid = res["action_id"]

    # No nonce → rejected, no mutation.
    with pytest.raises(BrokerError, match="(?i)(approval rejected|nonce)"):
        client.approve(aid, nonce=None)
    assert reconciler.states["J4"] == "RUNNING"
    assert reconciler.calls == []

    # Forged nonce → rejected, no mutation.
    with pytest.raises(BrokerError, match="(?i)(approval rejected|nonce)"):
        client.approve(aid, nonce="forged.deadbeef")
    assert reconciler.states["J4"] == "RUNNING"
    assert reconciler.calls == []

    # Only a broker-minted nonce (out-of-band) releases it.
    nonce = server.mint_approval_nonce(aid)
    client.approve(aid, nonce=nonce)
    assert reconciler.states["J4"] == "NEEDS_ATTENTION"


def test_control_card_is_held_not_auto(tmp_path, creds):
    """A control card is held by default (SafeLane allowed_origins=∅) — never auto-sent."""
    reconciler = _Reconciler({"J5": "RUNNING"})
    server = _make_server(tmp_path, creds, reconciler)
    client = _client_for(server)

    for verb_type in ("job_reject", "job_rescope", "queue_reprioritize"):
        res = client.enqueue_action(
            type=verb_type, summary=f"{verb_type} J5",
            payload=_control_payload("J5", verb_type.replace("job_", "").replace("queue_", ""), {"delta": 1, "new_spec": "x"}),
            origin="mission_control",
        )
        assert res["disposition"] == "held", f"{verb_type} must hold, never auto"
    assert reconciler.calls == [], "nothing executes until a nonce approval"


# ---------------------------------------------------------------------------
# REQ-03: fail-closed on illegal transition / unknown verb (no state change).
# ---------------------------------------------------------------------------


def test_control_executor_rejects_illegal_transition(tmp_path, creds):
    """A job_reject on a DONE job fails closed: the reconciler raises, the executor
    surfaces a failed action, and the job state is unchanged."""
    reconciler = _Reconciler({"J6": "DONE"})
    server = _make_server(tmp_path, creds, reconciler)
    client = _client_for(server)

    res = client.enqueue_action(
        type="job_reject", summary="reject DONE J6",
        payload=_control_payload("J6", "reject", {}), origin="mission_control",
    )
    aid = res["action_id"]
    nonce = server.mint_approval_nonce(aid)
    with pytest.raises(BrokerError):
        client.approve(aid, nonce=nonce)
    assert reconciler.states["J6"] == "DONE", "illegal reject must not change state"


def test_control_executor_rejects_unknown_verb(tmp_path, creds):
    """An unknown control_verb fails closed (no reconciler method matched)."""
    reconciler = _Reconciler({"J7": "RUNNING"})
    control_exec = build_control_executor(reconciler)
    row = {
        "type": "job_reject",
        "payload": _control_payload("J7", "detonate", {}),
    }
    with pytest.raises(Exception):
        control_exec(row)
    assert reconciler.calls == []
    assert reconciler.states["J7"] == "RUNNING"


def test_control_executor_imports_nothing_from_factory(tmp_path, creds):
    """Layering invariant: the broker-side control_executor module must import
    NOTHING from factory (the reconciler is injected). Mirrors merge_gate /
    retro_diff_executor. Anti-weakening: a factory import here breaks the wall."""
    import ast

    import broker.executors.control_executor as mod

    tree = ast.parse(open(mod.__file__).read())
    imported: list = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("factory"):
            imported.append(node.module)
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names if a.name.startswith("factory"))
    assert imported == [], f"control_executor must import nothing from factory, found: {imported}"


def test_control_executor_forwards_rescope_spec_verbatim(tmp_path, creds):
    """The executor forwards the (factory-side already-fenced) new_spec to the
    reconciler verbatim — it does not itself import injection_scan."""
    reconciler = _Reconciler({"J8": "RUNNING"})
    control_exec = build_control_executor(reconciler)
    fenced = "[EXTERNAL CONTENT — flagged: ignore-instructions]\nx\n[/EXTERNAL CONTENT]"
    row = {"type": "job_rescope", "payload": _control_payload("J8", "rescope", {"new_spec": fenced})}
    control_exec(row)
    assert reconciler.rescoped["J8"] == fenced


def test_control_executor_requires_reconciler(tmp_path, creds):
    """Fail-closed: a control_executor built with no reconciler cannot effect anything."""
    with pytest.raises((TypeError, ValueError)):
        build_control_executor(None)


def test_unregistered_control_type_fails_closed(tmp_path, creds):
    """A broker with NO control executor registered (the standalone posture) fails
    closed on a control card — UnknownActionType, no mutation. This is why a
    standalone broker (which imports nothing from factory) can never mutate the fleet."""
    reconciler = _Reconciler({"J9": "RUNNING"})
    server = BrokerServer(
        socket_path=tmp_path / "broker.sock",
        db_path=tmp_path / "held_actions.db",
        credentials=creds,
        executors={"message": lambda row: {"status": "sent"}},  # no control types
    )
    client = _client_for(server)
    res = client.enqueue_action(
        type="job_reject", summary="reject J9",
        payload=_control_payload("J9", "reject", {}), origin="mission_control",
    )
    aid = res["action_id"]
    nonce = server.mint_approval_nonce(aid)
    with pytest.raises(BrokerError, match="(?i)(unknown|executor|type)"):
        client.approve(aid, nonce=nonce)
    assert reconciler.states["J9"] == "RUNNING"


def test_broker_main_does_not_wire_control_types(tmp_path, creds):
    """No-regression: the additive server.py edit is documentation-only. main() must
    NOT register the control types in the standalone launch (they are supervisor
    -injected with a factory-side reconciler). A control card therefore fails closed
    on a standalone broker — proven behaviorally in the test above; here we assert
    the source of main() names no control executor build in its executors dict."""
    import ast
    import inspect

    import broker.server as srv

    main_src = inspect.getsource(srv.main)
    tree = ast.parse(main_src)
    # Collect the literal string keys of every dict in main() — the executors dict's
    # keys are the registered types. Comments/prose are not AST nodes, so a mention
    # of "job_reject" in a comment does not count; only a real dict key does.
    dict_keys: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    dict_keys.add(k.value)
    for control_type in ("job_reject", "job_rescope", "queue_reprioritize"):
        assert control_type not in dict_keys, f"main() must not register {control_type}"
    # main() must not CALL build_control_executor (a Call to that name) — a prose
    # mention in a comment is not an AST Call, so it does not count.
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "build_control_executor" not in called
