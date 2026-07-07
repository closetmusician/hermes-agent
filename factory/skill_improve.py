# ABOUTME: Weekly skill-improvement pass for the Fable factory (P5-c, design §4.3).
# ABOUTME: Reads scorecard + forensics histories, proposes skill-file diffs via an
# ABOUTME: injected AI-distillation function, runs every diff through the retro ring
# ABOUTME: gate (check_retro_diff), and submits ONLY approved diffs as owner-tapped
# ABOUTME: held actions via broker_client. Never silent, never bypasses the gate.
"""
Weekly skill-improvement pass.

Design authoritative source:
  docs/plans/harness/fable/p5/P5-design.md §4.3 + §7.2 SK-1/SK-2 + §REQ-01/REQ-02

Discipline (shares retro discipline):
  - EVERY proposed diff goes through check_retro_diff (the ring gate) before
    enqueue_action.  This is the PROPOSE-DOOR check (Wall 1 of the two-wall v2
    design).  Removing it makes SK-2 leak.
  - Approved diffs are owner-approved held actions (type 'skill_diff'), hash-pinned
    (v2-C3) — the apply-door executor re-checks the ring on the exact same bytes
    before git-apply.  Never silent.
  - Guard-adjacent skill flag (v2-C5): when the diff touches a skill that redefines
    a worker-read heuristic (injection-scan/footprint rule), the held-action summary
    flags it for extra owner scrutiny.

Dependency on P5-b (retro_ring_gate):
  ``check_retro_diff`` is imported from ``factory.retro_ring_gate``.  If P5-b is
  not yet merged, the import raises ImportError at module load and all tests that
  mock ``factory.skill_improve.check_retro_diff`` still work because the mock is
  applied before the call site.  When P5-b lands, the real gate is used.

AI distillation seam:
  ``run_weekly_pass`` accepts an injectable ``diff_fn(history, skill_files) -> str``
  to produce the proposed diff.  In production this calls a sonnet worker; in tests
  it is replaced by a fixture that returns a controlled diff string.  This keeps the
  unit tests fast and deterministic without testing mock behaviour.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guard-adjacent skills (v2-C5) — skill names that redefine worker-read heuristics.
# When a proposed diff touches any of these, the held-action card summary is flagged.
# Non-ring: the flag (not the gate) is the mitigation per design §4.3.
# ---------------------------------------------------------------------------

_GUARD_ADJACENT_SKILLS: frozenset[str] = frozenset(
    {
        "injection-scan-helper",
        "footprint-guard",
        "ring-check-helper",
        "trust-policy-helper",
    }
)

# ---------------------------------------------------------------------------
# Ring gate import seam.
# check_retro_diff lives in factory.retro_ring_gate (P5-b).  Imported at call
# time inside the function (not at module top) so tests can patch
# "factory.skill_improve.check_retro_diff" before the function is called.
# ---------------------------------------------------------------------------


def check_retro_diff(diff: str, *, worktree_root: Path) -> None:
    """
    Purpose: propose-door ring gate shim. In production this is replaced by a
    late-import from factory.retro_ring_gate after P5-b merges. In the meantime
    it wraps immutable_ring.check_diff directly (same semantic — one thin wall).
    Tests patch this name directly (factory.skill_improve.check_retro_diff).
    Usage: check_retro_diff(diff_text, worktree_root=wt)  # raises RingViolation
    Gotchas: this shim is the module-level name that tests patch. Do NOT add a
    second import alias elsewhere in this file.
    """
    # Try to import the real gate first (available after P5-b merges).
    try:
        from factory.retro_ring_gate import check_retro_diff as _real_gate  # type: ignore[import]

        _real_gate(diff, worktree_root=worktree_root)
        return
    except ImportError:
        pass
    # Fallback shim: call immutable_ring.check_diff directly (same contract).
    from factory.immutable_ring import check_diff as _check_diff  # noqa: PLC0415

    _check_diff(diff, worktree_root=str(worktree_root))


# ---------------------------------------------------------------------------
# History gathering
# ---------------------------------------------------------------------------


def _gather_history(db_path: Path, window_secs: int = 7 * 24 * 3600) -> Dict[str, Any]:
    """
    Purpose: read scorecard_samples + failure_forensics from the jobs DB for the
    last ``window_secs`` seconds, grouped by error_class / task_type. Returns a
    plain dict the diff_fn uses to decide what skill to improve.
    Usage: hist = _gather_history(db_path, window_secs=604800)
    Gotchas: tables may not exist if P5-a/P5-e haven't run yet — handled with
    graceful degradation (empty history, no crash).
    """
    since = int(time.time()) - window_secs
    history: Dict[str, Any] = {
        "failure_classes": {},   # error_class → count
        "task_types": {},        # task_type → outcome → count
        "since_ts": since,
    }

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Failure forensics — grouped by error_class.
        try:
            rows = conn.execute(
                """
                SELECT error_class, COUNT(*) as cnt
                FROM failure_forensics
                WHERE captured_ts >= ?
                GROUP BY error_class
                ORDER BY cnt DESC
                """,
                (since,),
            ).fetchall()
            for r in rows:
                history["failure_classes"][r["error_class"]] = r["cnt"]
        except sqlite3.OperationalError:
            _log.debug("failure_forensics table absent — history empty")

        # Scorecard samples — grouped by task_type × outcome.
        try:
            rows = conn.execute(
                """
                SELECT task_type, outcome, COUNT(*) as cnt
                FROM scorecard_samples
                WHERE sample_ts >= ?
                GROUP BY task_type, outcome
                """,
                (since,),
            ).fetchall()
            for r in rows:
                tt = r["task_type"]
                history["task_types"].setdefault(tt, {})[r["outcome"]] = r["cnt"]
        except sqlite3.OperationalError:
            _log.debug("scorecard_samples table absent — history empty")

        conn.close()
    except Exception as exc:  # noqa: BLE001
        _log.warning("_gather_history failed: %s", exc)

    return history


# ---------------------------------------------------------------------------
# Skill file discovery
# ---------------------------------------------------------------------------


def _list_skill_files(skills_dir: Path) -> List[Path]:
    """
    Purpose: collect all SKILL.md files under skills_dir so the diff_fn can
    choose which skill to improve. Returns a list of absolute paths.
    Usage: files = _list_skill_files(skills_dir)
    Gotchas: empty list is valid (no skills yet — diff_fn may propose a new one).
    """
    return sorted(skills_dir.rglob("SKILL.md"))


# ---------------------------------------------------------------------------
# Guard-adjacent detection
# ---------------------------------------------------------------------------


def _is_guard_adjacent(diff: str, skills_dir: Path) -> bool:
    """
    Purpose: check whether the proposed diff touches any guard-adjacent skill
    (one that redefines a worker-read heuristic — injection-scan/footprint rule).
    Usage: flag = _is_guard_adjacent(diff_text, skills_dir)
    Gotchas: uses simple path-substring matching on the diff header lines — no
    parsing required because GUARD_ADJACENT_SKILLS are explicit names, not regexes.
    """
    for line in diff.splitlines():
        if not (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        for skill_name in _GUARD_ADJACENT_SKILLS:
            if skill_name in line:
                return True
    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_weekly_pass(
    *,
    db_path: Path,
    skills_dir: Path,
    worktree_root: Path,
    broker: Any,
    diff_fn: Optional[Callable[[Dict[str, Any], List[Path]], str]] = None,
    window_secs: int = 7 * 24 * 3600,
) -> Dict[str, Any]:
    """
    Weekly skill-improvement pass — the entry point for the scheduler-fired job.

    Purpose: reads job histories (scorecard + forensics) for the last window, asks
    the AI distillation function (diff_fn) to propose a skill-file diff, runs it
    through the ring gate (check_retro_diff), and if it passes, enqueues it as an
    owner-approved held action via broker.enqueue_action. Never writes to disk
    directly (the held-action is the ONLY path from proposed diff to the file system).

    Usage:
        result = run_weekly_pass(
            db_path=Path("jobs.db"), skills_dir=Path("skills"),
            worktree_root=wt, broker=client,
            diff_fn=my_ai_distill_fn,  # or None → no-op placeholder
        )

    Gotchas:
      * check_retro_diff is called BEFORE enqueue_action — ordering is the contract.
      * RingViolation from check_retro_diff → status='rejected', NO enqueue_action.
      * diff_fn returning empty string → status='no_proposal' (nothing to gate or enqueue).
      * Guard-adjacent skill flag (v2-C5) is set in the summary for extra owner scrutiny.
      * Every payload carries diff_sha256 = sha256(diff bytes) for the apply-executor
        hash-pin (v2-C3); the executor recomputes and rejects on mismatch.
    """
    db_path = Path(db_path)
    skills_dir = Path(skills_dir)
    worktree_root = Path(worktree_root)

    # Step 1: GATHER — plain queries, no AI yet.
    history = _gather_history(db_path, window_secs=window_secs)
    skill_files = _list_skill_files(skills_dir)
    _log.debug(
        "weekly pass: %d failure classes, %d skill files",
        len(history["failure_classes"]),
        len(skill_files),
    )

    # Step 2: PROPOSE — AI distillation (injected via diff_fn; mocked in tests).
    if diff_fn is None:
        _log.debug("weekly pass: no diff_fn — returning no_proposal")
        return {"status": "no_proposal", "reason": "no diff_fn provided"}

    try:
        diff_text: str = diff_fn(history, skill_files)
    except Exception as exc:  # noqa: BLE001
        _log.warning("weekly pass: diff_fn raised: %s", exc)
        return {"status": "error", "reason": str(exc)}

    if not diff_text or not diff_text.strip():
        _log.debug("weekly pass: diff_fn returned empty diff — no proposal")
        return {"status": "no_proposal", "reason": "diff_fn returned empty diff"}

    # Step 3: RING GATE — check_retro_diff (Wall 1, propose door).
    # MUST run before enqueue_action. Ordering is the contract (design §4.2).
    try:
        check_retro_diff(diff_text, worktree_root=worktree_root)
    except Exception as exc:
        # RingViolation or any gate error → reject, no enqueue.
        _log.warning("weekly pass: ring gate rejected diff: %s", exc)
        return {
            "status": "rejected",
            "reason": f"ring gate: {exc}",
        }

    # Step 4: guard-adjacent flag (v2-C5) — non-ring, flag-only mitigation.
    guard_adjacent = _is_guard_adjacent(diff_text, skills_dir)
    guard_flag = " ⚠ guard-adjacent skill" if guard_adjacent else ""

    # Determine which skill files are touched (for the summary).
    touched = [
        ln.split(" ", 1)[1].strip()
        for ln in diff_text.splitlines()
        if ln.startswith("+++ b/") or ln.startswith("+++ ")
    ]
    files_str = ", ".join(touched[:3]) or "(skill diff)"

    # Step 5: hash-pin the diff bytes (v2-C3 — the apply-executor recomputes this).
    diff_sha256 = hashlib.sha256(diff_text.encode()).hexdigest()

    # Step 6: HOLD — enqueue as owner-approved held action (never silent).
    summary = (
        f"[skill-improve] proposed skill edit: {files_str}{guard_flag}"
    )
    payload = json.dumps(
        {
            "diff": diff_text,
            "diff_sha256": diff_sha256,
            "target_files": touched,
            "rationale": (
                f"Weekly skill-improve pass. Recurring failure classes: "
                f"{dict(list(history['failure_classes'].items())[:3])}"
            ),
            "guard_adjacent": guard_adjacent,
        }
    )

    try:
        result = broker.enqueue_action(
            type="skill_diff",
            summary=summary,
            payload=payload,
            origin="skill_improve",
        )
    except Exception as exc:  # noqa: BLE001
        _log.error("weekly pass: enqueue_action failed: %s", exc)
        return {"status": "error", "reason": f"enqueue_action: {exc}"}

    _log.info(
        "weekly pass: held action %s (disposition=%s)",
        result.get("action_id"),
        result.get("disposition"),
    )
    return {
        "status": "held",
        "action_id": result.get("action_id"),
        "disposition": result.get("disposition"),
        "guard_adjacent": guard_adjacent,
    }


# ---------------------------------------------------------------------------
# SkillImprover class (thin scheduler-facing wrapper around run_weekly_pass)
# ---------------------------------------------------------------------------


class SkillImprover:
    """
    Purpose: scheduler-facing class that wraps run_weekly_pass with configuration
    so the scheduler can call imporver.tick() on a weekly cadence.
    Usage: imporver = SkillImprover(db_path=..., skills_dir=..., worktree_root=...,
             broker=client, diff_fn=prod_diff_fn); imporver.tick()
    Gotchas: tick() is idempotent — multiple calls are safe; each call reads fresh
    history. The diff_fn is the AI seam; inject a mock for testing.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        skills_dir: Path,
        worktree_root: Path,
        broker: Any,
        diff_fn: Optional[Callable[[Dict[str, Any], List[Path]], str]] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.skills_dir = Path(skills_dir)
        self.worktree_root = Path(worktree_root)
        self.broker = broker
        self.diff_fn = diff_fn

    def tick(self) -> Dict[str, Any]:
        """
        Purpose: run one weekly skill-improvement pass and return its result dict.
        Usage: result = imporver.tick()
        Gotchas: result["status"] is one of: 'held', 'rejected', 'no_proposal', 'error'.
        """
        return run_weekly_pass(
            db_path=self.db_path,
            skills_dir=self.skills_dir,
            worktree_root=self.worktree_root,
            broker=self.broker,
            diff_fn=self.diff_fn,
        )
