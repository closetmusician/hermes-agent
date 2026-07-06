# ABOUTME: Broker-only credential resolution — the secrets the assistant must
# ABOUTME: never hold. Owns the broker_secret (HMAC key for approval nonces) and
# ABOUTME: resolves egress creds from the broker's own scope. In P1a the egress
# ABOUTME: lookup reads the broker process env (the real starving/relocation of
# ABOUTME: assistant-side sources is P1a-h); the broker_secret is generated once
# ABOUTME: and persisted 0600 so nonces stay unforgeable across restarts.
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Optional


class BrokerCredentials:
    """
    Purpose: single source of the broker's own secrets, unreachable by the
    assistant. Holds the nonce-signing broker_secret and resolves egress creds.
    Usage: creds = BrokerCredentials(secret_dir=~/.hermes/broker); creds.broker_secret().
    Gotchas: broker_secret persists to a 0600 file so the same key survives a
    broker restart (in-flight nonces remain valid); if the file is world/group
    readable it would let another process forge nonces — the mode is asserted.
    """

    def __init__(self, secret_dir: Path):
        self._secret_dir = Path(secret_dir)
        self._secret_path = self._secret_dir / "broker_secret"
        self._cached: Optional[bytes] = None

    def broker_secret(self) -> bytes:
        """
        Purpose: return the stable HMAC key used to mint/validate approval nonces.
        Usage: hmac.new(creds.broker_secret(), ...) inside ApprovalAuthority.
        Gotchas: generated once (32 random bytes) and stored 0600; NEVER sent over
        the socket or handed to any client — its secrecy is what makes the nonce
        unforgeable and thus blocks assistant self-approval.
        """
        if self._cached is not None:
            return self._cached
        self._secret_dir.mkdir(parents=True, exist_ok=True)
        if self._secret_path.exists():
            self._cached = self._secret_path.read_bytes()
            return self._cached
        secret = secrets.token_bytes(32)
        # Create with 0600 from the start (umask-independent) to avoid a brief
        # window where the secret is world-readable.
        fd = os.open(str(self._secret_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, secret)
        finally:
            os.close(fd)
        os.chmod(self._secret_path, 0o600)
        self._cached = secret
        return secret

    def egress_cred(self, name: str) -> Optional[str]:
        """
        Purpose: resolve an egress secret (send/push token) the broker needs.
        Usage: token = creds.egress_cred("TELEGRAM_BOT_TOKEN") inside an executor.
        Gotchas: returns None when absent (fail-closed — the executor must refuse,
        never fall back). In P1a this reads the broker process env; P1a-h relocates
        the real secrets out of every assistant-loaded source into this broker scope.
        """
        return os.environ.get(name)
