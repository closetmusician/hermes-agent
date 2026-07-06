# ABOUTME: RED-first tests for broker/credentials.py — broker-only secret store.
# ABOUTME: Holds the broker_secret (nonce HMAC key) and egress creds; resolves
# ABOUTME: from a broker-scoped env/file the assistant does not load. Asserts the
# ABOUTME: broker_secret is stable per store and absent egress creds resolve to None
# ABOUTME: (fail-closed) rather than raising at construction.
from broker.credentials import BrokerCredentials


def test_broker_secret_is_stable(tmp_path):
    creds = BrokerCredentials(secret_dir=tmp_path)
    s1 = creds.broker_secret()
    s2 = creds.broker_secret()
    assert s1 == s2
    assert isinstance(s1, bytes) and len(s1) >= 32


def test_broker_secret_persists_across_instances(tmp_path):
    a = BrokerCredentials(secret_dir=tmp_path)
    secret = a.broker_secret()
    b = BrokerCredentials(secret_dir=tmp_path)
    assert b.broker_secret() == secret


def test_secret_file_is_owner_only(tmp_path):
    import stat

    creds = BrokerCredentials(secret_dir=tmp_path)
    creds.broker_secret()
    mode = stat.S_IMODE((tmp_path / "broker_secret").stat().st_mode)
    assert mode == 0o600


def test_egress_cred_lookup_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    creds = BrokerCredentials(secret_dir=tmp_path)
    assert creds.egress_cred("TELEGRAM_BOT_TOKEN") is None


def test_egress_cred_lookup_from_broker_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "live-token-123")
    creds = BrokerCredentials(secret_dir=tmp_path)
    assert creds.egress_cred("TELEGRAM_BOT_TOKEN") == "live-token-123"
