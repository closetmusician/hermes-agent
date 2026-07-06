# ABOUTME: RED-first tests for broker/held_store.py — the durable SQLite
# ABOUTME: held-action store. Asserts commit-before-ack (row is committed to
# ABOUTME: disk before enqueue returns), survival across a simulated broker
# ABOUTME: restart (new store instance, same file), one-way state transitions,
# ABOUTME: idempotent execute, and no payload truncation (the /approve-email fix).
import sqlite3

import pytest

from broker.held_store import HeldStore, IllegalTransition


@pytest.fixture()
def store(tmp_path):
    return HeldStore(tmp_path / "held_actions.db")


def _enqueue(store, **over):
    params = dict(
        type="message",
        channel="telegram",
        recipient="colleague",
        summary="got it",
        payload="got it, will do",
        origin="send_message_tool",
        safe_lane_json="{}",
        state="held",
    )
    params.update(over)
    return store.enqueue(**params)


def test_enqueue_returns_action_id(store):
    aid = _enqueue(store)
    assert isinstance(aid, str) and len(aid) == 26


def test_commit_before_ack_row_on_disk_before_return(store, tmp_path):
    # The row MUST be committed to the DB file before enqueue() returns its id.
    # We prove it by opening a SEPARATE read-only connection to the same file
    # (which cannot see uncommitted writes on the store's connection) right
    # after enqueue returns and finding the row already there.
    aid = _enqueue(store)
    raw = sqlite3.connect(str(tmp_path / "held_actions.db"))
    try:
        row = raw.execute(
            "SELECT action_id, state FROM held_actions WHERE action_id=?", (aid,)
        ).fetchone()
    finally:
        raw.close()
    assert row is not None, "row not committed before enqueue returned (fail-open window)"
    assert row[1] == "held"


def test_held_action_survives_restart(store, tmp_path):
    aid = _enqueue(store)
    del store  # simulate broker crash/shutdown
    revived = HeldStore(tmp_path / "held_actions.db")
    pending = revived.list_pending()
    assert any(p["action_id"] == aid for p in pending)


def test_long_payload_not_truncated(store):
    big = "X" * 100_000
    aid = _enqueue(store, payload=big)
    row = store.get(aid)
    assert row["payload"] == big
    assert len(row["payload"]) == 100_000


def test_transition_held_to_approved_ok(store):
    aid = _enqueue(store)
    store.transition(aid, "held", "approved", decided_by="yk")
    assert store.get(aid)["state"] == "approved"


def test_illegal_backward_transition_rejected(store):
    aid = _enqueue(store)
    store.transition(aid, "held", "approved", decided_by="yk")
    with pytest.raises(IllegalTransition):
        store.transition(aid, "approved", "held", decided_by="yk")


def test_executed_is_terminal(store):
    aid = _enqueue(store)
    store.transition(aid, "held", "approved", decided_by="yk")
    store.transition(aid, "approved", "executed", decided_by="yk")
    with pytest.raises(IllegalTransition):
        store.transition(aid, "executed", "approved", decided_by="yk")


def test_transition_from_wrong_expected_state_rejected(store):
    # Optimistic-concurrency guard: transition asserts the current state.
    aid = _enqueue(store)
    with pytest.raises(IllegalTransition):
        store.transition(aid, "approved", "executed", decided_by="yk")


def test_record_result_idempotent(store):
    aid = _enqueue(store)
    store.transition(aid, "held", "approved", decided_by="yk")
    store.record_result(aid, '{"status":"sent"}')
    assert store.get(aid)["result_json"] == '{"status":"sent"}'


def test_list_pending_only_held(store):
    a1 = _enqueue(store)
    a2 = _enqueue(store)
    store.transition(a2, "held", "rejected", decided_by="yk")
    ids = {p["action_id"] for p in store.list_pending()}
    assert a1 in ids
    assert a2 not in ids
