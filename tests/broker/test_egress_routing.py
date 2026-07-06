# ABOUTME: RED-first tests for egress routing (P1a-h REQ-02/REQ-03). Proves that
# ABOUTME: with broker routing active + egress creds absent, send_message and the
# ABOUTME: `hermes send` CLI route through broker_client.enqueue_action OR refuse —
# ABOUTME: never a direct platform send. Also covers the raw-git-push refusal.
# ABOUTME: Real fail-closed broker_client (no internal mocks); egress path stubbed.
from __future__ import annotations

import json

import pytest


@pytest.fixture
def routing_on(monkeypatch):
    """Activate broker routing for the duration of a test."""
    monkeypatch.setenv("HERMES_BROKER_EGRESS_STARVE", "1")
    monkeypatch.setenv("HERMES_BROKER_ROUTE", "1")


def test_routing_active_reads_flag(monkeypatch):
    from tools.egress_broker import broker_routing_active

    monkeypatch.delenv("HERMES_BROKER_ROUTE", raising=False)
    assert broker_routing_active() is False
    monkeypatch.setenv("HERMES_BROKER_ROUTE", "1")
    assert broker_routing_active() is True


def test_send_message_routes_to_broker(routing_on, monkeypatch):
    """Gate #9: re-routed _handle_send frames enqueue_action, never an adapter."""
    import tools.send_message_tool as smt

    calls = {}

    def fake_enqueue(**kwargs):
        calls.update(kwargs)
        return {"action_id": "01ULID", "disposition": "held"}

    # Replace the assistant-side broker client's enqueue with a spy; the point
    # is the send path calls enqueue_action, not _send_to_platform.
    monkeypatch.setattr(smt, "_broker_enqueue", fake_enqueue, raising=False)

    # Blow up if the direct platform send is ever reached.
    def explode(*a, **k):
        raise AssertionError("direct _send_to_platform reached — routing bypassed")

    monkeypatch.setattr(smt, "_send_to_platform", explode)

    result = smt.send_message_tool(
        {"action": "send", "target": "telegram:123", "message": "hello"}
    )
    data = json.loads(result)
    assert calls.get("type") == "message"
    assert calls.get("payload") == "hello"
    assert data.get("routed") or data.get("action_id") or data.get("held")


def test_send_message_refuses_when_broker_down(routing_on, monkeypatch):
    """Broker unreachable ⇒ send_message errors (fail-closed), does NOT send."""
    import tools.send_message_tool as smt
    from broker_client import BrokerUnavailable

    def broker_down(**kwargs):
        raise BrokerUnavailable("broker unreachable")

    monkeypatch.setattr(smt, "_broker_enqueue", broker_down, raising=False)

    def explode(*a, **k):
        raise AssertionError("direct send attempted when broker down")

    monkeypatch.setattr(smt, "_send_to_platform", explode)

    result = smt.send_message_tool(
        {"action": "send", "target": "telegram:123", "message": "hello"}
    )
    data = json.loads(result)
    assert data.get("error"), "must surface an error, not send directly"
    assert "broker" in data["error"].lower()


def test_hermes_send_cli_refuses_or_routes(routing_on, monkeypatch, capsys):
    """Gate #1 (review P0-1): the REAL `hermes send` subcommand in an assistant
    context refuses OR frames enqueue_action — no direct adapter send."""
    from hermes_cli import send_cmd

    routed = {}

    def fake_enqueue(**kwargs):
        routed.update(kwargs)
        return {"action_id": "01ULID", "disposition": "held"}

    monkeypatch.setattr(send_cmd, "_broker_enqueue", fake_enqueue, raising=False)

    # If the CLI reaches the real tool send path, fail — it must route/refuse first.
    import tools.send_message_tool as smt

    def explode(*a, **k):
        raise AssertionError("hermes send reached direct platform send")

    monkeypatch.setattr(smt, "_send_to_platform", explode)

    import argparse

    args = argparse.Namespace(
        to="telegram", message="hello", file=None, subject=None,
        list_targets=False, quiet=False, json=True,
    )
    with pytest.raises(SystemExit) as exc:
        send_cmd.cmd_send(args)
    # Either routed (exit 0) or refused (nonzero) — but never a direct send.
    assert routed.get("type") == "message" or exc.value.code != 0


def test_raw_git_push_wrapper_refuses(routing_on, monkeypatch):
    """A raw `git push` through the broker git-push guard refuses when the
    sanctioned broker route isn't used (wall = cred-absence; this is the belt)."""
    from tools.egress_broker import guard_git_push

    # Simulate a raw push command the assistant might run via terminal.
    decision = guard_git_push("git push origin main")
    assert decision.blocked is True
    assert "broker" in decision.reason.lower()

    # A non-push git command is not blocked.
    assert guard_git_push("git status").blocked is False
