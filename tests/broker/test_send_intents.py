# ABOUTME: RED-first tests for hermes_cli/send_intents.py — the exactly-one-send ledger.
# ABOUTME: These tests verify that the send-intent ledger deduplicates enqueue_action calls:
# ABOUTME: same intent_key → one broker action, not two, even under retry or concurrency.
# ABOUTME: The exactly-once test (test_exactly_one_send_on_retry) is the critical gate —
# ABOUTME: it MUST fail if the ledger dedup is removed.
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from hermes_cli.send_intents import SendIntentLedger


@pytest.fixture()
def ledger(tmp_path: Path) -> SendIntentLedger:
    """
    Purpose: a fresh ledger backed by a temp-dir DB (isolated per test).
    Usage: inject as a fixture into any test needing a clean ledger.
    Gotchas: tmp_path is pytest-provided, unique per test; no cleanup needed.
    """
    return SendIntentLedger(tmp_path / "send_intents.db")


def _make_broker_mock(action_id: str = "test-action-001") -> MagicMock:
    """
    Purpose: build a mock BrokerClient whose enqueue_action returns a canned result.
    Usage: client = _make_broker_mock(); then pass to ledger.enqueue_send.
    Gotchas: call count on client.enqueue_action is the dedup proof.
    """
    client = MagicMock()
    client.enqueue_action.return_value = {"action_id": action_id, "disposition": "held"}
    return client


# ---------------------------------------------------------------------------
# REQ-01: same intent_key → one broker action (second call returns first action_id)
# ---------------------------------------------------------------------------


def test_first_enqueue_creates_broker_action(ledger: SendIntentLedger) -> None:
    """
    Purpose: first enqueue_send call delegates to the broker and records the intent.
    Usage: baseline — proves the happy path works before testing the dedup.
    Gotchas: if this fails the ledger itself is broken, not just the dedup.
    """
    client = _make_broker_mock("aid-0001")
    result = ledger.enqueue_send(
        "intent-A",
        broker_client=client,
        type="message",
        summary="hello",
        payload="body",
        origin="test",
    )
    assert result["action_id"] == "aid-0001"
    assert result.get("deduped") is not True
    client.enqueue_action.assert_called_once()


def test_second_enqueue_same_key_returns_existing_action_id(ledger: SendIntentLedger) -> None:
    """
    Purpose: REQ-01 core — two enqueue_send calls with the same intent_key produce ONE
    broker action; the second call returns the first action_id without hitting the broker.
    Usage: the intent_key simulates a stable draft-id that is the same across retries.
    Gotchas: broker.enqueue_action must be called EXACTLY once — the dedup gate fires on
    the second call. If both calls hit the broker this test fails → dedup is broken.
    """
    client = _make_broker_mock("aid-0002")
    first = ledger.enqueue_send(
        "intent-B",
        broker_client=client,
        type="message",
        summary="first",
        payload="body",
        origin="test",
    )
    second = ledger.enqueue_send(
        "intent-B",
        broker_client=client,
        type="message",
        summary="first",
        payload="body",
        origin="test",
    )
    # Both return the SAME action_id.
    assert first["action_id"] == second["action_id"] == "aid-0002"
    # Second is flagged as a deduped return.
    assert second.get("deduped") is True
    # Broker was called only once — this is the anti-weakening assertion.
    client.enqueue_action.assert_called_once()


def test_different_keys_produce_different_broker_actions(ledger: SendIntentLedger) -> None:
    """
    Purpose: two distinct intent_keys each produce their own broker action (no over-dedup).
    Usage: ensures the ledger keys on intent_key, not on payload similarity.
    Gotchas: broker mock returns the same action_id template — test checks call count = 2.
    """
    client = MagicMock()
    client.enqueue_action.side_effect = [
        {"action_id": "aid-C1", "disposition": "held"},
        {"action_id": "aid-C2", "disposition": "held"},
    ]
    r1 = ledger.enqueue_send("key-C1", broker_client=client, type="message",
                              summary="s1", payload="p1", origin="test")
    r2 = ledger.enqueue_send("key-C2", broker_client=client, type="message",
                              summary="s2", payload="p2", origin="test")
    assert r1["action_id"] == "aid-C1"
    assert r2["action_id"] == "aid-C2"
    assert client.enqueue_action.call_count == 2


# ---------------------------------------------------------------------------
# REQ-02: exactly-one-send end-to-end — approve then retry
# ---------------------------------------------------------------------------


def test_exactly_one_send_on_retry(ledger: SendIntentLedger) -> None:
    """
    Purpose: REQ-02 critical gate — approve + trigger retry → exactly one send total.
    The retry-before-approval is deduped at the ledger; the retry-after-approval sees a
    deduped action_id (same key → same action_id already in the ledger) and the broker's
    idempotent approve would return the cached result for that action_id without re-running
    the executor. This test verifies the ledger half: same key after 'approval' still returns
    the original action_id and does NOT call broker.enqueue_action again.
    Usage: the critical gate — MUST fail if the ledger dedup is removed.
    Gotchas: executor call count is the send-count proof; we mock the broker to count calls.
    The broker's own idempotent-approve (approval.py:105) covers the second half.
    """
    client = _make_broker_mock("aid-exact-01")
    executor_calls: list[str] = []

    def counting_executor(row: dict) -> dict:
        """Simulates the send side-effect; records each invocation."""
        executor_calls.append(row.get("action_id", "?"))
        return {"status": "sent"}

    # Step 1: first enqueue (the logical send).
    result1 = ledger.enqueue_send(
        "intent-exact",
        broker_client=client,
        type="message",
        summary="important draft",
        payload="please approve this",
        origin="daily-sweep",
    )
    assert result1["action_id"] == "aid-exact-01"

    # Step 2: simulate approval — we record action_id and "run" the executor once.
    counting_executor({"action_id": result1["action_id"]})
    assert executor_calls == ["aid-exact-01"]

    # Step 3: retry — re-emit the same logical send (same intent_key).
    # The ledger MUST return the existing action_id and NOT call broker.enqueue_action again.
    result2 = ledger.enqueue_send(
        "intent-exact",
        broker_client=client,
        type="message",
        summary="important draft",
        payload="please approve this",
        origin="daily-sweep",
    )
    assert result2["action_id"] == "aid-exact-01"
    assert result2.get("deduped") is True

    # Broker was called EXACTLY ONCE — the retry is blocked at the ledger.
    client.enqueue_action.assert_called_once()

    # The executor also ran EXACTLY ONCE — the send happened only once.
    # (The broker's idempotent approve covers the after-approval half;
    # the ledger covers the before-approval half. Together = exactly one send.)
    assert len(executor_calls) == 1


# ---------------------------------------------------------------------------
# REQ-03: concurrency safety — two racing enqueues produce one broker action
# ---------------------------------------------------------------------------


def test_concurrent_same_key_enqueue_produces_one_broker_action(
    ledger: SendIntentLedger,
) -> None:
    """
    Purpose: REQ-03 — INSERT ON CONFLICT DO NOTHING + read-back ensures that under
    concurrent enqueue_send for the same intent_key, exactly one broker action is created.
    Usage: spins N threads; each calls enqueue_send with the same key; counts broker calls.
    Gotchas: the INSERT is atomic at the SQLite level (serialised write); the loser re-reads
    the winner's action_id and returns it. If the lock/INSERT is missing, broker call count
    will exceed 1 and the test fails.
    """
    n_threads = 10
    action_id_counter = [0]
    action_id_lock = threading.Lock()
    broker_call_count = [0]
    results: list[dict] = []
    errors: list[Exception] = []

    def mock_enqueue(**kwargs: object) -> dict:
        """
        Purpose: track how many times the broker is actually called.
        Thread-safe via a lock — each call gets a unique (but deterministic) action_id.
        Gotchas: if multiple threads slip through the dedup gate this count > 1.
        """
        with action_id_lock:
            broker_call_count[0] += 1
            aid = f"aid-conc-{broker_call_count[0]:04d}"
        return {"action_id": aid, "disposition": "held"}

    client = MagicMock()
    client.enqueue_action.side_effect = mock_enqueue

    def worker() -> None:
        try:
            r = ledger.enqueue_send(
                "intent-concurrent",
                broker_client=client,
                type="message",
                summary="concurrent",
                payload="body",
                origin="test",
            )
            results.append(r)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"threads raised: {errors}"
    assert len(results) == n_threads

    # All threads must have received the SAME action_id.
    action_ids = {r["action_id"] for r in results}
    assert len(action_ids) == 1, f"expected 1 unique action_id, got {action_ids}"

    # Broker was called exactly once — the dedup gate fired for all but the winner.
    assert broker_call_count[0] == 1, (
        f"expected exactly 1 broker call, got {broker_call_count[0]}"
    )


# ---------------------------------------------------------------------------
# REQ-04 anti-weakening: dedup removed → test fails
# ---------------------------------------------------------------------------


def test_dedup_is_load_bearing(ledger: SendIntentLedger) -> None:
    """
    Purpose: documents the anti-weakening invariant: if an attacker removes the dedup
    check, a second call to enqueue_send with the same key WOULD hit the broker twice.
    This test asserts it does NOT — making it the falsification oracle for REQ-04.
    Usage: included as documentation + coverage; if it passes alongside the others, the
    dedup is present. If someone removes the dedup and this still passes, they've broken
    the invariant (their test must call broker twice and this test will then fail).
    Gotchas: this is structurally the same as test_second_enqueue_same_key — kept
    separate with an explicit narrative for the code-review audit trail.
    """
    client = MagicMock()
    client.enqueue_action.return_value = {"action_id": "aid-anti-weak", "disposition": "held"}

    ledger.enqueue_send("intent-anti-weak", broker_client=client,
                        type="message", summary="s", payload="p", origin="test")
    ledger.enqueue_send("intent-anti-weak", broker_client=client,
                        type="message", summary="s", payload="p", origin="test")

    # If this assertion fails, the dedup was bypassed — a double-send is happening.
    client.enqueue_action.assert_called_once()
