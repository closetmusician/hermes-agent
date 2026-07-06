# ABOUTME: The 5-condition safe-lane evaluator. The broker auto-sends IFF all
# ABOUTME: five conditions hold; ANY failure => hold for approval. The five:
# ABOUTME: C1 signal understood, C2 source-of-truth known, C3 operational-not-
# ABOUTME: strategic, C4 authority clear, C5 mistake recoverable. P1a ships this
# ABOUTME: conservative (near-empty allow, C4 defaults FALSE) so almost nothing
# ABOUTME: auto-sends — "start empty, widen as trust builds".
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

# Known action types the broker understands (C1). An unknown type cannot be
# reasoned about, so it always holds.
_KNOWN_TYPES = {"message", "git_push", "git_push_force", "pm_os_write", "merge"}


@dataclass
class Decision:
    """
    Purpose: the result of evaluating an action against the 5 conditions.
    Usage: d = lane.evaluate(action); if d.disposition == "auto_sent": send.
    Gotchas: `conditions` maps C1..C5 -> bool; disposition is "auto_sent" only
    when every value is True. to_json() feeds the held_actions.safe_lane_json col.
    """

    disposition: str  # "auto_sent" | "held"
    conditions: Dict[str, bool]
    reasons: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {"disposition": self.disposition, "conditions": self.conditions, "reasons": self.reasons}
        )


class SafeLane:
    """
    Purpose: compute the 5 falsifiable conditions and decide auto-send vs hold.
    Usage: SafeLane(allow_list=..., strategic_markers=..., ...).evaluate(action).
    Gotchas: this is the security-critical classifier — a wrong TRUE lets the
    assistant auto-send something it shouldn't. It is intentionally conservative:
    C4 (authority) requires an explicitly-allowed origin, so with an empty
    allowed_origins set NOTHING auto-sends. Widening is a P1b policy change, not
    a code change here.
    """

    def __init__(
        self,
        *,
        allow_list: Set[str],
        strategic_markers: Set[str],
        irreversible_types: Set[str],
        operational_cap: int,
        allowed_origins: Set[str],
    ):
        self._allow_list = set(allow_list)
        self._strategic = {m.lower() for m in strategic_markers}
        self._irreversible = set(irreversible_types)
        self._cap = operational_cap
        self._allowed_origins = set(allowed_origins)

    def evaluate(self, action: Dict[str, Any]) -> Decision:
        """
        Purpose: run all five predicates and produce the auto/hold decision.
        Usage: decision = lane.evaluate({type, channel, recipient, payload, origin}).
        Gotchas: every condition is evaluated (not short-circuited) so the stored
        safe_lane_json records WHY an action held — auditable, and lets P1b learn.
        A single False anywhere forces "held".
        """
        payload = action.get("payload") or ""
        atype = action.get("type") or ""
        recipient = action.get("recipient") or ""
        origin = action.get("origin") or ""
        reasons: List[str] = []

        # C1 — signal understood: known type AND a non-empty, well-formed payload.
        c1 = atype in _KNOWN_TYPES and isinstance(payload, str) and payload.strip() != ""
        if not c1:
            reasons.append("C1: unknown type or empty/malformed payload")

        # C2 — source of truth known: recipient on the allow-list.
        c2 = recipient in self._allow_list
        if not c2:
            reasons.append("C2: recipient not on allow-list")

        # C3 — operational not strategic: no strategic marker AND within cap.
        lower = payload.lower()
        hit = next((m for m in self._strategic if m in lower), None)
        c3 = hit is None and len(payload) <= self._cap
        if not c3:
            reasons.append(
                f"C3: strategic marker {hit!r}" if hit else "C3: payload exceeds operational cap"
            )

        # C4 — authority clear: origin is an explicitly-approved principal flow.
        # P1a default: allowed_origins is near-empty, so this fails by default.
        c4 = origin in self._allowed_origins
        if not c4:
            reasons.append("C4: origin authority not established (conservative default)")

        # C5 — mistake recoverable: the action type is not on the irreversible set.
        c5 = atype not in self._irreversible
        if not c5:
            reasons.append("C5: action is irreversible")

        conditions = {"C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5}
        disposition = "auto_sent" if all(conditions.values()) else "held"
        return Decision(disposition=disposition, conditions=conditions, reasons=reasons)
