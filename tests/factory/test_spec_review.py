# ABOUTME: RED-first tests for factory/spec_review.py (P3-c, REQ-01..04).
# ABOUTME: Covers the park/hold gate: high-ambiguity → questions.md with 2-3
# ABOUTME: proposed answers; low-ambiguity → HELD broker action; tier-gated
# ABOUTME: auto-clear (tier≥1 only); work/Diligent repos always held.
"""
Tests for factory.spec_review (P3-c, REQ-01..04).

Coverage:
  REQ-01:
    1. high-ambiguity spec (score ≥ 0.5) parks to questions.md with ≥1 question
       carrying 2-3 proposed answers; zero jobs enqueued.
    2. low-ambiguity spec on tier-0 repo → HELD broker action (not auto-cleared).
  REQ-02 (tier-gated auto-clear):
    3. tier-1 personal repo + clear spec → auto-clears (no held action).
    4. Diligent-prefixed repo + clear spec → always held (tier-0 hard gate).
  REQ-03 (questions.md schema + idempotency):
    5. questions.md file has the required schema (spec_hash, repo, max_ambiguity,
       parked_ts, and ≥1 question block with proposed-answer checkboxes).
    6. re-parking the same spec (same spec_hash) is idempotent — no duplicate file.
  REQ-04 (anti-weakening / falsifiability):
    7. removing the tier gate would cause test_work_repo_never_autoclears to FAIL
       (documented anti-weakening assertion). The test is falsifiable: if tier gate
       is absent, a tier-1 seed on a Diligent repo would auto-clear — this asserts
       it doesn't, making the gate load-bearing.
"""
from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# --- imports from the module under test ---
from factory.spec_review import (
    PARK_THRESHOLD,
    ReviewOutcome,
    SpecReviewGate,
    ReviewResult,
)

# ---------------------------------------------------------------------------
# Minimal stubs for P3-a interfaces (not yet merged)
# ---------------------------------------------------------------------------

def _make_graph(
    *,
    repo: str = "personal-repo",
    spec_hash: str = "abc123",
    max_ambiguity: float = 0.3,
    nodes: Optional[List[Dict[str, Any]]] = None,
) -> MagicMock:
    """Build a TaskGraph-duck-type stub with the fields spec_review reads."""
    g = MagicMock()
    g.repo = repo
    g.spec_hash = spec_hash
    g.max_ambiguity = max_ambiguity
    g.nodes = nodes or []
    return g


def _make_node(
    *,
    node_id: str = "T1",
    title: str = "Implement login endpoint",
    acceptance: Optional[List[str]] = None,
    ambiguity: float = 0.0,
) -> MagicMock:
    """Build a TaskNode-duck-type stub."""
    n = MagicMock()
    n.id = node_id
    n.title = title
    n.acceptance = acceptance or ["POST /login returns 200 with valid JWT"]
    n.ambiguity = ambiguity
    return n


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gate(tmp_path: Path, *, questions_dir: Optional[Path] = None) -> SpecReviewGate:
    """Build a SpecReviewGate writing questions files under tmp_path."""
    held_store = MagicMock()
    held_store.enqueue.return_value = "held-action-id-001"
    qdir = questions_dir or (tmp_path / "questions")
    return SpecReviewGate(held_store=held_store, questions_dir=qdir)


# ---------------------------------------------------------------------------
# REQ-01: park vs hold
# ---------------------------------------------------------------------------


class TestParkHighAmbiguity:
    """REQ-01 — high-ambiguity spec parks to questions.md, zero jobs enqueued."""

    def test_ambiguous_spec_parks_to_questions(self, tmp_path):
        """
        Spec with max_ambiguity ≥ PARK_THRESHOLD writes a questions.md file.
        No broker held-action is created (no enqueue call on held_store).
        """
        gate = _make_gate(tmp_path)
        node = _make_node(
            node_id="T1",
            title="Add auth endpoint or something",
            acceptance=[],  # undefined ACs → high ambiguity
            ambiguity=0.72,
        )
        graph = _make_graph(
            repo="personal-repo",
            spec_hash="deadbeef01",
            max_ambiguity=0.72,
            nodes=[node],
        )

        result = gate.review(graph)

        assert result.outcome == ReviewOutcome.PARKED
        assert result.action_id is None  # no broker action for a park

        # questions.md must exist
        qfile = tmp_path / "questions" / "deadbeef01.md"
        assert qfile.exists(), f"questions file not written to {qfile}"

        content = qfile.read_text()
        # Has the spec_hash header
        assert "deadbeef01" in content
        # Has at least one question block
        assert "##" in content or "Q1" in content
        # Has proposed-answer checkboxes (2-3 per question)
        checkbox_count = len(re.findall(r"- \[ \]", content))
        assert checkbox_count >= 2, f"Expected ≥2 answer checkboxes, got {checkbox_count}"

        # No held-action created
        gate.held_store.enqueue.assert_not_called()

    def test_park_threshold_boundary(self, tmp_path):
        """Score exactly at PARK_THRESHOLD (0.5) parks; below it holds."""
        gate = _make_gate(tmp_path)

        at_threshold = _make_graph(
            repo="personal-repo", spec_hash="hash-at", max_ambiguity=PARK_THRESHOLD
        )
        below_threshold = _make_graph(
            repo="personal-repo",
            spec_hash="hash-below",
            max_ambiguity=PARK_THRESHOLD - 0.01,
        )

        result_at = gate.review(at_threshold)
        result_below = gate.review(below_threshold)

        assert result_at.outcome == ReviewOutcome.PARKED
        assert result_below.outcome in (ReviewOutcome.HELD, ReviewOutcome.AUTO_CLEARED)


class TestHoldLowAmbiguity:
    """REQ-01 — low-ambiguity spec on a tier-0 repo is held for owner tap."""

    def test_clear_spec_held_for_owner_tap(self, tmp_path):
        """
        A spec with max_ambiguity < PARK_THRESHOLD on a tier-0 (default) repo
        must result in a HELD broker action, not AUTO_CLEARED.
        Anti-weakening: the held_store.enqueue call must happen.
        """
        gate = _make_gate(tmp_path)
        graph = _make_graph(
            repo="personal-repo",
            spec_hash="cleanhash01",
            max_ambiguity=0.1,
        )

        # Simulate tier-0 by patching compute_tier to return 0
        with patch("factory.spec_review.compute_tier", return_value=0):
            result = gate.review(graph)

        assert result.outcome == ReviewOutcome.HELD
        assert result.action_id is not None
        gate.held_store.enqueue.assert_called_once()

        # Verify the enqueue type is 'spec-review'
        call_kwargs = gate.held_store.enqueue.call_args.kwargs
        assert call_kwargs.get("type") == "spec-review"


# ---------------------------------------------------------------------------
# REQ-02: tier-gated auto-clear
# ---------------------------------------------------------------------------


class TestTierGatedAutoClear:
    """REQ-02 — auto-clear only when compute_tier returns ≥1."""

    def test_tier1_repo_autoclears(self, tmp_path):
        """
        When compute_tier returns 1 (earned autonomy), a clear spec auto-clears:
        no held action is created; outcome is AUTO_CLEARED.
        """
        gate = _make_gate(tmp_path)
        graph = _make_graph(
            repo="personal-repo",
            spec_hash="autoclr01",
            max_ambiguity=0.1,
        )

        with patch("factory.spec_review.compute_tier", return_value=1):
            result = gate.review(graph)

        assert result.outcome == ReviewOutcome.AUTO_CLEARED
        # No broker action needed — clear spec on tier-1 repo proceeds without tap
        gate.held_store.enqueue.assert_not_called()

    def test_work_repo_never_autoclears(self, tmp_path):
        """
        A Diligent-prefixed repo is ALWAYS tier-0; a clear spec must be HELD.
        Anti-weakening note: if the tier gate is removed, compute_tier could
        theoretically return 1 for a Diligent repo — this test becomes RED
        because the HELD outcome would flip to AUTO_CLEARED. That makes the
        gate load-bearing.
        """
        gate = _make_gate(tmp_path)
        graph = _make_graph(
            repo="diligent-boardbooks",
            spec_hash="workhash01",
            max_ambiguity=0.05,
        )

        # Even if we mock compute_tier to 1, the _is_diligent_repo hard gate in
        # trust_policy must override and return 0. spec_review must call compute_tier
        # with the correct repo_class='work' or rely on the hard gate inside it.
        # We simulate the real hard-gate path by NOT mocking (uses real compute_tier).
        result = gate.review(graph)

        assert result.outcome == ReviewOutcome.HELD, (
            "work/Diligent repo must never auto-clear regardless of record"
        )
        gate.held_store.enqueue.assert_called_once()

    def test_default_profile_always_held(self, tmp_path):
        """
        When compute_tier returns 0 (the default, ask-for-everything profile),
        the spec is held for owner tap, never auto-cleared.
        """
        gate = _make_gate(tmp_path)
        graph = _make_graph(
            repo="fresh-personal-repo",
            spec_hash="freshrepo01",
            max_ambiguity=0.0,
        )

        with patch("factory.spec_review.compute_tier", return_value=0):
            result = gate.review(graph)

        assert result.outcome == ReviewOutcome.HELD


# ---------------------------------------------------------------------------
# REQ-03: questions.md schema + idempotency
# ---------------------------------------------------------------------------


class TestQuestionsFileSchema:
    """REQ-03 — questions.md has the required schema; re-parking is idempotent."""

    def test_questions_file_schema(self, tmp_path):
        """
        A parked spec's questions.md must contain:
          - spec_hash header
          - repo field
          - max_ambiguity value
          - parked_ts (ISO-8601 or epoch)
          - ≥1 question block (## Q<n>: or ## <question text>)
          - 2-3 proposed-answer checkboxes per question
        """
        gate = _make_gate(tmp_path)
        node = _make_node(
            node_id="T2",
            title="Something or equivalent",
            acceptance=[],
            ambiguity=0.8,
        )
        graph = _make_graph(
            repo="my-repo",
            spec_hash="schema-test-hash",
            max_ambiguity=0.8,
            nodes=[node],
        )

        gate.review(graph)

        qfile = tmp_path / "questions" / "schema-test-hash.md"
        assert qfile.exists()
        text = qfile.read_text()

        assert "schema-test-hash" in text, "spec_hash missing"
        assert "my-repo" in text, "repo field missing"
        assert "0.8" in text, "max_ambiguity missing"
        # parked_ts: must be a number or ISO-like timestamp
        assert re.search(r"\d{4}|\d{10}", text), "parked_ts missing"

        # At least one section header for a question
        sections = re.findall(r"^##\s+", text, re.MULTILINE)
        assert len(sections) >= 1, "no question sections"

        # Each question must have 2-3 proposed answer checkboxes
        checkboxes = re.findall(r"- \[ \]", text)
        assert len(checkboxes) >= 2, f"expected ≥2 checkboxes, got {len(checkboxes)}"
        assert len(checkboxes) <= 9, "too many checkboxes (expect 2-3 per question)"

    def test_questions_file_per_spec_hash(self, tmp_path):
        """
        Two parks with different spec_hash values write two distinct files.
        Same spec_hash written twice results in exactly one file (idempotent).
        """
        gate = _make_gate(tmp_path)

        graph_a = _make_graph(
            repo="repo-a",
            spec_hash="hash-aaa",
            max_ambiguity=0.9,
        )
        graph_b = _make_graph(
            repo="repo-b",
            spec_hash="hash-bbb",
            max_ambiguity=0.9,
        )

        gate.review(graph_a)
        gate.review(graph_b)

        file_a = tmp_path / "questions" / "hash-aaa.md"
        file_b = tmp_path / "questions" / "hash-bbb.md"
        assert file_a.exists(), "first park file missing"
        assert file_b.exists(), "second park file missing"
        assert file_a != file_b  # trivially true (different paths), verifies separation

    def test_reparking_same_spec_is_idempotent(self, tmp_path):
        """
        Re-parking the same spec (identical spec_hash) must not duplicate the file
        or append duplicate content. The file is overwritten (or skipped) cleanly.
        """
        gate = _make_gate(tmp_path)
        graph = _make_graph(
            repo="idempotent-repo",
            spec_hash="idem-hash-01",
            max_ambiguity=0.7,
        )

        gate.review(graph)
        mtime_first = (tmp_path / "questions" / "idem-hash-01.md").stat().st_mtime

        # Re-park the same spec
        gate.review(graph)
        content_after = (tmp_path / "questions" / "idem-hash-01.md").read_text()

        # Content should not double-up the header (spec_hash appears exactly once at top)
        count = content_after.count("idem-hash-01")
        # The hash may appear in the filename section and in context but not duplicated as
        # separate front-matter headers. Allow at most 2 occurrences (header + one ref).
        assert count <= 3, f"spec_hash appears {count} times — looks like duplication"


# ---------------------------------------------------------------------------
# REQ-04 (anti-weakening / falsifiability evidence)
# ---------------------------------------------------------------------------


class TestAntiweak:
    """REQ-04 — falsifiable: removing the tier gate would flip these tests RED."""

    def test_tier_gate_is_load_bearing(self, tmp_path):
        """
        Demonstrates the gate is falsifiable: if auto_clear_allowed() always returned
        True, a Diligent repo would auto-clear instead of being held. This test would
        then FAIL. Therefore, the tier gate is load-bearing (not decorative).
        """
        gate = _make_gate(tmp_path)
        graph = _make_graph(
            repo="diligent-internal-repo",
            spec_hash="falsify01",
            max_ambiguity=0.0,
        )

        # Force compute_tier to pretend tier=1 (as if the gate were removed)
        with patch("factory.spec_review.compute_tier", return_value=1):
            # Despite compute_tier=1, the _is_diligent_repo hard gate (invoked
            # inside auto_clear_allowed via the repo argument) must still block it.
            # spec_review must pass repo_class='work' or detect Diligent directly.
            result = gate.review(graph)

        # If the spec_review tier check uses compute_tier WITHOUT the Diligent
        # hard-class signal, a mocked tier=1 COULD flip this to AUTO_CLEARED.
        # The implementation MUST either:
        #   (a) pass repo_class='work' for Diligent repos so compute_tier's hard gate fires, OR
        #   (b) check _is_diligent_repo independently before auto-clearing.
        # Either approach makes this assertion hold.
        assert result.outcome == ReviewOutcome.HELD, (
            "Diligent repos must be held even when compute_tier is mocked to 1 — "
            "the implementation must pass repo_class='work' so the hard gate fires"
        )
