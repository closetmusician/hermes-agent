# ABOUTME: Assistant-side egress routing gate (design §1.3, §1.7). When broker
# ABOUTME: routing is active, every send/push must go through broker_client
# ABOUTME: (enqueue_action) or be refused — never a direct platform send. Holds
# ABOUTME: NO credentials. Feature-flagged OFF by default so the running gateway
# ABOUTME: keeps its current direct-send behavior until the staged cutover.
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


def broker_routing_active(environ=None) -> bool:
    """
    Purpose: report whether egress must be routed through the broker.
    Usage: if broker_routing_active(): route via broker instead of sending direct.
    Gotchas: gated by HERMES_BROKER_ROUTE=1, default OFF — a no-op for the running
    gateway until the owner does the staged cutover. Keep this disjoint from the
    starving flag so routing and cred-absence can be staged/tested independently.
    """
    env = environ if environ is not None else os.environ
    return env.get("HERMES_BROKER_ROUTE", "").strip() in ("1", "true", "yes")


def _broker_socket_path() -> Path:
    """Resolve the broker unix-socket path (mirrors broker/server.py main())."""
    home = Path(os.path.expanduser("~/.hermes/broker"))
    hh = os.getenv("HERMES_HOME")
    if hh:
        home = Path(hh) / "broker"
    return home / "broker.sock"


def _broker_enqueue(**kwargs: Any) -> Dict[str, Any]:
    """
    Purpose: the single assistant-side call that hands an egress action to the
    broker. Frames enqueue_action over the UDS; holds no credentials.
    Usage: _broker_enqueue(type="message", channel=..., recipient=..., payload=...).
    Gotchas: raises BrokerUnavailable (fail-closed) if the broker is down — the
    caller MUST surface that as an error, never fall back to a direct send. This
    function is the seam tests patch to spy on routing without a live broker.
    """
    from broker_client import BrokerClient

    client = BrokerClient(_broker_socket_path())
    return client.enqueue_action(**kwargs)


@dataclass
class GitPushDecision:
    """Outcome of the git-push guard: blocked (with reason) or allowed."""

    blocked: bool
    reason: str = ""


def guard_git_push(command: str) -> GitPushDecision:
    """
    Purpose: belt that refuses a raw `git push` when broker routing is active —
    the sanctioned push path is enqueue_action{type:"git_push"}. The WALL is
    cred-absence (no push token in the assistant env); this guard is the loud belt.
    Usage: decision = guard_git_push("git push origin main"); if decision.blocked: refuse.
    Gotchas: matches `git ... push` (subcommand form), not the substring "push"
    in an unrelated path; a non-push git command is allowed. Regex evasion is
    expected and acceptable — the token-absence wall closes the actual send.
    """
    import re

    text = (command or "").strip()
    # `git` followed (allowing global flags like -C <dir>) by the `push` verb.
    if re.search(r"\bgit\b(?:\s+-\S+(?:\s+\S+)?)*\s+push\b", text):
        return GitPushDecision(
            blocked=True,
            reason=(
                "raw `git push` is not permitted from the assistant — route via "
                "the broker (enqueue_action type=git_push). Push credentials live "
                "only in the broker process."
            ),
        )
    return GitPushDecision(blocked=False)


def route_message(
    *,
    channel: Optional[str],
    recipient: Optional[str],
    payload: str,
    origin: str,
    summary: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Purpose: route a message send through the broker and normalize the result
    into the send-tool's JSON shape (`{routed, action_id, disposition}` on
    success). Shared by send_message_tool and the `hermes send` CLI.
    Usage: res = route_message(channel="telegram", recipient="123", payload=msg, origin="send_message").
    Gotchas: on BrokerUnavailable returns an `{error: ...}` dict (fail-closed) —
    the caller renders it; there is deliberately NO direct-send fallback.
    """
    from broker_client import BrokerUnavailable, BrokerError

    summary = summary or (payload[:120] if payload else "")
    try:
        res = _broker_enqueue(
            type="message",
            channel=channel,
            recipient=recipient,
            summary=summary,
            payload=payload,
            origin=origin,
        )
    except BrokerUnavailable as exc:
        return {"error": f"broker unavailable — message not sent (fail-closed): {exc}"}
    except BrokerError as exc:
        return {"error": f"broker rejected the action: {exc}"}
    return {
        "routed": True,
        "success": True,
        "action_id": res.get("action_id"),
        "disposition": res.get("disposition"),
        "note": f"routed to broker ({res.get('disposition', 'queued')})",
    }
