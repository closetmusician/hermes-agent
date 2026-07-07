# ABOUTME: Approval authority — closes the P0-3 self-approval hole. The broker
# ABOUTME: mints a single-use, action-bound nonce (HMAC over action_id ∥ salt with
# ABOUTME: the broker-only secret) that is delivered OUT-OF-BAND in the Telegram
# ABOUTME: button, never over the enqueue socket. approve() requires a valid,
# ABOUTME: unburned nonce for THAT action; a caller without one (the assistant) is
# ABOUTME: REJECTED. Execution is idempotent by action_id (approve twice => one send).
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from typing import Any, Callable, Dict, Optional

from broker.held_store import HeldStore, IllegalTransition

# The executor receives the full held-action row and performs the real egress,
# returning a JSON-serialisable result dict. Injected so the security logic here
# is testable without real sends (and so P1a-h owns the actual send code).
Executor = Callable[[Dict[str, Any]], Dict[str, Any]]


class ApprovalRejected(Exception):
    """Raised when an approve() call lacks a valid, unburned, action-bound nonce."""


class ApprovalAuthority:
    """
    Purpose: the SEPARATE approval authority — who-may-approve is decoupled from
    who-may-enqueue. Mints/validates/burns nonces and drives held->executed.
    Usage: auth = ApprovalAuthority(store, broker_secret); n = auth.mint_nonce(aid);
    auth.approve(aid, n, executor=real_send).
    Gotchas: the ONLY way to obtain a valid nonce is auth.mint_nonce(), which the
    broker calls when it emits the Telegram button — the nonce goes to Telegram,
    NOT back over the enqueue socket, so the assistant can neither receive nor
    forge one (it lacks broker_secret). Removing the nonce check would make a
    self-approval test fail — that is the guard being real, not theatre.
    """

    def __init__(self, store: HeldStore, broker_secret: bytes):
        self._store = store
        self._secret = broker_secret
        self._lock = threading.Lock()
        # action_id -> valid nonce string, present only while unburned. Minting a
        # new nonce for an action replaces any prior one (last button wins).
        self._live: Dict[str, str] = {}

    def mint_nonce(self, action_id: str) -> str:
        """
        Purpose: mint a single-use nonce bound to this specific action_id.
        Usage: nonce = auth.mint_nonce(aid) — broker-side only, delivered via Telegram.
        Gotchas: the nonce = HMAC(broker_secret, action_id ∥ random_salt); it is
        unforgeable without the secret and unique per mint. Stored in _live so it
        can be validated and burned; it is NEVER returned over the enqueue socket.
        """
        salt = secrets.token_bytes(16)
        mac = hmac.new(self._secret, action_id.encode() + salt, hashlib.sha256).hexdigest()
        # Prefix with the salt so distinct mints for the same action differ.
        nonce = salt.hex() + "." + mac
        with self._lock:
            self._live[action_id] = nonce
        return nonce

    def _validate(self, action_id: str, nonce: Optional[str]) -> bool:
        """
        Purpose: constant-time check that `nonce` is the live nonce for action_id.
        Usage: internal — called by approve() before any egress.
        Gotchas: rejects None, wrong-action, expired/burned, and forged nonces.
        Uses hmac.compare_digest to avoid timing leaks on the comparison.
        """
        if not nonce:
            return False
        live = self._live.get(action_id)
        if live is None:
            return False
        return hmac.compare_digest(live, nonce)

    def approve(
        self,
        action_id: str,
        nonce: Optional[str],
        *,
        executor: Executor,
        decided_by: str = "telegram",
    ) -> Dict[str, Any]:
        """
        Purpose: validate the nonce, then execute the real egress exactly once.
        Usage: result = auth.approve(aid, nonce, executor=message_executor).
        Gotchas: (1) rejects any call without a valid action-bound nonce — this is
        the self-approval wall. (2) idempotent by action_id: if the action is
        already executed, returns the cached result WITHOUT re-sending, even with a
        fresh nonce. (3) burns the nonce on use so replay is rejected. `decided_by`
        labels the decision on the row: the human path passes "telegram" (default);
        the P1b-e broker auto-merge passes "trust:auto" (still nonce-gated — the
        broker minted the nonce internally after its server-side gate passed).
        """
        import json

        row = self._store.get(action_id)
        if row is None:
            raise ApprovalRejected(f"unknown action {action_id}")

        # Nonce validity is checked FIRST — a burned/forged/absent nonce is always
        # rejected, even on an already-executed action. This makes nonce replay
        # (re-tapping the same button) a rejection, not a silent no-op, while a
        # legitimately re-minted nonce still gets the idempotent cached result.
        with self._lock:
            if not self._validate(action_id, nonce):
                raise ApprovalRejected("invalid or missing approval nonce")
            # Burn the nonce BEFORE executing so a concurrent replay cannot slip in.
            self._live.pop(action_id, None)

        # Idempotency: with a valid (freshly minted) nonce on an action that is
        # already executed, return the cached result and do NOT re-run egress.
        if row["state"] == "executed":
            return json.loads(row["result_json"]) if row["result_json"] else {"status": "executed"}

        # Move held -> approved (also guards against a racing decision).
        self._store.transition(action_id, "held", "approved", decided_by=decided_by)
        try:
            result = executor(row)
        except Exception as exc:  # executor failed — mark failed, surface the error
            self._store.transition(action_id, "approved", "failed", decided_by=decided_by)
            raise

        self._store.record_result(action_id, json.dumps(result, default=str))
        self._store.transition(action_id, "approved", "executed", decided_by=decided_by)
        return result

    def reject(self, action_id: str, *, reason: Optional[str] = None) -> None:
        """
        Purpose: reject a held action so it is never sent.
        Usage: auth.reject(aid, reason="not now").
        Gotchas: burns any live nonce and moves held->rejected (terminal); no egress.
        """
        with self._lock:
            self._live.pop(action_id, None)
        self._store.transition(action_id, "held", "rejected", decided_by="telegram")
