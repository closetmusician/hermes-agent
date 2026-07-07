# ABOUTME: Forensic bundle capture (P4-a, design §3.3 + Revision v2 §R4/F10). Two
# ABOUTME: paths: capture_pre_kill (SUPERVISED — the bundle lands BEFORE the kill
# ABOUTME: signal so morning debugging is 30s) and capture_post_mortem (CRASH / dead
# ABOUTME: pgid — best-effort snapshot of what survives, never raises). Each bundle
# ABOUTME: is {transcript-tail, failing-test, phase.diff, meta.json}. Pure I/O, no AI.
"""
Forensic capture for supervised kills and crashes (P4-a).

The order is load-bearing for the SUPERVISED path: capture BEFORE the kill signal,
so a stalled / cost-stopped worker leaves a debuggable bundle. For a CRASH (OOM,
kill -9, dead pgid) there is no "before" — capture_post_mortem snapshots whatever
survived on disk (a truncated transcript tail is accepted) and MUST NOT raise,
because it runs inside the reclaim path that cannot be allowed to crash the tick.

No AI in the loop — this is a plain file snapshot.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

# How many bytes of the worker transcript to keep — the tail is where the failure
# is; the head is rarely diagnostic and can be large.
_TRANSCRIPT_TAIL_BYTES = 32 * 1024

_WORKER_OUT = ".factory/worker.out"


def _bundle_dir(out_dir: Path, job_id: str) -> Path:
    """The per-job forensic bundle directory (created if absent)."""
    d = Path(out_dir) / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_transcript_tail(worktree: Path, dest: Path) -> None:
    """
    Purpose: copy the last _TRANSCRIPT_TAIL_BYTES of the worker output into the
    bundle. Best-effort — a missing/reaped worktree yields an empty tail file.
    Usage: internal to both capture paths.
    Gotchas: never raises; a truncated tail is explicitly acceptable for the crash
    path (the process died mid-write).
    """
    out = Path(worktree) / _WORKER_OUT
    text = ""
    try:
        if out.exists():
            data = out.read_bytes()
            text = data[-_TRANSCRIPT_TAIL_BYTES:].decode("utf-8", errors="replace")
    except OSError:
        text = ""
    dest.write_text(text)


def _write_phase_diff(worktree: Path, dest: Path) -> None:
    """
    Purpose: snapshot the worktree's uncommitted diff at kill/crash time. Best
    effort — outside a git worktree (tests, reaped tree) an empty diff is written.
    Usage: internal.
    Gotchas: never raises; git failures (not a repo, git absent) are swallowed and
    leave an empty phase.diff so the bundle shape is stable.
    """
    diff = ""
    try:
        if Path(worktree).exists():
            proc = subprocess.run(
                ["git", "-C", str(worktree), "diff"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            diff = proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        diff = ""
    dest.write_text(diff)


def _write_meta(dest: Path, meta: Dict[str, Any]) -> None:
    """Write the meta.json (model/prompt_hash/phase/rung/cost/kill_reason)."""
    dest.write_text(json.dumps(dict(meta or {}), indent=2, sort_keys=True))


def capture_pre_kill(
    job_id: str,
    worktree: Path,
    out_dir: Path,
    meta: Dict[str, Any],
    failing_test: Optional[str] = None,
) -> Path:
    """
    Purpose: SUPERVISED-kill capture — write the full bundle BEFORE the caller sends
    the kill signal, so a stalled/cost-stopped worker is always debuggable.
    Usage: bundle = capture_pre_kill(jid, wt, out_dir, meta, failing_test); kill().
    Gotchas: the CALLER must invoke this before the kill (the ordering is the
    contract; §10-P4a-#4 asserts it with a spy). This path may raise on a genuinely
    broken out_dir — unlike the post-mortem path, a supervised kill has a live
    supervisor that can surface the error.
    """
    bundle = _bundle_dir(out_dir, job_id)
    _write_transcript_tail(Path(worktree), bundle / "transcript-tail.txt")
    (bundle / "failing-test.txt").write_text(failing_test or "")
    _write_phase_diff(Path(worktree), bundle / "phase.diff")
    _write_meta(bundle / "meta.json", meta)
    return bundle


def capture_post_mortem(
    job_id: str,
    worktree: Path,
    out_dir: Path,
    meta: Dict[str, Any],
    failing_test: Optional[str] = None,
) -> Path:
    """
    Purpose: CRASH capture (dead pgid) — snapshot whatever survived on disk. Called
    inside _reclaim_dead_job, so it MUST NOT raise: a broken bundle write can never
    be allowed to crash the integrity sweep / tick loop.
    Usage: bundle = capture_post_mortem(jid, wt, out_dir, meta); route RESUMABLE.
    Gotchas: a reaped worktree yields empty transcript/diff but a valid meta.json —
    the bundle shape is stable so the morning card always has a path to point at.
    Any exception is swallowed; the returned path always at least has meta.json.
    """
    try:
        bundle = _bundle_dir(out_dir, job_id)
    except OSError:
        # Absolute last resort — fall back to a temp-less path we can still return.
        bundle = Path(out_dir) / job_id
    try:
        _write_transcript_tail(Path(worktree), bundle / "transcript-tail.txt")
    except OSError:
        pass
    try:
        (bundle / "failing-test.txt").write_text(failing_test or "")
    except OSError:
        pass
    try:
        _write_phase_diff(Path(worktree), bundle / "phase.diff")
    except OSError:
        pass
    try:
        _write_meta(bundle / "meta.json", meta)
    except OSError:
        pass
    return bundle
