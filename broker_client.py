# ABOUTME: Assistant-side thin broker client — frames JSON-RPC over the broker's
# ABOUTME: unix socket and returns the result. Holds NO credentials and exposes
# ABOUTME: NO send/git_push verb (only enqueue_action/list_pending/approve/reject/
# ABOUTME: health), so there is one policy funnel. Fail-closed: if the broker/
# ABOUTME: socket is unavailable it RAISES BrokerUnavailable — never a silent
# ABOUTME: direct-send fallback. This is what makes egress go through the broker.
from __future__ import annotations

import socket
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from broker.jsonrpc import decode_frames, encode_request


class BrokerUnavailable(Exception):
    """
    Raised when the broker socket cannot be reached or the call fails at the
    transport layer. The caller MUST propagate this (fail-closed) — there is
    deliberately no direct-send fallback path.
    """


class BrokerError(Exception):
    """Raised when the broker returns a JSON-RPC error (e.g. approval rejected)."""


class BrokerClient:
    """
    Purpose: the only way the assistant reaches egress — a credential-free stub.
    Usage: BrokerClient(sock).enqueue_action(type=..., recipient=..., payload=...).
    Gotchas: intentionally lacks send()/git_push() (those are broker-internal); if
    the broker is down every call raises BrokerUnavailable (no fallback); one
    connection per call (simple, crash-safe — the broker is local and low-latency).
    """

    _req_counter = 0

    def __init__(
        self,
        socket_path: Path,
        *,
        timeout: float = 5.0,
        connector: Optional[Callable[[], socket.socket]] = None,
    ):
        self._socket_path = Path(socket_path)
        self._timeout = timeout
        # `connector` lets a test inject a pre-connected socket (e.g. one end of a
        # socketpair) so the real framing/dispatch is exercised where a filesystem
        # bind is unavailable. Production leaves it None => connect by path.
        self._connector = connector

    def _connect(self) -> socket.socket:
        if self._connector is not None:
            return self._connector()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        sock.connect(str(self._socket_path))
        return sock

    def _call(self, method: str, params: Dict[str, Any]) -> Any:
        """
        Purpose: send one JSON-RPC request and return its result (or raise).
        Usage: internal — used by every public method.
        Gotchas: connection refused / missing socket => BrokerUnavailable (fail
        -closed); a JSON-RPC error frame => BrokerError; reads until it has one
        complete newline-framed response.
        """
        BrokerClient._req_counter += 1
        req_id = BrokerClient._req_counter
        try:
            with self._connect() as sock:
                sock.sendall(encode_request(method, params, req_id))
                buf = b""
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    frames, _rem = decode_frames(buf)
                    if frames:
                        resp = frames[0]
                        if "error" in resp:
                            raise BrokerError(resp["error"].get("message", "broker error"))
                        return resp.get("result")
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError) as exc:
            raise BrokerUnavailable(f"broker unreachable at {self._socket_path}: {exc}") from exc
        raise BrokerUnavailable("broker closed connection without a response")

    def health(self) -> Dict[str, Any]:
        return self._call("health", {})

    def enqueue_action(
        self,
        *,
        type: str,
        summary: str,
        payload: str,
        origin: str,
        channel: Optional[str] = None,
        recipient: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Purpose: the assistant's SOLE egress verb — hand an action to the broker.
        Usage: res = client.enqueue_action(type="message", recipient=..., payload=...).
        Gotchas: returns {action_id, disposition}; disposition 'held' means it is
        awaiting nonce-gated approval. Never sends directly — the broker decides.
        """
        return self._call(
            "enqueue_action",
            {
                "type": type,
                "channel": channel,
                "recipient": recipient,
                "summary": summary,
                "payload": payload,
                "origin": origin,
            },
        )

    def list_pending(self) -> List[Dict[str, Any]]:
        return self._call("list_pending", {})

    def approve(self, action_id: str, *, nonce: Optional[str]) -> Dict[str, Any]:
        """
        Purpose: approve a held action — REQUIRES a broker-minted nonce.
        Usage: the gateway Telegram-callback calls this with the button's nonce.
        Gotchas: the assistant has no nonce (it goes to Telegram, not over this
        socket) so an assistant approve() is rejected by the broker (BrokerError).
        """
        return self._call("approve", {"action_id": action_id, "nonce": nonce})

    def reject(self, action_id: str, *, nonce: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
        return self._call("reject", {"action_id": action_id, "nonce": nonce, "reason": reason})
