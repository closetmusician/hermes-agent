# ABOUTME: Broker-side egress executors — the ONLY code that performs a real send
# ABOUTME: or push. Runs inside the broker process, which holds the credentials the
# ABOUTME: assistant does not. message_executor performs a platform message send;
# ABOUTME: git_push_executor performs a real `git push`. Both fail-closed when the
# ABOUTME: required credential is absent (never fall back to a partial/direct path).
# ABOUTME: ExecutorRegistry maps held-action type strings to their executor callable,
# ABOUTME: so BrokerServer can dispatch by type without holding a single executor ref.
from __future__ import annotations

from typing import Any, Callable, Dict

# An executor receives the full held-action row dict and returns a JSON-serialisable
# result dict.  It is the only code that performs real egress.
Executor = Callable[[Dict[str, Any]], Dict[str, Any]]


class UnknownActionType(Exception):
    """
    Purpose: raised when the registry is asked for an unregistered action type.
    Usage: raised by ExecutorRegistry.get() when the type has no executor.
    Gotchas: this is the fail-closed signal — a missing entry must never silently
    fall through to a wrong executor.  The broker surfaces this as an error frame
    so the caller sees an explicit failure, not a misdirected send.
    """


class ExecutorRegistry:
    """
    Purpose: type→executor mapping for BrokerServer dispatch (P2-g-maint REQ-01/02).
    Usage: registry = ExecutorRegistry({"message": msg_exec, "git_push": push_exec});
           executor = registry.get(row["type"])  # raises UnknownActionType on miss.
    Gotchas: the registry is the authoritative dispatch table; adding a new executor
    type (e.g. 'merge' for P2-g) requires only passing it in the constructor dict —
    no edits to broker/server.py.  Fail-closed: unknown types raise UnknownActionType,
    never fall back silently.  The registry is read-only after construction (no
    register-at-runtime) so there is no race condition on the mapping.
    """

    def __init__(self, executors: Dict[str, Executor]) -> None:
        """
        Purpose: build the registry from the provided type→executor mapping.
        Usage: ExecutorRegistry({"message": msg_exec, "git_push": push_exec}).
        Gotchas: the mapping is shallow-copied so callers cannot mutate it later;
        keys are lowercased for case-insensitive matching.
        """
        self._map: Dict[str, Executor] = {k.lower(): v for k, v in executors.items()}

    def get(self, action_type: str) -> Executor:
        """
        Purpose: resolve the executor for the given action type string.
        Usage: executor = registry.get(row["type"]).
        Gotchas: raises UnknownActionType if the type is not registered — never
        returns None, never silently dispatches to a default/wrong executor.
        The call site should surface the exception as an error response so the
        caller sees a clear failure.
        """
        key = (action_type or "").lower()
        executor = self._map.get(key)
        if executor is None:
            raise UnknownActionType(
                f"no executor registered for action type {action_type!r}; "
                f"registered types: {sorted(self._map)}"
            )
        return executor
