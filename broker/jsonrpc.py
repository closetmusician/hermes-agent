# ABOUTME: Newline-framed JSON-RPC 2.0 codec for the broker unix socket.
# ABOUTME: One request/response per line — the simplest framing that survives a
# ABOUTME: crash mid-write: a half-written (unterminated) final line is left in
# ABOUTME: the remainder buffer and discarded on reconnect, never misparsed.
# ABOUTME: No third-party dependency; encodes requests, results, and error frames.
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple


def encode_request(method: str, params: Dict[str, Any], req_id: int) -> bytes:
    """
    Purpose: serialise a JSON-RPC 2.0 request as a single newline-framed line.
    Usage: sock.sendall(encode_request("health", {}, req_id=1)).
    Gotchas: the trailing newline is the frame delimiter — decode relies on it.
    """
    obj = {"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}
    return (json.dumps(obj) + "\n").encode("utf-8")


def encode_response(result: Any, req_id: int) -> bytes:
    """
    Purpose: serialise a successful JSON-RPC result frame.
    Usage: sock.sendall(encode_response({"status": "ok"}, req_id)).
    Gotchas: never include an "error" key on a success frame.
    """
    obj = {"jsonrpc": "2.0", "result": result, "id": req_id}
    return (json.dumps(obj) + "\n").encode("utf-8")


def encode_error(code: int, message: str, req_id: Any) -> bytes:
    """
    Purpose: serialise a JSON-RPC error frame (e.g. rejected approval).
    Usage: sock.sendall(encode_error(-32000, "rejected", req_id)).
    Gotchas: req_id may be None when the offending request could not be parsed.
    """
    obj = {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": req_id}
    return (json.dumps(obj) + "\n").encode("utf-8")


def decode_frames(buf: bytes) -> Tuple[List[Dict[str, Any]], bytes]:
    """
    Purpose: split a byte buffer into complete JSON-RPC frames + a remainder.
    Usage: frames, remainder = decode_frames(accumulated_bytes); keep remainder.
    Gotchas: a trailing fragment WITHOUT a newline is returned unparsed in the
    remainder (crash-safety: a half-written line is discarded, not misparsed). A
    complete line that is not valid JSON raises ValueError — a real protocol
    error is surfaced loudly, never silently swallowed.
    """
    frames: List[Dict[str, Any]] = []
    # Split off complete lines; the last element is the (possibly empty) tail
    # with no trailing newline — that is the remainder to carry forward.
    *complete, remainder = buf.split(b"\n")
    for line in complete:
        if line.strip() == b"":
            continue
        obj = json.loads(line.decode("utf-8"))  # raises ValueError on garbage
        frames.append(obj)
    return frames, remainder
