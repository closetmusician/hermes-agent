# ABOUTME: RED-first tests for the credential-starving mechanism (P1a-h REQ-01).
# ABOUTME: Proves the assistant's os.environ keeps its MODEL-inference key but is
# ABOUTME: stripped of every EGRESS/send/push secret after the real env_loader
# ABOUTME: load path runs with starving active, while the broker's cred loader
# ABOUTME: still resolves the egress secrets. No mocks — real filter logic.
from __future__ import annotations

import os

import pytest


# The two disjoint credential classes from design §1.6. The gate asserts the
# MODEL key survives (assistant can converse) and every EGRESS key is gone.
MODEL_KEYS = ["ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"]
EGRESS_KEYS = [
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
    "WEIXIN_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "SLACK_BOT_TOKEN",
    "SIGNAL_TOKEN",
    "MICROSOFT_GRAPH_CLIENT_SECRET",
    "MS_GRAPH_CLIENT_SECRET",
]


def _write_mixed_env(home):
    """Write a fixture ~/.hermes/.env holding BOTH credential classes."""
    lines = [f"{k}=model-secret-{k.lower()}" for k in MODEL_KEYS]
    lines += [f"{k}=egress-secret-{k.lower()}" for k in EGRESS_KEYS]
    (home / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_classify_splits_model_from_egress():
    """Every enumerated key classifies into exactly the intended class."""
    from hermes_cli.egress_creds import is_egress_credential

    for k in EGRESS_KEYS:
        assert is_egress_credential(k) is True, f"{k} must be EGRESS"
    for k in MODEL_KEYS:
        assert is_egress_credential(k) is False, f"{k} must NOT be EGRESS (model key)"


def test_starve_removes_only_egress(monkeypatch):
    """starve_egress_credentials strips EGRESS keys, keeps MODEL keys."""
    from hermes_cli.egress_creds import starve_egress_credentials

    for k in MODEL_KEYS:
        monkeypatch.setenv(k, "model-live")
    for k in EGRESS_KEYS:
        monkeypatch.setenv(k, "egress-live")

    removed = starve_egress_credentials(os.environ)

    for k in MODEL_KEYS:
        assert os.environ.get(k) == "model-live", f"{k} (model) must survive"
    for k in EGRESS_KEYS:
        assert k not in os.environ, f"{k} (egress) must be starved"
    assert set(removed) == set(EGRESS_KEYS)


def test_assistant_process_env_starved_of_egress_creds(tmp_path, monkeypatch):
    """Gate test #2 (review P0-2): after the REAL env_loader load path with
    starving active, os.environ has a live MODEL key and NO live EGRESS secret."""
    from hermes_cli import env_loader

    home = tmp_path / "hermes_home"
    home.mkdir()
    _write_mixed_env(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BROKER_EGRESS_STARVE", "1")

    env_loader.load_hermes_dotenv(hermes_home=str(home))

    assert os.environ.get("ANTHROPIC_API_KEY"), "MODEL key must be present"
    for k in EGRESS_KEYS:
        assert k not in os.environ, f"EGRESS {k} must be starved from process env"


def test_starve_off_by_default_is_noop(tmp_path, monkeypatch):
    """With the flag unset the loader is a NO-OP for the running gateway:
    egress creds still load (preserves current behavior until staged cutover)."""
    from hermes_cli import env_loader

    home = tmp_path / "hermes_home"
    home.mkdir()
    _write_mixed_env(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_BROKER_EGRESS_STARVE", raising=False)

    env_loader.load_hermes_dotenv(hermes_home=str(home))

    # Unstarved: both classes present, exactly as today.
    assert os.environ.get("ANTHROPIC_API_KEY")
    assert os.environ.get("TELEGRAM_BOT_TOKEN")


def test_broker_cred_loader_gets_egress(tmp_path, monkeypatch):
    """The broker's cred loader DOES resolve the egress keys from its own scope
    even while the assistant env is starved of them."""
    from hermes_cli.egress_creds import load_broker_egress_source
    from broker.credentials import BrokerCredentials

    # A broker-only egress source file, mode 0600, holding the egress secrets.
    src = tmp_path / "broker" / "egress.env"
    src.parent.mkdir(parents=True)
    src.write_text("\n".join(f"{k}=egress-{k}" for k in EGRESS_KEYS) + "\n", encoding="utf-8")
    os.chmod(src, 0o600)

    egress = load_broker_egress_source(src)
    for k in EGRESS_KEYS:
        assert egress.get(k) == f"egress-{k}"

    # And BrokerCredentials.egress_cred resolves against a scope holding them.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "egress-TELEGRAM_BOT_TOKEN")
    creds = BrokerCredentials(secret_dir=tmp_path / "broker")
    assert creds.egress_cred("TELEGRAM_BOT_TOKEN") == "egress-TELEGRAM_BOT_TOKEN"
