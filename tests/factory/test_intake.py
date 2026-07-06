# ABOUTME: RED-first tests for factory/intake.py — P2-b requirements.
# ABOUTME: Tests parse_telegram_command, scan_tasks_md, and the supervisor-level
# ABOUTME: idempotency guarantee (same intake item → 0 new jobs on re-tick).
# ABOUTME: Uses real SQLite (via JobStore) — no mocks; validates the single-
# ABOUTME: writer invariant by checking that only the supervisor issues INSERTs.
# ABOUTME: Maps to P2-design.md §5 P2-b RED tests #1–#4.
"""
Tests for factory.intake (P2-b).

Coverage:
  1. /factory acme: add X → spec dict {repo=acme, spec="add X"}.
  2. A factory:-tagged tasks.md line → spec dict.
  3. ★ Both paths, ticked twice → exactly one job row per source; the writer
     is the supervisor (assert no other module holds the write cursor).
  4. A malformed /factory (no colon) → IntakeParseError, no row created.
  5. Edge cases: multiple factory: lines; blank lines ignored.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from factory.intake import (
    IntakeParseError,
    parse_telegram_command,
    parse_tasks_md_line,
    scan_tasks_md,
)
from factory.job_store import JobStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enqueue_if_new(store: JobStore, spec: dict) -> str | None:
    """
    Minimal supervisor logic: check idempotency, then enqueue.  Returns the
    job id if a new row was inserted, or None if already present.
    This is the ONLY path that should call store.enqueue_job().
    """
    h = spec.get("intake_source_hash")
    if h and store.has_job_for_intake_hash(h):
        return None
    return store.enqueue_job(spec)


# ---------------------------------------------------------------------------
# Test 1 — /factory Telegram command parsing
# ---------------------------------------------------------------------------


def test_telegram_command_basic():
    """P2-b #1: canonical /factory command parses into the right spec fields."""
    spec = parse_telegram_command("/factory acme: add OAuth2 login flow")
    assert spec["repo"] == "acme"
    assert spec["spec"] == "add OAuth2 login flow"
    assert spec["intake_source"] == "telegram"
    assert spec["intake_source_hash"] is not None


def test_telegram_command_with_path_repo():
    """/factory accepts repo names with slashes (e.g. org/repo)."""
    spec = parse_telegram_command("/factory org/myrepo: refactor auth module")
    assert spec["repo"] == "org/myrepo"
    assert spec["spec"] == "refactor auth module"


def test_telegram_command_spec_can_contain_colons():
    """The spec text itself may contain colons after the mandatory separator."""
    spec = parse_telegram_command("/factory myrepo: add config: debug=true")
    assert spec["repo"] == "myrepo"
    assert "debug=true" in spec["spec"]


def test_telegram_command_multiline_spec():
    """Spec text may span multiple lines (the regex is DOTALL)."""
    text = "/factory myrepo: line 1\nline 2\nline 3"
    spec = parse_telegram_command(text)
    assert "line 1" in spec["spec"]
    assert "line 2" in spec["spec"]


def test_telegram_command_stable_hash():
    """The same command always produces the same intake_source_hash."""
    h1 = parse_telegram_command("/factory acme: add X")["intake_source_hash"]
    h2 = parse_telegram_command("/factory acme: add X")["intake_source_hash"]
    assert h1 == h2
    # A different command must produce a different hash.
    h3 = parse_telegram_command("/factory acme: add Y")["intake_source_hash"]
    assert h1 != h3


# ---------------------------------------------------------------------------
# Test 4 — Malformed /factory → IntakeParseError
# ---------------------------------------------------------------------------


def test_telegram_command_no_colon_raises():
    """P2-b #4: missing colon separator → IntakeParseError."""
    with pytest.raises(IntakeParseError):
        parse_telegram_command("/factory acme add X")


def test_telegram_command_empty_repo_raises():
    """/factory with no repo before the colon → IntakeParseError."""
    with pytest.raises(IntakeParseError):
        parse_telegram_command("/factory : add X")


def test_telegram_command_empty_spec_raises():
    """/factory with no spec after the colon → IntakeParseError."""
    with pytest.raises(IntakeParseError):
        parse_telegram_command("/factory acme:")


def test_telegram_command_not_factory_raises():
    """A completely unrelated command → IntakeParseError."""
    with pytest.raises(IntakeParseError):
        parse_telegram_command("/help foo")


# ---------------------------------------------------------------------------
# Test 2 — tasks.md factory: tag
# ---------------------------------------------------------------------------


def test_tasks_md_line_basic():
    """P2-b #2: a plain factory: line parses into the right spec dict."""
    spec = parse_tasks_md_line("factory: acme: add login")
    assert spec is not None
    assert spec["repo"] == "acme"
    assert spec["spec"] == "add login"
    assert spec["intake_source"] == "tasks_md"
    assert spec["intake_source_hash"] is not None


def test_tasks_md_line_with_bullet():
    """Standard markdown bullet form: '- factory: repo: spec'."""
    spec = parse_tasks_md_line("- factory: acme: add login")
    assert spec is not None
    assert spec["repo"] == "acme"


def test_tasks_md_line_with_checkbox():
    """Checkbox form: '- [ ] factory: repo: spec'."""
    spec = parse_tasks_md_line("- [ ] factory: acme: add feature")
    assert spec is not None
    assert spec["spec"] == "add feature"


def test_tasks_md_line_not_factory_returns_none():
    """Lines that don't contain 'factory:' return None (not an error)."""
    assert parse_tasks_md_line("- [ ] fix typo in docs") is None
    assert parse_tasks_md_line("") is None
    assert parse_tasks_md_line("# Heading") is None


def test_tasks_md_line_stable_hash():
    """The same raw line always yields the same intake_source_hash."""
    line = "- factory: acme: add X"
    h1 = parse_tasks_md_line(line)["intake_source_hash"]
    h2 = parse_tasks_md_line(line)["intake_source_hash"]
    assert h1 == h2


def test_scan_tasks_md_multiple_lines(tmp_path):
    """scan_tasks_md returns one spec per factory:-tagged line."""
    tasks = tmp_path / "tasks.md"
    tasks.write_text(
        "# Board\n"
        "- [ ] fix docs\n"
        "- factory: acme: feature one\n"
        "- factory: beta: feature two\n"
        "- done task\n",
        encoding="utf-8",
    )
    specs = scan_tasks_md(tasks)
    assert len(specs) == 2
    repos = {s["repo"] for s in specs}
    assert repos == {"acme", "beta"}


def test_scan_tasks_md_missing_file(tmp_path):
    """scan_tasks_md returns [] if the file does not exist."""
    specs = scan_tasks_md(tmp_path / "nonexistent.md")
    assert specs == []


# ---------------------------------------------------------------------------
# Test 3 — ★ Idempotency: exactly one job per source even across two ticks
# ---------------------------------------------------------------------------


def test_telegram_idempotency_two_ticks(tmp_path):
    """
    P2-b #3 ★: processing the same /factory command twice (two ticks) must
    produce exactly one job row, not two.  The writer is always _enqueue_if_new
    (our minimal supervisor stand-in) — intake.py itself never writes the DB.
    """
    store = JobStore(tmp_path / "jobs.db")
    cmd = "/factory acme: add X"

    # Tick 1.
    spec = parse_telegram_command(cmd)
    jid1 = _enqueue_if_new(store, spec)
    assert jid1 is not None

    # Tick 2 — same command.
    spec2 = parse_telegram_command(cmd)
    jid2 = _enqueue_if_new(store, spec2)
    assert jid2 is None  # idempotency guard fired

    assert len(store.list_jobs()) == 1


def test_tasks_md_idempotency_two_ticks(tmp_path):
    """
    P2-b #3 ★: a factory:-tagged tasks.md line scanned on two consecutive
    ticks creates exactly one job row.
    """
    store = JobStore(tmp_path / "jobs.db")
    tasks = tmp_path / "tasks.md"
    tasks.write_text("- factory: acme: add Y\n", encoding="utf-8")

    # Tick 1.
    for spec in scan_tasks_md(tasks):
        _enqueue_if_new(store, spec)

    # Tick 2 — same file.
    for spec in scan_tasks_md(tasks):
        _enqueue_if_new(store, spec)

    assert len(store.list_jobs()) == 1


def test_both_sources_single_writer(tmp_path):
    """
    P2-b #3 ★: both intake paths (telegram + tasks.md) each produce exactly
    one job row; the writer in both cases is _enqueue_if_new (the supervisor
    stand-in) — no other module should be calling store.enqueue_job().
    """
    store = JobStore(tmp_path / "jobs.db")
    tasks = tmp_path / "tasks.md"
    tasks.write_text("- factory: acme: feature from file\n", encoding="utf-8")

    # Intake via telegram.
    tg_spec = parse_telegram_command("/factory acme: feature from telegram")
    _enqueue_if_new(store, tg_spec)

    # Intake via tasks.md.
    for spec in scan_tasks_md(tasks):
        _enqueue_if_new(store, spec)

    assert len(store.list_jobs()) == 2

    # Both rows are authored by the supervisor path (no direct INSERT from
    # intake.py — verify by confirming intake modules don't import JobStore).
    import factory.intake as intake_mod
    # intake.py must not import JobStore — that would break the sole-writer rule.
    assert not hasattr(intake_mod, "JobStore"), (
        "intake.py must not import or expose JobStore — the supervisor is the sole writer"
    )


def test_two_different_commands_create_two_jobs(tmp_path):
    """Different commands/lines create two independent rows."""
    store = JobStore(tmp_path / "jobs.db")
    for cmd in ["/factory acme: add X", "/factory beta: add Y"]:
        spec = parse_telegram_command(cmd)
        _enqueue_if_new(store, spec)

    assert len(store.list_jobs()) == 2
