# ABOUTME: The broker server — a unix-domain-socket JSON-RPC listener that runs
# ABOUTME: as its own process and owns all egress. Dispatches enqueue_action (runs
# ABOUTME: the safe-lane, auto-sends or holds), list_pending, approve (nonce-gated
# ABOUTME: — self-approval-proof), reject, health, and resolve_model_key (REQ-03).
# ABOUTME: Uses an ExecutorRegistry for by-type dispatch so new action types (merge,
# ABOUTME: git_push) can be added without editing this file. Maintainer-authored per
# ABOUTME: P2-g-maint; never produced by a factory worker (immutable-ring boundary).
from __future__ import annotations

import os
import socket
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from broker.approval import ApprovalAuthority, ApprovalRejected
from broker.credentials import BrokerCredentials
from broker.executors import ExecutorRegistry, UnknownActionType
from broker.held_store import HeldStore
from broker.jsonrpc import decode_frames, encode_error, encode_response
from broker.safe_lane import SafeLane

# Methods the client stub is allowed to call. `send`/`git_push` are internal
# executor verbs and are deliberately NOT in this set — the assistant has exactly
# one entry point (enqueue_action), so there is one policy funnel.
# resolve_model_key is read-only: the supervisor uses it to fetch its worker's
# inference key without the broker handing over egress credentials.
_CLIENT_METHODS = {
    "health",
    "enqueue_action",
    "list_pending",
    "approve",
    "reject",
    "resolve_model_key",
    # auto_merge (P1b-e) carries NO nonce: the broker mints+burns the nonce
    # INTERNALLY after its own server-side gate passes. The mint verb is NEVER an
    # RPC — a client can request an auto-merge but can never obtain a nonce.
    "auto_merge",
}

# Provider → inference key name.  Only *_API_KEY names — no egress credentials.
# The broker resolves the VALUE from its own env (the only process that holds it).
_PROVIDER_KEY_MAP: Dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "cohere": "COHERE_API_KEY",
}


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
        # 'merge' joins the held-by-default/irreversible set (design §V2.4): a merge
        # to a protected branch can NEVER auto-send through the SafeLane path — the
        # ONLY release path is the tier-gated auto_merge RPC, whose binding condition
        # is the trust tier recomputed server-side (not C2/C3/C4/C5).
        irreversible_types={"git_push_force", "merge"},
        operational_cap=2000,
        allowed_origins=set(),
    )


class BrokerServer:
    """
    Purpose: the credential-holding broker process — sole egress path.
    Usage: BrokerServer(socket_path, db_path, credentials, executors={...}).serve_forever().
    Gotchas: single accept loop, one worker thread per connection; the socket is
    unlinked+recreated 0600 on bind. approve is nonce-gated by ApprovalAuthority —
    a client cannot self-approve. serve_forever blocks; run it in a thread or as a
    process. shutdown() stops the loop and removes the socket.
    The executors dict maps action-type strings to their executor callables; unknown
    types are rejected fail-closed (never dispatched to a wrong executor).
    """

    def __init__(
        self,
        *,
        socket_path: Path,
        db_path: Path,
        credentials: BrokerCredentials,
        executors: Dict[str, Any],
        safe_lane: Optional[SafeLane] = None,
        merge_gate: Optional[Any] = None,
    ):
        """
        Purpose: wire the server with a per-type executor registry.
        Usage: BrokerServer(socket_path=..., db_path=..., credentials=...,
               executors={"message": msg_exec, "git_push": push_exec},
               merge_gate=MergeGate(...)).
        Gotchas: `executors` is the authoritative type→callable map; passing an
        unknown action type through enqueue+approve fails closed (UnknownActionType
        surfaced as a JSON-RPC error).  The old `message_executor=` kwarg is gone —
        callers must use `executors={"message": ...}` (P2-g-maint clean break).
        `merge_gate` is the P1b-e server-side auto-merge gate: when absent, the
        auto_merge RPC fails CLOSED (every request → held) — a broker with no gate
        never auto-merges. Set it via the attribute or this kwarg to enable auto.
        """
        self._socket_path = Path(socket_path)
        self._store = HeldStore(db_path)
        self._creds = credentials
        self._registry = ExecutorRegistry(executors)
        self._safe_lane = safe_lane or _default_safe_lane()
        self._approval = ApprovalAuthority(self._store, credentials.broker_secret())
        # The P1b-e auto-merge gate (design §V2.1). None ⇒ auto_merge fails closed.
        self._merge_gate = merge_gate
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()

    def _executor_for(self, row: Dict[str, Any]) -> Any:
        """
        Purpose: resolve the executor for the given held-action row by its type.
        Usage: internal — called in enqueue (auto_sent path) and approve paths.
        Gotchas: raises UnknownActionType if the row's type has no registered executor;
        this is the fail-closed guarantee — an unrecognised type never runs a wrong
        executor.  The exception propagates up to _dispatch() which encodes it as an
        error frame so the client sees an explicit failure.
        """
        action_type = row.get("type") or ""
        return self._registry.get(action_type)

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
        UnknownActionType is surfaced as an error frame so callers see a clear
        failure when they supply an unregistered action type.
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
        except UnknownActionType as exc:
            return encode_error(-32002, f"unknown executor type: {exc}", req_id)
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
        Fail-closed: if the action type has no registered executor on the auto_sent
        path, UnknownActionType is raised — surfaced as error -32002.
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
            executor = self._executor_for(row)
            self._store.transition(aid, "held", "approved", decided_by="safe_lane")
            import json

            result = executor(row)
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
        The executor is resolved from the registry at approval time (by row type),
        not stored at enqueue time, so future executor changes take effect immediately.
        """
        aid = params.get("action_id")
        nonce = params.get("nonce")
        row = self._store.get(aid)
        if row is None:
            raise ApprovalRejected(f"unknown action {aid}")
        executor = self._executor_for(row)
        result = self._approval.approve(aid, nonce, executor=executor)
        return {"status": "executed", "result": result}

    def _rpc_auto_merge(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Purpose: the tier-gated auto-merge RPC (P1b-e, design §V2.2) — the ONLY
        no-nonce release path, and it is gated server-side. Given a held 'merge'
        action_id (NO nonce from the caller), the broker runs its own merge_gate
        (ring re-check + never-graduates + tier recompute over the diff, fail-closed);
        on AUTO-OK the broker MINTS+BURNS a nonce INTERNALLY and runs the SAME
        nonce+executor path as a human approval (decided_by="trust:auto"); on HELD it
        leaves the card held for a human tap.
        Usage: (supervisor) client.auto_merge(action_id).
        Gotchas: the caller carries NO nonce and cannot obtain one — the mint stays
        broker-internal, never crossing the socket. Fail-closed everywhere: no gate
        configured, non-merge/non-held row, or any gate 'held' → the merge does NOT
        execute. This closes review P0-1 (server-side re-check) and P0-2 (self-mint).
        """
        import time

        aid = params.get("action_id")
        row = self._store.get(aid)
        if row is None:
            raise ApprovalRejected(f"unknown action {aid}")
        # auto_merge only ever releases a HELD 'merge' card — anything else is a
        # fail-closed rejection (never let a message/push slip through this verb).
        if row.get("type") != "merge":
            raise ApprovalRejected(f"auto_merge only applies to 'merge' actions, not {row.get('type')!r}")
        if row.get("state") != "held":
            raise ApprovalRejected(f"action {aid} is not held (state={row.get('state')!r})")

        # No gate configured ⇒ fail closed (leave held). A broker that cannot
        # re-check never auto-merges.
        if self._merge_gate is None:
            return {"action_id": aid, "disposition": "held", "reason": "no_gate"}

        import json as _json

        try:
            card = _json.loads(row.get("payload") or "{}")
        except (TypeError, ValueError):
            return {"action_id": aid, "disposition": "held", "reason": "bad_payload"}

        disposition, tier, reason = self._merge_gate.evaluate(card, now_ms=int(time.time() * 1000))
        if disposition != "auto":
            # Held: the card stays held; a human still approves it via the normal
            # nonce-gated Telegram button. The merge does NOT execute here.
            return {"action_id": aid, "disposition": "held", "reason": reason, "tier": tier}

        # AUTO-OK: the broker mints+burns a nonce INTERNALLY and runs the identical
        # nonce+executor path as a human approval — the mint never crosses the socket.
        nonce = self._approval.mint_nonce(aid)
        executor = self._executor_for(row)
        result = self._approval.approve(aid, nonce, executor=executor, decided_by="trust:auto")
        return {"action_id": aid, "disposition": "auto", "tier": tier, "result": result}

    def _rpc_reject(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._approval.reject(params.get("action_id"), reason=params.get("reason"))
        return {"status": "rejected"}

    def _rpc_resolve_model_key(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Purpose: return the inference key name (and value) for the given provider.
        Usage: client.resolve_model_key(provider="anthropic") — used by the supervisor
        to build the scrubbed worker env (§2.3(A) P2-g-maint REQ-03).
        Gotchas: the broker is the SOLE credential holder.  This RPC returns ONLY
        a model-inference key (*_API_KEY) — it NEVER returns an egress credential
        (GITHUB_TOKEN, GH_TOKEN, TELEGRAM_BOT_TOKEN, etc.).  If the broker process
        does not have the key in its env, it returns None for the value so the caller
        can fail-closed rather than launching a worker with no model key.
        The key_name is always returned so the caller can name the env var correctly
        even when the value is absent.
        """
        provider = (params.get("provider") or "").lower()
        key_name = _PROVIDER_KEY_MAP.get(provider)
        if key_name is None:
            # Unknown provider — fail with a clear message rather than silently
            # returning None.  The caller should use a known provider name.
            raise ValueError(
                f"unknown provider {provider!r}; known: {sorted(_PROVIDER_KEY_MAP)}"
            )
        # Read the value from the broker's own env.  The broker process is the sole
        # holder of credentials; the supervisor calling this RPC has no direct access.
        value = os.environ.get(key_name)
        return {"key_name": key_name, "value": value}

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
    # P1a-h wires the REAL executors here: the broker process holds the egress tokens
    # (relocated out of the assistant env by the starving mechanism), so the send and
    # push engines actually deliver.  This is the only place real egress happens.
    from broker.executors.git_push_executor import build_git_push_executor
    from broker.executors.merge_executor import build_merge_executor
    from broker.executors.message_executor import build_message_executor
    from broker.executors.retro_diff_executor import build_retro_diff_executor

    server = BrokerServer(
        socket_path=home / "broker.sock",
        db_path=home / "held_actions.db",
        credentials=creds,
        executors={
            "message": build_message_executor(creds.egress_cred),
            "git_push": build_git_push_executor(creds.egress_cred),
            "merge": build_merge_executor(creds.egress_cred),
            # WALL 2 of the crown self-modification guard (P5 v2-C1): the retro_diff
            # apply door re-checks the ring over the exact hash-pinned bytes before
            # git-apply. The registry dispatches by type — no _rpc_approve edit.
            "retro_diff": build_retro_diff_executor(),
            # P6-a mission-control control types (job_reject / job_rescope /
            # queue_reprioritize) are deliberately NOT wired in this standalone
            # launch — their control_executor needs a factory-side RECONCILER, and
            # the broker imports nothing from factory (same rule as merge_gate). The
            # SUPERVISOR builds build_control_executor(reconciler) and passes the
            # three control types in via `executors=` when it constructs the server.
            # Absent here ⇒ any control card fails CLOSED (UnknownActionType) — a
            # standalone broker can never mutate the fleet.
        },
        # merge_gate is deliberately NOT wired here (fail-closed): the auto_merge RPC
        # holds every request until P1b-d injects a MergeGate whose compute_tier +
        # ledger reader come from the factory side (the broker imports nothing from
        # factory — the gate is constructed by the supervisor and passed in). A
        # standalone broker launch therefore never auto-merges — it holds for a human.
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
