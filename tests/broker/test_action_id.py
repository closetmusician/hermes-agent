# ABOUTME: RED-first tests for broker/action_id.py — ULID generation.
# ABOUTME: Asserts action IDs are 26-char Crockford base32 ULIDs, are stable
# ABOUTME: opaque strings (NEVER derived from message body — the /approve-email
# ABOUTME: bug class), sort lexicographically by creation time, and do not
# ABOUTME: collide across a tight generation loop.
import re

from broker.action_id import new_action_id

CROCKFORD = re.compile(r"^[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$")


def test_action_id_is_26_char_crockford_base32():
    aid = new_action_id()
    assert CROCKFORD.match(aid), f"not a ULID: {aid!r}"


def test_action_id_not_derived_from_payload():
    # Same payload twice MUST yield different ids — the id is opaque, never a
    # hash of the body. This is the exact fix for the four-way /approve-email bug.
    payload = "email the board a status update"
    a = new_action_id()
    b = new_action_id()
    assert a != b


def test_action_ids_sort_by_creation_time():
    ids = [new_action_id() for _ in range(50)]
    # ULIDs are lexicographically sortable by creation time; generated in order
    # they must already be non-decreasing.
    assert ids == sorted(ids)


def test_action_ids_do_not_collide():
    ids = {new_action_id() for _ in range(5000)}
    assert len(ids) == 5000
