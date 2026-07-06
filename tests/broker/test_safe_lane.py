# ABOUTME: RED-first tests for broker/safe_lane.py — the 5-condition evaluator.
# ABOUTME: Auto-send requires ALL FIVE conditions TRUE; any failure => hold.
# ABOUTME: Verifies the plan's two canonical examples classify correctly
# ABOUTME: ("got it" to a known colleague => auto; "email the board" => hold) and
# ABOUTME: that flipping each condition FALSE independently forces a hold.
import pytest

from broker.safe_lane import SafeLane, Decision


@pytest.fixture()
def lane():
    # Conservative but with an explicitly-allowed operational reply lane so the
    # positive example can auto-send: colleague on allow-list, tier permits.
    return SafeLane(
        allow_list={"colleague"},
        strategic_markers={"board", "exec", "legal", "pricing"},
        irreversible_types={"git_push_force"},
        operational_cap=2000,
        allowed_origins={"telegram_reply"},
    )


def _action(**over):
    a = dict(
        type="message",
        channel="telegram",
        recipient="colleague",
        payload="got it, will do",
        origin="telegram_reply",
    )
    a.update(over)
    return a


def test_all_five_hold_auto_sends(lane):
    d = lane.evaluate(_action())
    assert d.disposition == "auto_sent", d.reasons
    assert all(d.conditions.values())


def test_email_the_board_holds(lane):
    d = lane.evaluate(
        _action(recipient="board", payload="email the board a status update")
    )
    assert d.disposition == "held"


def test_c1_unknown_type_holds(lane):
    d = lane.evaluate(_action(type="quantum_teleport"))
    assert d.disposition == "held"
    assert d.conditions["C1"] is False


def test_c1_empty_payload_holds(lane):
    d = lane.evaluate(_action(payload=""))
    assert d.disposition == "held"
    assert d.conditions["C1"] is False


def test_c2_recipient_off_allowlist_holds(lane):
    d = lane.evaluate(_action(recipient="stranger"))
    assert d.disposition == "held"
    assert d.conditions["C2"] is False


def test_c3_strategic_marker_holds(lane):
    d = lane.evaluate(_action(payload="quick note on pricing strategy"))
    assert d.disposition == "held"
    assert d.conditions["C3"] is False


def test_c3_over_operational_cap_holds(lane):
    d = lane.evaluate(_action(payload="Y" * 3000))
    assert d.disposition == "held"
    assert d.conditions["C3"] is False


def test_c4_unapproved_origin_holds(lane):
    d = lane.evaluate(_action(origin="autonomous_worker"))
    assert d.disposition == "held"
    assert d.conditions["C4"] is False


def test_c5_irreversible_holds(lane):
    d = lane.evaluate(_action(type="git_push_force"))
    assert d.disposition == "held"
    # irreversible fails C5 (may also fail C1 if type unknown; here type is known-irreversible)
    assert d.conditions["C5"] is False


def test_each_single_failure_holds(lane):
    # Flip each condition FALSE in turn from an otherwise-passing action.
    flips = [
        _action(type="quantum_teleport"),          # C1
        _action(recipient="stranger"),             # C2
        _action(payload="board update"),           # C3
        _action(origin="autonomous_worker"),       # C4
        _action(type="git_push_force"),            # C5
    ]
    for a in flips:
        assert lane.evaluate(a).disposition == "held"


def test_conservative_default_holds_most():
    # With near-empty allow-list + no allowed origins (P1a tier-0 default),
    # essentially everything holds.
    strict = SafeLane(
        allow_list=set(),
        strategic_markers={"board"},
        irreversible_types=set(),
        operational_cap=2000,
        allowed_origins=set(),
    )
    d = strict.evaluate(_action())
    assert d.disposition == "held"


def test_decision_is_serializable(lane):
    d = lane.evaluate(_action())
    js = d.to_json()
    assert "C1" in js and "C5" in js
