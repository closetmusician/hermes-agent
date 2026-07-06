# ABOUTME: Intake seam for the Fable factory — normalizes two intake sources
# ABOUTME: into a stable job-spec dict WITHOUT writing the DB.  The supervisor
# ABOUTME: is the SOLE writer; this module only parses and returns spec dicts.
# ABOUTME: Source 1: /factory <repo>: <spec> (Telegram command string).
# ABOUTME: Source 2: tasks.md factory:-tagged lines (idempotent by source hash).
"""
Intake seam — parse and normalize two job-intake sources into job-spec dicts.

Neither path writes the job store.  The caller (supervisor) receives a spec
dict and calls store.enqueue_job(spec) if the intake_source_hash is fresh
(store.has_job_for_intake_hash returns False).  This keeps the single-writer
invariant: no other module ever touches the jobs write cursor.

Source A — Telegram /factory command:
    /factory <repo>: <spec text>
    e.g. /factory acme: add OAuth2 login flow

Source B — tasks.md factory: tag:
    A line in tasks.md whose text contains a "factory:" prefix.
    The line is hashed (SHA-256 of the stripped line) as the idempotency key.
    The supervisor drains new tagged lines each tick; already-seen lines
    (hash in DB) are silently skipped.

Both return a spec dict with shape:
    {
        "repo": str,
        "spec": str,
        "base_branch": str,       # default "main"
        "kind": str,              # default "feature"
        "worker": str,            # default "claude"
        "model": str,             # default "claude-opus-4-5"
        "budget_usd": float,      # default 1.0
        "timeout_min": int,       # default 30
        "intake_source": str,     # "telegram" | "tasks_md"
        "intake_source_hash": str # stable idempotency key
    }
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import List, Optional


class IntakeParseError(Exception):
    """Raised when an intake source is malformed and cannot be parsed."""


# ---------------------------------------------------------------------------
# Source A: Telegram /factory command
# ---------------------------------------------------------------------------

# Expected format: /factory <repo>: <spec>
# The repo must be non-empty; the colon+space separator is mandatory.
_FACTORY_CMD_RE = re.compile(
    r"^/factory\s+([A-Za-z0-9_./-]+)\s*:\s*(.+)$",
    re.DOTALL,
)


def parse_telegram_command(text: str, *, defaults: Optional[dict] = None) -> dict:
    """
    Purpose: parse a Telegram /factory command string into a job-spec dict.
    Usage: spec = parse_telegram_command("/factory acme: add login flow").
    Gotchas: raises IntakeParseError on any format violation (no colon, empty
    repo, empty spec) — never silently produces a partial spec.  The returned
    dict includes a stable intake_source_hash so the supervisor can guard
    idempotency.
    """
    if defaults is None:
        defaults = {}
    text = text.strip()
    m = _FACTORY_CMD_RE.match(text)
    if m is None:
        raise IntakeParseError(
            f"invalid /factory command — expected '/factory <repo>: <spec>', got: {text!r}"
        )
    repo = m.group(1).strip()
    spec = m.group(2).strip()
    if not repo:
        raise IntakeParseError("/factory command has empty repo")
    if not spec:
        raise IntakeParseError("/factory command has empty spec")

    # Hash is over the normalized canonical text so the same command from two
    # Telegram messages doesn't create two jobs.
    canonical = f"telegram:/factory {repo}: {spec}"
    source_hash = _stable_hash(canonical)

    return {
        "repo": repo,
        "spec": spec,
        "base_branch": defaults.get("base_branch", "main"),
        "kind": defaults.get("kind", "feature"),
        "worker": defaults.get("worker", "claude"),
        "model": defaults.get("model", "claude-opus-4-5"),
        "budget_usd": defaults.get("budget_usd", 1.0),
        "timeout_min": defaults.get("timeout_min", 30),
        "intake_source": "telegram",
        "intake_source_hash": source_hash,
    }


# ---------------------------------------------------------------------------
# Source B: tasks.md factory: tag
# ---------------------------------------------------------------------------

# A tasks.md line is eligible if it contains "factory:" (case-insensitive)
# with an optional whitespace.  The line format is:
#     - factory: <repo>: <spec>
# or:
#     factory: <repo>: <spec>
# The leading bullet/dash and checkbox markers are stripped.
_FACTORY_TAG_RE = re.compile(
    r"^\s*(?:-\s+)?(?:\[[xX ]\]\s+)?factory:\s+([A-Za-z0-9_./-]+)\s*:\s*(.+)$",
    re.IGNORECASE,
)


def scan_tasks_md(tasks_md_path: Path, *, defaults: Optional[dict] = None) -> List[dict]:
    """
    Purpose: scan a tasks.md file and return a spec dict for every
    factory:-tagged line.  Returns an empty list if the file does not exist
    or contains no such lines.
    Usage: specs = scan_tasks_md(Path("tasks.md")).
    Gotchas: each spec includes an intake_source_hash derived from the raw
    line text; re-scanning the same file produces identical hashes so the
    supervisor's idempotency guard (has_job_for_intake_hash) prevents duplicate
    job rows even when the file is scanned on every tick.
    """
    if defaults is None:
        defaults = {}

    tasks_path = Path(tasks_md_path)
    if not tasks_path.exists():
        return []

    specs: List[dict] = []
    for raw_line in tasks_path.read_text(encoding="utf-8").splitlines():
        spec = _parse_tasks_md_line(raw_line, defaults=defaults)
        if spec is not None:
            specs.append(spec)
    return specs


def parse_tasks_md_line(line: str, *, defaults: Optional[dict] = None) -> Optional[dict]:
    """
    Purpose: parse a single tasks.md line into a job-spec dict, or return
    None if the line is not a factory: tag.
    Usage: spec = parse_tasks_md_line("- factory: acme: add login").
    Gotchas: None = not a factory tag (not an error); IntakeParseError is
    raised only for factory:-prefixed lines that are structurally malformed.
    """
    return _parse_tasks_md_line(line, defaults=defaults)


def _parse_tasks_md_line(line: str, *, defaults: Optional[dict] = None) -> Optional[dict]:
    """Internal: parse one tasks.md line, returning None if not a factory tag."""
    if defaults is None:
        defaults = {}
    stripped = line.strip()
    if not stripped:
        return None
    # Quick pre-filter before the full regex.
    if "factory:" not in stripped.lower():
        return None

    m = _FACTORY_TAG_RE.match(stripped)
    if m is None:
        # The line contains "factory:" but doesn't match the expected pattern.
        raise IntakeParseError(
            f"malformed factory: tag in tasks.md — "
            f"expected 'factory: <repo>: <spec>', got: {stripped!r}"
        )

    repo = m.group(1).strip()
    spec = m.group(2).strip()
    if not repo:
        raise IntakeParseError(f"factory: tag has empty repo in: {stripped!r}")
    if not spec:
        raise IntakeParseError(f"factory: tag has empty spec in: {stripped!r}")

    # Hash is over the raw original line so the same physical line always
    # produces the same hash across ticks.
    source_hash = _stable_hash(f"tasks_md:{line}")

    return {
        "repo": repo,
        "spec": spec,
        "base_branch": defaults.get("base_branch", "main"),
        "kind": defaults.get("kind", "feature"),
        "worker": defaults.get("worker", "claude"),
        "model": defaults.get("model", "claude-opus-4-5"),
        "budget_usd": defaults.get("budget_usd", 1.0),
        "timeout_min": defaults.get("timeout_min", 30),
        "intake_source": "tasks_md",
        "intake_source_hash": source_hash,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stable_hash(text: str) -> str:
    """
    Purpose: compute a stable SHA-256 hex digest of text for use as an
    idempotency key.
    Usage: h = _stable_hash("telegram:/factory acme: foo").
    Gotchas: any whitespace variation in the input changes the hash — callers
    must normalize text before hashing.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
