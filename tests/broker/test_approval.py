# ABOUTME: RED-first tests for broker/approval.py — the approval-authority split
# ABOUTME: that closes the P0-3 self-approval hole. A broker-minted single-use
# ABOUTME: nonce is bound to a specific action_id; approve() without a valid
# ABOUTME: nonce (the assistant over the enqueue socket) is REJECTED; the nonce
# ABOUTME: is single-use (replay rejected) and cannot be minted without the secret.
import pytest

from broker.held_store import HeldStore
from broker.approval import ApprovalAuthority, ApprovalRejected


@pytest.fixture()
def store(tmp_path):
    return HeldStore(tmp_path / "held_actions.db")


@pytest.fixture()
def authority(store):
    return ApprovalAuthority(store, broker_secret=b"broker-only-secret")


def _enqueue(store):
    return store.enqueue(
        type="message",
        channel="telegram",
        recipient="board",
        summary="status update",
        payload="the full body",
        origin="send_message_tool",
        safe_lane_json="{}",
        state="held",
    )


def test_mint_nonce_is_opaque_and_bound(authority, store):
    aid = _enqueue(store)
    nonce = authority.mint_nonce(aid)
    assert isinstance(nonce, str) and len(nonce) >= 16
    # A different action's nonce must not validate against this one.
    other = _enqueue(store)
    other_nonce = authority.mint_nonce(other)
    assert nonce != other_nonce


def test_approve_with_valid_nonce_executes(authority, store):
    aid = _enqueue(store)
    nonce = authority.mint_nonce(aid)
    executed = []
    result = authority.approve(aid, nonce, executor=lambda row: executed.append(row["action_id"]) or {"status": "sent"})
    assert result["status"] == "sent"
    assert executed == [aid]
    assert store.get(aid)["state"] == "executed"


def test_approve_without_nonce_rejected(authority, store):
    # The assistant calling approve over the enqueue socket with NO valid nonce.
    aid = _enqueue(store)
    sent = []
    with pytest.raises(ApprovalRejected):
        authority.approve(aid, None, executor=lambda row: sent.append(1))
    assert sent == []
    assert store.get(aid)["state"] == "held"


def test_approve_with_wrong_nonce_rejected(authority, store):
    aid = _enqueue(store)
    authority.mint_nonce(aid)
    sent = []
    with pytest.raises(ApprovalRejected):
        authority.approve(aid, "totally-made-up-nonce", executor=lambda row: sent.append(1))
    assert sent == []


def test_nonce_bound_to_action_not_transferable(authority, store):
    # A valid nonce for action A must not approve action B.
    a = _enqueue(store)
    b = _enqueue(store)
    nonce_a = authority.mint_nonce(a)
    sent = []
    with pytest.raises(ApprovalRejected):
        authority.approve(b, nonce_a, executor=lambda row: sent.append(1))
    assert sent == []


def test_nonce_single_use_replay_rejected(authority, store):
    aid = _enqueue(store)
    nonce = authority.mint_nonce(aid)
    authority.approve(aid, nonce, executor=lambda row: {"status": "sent"})
    # Second use of the same nonce must be rejected (burned).
    with pytest.raises(ApprovalRejected):
        authority.approve(aid, nonce, executor=lambda row: {"status": "sent-again"})


def test_approve_is_idempotent_by_action_id(authority, store):
    # Re-approving an already-executed action returns the cached result and
    # does NOT execute egress twice, even with a fresh nonce.
    aid = _enqueue(store)
    nonce = authority.mint_nonce(aid)
    calls = []
    r1 = authority.approve(aid, nonce, executor=lambda row: calls.append(1) or {"status": "sent"})
    nonce2 = authority.mint_nonce(aid)
    r2 = authority.approve(aid, nonce2, executor=lambda row: calls.append(1) or {"status": "sent"})
    assert len(calls) == 1, "egress executed more than once"
    assert r1 == r2


def test_reject_by_id_no_egress(authority, store):
    aid = _enqueue(store)
    authority.reject(aid, reason="not now")
    assert store.get(aid)["state"] == "rejected"


def test_cannot_mint_without_secret_matching(store):
    # An authority with a different secret cannot mint a nonce that the real
    # authority will accept — proves the nonce is unforgeable without the secret.
    real = ApprovalAuthority(store, broker_secret=b"real-secret")
    forger = ApprovalAuthority(store, broker_secret=b"guessed-secret")
    aid = _enqueue(store)
    forged = forger.mint_nonce(aid)
    sent = []
    with pytest.raises(ApprovalRejected):
        real.approve(aid, forged, executor=lambda row: sent.append(1))
    assert sent == []
