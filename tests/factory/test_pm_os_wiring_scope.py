# ABOUTME: RED-first scope tests for P6-d — pm_os wiring into the mission-control panel.
# ABOUTME: Tests confirm Jira intake and briefing are surfaced in the panel; pulse,
# ABOUTME: weekly, and exec-narrative pipelines are explicitly NOT wired (negative-
# ABOUTME: inclusive). Any wired-pipeline action that mutates state rides the broker.
# ABOUTME: If a parked pipeline is wired in, the negative tests fail (anti-weakening).
"""
Tests for factory.panel_wiring (P6-d).

Design authoritative source: docs/plans/harness/fable/p6/P6-design.md §4 (REQ-04),
§5 P6-d task row RED tests.

REQ-01: Jira intake (jira_poller) + briefing (morning_packet) surfaced in the panel.
REQ-02: pulse/weekly/exec-narrative are NOT wired (parked — grep/import asserted absent).
REQ-03: any panel action that mutates state routes through the broker, not direct mutation.
REQ-04: RED-first; the parked-pipelines-absent tests FAIL if a parked pipeline is wired.

Test index:
  1. test_jira_intake_surfaced — Jira-sourced jobs appear in the panel live view.
  2. test_briefing_generator_wired — panel standup view reads the P4 morning packet.
  3. test_no_pulse_pipeline_added — pulse pipeline is not imported/referenced.
  4. test_no_weekly_pipeline_added — weekly pipeline is not imported/referenced.
  5. test_no_exec_narrative_pipeline_added — exec_narrative pipeline not imported/referenced.

Broker invariant (REQ-03) is covered inline in test_jira_intake_surfaced: the panel's
trigger_jira_writeback action routes through broker_client.enqueue_action, not direct HTTP.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from factory.job_store import JobStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> JobStore:
    """Return a real in-process JobStore backed by a tmp SQLite file."""
    return JobStore(db_path=tmp_path / "jobs.db")


def _enqueue_jira_job(store: JobStore, *, key: str = "SAND-1") -> str:
    """Insert a minimal job row whose intake_source is 'jira'."""
    return store.enqueue_job({
        "repo": "sandbox",
        "spec": f"[{key}] implement feature",
        "kind": "feature",
        "worker": "claude",
        "model": "claude-sonnet",
        "budget_usd": 1.0,
        "timeout_min": 30,
        "intake_source": "jira",
        "intake_source_hash": f"jira:{key}:2026-07-07",
    })


# ---------------------------------------------------------------------------
# 1. test_jira_intake_surfaced
# ---------------------------------------------------------------------------


def test_jira_intake_surfaced(tmp_path: Path) -> None:
    """
    Purpose: a Jira-sourced job appears in the panel's live view and the
    panel's writeback action is broker-gated (not a direct Jira HTTP call).
    Requirement: REQ-01 (Jira surfaced) + REQ-03 (broker routing).
    Anti-weakening: if JiraPanelView.live_jira_jobs() is removed or returns []
    for a Jira job, this test fails.
    """
    from factory.panel_wiring import JiraPanelView

    store = _make_store(tmp_path)
    job_id = _enqueue_jira_job(store, key="SAND-42")

    # Read-only view requires no broker for listing.
    view = JiraPanelView(store=store, broker_client=None)
    jobs = view.live_jira_jobs()

    # job rows from list_jobs() use "id" (the column name), not "job_id".
    assert any(j["id"] == job_id for j in jobs), (
        "JiraPanelView.live_jira_jobs() must include the Jira-sourced job"
    )
    assert all(j.get("intake_source") == "jira" for j in jobs), (
        "live_jira_jobs() must return only jira-intake jobs"
    )

    # REQ-03: the broker-gated writeback path calls enqueue_action, not direct HTTP.
    mock_broker = MagicMock()
    mock_broker.enqueue_action.return_value = {"action_id": "act-1", "disposition": "held"}
    view_with_broker = JiraPanelView(store=store, broker_client=mock_broker)
    action_id = view_with_broker.trigger_jira_writeback(
        job_id=job_id,
        ticket_key="SAND-42",
        status="In Progress",
        requested_by="yk",
    )

    mock_broker.enqueue_action.assert_called_once()
    call_kwargs = mock_broker.enqueue_action.call_args.kwargs
    assert call_kwargs.get("type") == "jira_writeback", (
        "trigger_jira_writeback must enqueue type='jira_writeback' via broker"
    )
    assert action_id == "act-1", "trigger_jira_writeback must return the broker action_id"


# ---------------------------------------------------------------------------
# 2. test_briefing_generator_wired
# ---------------------------------------------------------------------------


def test_briefing_generator_wired(tmp_path: Path) -> None:
    """
    Purpose: the panel's standup view reads the P4 morning packet (morning_packet.build).
    Requirement: REQ-01 (briefing surfaced).
    Anti-weakening: if BriefingPanelView.standup_packet() does not return a MorningPacket
    (or equivalent) drawn from morning_packet.build(), this test fails.
    """
    from factory.panel_wiring import BriefingPanelView
    from factory.morning_packet import MorningPacket

    store = _make_store(tmp_path)

    # Put a job in AWAITING_APPROVAL state so the packet is non-empty.
    jid = store.enqueue_job({
        "repo": "test-repo",
        "spec": "deploy dashboard",
        "kind": "feature",
        "worker": "claude",
        "model": "claude-sonnet",
        "budget_usd": 1.0,
        "timeout_min": 30,
    })
    # Drive job to AWAITING_APPROVAL.
    states = ["QUEUED", "ADMITTED", "RUNNING", "TEST", "REVIEW", "AWAITING_APPROVAL"]
    for frm, to in zip(states, states[1:]):
        store.transition(jid, frm, to)

    view = BriefingPanelView(store=store, held_store=None)
    packet = view.standup_packet()

    assert isinstance(packet, MorningPacket), (
        "BriefingPanelView.standup_packet() must return a MorningPacket from morning_packet.build()"
    )
    assert len(packet.cards) == 1, (
        "standup_packet() must include the AWAITING_APPROVAL job"
    )
    assert packet.cards[0].job_id == jid


# ---------------------------------------------------------------------------
# 3. test_no_pulse_pipeline_added
# ---------------------------------------------------------------------------


def _panel_wiring_source() -> str:
    """Return the source text of factory/panel_wiring.py for static analysis."""
    src_path = Path(__file__).parent.parent.parent / "factory" / "panel_wiring.py"
    return src_path.read_text(encoding="utf-8")


def _panel_wiring_ast() -> ast.Module:
    """Parse factory/panel_wiring.py to an AST for import-graph checks."""
    return ast.parse(_panel_wiring_source())


def _imported_names(tree: ast.Module) -> List[str]:
    """
    Collect all module/attribute names referenced in import statements in the AST.
    Returns a flat list of dotted names (e.g. ['factory.pulse', 'pulse']).
    """
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
            for alias in node.names:
                names.append(alias.name)
    return names


def test_no_pulse_pipeline_added() -> None:
    """
    Purpose: assert the 'pulse' pipeline module is NOT imported in panel_wiring.py.
    Requirement: REQ-02 (pulse PARKED — must not be added).
    Anti-weakening: this test FAILS if a pulse pipeline module is imported/wired in.

    Note: documentary strings mentioning the word 'pulse' (e.g. listing what is
    PARKED) are expected and intentionally NOT flagged — only import-graph wiring
    (an actual import statement) is the actionable signal.
    """
    tree = _panel_wiring_ast()
    imported = _imported_names(tree)

    # Check import-level references only — catches actual wiring, not doc comments.
    pulse_imported = any(
        name.lower() in ("pulse", "factory.pulse", "pm_os.pulse")
        or name.lower().startswith("pulse.")
        for name in imported
    )

    assert not pulse_imported, (
        "panel_wiring.py must NOT import any pulse pipeline module (pulse is PARKED)"
    )


# ---------------------------------------------------------------------------
# 4. test_no_weekly_pipeline_added
# ---------------------------------------------------------------------------


def test_no_weekly_pipeline_added() -> None:
    """
    Purpose: assert the 'weekly' pipeline module is NOT imported in panel_wiring.py.
    Requirement: REQ-02 (weekly PARKED — must not be added).
    Anti-weakening: this test FAILS if a weekly pipeline module is imported/wired in.

    Note: documentary strings mentioning 'weekly' (listing what is PARKED) are
    expected and NOT flagged — only import-graph wiring is actionable.
    """
    tree = _panel_wiring_ast()
    imported = _imported_names(tree)

    weekly_imported = any(
        name.lower() in ("weekly", "factory.weekly", "pm_os.weekly")
        or name.lower().startswith("weekly.")
        for name in imported
    )

    assert not weekly_imported, (
        "panel_wiring.py must NOT import any weekly pipeline module (weekly is PARKED)"
    )


# ---------------------------------------------------------------------------
# 5. test_no_exec_narrative_pipeline_added
# ---------------------------------------------------------------------------


def test_no_exec_narrative_pipeline_added() -> None:
    """
    Purpose: assert the 'exec_narrative' pipeline module is NOT imported in panel_wiring.py.
    Requirement: REQ-02 (exec-narrative PARKED — must not be added).
    Anti-weakening: this test FAILS if exec_narrative is imported/wired in.

    Note: documentary strings mentioning 'exec-narrative' or 'exec_narrative' (listing
    what is PARKED) are expected and NOT flagged — only import-graph wiring is actionable.
    """
    tree = _panel_wiring_ast()
    imported = _imported_names(tree)

    exec_nar_imported = any(
        name.lower() in ("exec_narrative", "factory.exec_narrative", "pm_os.exec_narrative")
        or "exec_narrative" in name.lower()
        for name in imported
    )

    assert not exec_nar_imported, (
        "panel_wiring.py must NOT import exec_narrative pipeline (exec-narrative is PARKED)"
    )
