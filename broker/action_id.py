# ABOUTME: ULID action-id generation for held broker actions.
# ABOUTME: A ULID is a 128-bit value: 48-bit millisecond timestamp + 80 random
# ABOUTME: bits, rendered as 26 Crockford base32 chars. It is stable, opaque,
# ABOUTME: collision-free without coordination, and lexicographically sortable by
# ABOUTME: creation time — and is NEVER derived from a message body (the fix for
# ABOUTME: the four-way /approve-email hashing bug).
from __future__ import annotations

import os
import threading
import time

# Crockford base32 alphabet (excludes I, L, O, U to avoid transcription errors).
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Monotonic state: within a single millisecond we increment the random field
# rather than re-randomising, so ids minted in the same ms still sort by
# creation order (ULID monotonic-random spec). Guarded by a lock for thread use.
_lock = threading.Lock()
_last_ms = -1
_last_rand = 0
_RAND_MAX = (1 << 80) - 1


def _encode(value: int, length: int) -> str:
    """
    Purpose: render an integer as a fixed-length Crockford base32 string.
    Usage: internal helper for new_action_id (10-char time + 16-char random).
    Gotchas: pads on the left with '0' so lexicographic order matches numeric
    order — the property that makes ULIDs sortable by creation time.
    """
    chars = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        chars.append(_ALPHABET[rem])
    return "".join(reversed(chars))


def new_action_id(now_ms: int | None = None) -> str:
    """
    Purpose: mint a fresh, opaque, time-sortable action id for a held action.
    Usage: aid = new_action_id(); pass as the stable key everywhere downstream.
    Gotchas: within the same millisecond the 80-bit random field is monotonically
    incremented (not re-randomised) so ids remain lexicographically ordered by
    creation time; the id must NEVER be replaced by a hash of the payload.
    """
    global _last_ms, _last_rand
    with _lock:
        ms = int(time.time() * 1000) if now_ms is None else now_ms
        ms &= (1 << 48) - 1
        if ms == _last_ms:
            # Same ms: increment the previous random field to preserve order.
            _last_rand = (_last_rand + 1) & _RAND_MAX
            rand = _last_rand
        else:
            _last_ms = ms
            rand = int.from_bytes(os.urandom(10), "big")
            _last_rand = rand
    time_part = _encode(ms, 10)
    rand_part = _encode(rand, 16)
    return time_part + rand_part
