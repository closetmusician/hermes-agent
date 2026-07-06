# ABOUTME: Hermes send/action broker package — the egress security boundary.
# ABOUTME: A separate process that owns all egress credentials, holds actions
# ABOUTME: durably (SQLite), enforces the 5-condition safe-lane, and exposes a
# ABOUTME: self-approval-proof approval surface (broker-minted single-use nonce).
# ABOUTME: The assistant reaches it only via broker_client over a unix socket.
__version__ = "0.1.0"
