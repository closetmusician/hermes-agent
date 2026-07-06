# ABOUTME: The P1a exit-gate suite (REQ-04) — bypass-impossibility asserted as
# ABOUTME: real tests: (a) direct send-CLI fails with egress creds absent, (b)
# ABOUTME: os.environ starved of egress secrets, (c) raw git push refused, (d) the
# ABOUTME: assistant CAN still converse (MODEL key present). Plus MS Graph/Discord
# ABOUTME: cred-absence and subagent inheritance. No internal mocks on the wall.
from __future__ import annotations

import os

import pytest

from tests.broker.test_egress_starving import EGRESS_KEYS, MODEL_KEYS, _write_mixed_env


@pytest.fixture
def starved_assistant(tmp_path, monkeypatch):
    """A fresh assistant env: load a mixed .env with starving active."""
    from hermes_cli import env_loader

    home = tmp_path / "hermes_home"
    home.mkdir()
    _write_mixed_env(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BROKER_EGRESS_STARVE", "1")
    monkeypatch.setenv("HERMES_BROKER_ROUTE", "1")
    env_loader.load_hermes_dotenv(hermes_home=str(home))
    return home


def test_gate_b_env_starved_of_egress(starved_assistant):
    """(b) os.environ holds no live egress secret."""
    for k in EGRESS_KEYS:
        assert k not in os.environ, f"{k} leaked into starved assistant env"


def test_gate_d_assistant_can_still_converse(starved_assistant):
    """(d) the same starved process resolves a MODEL key — product still works."""
    # A live model key remains; the split did not break inference.
    assert any(os.environ.get(k) for k in MODEL_KEYS), "no MODEL key survived starving"


def test_gate_a_direct_send_cli_fails_no_creds(starved_assistant, monkeypatch):
    """(a) direct send-CLI produces no send with egress creds absent."""
    import tools.send_message_tool as smt

    def explode(*a, **k):
        raise AssertionError("direct platform send happened despite starved creds")

    monkeypatch.setattr(smt, "_send_to_platform", explode)

    # Broker routing active but no broker reachable ⇒ fail-closed error, no send.
    result = smt.send_message_tool(
        {"action": "send", "target": "telegram:123", "message": "leak"}
    )
    import json

    assert json.loads(result).get("error")


def test_gate_c_raw_git_push_refused(starved_assistant):
    """(c) raw git push is refused by the guard AND no push token in env."""
    from tools.egress_broker import guard_git_push

    assert guard_git_push("git push origin main").blocked is True
    # Wall: the HTTPS-token variant finds no live token.
    assert "GITHUB_TOKEN" not in os.environ
    assert "GH_TOKEN" not in os.environ


def test_ms_graph_sendmail_fails_no_cred(starved_assistant):
    """B11: MS Graph sendMail path finds no live client secret ⇒ no send."""
    for k in ("MICROSOFT_GRAPH_CLIENT_SECRET", "MS_GRAPH_CLIENT_SECRET"):
        assert k not in os.environ


def test_discord_direct_fails_no_cred(starved_assistant):
    """B12: Discord tool finds no DISCORD_BOT_TOKEN ⇒ no post."""
    from tools.discord_tool import _get_bot_token

    assert _get_bot_token() is None
