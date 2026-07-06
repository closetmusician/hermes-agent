# ABOUTME: Integration tests for broker/server.py + broker_client.py over a REAL
# ABOUTME: unix socket (socketpair bridge — real bytes, real framing, real
# ABOUTME: dispatch, real nonce logic; NOT a mock). Covers health/enqueue/
# ABOUTME: list_pending round-trip, the client exposing NO send/git_push verb,
# ABOUTME: fail-closed (broker down => client RAISES), and the P0-3 gate: self-
# ABOUTME: approve over the socket (no nonce) is REJECTED while an out-of-band
# ABOUTME: nonce approve executes. Path/0600 tests run only where UDS bind works.
import os
import socket
import stat
import threading

import pytest

from broker.server import BrokerServer
from broker.credentials import BrokerCredentials
import broker_client
from broker_client import BrokerClient, BrokerUnavailable, BrokerError


def _bind_available(tmp_path) -> bool:
    """Some sandboxes block AF_UNIX bind() on a filesystem path; detect it."""
    p = tmp_path / "_probe.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(str(p))
        return True
    except (PermissionError, OSError):
        return False
    finally:
        s.close()
        try:
            p.unlink()
        except OSError:
            pass


@pytest.fixture()
def broker(tmp_path):
    """A BrokerServer whose per-connection dispatch is driven over a socketpair.

    This exercises the identical production code path (serve_connection) with real
    socket bytes — the only thing NOT covered is the filesystem bind, which the
    path/mode tests cover separately when the environment permits it.
    """
    creds = BrokerCredentials(secret_dir=tmp_path)
    executed = []
    server = BrokerServer(
        socket_path=tmp_path / "broker.sock",
        db_path=tmp_path / "held_actions.db",
        credentials=creds,
        message_executor=lambda row: executed.append(row["action_id"]) or {"status": "sent"},
    )
    return server, executed


def _client_for(server) -> BrokerClient:
    """Build a client whose connector hands each call a fresh socketpair end that
    a daemon thread serves via the real server.serve_connection()."""

    def connector():
        cli_end, srv_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        t = threading.Thread(target=server.serve_connection, args=(srv_end,), daemon=True)
        t.start()
        return cli_end

    return BrokerClient(socket_path="/unused", connector=connector)


# -- path / 0600 (environment-gated) ---------------------------------------

def test_socket_mode_is_0600(tmp_path):
    if not _bind_available(tmp_path):
        pytest.skip("AF_UNIX bind() blocked in this sandbox — see report §env")
    import time

    creds = BrokerCredentials(secret_dir=tmp_path)
    server = BrokerServer(
        socket_path=tmp_path / "broker.sock",
        db_path=tmp_path / "held_actions.db",
        credentials=creds,
        message_executor=lambda row: {"status": "sent"},
    )
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    for _ in range(100):
        if (tmp_path / "broker.sock").exists():
            break
        time.sleep(0.01)
    try:
        mode = stat.S_IMODE(os.stat(tmp_path / "broker.sock").st_mode)
        assert mode == 0o600
    finally:
        server.shutdown()
        t.join(timeout=5)


# -- real round-trip over socketpair (always runs) -------------------------

def test_health_enqueue_list_round_trip(broker):
    server, _ = broker
    client = _client_for(server)
    h = client.health()
    assert h["status"] in ("ok", "healthy")
    assert "pending_count" in h

    res = client.enqueue_action(
        type="message",
        channel="telegram",
        recipient="board",
        summary="status",
        payload="body",
        origin="send_message_tool",
    )
    assert "action_id" in res
    assert res["disposition"] in ("held", "auto_sent")

    pending = client.list_pending()
    assert any(p["action_id"] == res["action_id"] for p in pending)


def test_client_has_no_send_or_git_push_method():
    # The assistant-side stub must NOT expose the internal executor verbs.
    assert not hasattr(BrokerClient, "send")
    assert not hasattr(BrokerClient, "git_push")
    assert not hasattr(broker_client, "send")


def test_fail_closed_when_broker_down_client_raises(tmp_path):
    # No server on this socket path => the client must RAISE, never fall back.
    dead_sock = tmp_path / "nonexistent.sock"
    client = BrokerClient(dead_sock)
    with pytest.raises(BrokerUnavailable):
        client.enqueue_action(
            type="message",
            channel="telegram",
            recipient="x",
            summary="s",
            payload="p",
            origin="send_message_tool",
        )


def test_assistant_cannot_self_approve_over_socket(broker):
    # THE P0-3 GATE. The assistant enqueues a held action, then tries to approve
    # its OWN action over the same enqueue socket. Without a broker-minted nonce
    # this MUST be rejected and NO egress may occur.
    server, executed = broker
    client = _client_for(server)
    res = client.enqueue_action(
        type="message",
        channel="telegram",
        recipient="board",
        summary="status",
        payload="body",
        origin="send_message_tool",
    )
    aid = res["action_id"]
    assert res["disposition"] == "held"

    with pytest.raises(BrokerError):
        client.approve(aid, nonce=None)
    with pytest.raises(BrokerError):
        client.approve(aid, nonce="i-made-this-up")

    assert executed == [], "self-approval leaked an egress"


def test_out_of_band_nonce_approve_executes(broker):
    # The sanctioned path: the broker mints a nonce (as when emitting the Telegram
    # button); approve WITH that nonce executes the egress exactly once.
    server, executed = broker
    client = _client_for(server)
    res = client.enqueue_action(
        type="message",
        channel="telegram",
        recipient="board",
        summary="status",
        payload="body",
        origin="send_message_tool",
    )
    aid = res["action_id"]
    nonce = server.mint_approval_nonce(aid)  # broker-side; assistant never sees this
    out = client.approve(aid, nonce=nonce)
    assert out["status"] in ("executed", "ok", "sent")
    assert executed == [aid]


def test_removing_nonce_check_would_break_self_approve_guard(broker):
    # Meta-assertion that the guard is REAL, not theatre: the same enqueued action
    # that rejects a no-nonce approve DOES execute with the minted nonce. If the
    # nonce check were removed, the no-nonce approve above would succeed and this
    # pairing would be inconsistent.
    server, executed = broker
    client = _client_for(server)
    res = client.enqueue_action(
        type="message", channel="telegram", recipient="board",
        summary="s", payload="b", origin="send_message_tool",
    )
    aid = res["action_id"]
    with pytest.raises(BrokerError):
        client.approve(aid, nonce=None)
    assert executed == []
    nonce = server.mint_approval_nonce(aid)
    client.approve(aid, nonce=nonce)
    assert executed == [aid]


def test_broker_has_main_entrypoint():
    # The server exposes a main() so it can run as its own PID (separate process).
    import broker.server as srv

    assert hasattr(srv, "main"), "broker/server.py needs a main() entrypoint"
