# ABOUTME: The worker-result JSON schema + validator (P2 design §2.5). A factory
# ABOUTME: worker (claude -p / codex exec) must emit exactly this shape; the runner
# ABOUTME: validates its output against the schema. Malformed / non-conforming
# ABOUTME: output is caught and turned into a structured error (NEEDS_ATTENTION),
# ABOUTME: never a crash — a worker that won't speak the schema is not trusted.
"""
Worker-result schema and validation.

Design authoritative source: docs/plans/harness/fable/p2/P2-design.md §2.5

The worker returns a single JSON object:

    { "status": "completed" | "failed" | "needs_attention",
      "branch": "factory/...",          # must match the worktree branch
      "summary": "<free text>",
      "test_result": "pass"|"fail"|"not_run"|"flaky" }

``validate_worker_result`` parses raw worker stdout (which may contain log noise
around the JSON) and validates the extracted object against the schema. On any
failure it raises ``WorkerResultInvalid`` carrying a human-readable reason — the
runner converts that into a NEEDS_ATTENTION transition rather than crashing.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from jsonschema import Draft7Validator

# The schema, verbatim from design §2.5.
WORKER_RESULT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["status", "branch", "summary", "test_result"],
    "properties": {
        "status": {"enum": ["completed", "failed", "needs_attention"]},
        "branch": {"type": "string", "pattern": "^factory/"},
        "summary": {"type": "string"},
        "test_result": {"enum": ["pass", "fail", "not_run", "flaky"]},
    },
}

_VALIDATOR = Draft7Validator(WORKER_RESULT_SCHEMA)


class WorkerResultInvalid(Exception):
    """
    Raised when worker output cannot be parsed or does not conform to the schema.

    Purpose: give the runner a single, catchable error to map to NEEDS_ATTENTION.
    Usage: raised by validate_worker_result / parse_worker_output.
    Gotchas: this is expected, not exceptional — a worker producing garbage is a
    normal failure mode the supervisor must handle, not a bug in the runner.
    """


def _extract_json_object(raw: str) -> Dict[str, Any]:
    """
    Purpose: pull the JSON result object out of raw worker stdout, which may be
    wrapped in log lines or surrounded by whitespace. Tries a direct parse first,
    then falls back to the last balanced top-level {...} block in the text.
    Usage: obj = _extract_json_object(worker_stdout)
    Gotchas: raises WorkerResultInvalid if no JSON object can be recovered; a
    worker emitting several objects has only its LAST (final) result honored —
    that is the schema'd summary the CLI prints at the end of the run.
    """
    text = raw.strip()
    if not text:
        raise WorkerResultInvalid("worker produced no output")

    # Fast path: the whole output is the JSON object.
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Fallback: scan for the last balanced top-level object. Workers that print
    # progress lines before the final JSON summary land here.
    last_obj: Optional[Dict[str, Any]] = None
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = text[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            last_obj = parsed
                    except json.JSONDecodeError:
                        pass
                    start = -1

    if last_obj is None:
        raise WorkerResultInvalid("no JSON result object found in worker output")
    return last_obj


def validate_worker_result(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Purpose: validate an already-parsed dict against WORKER_RESULT_SCHEMA.
    Usage: clean = validate_worker_result({"status": "completed", ...})
    Gotchas: raises WorkerResultInvalid aggregating every schema error (so a
    worker gets one full report, not a first-error-only message). Returns the
    same dict on success for convenient chaining.
    """
    errors = sorted(_VALIDATOR.iter_errors(obj), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in errors
        )
        raise WorkerResultInvalid(f"worker result does not match schema: {detail}")
    return obj


def parse_worker_output(raw: str, *, expected_branch: Optional[str] = None) -> Dict[str, Any]:
    """
    Purpose: end-to-end parse+validate of raw worker stdout into a schema-valid
    result dict — the single entry point the runner calls on collect_result.
    Usage: result = parse_worker_output(worker_result.output,
                                        expected_branch="factory/acme/<id>-slug")
    Gotchas:
      * raises WorkerResultInvalid on ANY failure (unparseable, non-conforming,
        or a branch that does not match the worktree branch the supervisor
        recorded — a worker claiming a different branch is not trusted).
      * expected_branch is optional so the schema check can run standalone in
        unit tests without a worktree.
    """
    obj = _extract_json_object(raw)
    validate_worker_result(obj)
    if expected_branch is not None and obj.get("branch") != expected_branch:
        raise WorkerResultInvalid(
            f"worker claimed branch {obj.get('branch')!r} but the supervisor "
            f"recorded {expected_branch!r}"
        )
    return obj
