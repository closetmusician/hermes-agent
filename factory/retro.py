# ABOUTME: The nightly retro orchestration (P5 design §4.1, deliverable 5.3) — a
# ABOUTME: HELD-ACTION PRODUCER, never a silent editor. GATHER (failures+scorecard) →
# ABOUTME: PROPOSE (an AI worker emits ONE unified diff over skills/prompts/non-ring
# ABOUTME: policy) → propose-gate (WALL 1, ring-rejects) → HOLD as an owner-approved
# ABOUTME: broker action carrying {diff, diff_sha256, rationale, target_files}.
"""
Retro orchestration — the crown self-modification loop, walled twice.

Design authoritative source: docs/plans/harness/fable/p5/P5-design.md §4.1.

The retro is a scheduled job that reads recent failures and proposes an
improvement diff. Its self-modification is contained by TWO independent
fail-closed walls over the same hash-pinned bytes:

  * WALL 1 (propose) — ``retro_ring_gate.check_retro_diff`` (here, step 3). A
    ring-touching proposal is dropped BEFORE ``enqueue_action`` — the owner never
    sees it. Order is the contract: gate → enqueue, NEVER enqueue → gate.
  * WALL 2 (apply) — the broker ``retro_diff`` executor re-checks the ring over
    the exact hash-verified bytes after the owner tap, before ``git apply``.

The retro NEVER writes the proposed diff to disk itself — the diff's ONLY path to
disk is the owner tap on the held action. The AI proposer and the broker client
are INJECTED seams (the diff-text source and the egress boundary), so the flow is
unit-testable without a real fleet, and the gate is never mocked.

GATHER inputs (scorecard rollup + failure forensics grouped by error_class) are
injected as a ``gather`` callable so this module does not hard-depend on the
sibling P5-a/P5-e modules before they merge — it codes against the documented
GATHER contract (a dict of {failure_class, samples, ...}).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, Optional

from factory.retro_ring_gate import RetroRingRejected, check_retro_diff

# The GATHER seam: read the last window's failure forensics + scorecard rollup and
# return the retro's inputs. Injected so the retro can run before P5-a/P5-e merge.
Gather = Callable[[], Dict[str, Any]]

# The PROPOSE seam: an AI retro worker that, given the gathered inputs, emits ONE
# unified diff (text) proposing edits to skills / prompts / non-ring policy docs.
# Its output is INERT TEXT until it clears WALL 1 and the owner tap.
Propose = Callable[[Dict[str, Any]], str]


def _sha256(text: str) -> str:
    """
    Purpose: hash the exact proposed-diff bytes so the apply door can confirm the
    applied artifact equals the gated one (v2-C3 hash-pin).
    Usage: diff_sha256 = _sha256(proposed_diff)
    Gotchas: encodes UTF-8 explicitly — the apply executor recomputes identically.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_retro(
    *,
    broker_client: Any,
    propose: Propose,
    gather: Gather,
    worktree_root: str,
    origin: str = "factory.retro",
    on_reject: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """
    Run one nightly retro tick: GATHER → PROPOSE → GATE (WALL 1) → HOLD.

    Purpose: produce an owner-approved held action from a retro's improvement
    proposal, or drop the proposal at the gate — NEVER apply silently. This is the
    crown loop's propose half; the apply half is the broker executor (WALL 2).
    Usage:
        result = run_retro(broker_client=client, propose=ai_worker,
                           gather=read_inputs, worktree_root=scratch)
        # result["disposition"] in {"held", "rejected"}.
    Gotchas:
      * Order is load-bearing: the propose gate (step 3) runs BEFORE
        enqueue_action (step 4). A ``RetroRingRejected`` means enqueue_action is
        NEVER called (RT-6) — a ring proposal must never become a held action.
      * The diff is hash-pinned in the payload (diff_sha256) so the apply executor
        can confirm the applied bytes match the gated bytes (RT-7 TOCTOU close).
      * This function writes NOTHING to disk — the diff reaches disk only via the
        owner-approved broker executor (RT-1 asserts zero direct writes).
      * ``on_reject`` is an optional hook (log/forensics) called with (reason,
        detail) on a dropped proposal; it must not re-raise.
    """
    # 1. GATHER — plain queries over failure forensics + scorecard (injected).
    gathered = gather()

    # 2. PROPOSE — the AI worker emits ONE unified diff (inert text, not applied).
    proposed_diff = propose(gathered)

    # 3. GATE (WALL 1) — ring-reject BEFORE enqueue. A ring-touching or empty/
    #    unparseable proposal is dropped here; the owner never sees it.
    try:
        check_retro_diff(proposed_diff, worktree_root=worktree_root)
    except RetroRingRejected as exc:
        reason = "ring"
        if on_reject is not None:
            on_reject(reason, str(exc))
        return {"disposition": "rejected", "reason": reason, "detail": str(exc)}

    # 4. HOLD — the surviving diff becomes an owner-approved held action, hash-pinned.
    rationale = ""
    if isinstance(gathered, dict):
        rationale = str(gathered.get("rationale") or gathered.get("failure_class") or "")
    payload = json.dumps(
        {
            "diff": proposed_diff,
            "diff_sha256": _sha256(proposed_diff),
            "rationale": rationale,
            "target_files": _target_files(proposed_diff),
            "worktree": worktree_root,
        }
    )
    result = broker_client.enqueue_action(
        type="retro_diff",
        summary=f"retro improvement: {rationale or 'proposed skill/prompt edit'}",
        payload=payload,
        origin=origin,
    )
    return {
        "disposition": result.get("disposition", "held"),
        "action_id": result.get("action_id"),
    }


def _target_files(diff: str) -> list:
    """
    Purpose: extract the new-path of each ``diff --git`` entry for the owner card
    summary (visibility only — the gate/executor do the real path checks).
    Usage: files = _target_files(proposed_diff)
    Gotchas: best-effort parse for the card; NOT a security boundary. The ring
    walls (check_retro_diff / check_ring) are authoritative on which paths are
    touched — this list is display metadata only.
    """
    files = []
    for line in diff.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            if p.startswith("b/"):
                p = p[2:]
            if p and p != "/dev/null":
                files.append(p)
    return files
