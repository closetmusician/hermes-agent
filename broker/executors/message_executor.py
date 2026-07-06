# ABOUTME: The broker's real message executor — turns an approved held-action row
# ABOUTME: into an actual platform send. This is the ONLY message egress in the
# ABOUTME: system after the P1a-h cutover; it runs in the broker process (which
# ABOUTME: holds the send tokens the assistant lacks). Reuses the existing send
# ABOUTME: engine (send_message_tool) rather than reimplementing 9 platform senders.
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional


def build_message_executor(
    egress_cred: Optional[Callable[[str], Optional[str]]] = None,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """
    Purpose: construct the executor the BrokerServer calls to perform a real send.
    Usage: server = BrokerServer(..., message_executor=build_message_executor(creds.egress_cred))
    Gotchas: the returned callable takes a held_actions row dict ({type, channel,
    recipient, payload, ...}) and returns a result dict. It runs the real
    send_message engine, which reads platform tokens from the BROKER process env
    (where the starving mechanism relocated them). If a token is absent the send
    engine surfaces an error — the executor returns it (fail-closed), it never
    fabricates success. `egress_cred` is accepted for future keychain-backed
    resolution; in P1a the send engine reads os.environ directly.
    """

    def _execute(row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Purpose: perform one real message send for an approved/auto-sent row.
        Usage: result = executor(row) — called by the broker, never the assistant.
        Gotchas: builds the send-tool target as `channel:recipient` (or bare
        channel for a home-channel send); returns the parsed tool JSON. Any
        exception is caught and returned as an error dict so a bad row can never
        crash the broker's accept loop.
        """
        channel = (row.get("channel") or "").strip()
        recipient = (row.get("recipient") or "").strip()
        payload = row.get("payload") or ""
        if not channel:
            return {"success": False, "error": "message row has no channel"}
        target = f"{channel}:{recipient}" if recipient else channel
        try:
            # Import lazily so the broker package has no import-time dependency on
            # the full tool/gateway stack (keeps `python -m broker.server` light).
            from tools.send_message_tool import send_message_tool

            # _broker_direct=True: run the real platform send, NOT the routing
            # gate (this call IS the broker; re-routing would recurse forever).
            raw = send_message_tool(
                {
                    "action": "send",
                    "target": target,
                    "message": payload,
                    "_broker_direct": True,
                }
            )
            try:
                return json.loads(raw) if isinstance(raw, str) else dict(raw)
            except (json.JSONDecodeError, TypeError):
                return {"success": False, "error": "unparseable send result", "raw": raw}
        except Exception as exc:  # noqa: BLE001 — never crash the broker on a send
            return {"success": False, "error": f"message send failed: {exc}"}

    return _execute
