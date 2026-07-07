# ABOUTME: RED-first tests for broker/executors/retro_diff_executor.py — WALL 2 of the
# ABOUTME: crown self-modification guard (the P0 fix). On owner approval the executor
# ABOUTME: RE-CHECKS the ring via broker.merge_gate.check_ring over the EXACT hash-
# ABOUTME: verified bytes BEFORE git-apply: a ring-touching diff fed straight to the
# ABOUTME: executor (simulating a bypassed propose-gate) is REJECTED and applies NOTHING.
"""
Tests for the retro_diff apply executor (P5-b REQ-02, closes review P0/V1 + V4).

WALL 2 (apply): even if the factory propose-gate were bypassed, mutated, or buggy,
the broker's own executor re-checks the ring over the exact bytes it is about to
apply — so an owner-approved apply NEVER touches a ring path. It also recomputes
the diff sha256 and rejects on mismatch (TOCTOU close, v2-C3).

Real broker objects + REAL local git worktrees throughout — the executor really
``git apply``s a clean diff and really refuses a ring diff, proven by inspecting
the worktree (zero writes on rejection). The ONLY injected seam is the git-apply
subprocess boundary is NOT stubbed — a real repo is used.

Anti-weakening (REQ-05): deleting the executor's ``check_ring`` call makes the
apply-door arm leak (a ring diff would apply); deleting the hash recompute makes
the TOCTOU arm leak (a mismatched diff would apply).
"""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from broker.executors.retro_diff_executor import build_retro_diff_executor


# ---------------------------------------------------------------------------
# Real-git fixture — a worktree the executor can `git apply` into.
# ---------------------------------------------------------------------------

def _git(cwd, *args):
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc


def _make_worktree(tmp_path):
    """A real git repo with a skill file and a ring file present, so a diff can apply."""
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-b", "main")
    _git(wt, "config", "user.email", "factory@test")
    _git(wt, "config", "user.name", "factory")
    _git(wt, "config", "commit.gpgsign", "false")
    # A skill file (app-space) and a broker file (ring) both exist for realism.
    (wt / "skills").mkdir()
    (wt / "skills" / "qa.md").write_text("old skill\n")
    (wt / "broker").mkdir()
    (wt / "broker" / "server.py").write_text("GUARD = True\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", "seed")
    return wt


def _skill_diff():
    """A clean app-space diff that applies to skills/qa.md."""
    return (
        "diff --git a/skills/qa.md b/skills/qa.md\n"
        "--- a/skills/qa.md\n"
        "+++ b/skills/qa.md\n"
        "@@ -1 +1 @@\n"
        "-old skill\n"
        "+new skill\n"
    )


def _ring_diff():
    """A diff touching broker/server.py — a ring path the apply door must refuse."""
    return (
        "diff --git a/broker/server.py b/broker/server.py\n"
        "--- a/broker/server.py\n"
        "+++ b/broker/server.py\n"
        "@@ -1 +1 @@\n"
        "-GUARD = True\n"
        "+GUARD = False\n"
    )


def _row(diff, worktree, *, sha=None):
    """Build a held retro_diff row carrying the hash-pinned payload."""
    payload = {
        "diff": diff,
        "diff_sha256": sha if sha is not None else hashlib.sha256(diff.encode()).hexdigest(),
        "worktree": str(worktree),
        "rationale": "improves things",
        "target_files": ["skills/qa.md"],
    }
    return {"type": "retro_diff", "payload": json.dumps(payload)}


# ---------------------------------------------------------------------------
# Happy path — a clean skill diff applies.
# ---------------------------------------------------------------------------

def test_clean_retro_diff_applies(tmp_path):
    """A non-ring, hash-matching diff is applied to the worktree."""
    wt = _make_worktree(tmp_path)
    executor = build_retro_diff_executor()
    result = executor(_row(_skill_diff(), wt))
    assert result["status"] == "applied", result
    assert (wt / "skills" / "qa.md").read_text() == "new skill\n"


# ---------------------------------------------------------------------------
# RT-3 (b) apply door — CROWN. A ring diff fed straight to the executor with a
# VALID hash (simulating a bypassed/buggy propose-gate) is REJECTED, applies NOTHING.
# ---------------------------------------------------------------------------

def test_ring_diff_rejected_at_apply_zero_writes(tmp_path):
    """RT-3(b): the executor re-checks the ring and refuses a ring-touching diff,
    even with a valid hash. The worktree is UNCHANGED (zero writes)."""
    wt = _make_worktree(tmp_path)
    before = (wt / "broker" / "server.py").read_text()
    executor = build_retro_diff_executor()

    result = executor(_row(_ring_diff(), wt))

    assert result["status"] != "applied", result
    assert result.get("reason") == "ring", result
    # APPLIES NOTHING: the ring file is byte-identical.
    assert (wt / "broker" / "server.py").read_text() == before
    # And no dirty working tree at all.
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(wt), capture_output=True, text=True
    ).stdout
    assert status.strip() == "", f"worktree was modified on a rejected ring diff: {status!r}"


@pytest.mark.parametrize(
    "ring_path,seed",
    [
        ("broker_client.py", "CLIENT = 1\n"),
        ("factory/cost_stops.py", "CAP = 5\n"),
        ("trust-policy.md", "policy\n"),
        ("docs/factory/never-graduates.md", "never\n"),
    ],
)
def test_apply_door_rejects_every_ring_family(tmp_path, ring_path, seed):
    """RT-3(b) parametrized: every ring family is refused at the apply door.

    (broker/server.py is exercised by test_ring_diff_rejected_at_apply_zero_writes,
    which seeds it in _make_worktree; here we cover the other ring families.)"""
    wt = _make_worktree(tmp_path)
    # Seed the ring file so a diff could otherwise apply.
    target = wt / ring_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(seed)
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", "seed ring file")

    diff = (
        f"diff --git a/{ring_path} b/{ring_path}\n"
        f"--- a/{ring_path}\n"
        f"+++ b/{ring_path}\n"
        "@@ -1 +1 @@\n"
        f"-{seed}"
        "+weakened\n"
    )
    executor = build_retro_diff_executor()
    result = executor(_row(diff, wt))
    assert result["status"] != "applied"
    assert result.get("reason") == "ring"
    assert target.read_text() == seed  # untouched


# ---------------------------------------------------------------------------
# RT-7 — PROPOSE/APPLY hash-pin (TOCTOU). Applied bytes != payload.diff_sha256
# → rejected with diff_hash_mismatch, applies nothing.
# ---------------------------------------------------------------------------

def test_hash_mismatch_rejected_applies_nothing(tmp_path):
    """RT-7: a payload whose diff bytes don't match diff_sha256 (owner-edit / re-render
    / stale-worktree) is rejected at the executor and applies nothing."""
    wt = _make_worktree(tmp_path)
    before = (wt / "skills" / "qa.md").read_text()
    executor = build_retro_diff_executor()

    # Valid clean diff, but a WRONG hash pinned in the payload.
    result = executor(_row(_skill_diff(), wt, sha="deadbeef" * 8))

    assert result["status"] != "applied", result
    assert result.get("reason") == "diff_hash_mismatch", result
    assert (wt / "skills" / "qa.md").read_text() == before


# ---------------------------------------------------------------------------
# fail-closed — empty / unparseable diff, missing worktree.
# ---------------------------------------------------------------------------

def test_empty_diff_rejected_at_apply(tmp_path):
    """RT-5 (apply layer): an empty diff is rejected fail-closed (check_ring raises)."""
    wt = _make_worktree(tmp_path)
    executor = build_retro_diff_executor()
    result = executor(_row("", wt))
    assert result["status"] != "applied"


def test_bad_payload_rejected(tmp_path):
    """A non-JSON payload is a clear error, never a partial apply."""
    executor = build_retro_diff_executor()
    result = executor({"type": "retro_diff", "payload": "not json{"})
    assert result["status"] != "applied"


# ---------------------------------------------------------------------------
# Anti-weakening witnesses — prove both walls are load-bearing.
# ---------------------------------------------------------------------------

def test_executor_calls_check_ring(tmp_path, monkeypatch):
    """Anti-weakening: the executor delegates to broker.merge_gate.check_ring.
    Deleting that call would let a ring diff apply — this asserts the delegation."""
    import broker.executors.retro_diff_executor as mod

    seen = []
    real = mod.check_ring

    def _spy(diff, *, worktree_root):
        seen.append(worktree_root)
        return real(diff, worktree_root=worktree_root)

    monkeypatch.setattr(mod, "check_ring", _spy)
    wt = _make_worktree(tmp_path)
    executor = build_retro_diff_executor()
    executor(_row(_skill_diff(), wt))
    assert seen, "executor did not call merge_gate.check_ring before applying"
