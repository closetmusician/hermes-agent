# ABOUTME: RED-first tests for broker/jsonrpc.py — newline-framed JSON-RPC 2.0.
# ABOUTME: Asserts request/response encode+decode round-trip, error frames,
# ABOUTME: and the crash-safety property that a half-written (truncated) line is
# ABOUTME: DISCARDED rather than misparsed — the framing guarantee §1.2 relies on.
import json

from broker.jsonrpc import (
    encode_request,
    encode_response,
    encode_error,
    decode_frames,
)


def test_encode_request_is_newline_terminated_json():
    frame = encode_request("health", {}, req_id=1)
    assert frame.endswith(b"\n")
    obj = json.loads(frame)
    assert obj["jsonrpc"] == "2.0"
    assert obj["method"] == "health"
    assert obj["id"] == 1


def test_encode_response_round_trips():
    frame = encode_response({"status": "ok"}, req_id=7)
    obj = json.loads(frame)
    assert obj["result"] == {"status": "ok"}
    assert obj["id"] == 7
    assert "error" not in obj


def test_encode_error_frame():
    frame = encode_error(-32000, "boom", req_id=9)
    obj = json.loads(frame)
    assert obj["error"]["code"] == -32000
    assert obj["error"]["message"] == "boom"


def test_decode_frames_splits_multiple_lines():
    buf = encode_request("a", {}, req_id=1) + encode_request("b", {}, req_id=2)
    frames, remainder = decode_frames(buf)
    assert len(frames) == 2
    assert frames[0]["method"] == "a"
    assert frames[1]["method"] == "b"
    assert remainder == b""


def test_halfline_is_discarded_not_misparsed():
    # A truncated final frame (broker crashed mid-write) must remain in the
    # remainder buffer, never yield a partial/garbage parsed object.
    good = encode_request("health", {}, req_id=1)
    truncated = good + b'{"jsonrpc":"2.0","method":"hea'  # no newline
    frames, remainder = decode_frames(truncated)
    assert len(frames) == 1
    assert frames[0]["method"] == "health"
    assert remainder == b'{"jsonrpc":"2.0","method":"hea'


def test_malformed_complete_line_raises_not_silent():
    # A complete (newline-terminated) but non-JSON line is a protocol error,
    # surfaced — not silently swallowed as if valid.
    import pytest

    bad = b"not json at all\n"
    with pytest.raises(ValueError):
        decode_frames(bad)
