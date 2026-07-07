# ABOUTME: RED-first tests for P2-g-maint: the type→executor registry on BrokerServer.
# ABOUTME: Covers REQ-01 (registry dispatch: message/git_push route correctly, unknown
# ABOUTME: type fails closed), REQ-02 (new executor registration without editing server.py),
# ABOUTME: and REQ-03 (resolve_model_key RPC returns a model key, never an egress key).
# ABOUTME: All tests use real BrokerServer objects — no mocks of the dispatch path.
import socket
import threading
from pathlib import Path
from typing import Any, Dict

import pytest

from broker.credentials import BrokerCredentials
from broker.executors import ExecutorRegistry, UnknownActionType
from broker.server import BrokerServer
from broker_client import BrokerClient, BrokerError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def creds(tmp_path) -> BrokerCredentials:
    return BrokerCredentials(secret_dir=tmp_path)


def _make_server(
    tmp_path: Path,
    creds: BrokerCredentials,
    extra_executors: Dict[str, Any] | None = None,
) -> tuple[BrokerServer, list, list]:
    """Build a BrokerServer with per-type execution tracking.

    Returns (server, message_calls, git_push_calls).  Optionally merges
    extra_executors into the registry so tests can inject a 'merge' executor.
    """
    message_calls: list = []
    git_push_calls: list = []

    def _msg(row: Dict[str, Any]) -> Dict[str, Any]:
        message_calls.append(row["action_id"])
        return {"status": "sent"}

    def _push(row: Dict[str, Any]) -> Dict[str, Any]:
        git_push_calls.append(row["action_id"])
        return {"status": "pushed"}

    executors: Dict[str, Any] = {"message": _msg, "git_push": _push}
    if extra_executors:
        executors.update(extra_executors)

    server = BrokerServer(
        socket_path=tmp_path / "broker.sock",
        db_path=tmp_path / "held_actions.db",
        credentials=creds,
        executors=executors,
    )
    return server, message_calls, git_push_calls


def _client_for(server: BrokerServer) -> BrokerClient:
    """Wire a BrokerClient over a socketpair so every call exercises real dispatch."""

    def connector():
        cli_end, srv_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        t = threading.Thread(target=server.serve_connection, args=(srv_end,), daemon=True)
        t.start()
        return cli_end

    return BrokerClient(socket_path="/unused", connector=connector)


# ---------------------------------------------------------------------------
# REQ-01: message actions route to the message executor
# ---------------------------------------------------------------------------


def test_message_action_dispatches_to_message_executor(tmp_path, creds):
    """Enqueuing a 'message' action and approving it calls the message executor,
    NOT the git_push executor.  Registry dispatch, not the old single-executor path."""
    server, msg_calls, push_calls = _make_server(tmp_path, creds)
    client = _client_for(server)

    res = client.enqueue_action(
        type="message",
        channel="telegram",
        recipient="board",
        summary="test message",
        payload="hello",
        origin="send_message_tool",
    )
    assert res["disposition"] == "held"
    aid = res["action_id"]

    nonce = server.mint_approval_nonce(aid)
    out = client.approve(aid, nonce=nonce)
    assert out["status"] in ("executed", "ok", "sent")

    assert msg_calls == [aid], "message executor should have been called"
    assert push_calls == [], "git_push executor must NOT be called for a message action"


# ---------------------------------------------------------------------------
# REQ-01: unknown type is rejected cleanly (fail-closed)
# ---------------------------------------------------------------------------


def test_unknown_action_type_fails_closed(tmp_path, creds):
    """An action with an unregistered type is rejected — the broker must NEVER
    run the wrong executor or silently succeed.  Fail-closed is the invariant."""
    server, msg_calls, push_calls = _make_server(tmp_path, creds)
    client = _client_for(server)

    res = client.enqueue_action(
        type="unknown_future_type",
        channel="telegram",
        recipient="board",
        summary="should be rejected",
        payload="x",
        origin="test",
    )
    # The action may be held (safe-lane conservatism) or auto_sent, but approval
    # MUST fail because there is no executor for this type.
    aid = res["action_id"]
    nonce = server.mint_approval_nonce(aid)

    with pytest.raises(BrokerError, match="(?i)(unknown|executor|type|not found|registry)"):
        client.approve(aid, nonce=nonce)

    assert msg_calls == [], "no executor should have been called for unknown type"
    assert push_calls == [], "no executor should have been called for unknown type"


# ---------------------------------------------------------------------------
# REQ-01: registry IS the dispatch path (regression — message_executor kwarg gone)
# ---------------------------------------------------------------------------


def test_server_rejects_old_single_executor_kwarg(tmp_path, creds):
    """BrokerServer must no longer accept the old message_executor= kwarg.

    If it still accepts the old interface and uses a single executor for all
    types, the per-type registry is not the real dispatch path.  This test
    asserts the old signature is gone.
    """
    with pytest.raises(TypeError):
        BrokerServer(
            socket_path=tmp_path / "broker.sock",
            db_path=tmp_path / "held_actions.db",
            credentials=creds,
            message_executor=lambda row: {"status": "sent"},
        )


# ---------------------------------------------------------------------------
# REQ-02: new executor can be registered and dispatched without editing server.py
# ---------------------------------------------------------------------------


def test_new_executor_registered_without_editing_server(tmp_path, creds):
    """P2-g must be able to add a 'merge' executor by passing it in the `executors`
    dict — no server.py modification required.  This is the seam REQ-02 requires."""
    merge_calls: list = []

    def _merge(row: Dict[str, Any]) -> Dict[str, Any]:
        merge_calls.append(row["action_id"])
        return {"status": "merged"}

    server, _, _ = _make_server(tmp_path, creds, extra_executors={"merge": _merge})
    client = _client_for(server)

    res = client.enqueue_action(
        type="merge",
        channel="telegram",
        recipient="board",
        summary="merge PR #42",
        payload="{}",
        origin="factory/supervisor",
    )
    aid = res["action_id"]
    nonce = server.mint_approval_nonce(aid)
    out = client.approve(aid, nonce=nonce)
    assert out["status"] in ("executed", "ok", "merged")
    assert merge_calls == [aid], "registered merge executor must have been called"


# ---------------------------------------------------------------------------
# REQ-02: ExecutorRegistry itself can be built and queried standalone
# ---------------------------------------------------------------------------


def test_executor_registry_standalone():
    """The registry can be built standalone and resolves registered types."""
    called = []

    def _exec(row):
        called.append(row.get("action_id"))
        return {}

    registry = ExecutorRegistry({"message": _exec})
    # Must resolve the registered type
    resolved = registry.get("message")
    assert resolved is _exec


def test_executor_registry_unknown_type_raises():
    """Unknown type raises UnknownActionType (not KeyError or silent None)."""
    registry = ExecutorRegistry({"message": lambda row: {}})
    with pytest.raises(UnknownActionType):
        registry.get("bogus_type")


# ---------------------------------------------------------------------------
# REQ-03: resolve_model_key RPC — returns a model key, not an egress key
# ---------------------------------------------------------------------------


def test_resolve_model_key_is_in_client_methods(tmp_path, creds):
    """resolve_model_key must be callable over the socket (a registered RPC method)."""
    server, _, _ = _make_server(tmp_path, creds)
    client = _client_for(server)

    result = client.resolve_model_key(provider="anthropic")
    # The result is a dict with at least a 'model_key' or 'key_name' field.
    assert isinstance(result, dict)
    assert "model_key" in result or "key_name" in result or "value" in result


def test_resolve_model_key_is_never_an_egress_key(tmp_path, creds):
    """The resolved key name must end in _API_KEY and must NOT be an egress key name
    (GITHUB_TOKEN, GH_TOKEN, TELEGRAM_*, etc.).  The broker gives the supervisor
    exactly one inference key, never a push or send credential."""
    server, _, _ = _make_server(tmp_path, creds)
    client = _client_for(server)

    for provider in ("anthropic", "openai"):
        result = client.resolve_model_key(provider=provider)
        # Extract the key name from whichever field the RPC uses
        key_name = result.get("key_name") or result.get("model_key") or ""
        assert key_name, f"resolve_model_key for {provider!r} returned no key name"

        # Must end in _API_KEY (model inference key pattern)
        assert key_name.upper().endswith("_API_KEY"), (
            f"{key_name!r} does not end in _API_KEY — inference keys must follow "
            "the *_API_KEY pattern"
        )

        # Must NOT be an egress key name
        _EGRESS_NAMES = {
            "GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN",
            "TELEGRAM_BOT_TOKEN", "SLACK_BOT_TOKEN",
            "DISCORD_BOT_TOKEN", "SENDGRID_API_KEY",
        }
        assert key_name.upper() not in _EGRESS_NAMES, (
            f"resolve_model_key must never return an egress credential, got {key_name!r}"
        )


def test_resolve_model_key_not_exposed_over_enqueue_socket_without_explicit_call(
    tmp_path, creds
):
    """The resolve_model_key RPC ONLY returns a key NAME (+ possibly a value the
    supervisor can use), never an actual egress token.  Specifically, the response
    must NOT contain the literal value of GITHUB_TOKEN or GH_TOKEN from os.environ."""
    import os

    # Seed a fake push token into the env (simulating a non-starved host)
    fake_token = "ghp_fakepushtoken99"
    os.environ["GITHUB_TOKEN"] = fake_token
    try:
        server, _, _ = _make_server(tmp_path, creds)
        client = _client_for(server)
        result = client.resolve_model_key(provider="anthropic")
        # Flatten result to string and assert the push token is NOT present
        result_str = str(result)
        assert fake_token not in result_str, (
            "resolve_model_key response must never contain an egress token value"
        )
    finally:
        os.environ.pop("GITHUB_TOKEN", None)


# ---------------------------------------------------------------------------
# REQ-04: backward-compat — existing tests still pass (no regressions)
# ---------------------------------------------------------------------------


def test_existing_server_functionality_preserved(tmp_path, creds):
    """Regression: health, enqueue, list_pending still work with the registry
    interface.  The registry refactor must not break any existing behaviour."""
    server, msg_calls, _ = _make_server(tmp_path, creds)
    client = _client_for(server)

    h = client.health()
    assert h["status"] in ("ok", "healthy")

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
