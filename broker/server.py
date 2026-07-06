# ABOUTME: The broker server — a unix-domain-socket JSON-RPC listener that runs
# ABOUTME: as its own process and owns all egress. Dispatches enqueue_action (runs
# ABOUTME: the safe-lane, auto-sends or holds), list_pending, approve (nonce-gated
# ABOUTME: — self-approval-proof), reject, and health. The socket is created 0600,
# ABOUTME: owner-only, no network listener ever. main() is the __main__ entrypoint
# ABOUTME: so the broker can run as a separate PID under its own launchd service.
from __future__ import annotations

import os
import socket
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from broker.approval import ApprovalAuthority, ApprovalRejected
from broker.credentials import BrokerCredentials
from broker.held_store import HeldStore
from broker.jsonrpc import decode_frames, encode_error, encode_response
from broker.safe_lane import SafeLane

# The message executor performs the real send. Injected (P1a-h owns the real one)
# so the server's routing/safe-lane/approval logic is testable without egress.
MessageExecutor = Callable[[Dict[str, Any]], Dict[str, Any]]

# Methods the client stub is allowed to call. `send`/`git_push` are internal
# executor verbs and are deliberately NOT in this set — the assistant has exactly
# one entry point (enqueue_action), so there is one policy funnel.
_CLIENT_METHODS = {"health", "enqueue_action", "list_pending", "approve", "reject"}


def _default_safe_lane() -> SafeLane:
    """
    Purpose: the P1a conservative default safe-lane (near-empty allow).
    Usage: used when the server is constructed without an explicit lane.
    Gotchas: allowed_origins is empty ⇒ C4 fails ⇒ essentially everything holds,
    the intended "start empty" posture. P1b widens this via policy files.
    """
    return SafeLane(
        allow_list=set(),
        strategic_markers={"board", "exec", "legal", "pricing", "confidential"},
        irreversible_types={"git_push_force"},
        operational_cap=2000,
        allowed_origins=set(),
    )


class BrokerServer:
    """
    Purpose: the credential-holding broker process — sole egress path.
    Usage: BrokerServer(socket_path, db_path, credentials, message_executor).serve_forever().
    Gotchas: single accept loop, one worker thread per connection; the socket is
    unlinked+recreated 0600 on bind. approve is nonce-gated by ApprovalAuthority —
    a client cannot self-approve. serve_forever blocks; run it in a thread or as a
    process. shutdown() stops the loop and removes the socket.
    """

    def __init__(
        self,
        *,
        socket_path: Path,
        db_path: Path,
        credentials: BrokerCredentials,
        message_executor: MessageExecutor,
        safe_lane: Optional[SafeLane] = None,
    ):
        self._socket_path = Path(socket_path)
        self._store = HeldStore(db_path)
        self._creds = credentials
        self._executor = message_executor
        self._safe_lane = safe_lane or _default_safe_lane()
        self._approval = ApprovalAuthority(self._store, credentials.broker_secret())
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()

    # -- lifecycle ----------------------------------------------------------

    def _bind(self) -> None:
        """
        Purpose: bind the unix socket 0600, owner-only, replacing any stale file.
        Usage: internal — called at the top of serve_forever.
        Gotchas: a leftover socket file from a crashed broker is unlinked first;
        the 0600 mode is set both via umask-safe fchmod so no other UID can connect.
        """
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self._socket_path.exists():
            self._socket_path.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # Restrict before anyone can connect.
        old_umask = os.umask(0o177)
        try:
            srv.bind(str(self._socket_path))
        finally:
            os.umask(old_umask)
        os.chmod(self._socket_path, 0o600)
        srv.listen(16)
        srv.settimeout(0.2)  # so the accept loop can observe _stop
        self._sock = srv

    def serve_forever(self) -> None:
        """
        Purpose: accept connections and dispatch JSON-RPC until shutdown().
        Usage: run in a thread (tests) or as the process main loop.
        Gotchas: each connection gets a daemon thread; accept has a short timeout
        so shutdown() is observed promptly. Errors in a connection never take down
        the loop (fail-isolated per connection).
        """
        self._bind()
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()  # type: ignore[union-attr]
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve_conn, args=(conn,), daemon=True).start()
        self._cleanup()

    def shutdown(self) -> None:
        """Stop the accept loop and remove the socket file."""
        self._stop.set()
        self._cleanup()

    def _cleanup(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        try:
            if self._socket_path.exists():
                self._socket_path.unlink()
        except OSError:
            pass

    # -- connection handling ------------------------------------------------

    def serve_connection(self, conn: socket.socket) -> None:
        """
        Purpose: serve one already-connected socket — the transport-agnostic core.
        Usage: run against an accept()ed socket, OR one end of a socketpair (tests
        exercise the real framing/dispatch/nonce logic without a filesystem bind).
        Gotchas: blocks until the peer closes; identical code path as production so
        a socketpair-driven test is not a mock — it is the real dispatch.
        """
        self._serve_conn(conn)

    def _serve_conn(self, conn: socket.socket) -> None:
        """
        Purpose: read newline-framed JSON-RPC frames off one connection and reply.
        Usage: internal — one thread per accepted connection.
        Gotchas: a half-written trailing frame stays buffered (never misparsed);
        a malformed complete frame yields a JSON-RPC error, not a crash.
        """
        buf = b""
        try:
            while not self._stop.is_set():
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                try:
                    frames, buf = decode_frames(buf)
                except ValueError:
                    conn.sendall(encode_error(-32700, "parse error", None))
                    buf = b""
                    continue
                for frame in frames:
                    conn.sendall(self._dispatch(frame))
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _dispatch(self, frame: Dict[str, Any]) -> bytes:
        """
        Purpose: route one JSON-RPC request to its handler and encode the reply.
        Usage: internal — returns the newline-framed response bytes.
        Gotchas: only _CLIENT_METHODS are reachable over the socket; unknown or
        internal-only verbs return a method-not-found error. ApprovalRejected is
        surfaced as an error frame so the assistant's self-approve attempt fails loud.
        """
        req_id = frame.get("id")
        method = frame.get("method")
        params = frame.get("params") or {}
        if method not in _CLIENT_METHODS:
            return encode_error(-32601, f"method not found: {method}", req_id)
        try:
            handler = getattr(self, f"_rpc_{method}")
            result = handler(params)
            return encode_response(result, req_id)
        except ApprovalRejected as exc:
            return encode_error(-32001, f"approval rejected: {exc}", req_id)
        except Exception as exc:  # defensive: never leak a stack over the socket
            return encode_error(-32000, str(exc), req_id)

    # -- RPC handlers -------------------------------------------------------

    def _rpc_health(self, params: Dict[str, Any]) -> Dict[str, Any]:
        pending = self._store.list_pending()
        return {"status": "ok", "pending_count": len(pending)}

    def _rpc_enqueue_action(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Purpose: run the safe-lane; auto-send (all 5 hold) or durably hold.
        Usage: client.enqueue_action(type=..., recipient=..., payload=..., origin=...).
        Gotchas: the row is committed BEFORE this returns (commit-before-ack). On
        auto_sent the executor runs and the row is recorded executed; on held the
        row waits for a nonce-gated approve. This is the assistant's ONLY egress verb.
        """
        action = {
            "type": params.get("type"),
            "channel": params.get("channel"),
            "recipient": params.get("recipient"),
            "payload": params.get("payload"),
            "origin": params.get("origin"),
        }
        decision = self._safe_lane.evaluate(action)
        aid = self._store.enqueue(
            type=params.get("type") or "",
            channel=params.get("channel"),
            recipient=params.get("recipient"),
            summary=params.get("summary") or "",
            payload=params.get("payload") or "",
            origin=params.get("origin") or "",
            safe_lane_json=decision.to_json(),
            state="held",
        )
        if decision.disposition == "auto_sent":
            row = self._store.get(aid)
            self._store.transition(aid, "held", "approved", decided_by="safe_lane")
            import json

            result = self._executor(row)
            self._store.record_result(aid, json.dumps(result, default=str))
            self._store.transition(aid, "approved", "executed", decided_by="safe_lane")
            return {"action_id": aid, "disposition": "auto_sent"}
        return {"action_id": aid, "disposition": "held"}

    def _rpc_list_pending(self, params: Dict[str, Any]) -> Any:
        return self._store.list_pending()

    def _rpc_approve(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Purpose: nonce-gated approve — executes egress only with a valid nonce.
        Usage: (gateway Telegram-callback) client.approve(action_id, nonce).
        Gotchas: a call without a valid broker-minted nonce raises ApprovalRejected
        → error frame → the assistant cannot self-approve. Idempotent by action_id.
        """
        aid = params.get("action_id")
        nonce = params.get("nonce")
        result = self._approval.approve(aid, nonce, executor=self._executor)
        return {"status": "executed", "result": result}

    def _rpc_reject(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._approval.reject(params.get("action_id"), reason=params.get("reason"))
        return {"status": "rejected"}

    # -- broker-side only (never over the client socket) --------------------

    def mint_approval_nonce(self, action_id: str) -> str:
        """
        Purpose: broker-side nonce mint for the Telegram button (out-of-band).
        Usage: nonce = server.mint_approval_nonce(aid); attach to the inline button.
        Gotchas: this is NOT an RPC method — it is only callable in-process by the
        broker/gateway when emitting the approval button, so the nonce never
        travels back over the enqueue socket the assistant uses.
        """
        return self._approval.mint_nonce(action_id)


def main() -> None:
    """
    Purpose: process entrypoint — run the broker as its own PID.
    Usage: python -m broker.server (under com.hermes.broker launchd service).
    Gotchas: paths default to ~/.hermes/broker/*; the real message executor is
    wired by P1a-h. Here we fail-closed with a stub executor that refuses so a
    misconfigured standalone launch never silently sends.
    """
    home = Path(os.path.expanduser("~/.hermes/broker"))

    creds = BrokerCredentials(secret_dir=home)
    # P1a-h wires the REAL message executor here: the broker process holds the
    # egress tokens (relocated out of the assistant env by the starving mechanism),
    # so the send engine it invokes actually delivers. This is the only place a
    # real message send happens after the cutover.
    from broker.executors.message_executor import build_message_executor

    server = BrokerServer(
        socket_path=home / "broker.sock",
        db_path=home / "held_actions.db",
        credentials=creds,
        message_executor=build_message_executor(creds.egress_cred),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
