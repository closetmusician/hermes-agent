# ABOUTME: RED-first tests for the retro propose-gate (factory/retro_ring_gate.py) —
# ABOUTME: WALL 1 of the crown self-modification guard. A retro-proposed diff touching
# ABOUTME: ANY ring path is REJECTED before it can ever become an owner-approved held
# ABOUTME: action; fail-closed on empty/unparseable/traversal/symlink; the gate protects
# ABOUTME: its OWN source (retro_ring_gate.py is itself a RING_PATHS member).
"""
Tests for the retro ring propose-gate (P5-b REQ-01/03, closes review V3 + RT-1/3-propose).

WALL 1 (propose): ``retro_ring_gate.check_retro_diff`` wraps the identical hardened
``immutable_ring.check_diff`` the worker submission path passes. A ring-touching
retro proposal is rejected by plain code BEFORE the owner ever sees it.

Anti-weakening (REQ-05): the crown rejection test FAILS if the wrapped
``check_diff`` call is removed (a ring-touching diff would then pass the gate).
Real ``immutable_ring`` objects throughout — no mocks.
"""

import pytest

from factory import retro_ring_gate
from factory.immutable_ring import RING_PATHS
from factory.retro_ring_gate import RetroRingRejected, check_retro_diff


# ---------------------------------------------------------------------------
# Diff fixtures — real unified-diff text a retro worker would emit.
# ---------------------------------------------------------------------------

def _app_diff(path="skills/retro/SKILL.md"):
    """A benign app/skill diff (NOT a ring path) — the gate must PASS this."""
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,1 +1,1 @@\n"
        "-old skill line\n"
        "+improved skill line\n"
    )


def _ring_diff(path):
    """A diff touching a ring path — the gate MUST reject this."""
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,1 +1,1 @@\n"
        "-guard line\n"
        "+weakened guard line\n"
    )


# ---------------------------------------------------------------------------
# RT-3 (a) propose door — CROWN. Parametrized over every ring family.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ring_path",
    [
        "factory/cost_stops.py",
        "broker/server.py",
        "broker_client.py",
        "trust-policy.md",
        "docs/factory/never-graduates.md",
        "factory/immutable_ring.py",
        "factory/injection_scan.py",
    ],
)
def test_retro_diff_touching_ring_rejected_at_propose(tmp_path, ring_path):
    """RT-3(a): a retro diff into a ring path is REJECTED at the propose gate,
    even with an improvement rationale — it never becomes a held action."""
    with pytest.raises(RetroRingRejected):
        check_retro_diff(_ring_diff(ring_path), worktree_root=str(tmp_path))


def test_propose_gate_passes_a_clean_skill_diff(tmp_path):
    """A non-ring skill/prompt diff clears the propose gate (retro's real job)."""
    # Must not raise.
    check_retro_diff(_app_diff(), worktree_root=str(tmp_path))


# ---------------------------------------------------------------------------
# RT-8 — gate self-protection parity. retro_ring_gate.py is a ring member on
# BOTH sides, and a retro diff editing it is rejected at the propose gate.
# ---------------------------------------------------------------------------

def test_retro_gate_is_a_ring_member_factory_side():
    """RT-8: factory/retro_ring_gate.py is in factory RING_PATHS (self-protection)."""
    assert "factory/retro_ring_gate.py" in RING_PATHS


def test_retro_gate_is_a_ring_member_broker_side():
    """RT-8: factory/retro_ring_gate.py is in BROKER_RING_PATHS too (AT-RING-2 parity)."""
    from broker.merge_gate import BROKER_RING_PATHS

    assert "factory/retro_ring_gate.py" in BROKER_RING_PATHS


def test_broker_ring_still_superset_after_addition():
    """RT-8: AT-RING-2 superset invariant stays green with the additive entries."""
    from broker.merge_gate import BROKER_RING_PATHS

    assert set(RING_PATHS).issubset(set(BROKER_RING_PATHS))


def test_retro_diff_editing_the_gate_itself_rejected(tmp_path):
    """RT-8: a retro proposing to edit retro_ring_gate.py is rejected at propose."""
    with pytest.raises(RetroRingRejected):
        check_retro_diff(
            _ring_diff("factory/retro_ring_gate.py"), worktree_root=str(tmp_path)
        )


# ---------------------------------------------------------------------------
# RT-4 — evasion inheritance (the gate delegates to check_diff canonicalization).
# ---------------------------------------------------------------------------

def test_traversal_into_ring_rejected_at_propose(tmp_path):
    """RT-4: a ``../../broker/x`` traversal is rejected (inherited canonicalization)."""
    diff = (
        "diff --git a/sub/../../broker/x.py b/sub/../../broker/x.py\n"
        "--- a/sub/../../broker/x.py\n"
        "+++ b/sub/../../broker/x.py\n"
        "@@ -0,0 +1 @@\n"
        "+attack\n"
    )
    with pytest.raises(RetroRingRejected):
        check_retro_diff(diff, worktree_root=str(tmp_path))


def test_rename_into_ring_rejected_at_propose(tmp_path):
    """RT-4: a rename INTO broker/ is rejected on the new-path side."""
    diff = (
        "diff --git a/app.py b/broker/app.py\n"
        "similarity index 100%\n"
        "rename from app.py\n"
        "rename to broker/app.py\n"
    )
    with pytest.raises(RetroRingRejected):
        check_retro_diff(diff, worktree_root=str(tmp_path))


# ---------------------------------------------------------------------------
# RT-5 — fail-closed on empty / unparseable diff.
# ---------------------------------------------------------------------------

def test_empty_diff_rejected_fail_closed(tmp_path):
    """RT-5: an empty proposed diff is rejected (nothing to propose = fail-closed)."""
    with pytest.raises(RetroRingRejected):
        check_retro_diff("", worktree_root=str(tmp_path))


def test_unparseable_diff_rejected_fail_closed(tmp_path):
    """RT-5: an un-parseable diff entry is rejected (inherited fail-closed)."""
    diff = "diff --git garbage-no-paths\n@@ nonsense @@\n"
    with pytest.raises(RetroRingRejected):
        check_retro_diff(diff, worktree_root=str(tmp_path))


# ---------------------------------------------------------------------------
# RT-5 anti-weakening — deleting the check_diff call makes the crown test leak.
# ---------------------------------------------------------------------------

def test_gate_actually_calls_check_diff_not_a_stub(tmp_path, monkeypatch):
    """Anti-weakening: the propose gate delegates to immutable_ring.check_diff.
    If the gate stopped calling check_diff, a ring diff would slip through — this
    test asserts the real delegation by confirming check_diff is invoked."""
    calls = []
    real_check = retro_ring_gate.check_diff

    def _spy(diff, *, worktree_root):
        calls.append((diff, worktree_root))
        return real_check(diff, worktree_root=worktree_root)

    monkeypatch.setattr(retro_ring_gate, "check_diff", _spy)
    # A clean diff clears; the spy must have seen it (proves the gate ran check_diff).
    check_retro_diff(_app_diff(), worktree_root=str(tmp_path))
    assert calls, "propose gate did not call immutable_ring.check_diff"
