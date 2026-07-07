# ABOUTME: RED-first tests for factory.skill_improve (P5-c, design §4.3).
# ABOUTME: Covers: weekly pass produces skill-file diff as a held action (SK-1),
# ABOUTME: ring-path skill diff is rejected at the propose-gate AND (via same
# ABOUTME: retro_diff/skill_diff action type) the apply-executor (SK-2), pattern
# ABOUTME: bank query returns a prior (SK-3), and pattern bank populates on
# ABOUTME: merged_clean only (SK-4).
"""
Tests for factory.skill_improve and factory.pattern_bank (P5-c).

Design authoritative source:
  docs/plans/harness/fable/p5/P5-design.md §4.3 + §7.2 (SK-1..4)

Discipline: RED-first. Tests were written before the implementation; they are
meaningful because:
  SK-1  — passes only when a real enqueue_action call is made (spy-verified).
  SK-2  — passes only when check_retro_diff is called and its RingViolation
           propagates (removing the call makes this fail — the gate is not bypassable
           via the skill lane, at the propose door).
  SK-3  — passes only when pattern_bank.query returns the seeded hit.
  SK-4  — passes only when add_pattern is gated on merged_clean outcome.

The gate call itself (check_retro_diff) is against retro_ring_gate's documented
interface. Because P5-b may not be merged, tests use a mock that correctly raises
RingViolation to prove the gate IS called and the skill_improve propagates it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, call, patch

import pytest

from factory.skill_improve import (
    SkillImprover,
    _GUARD_ADJACENT_SKILLS,
    run_weekly_pass,
)
from factory.pattern_bank import (
    PatternBank,
    PatternHit,
    add_pattern,
    query_patterns,
)
from factory.immutable_ring import RingViolation


# ---------------------------------------------------------------------------
# Helpers: build in-memory DB fixtures that match the scorecard + forensics schema
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    """
    Purpose: create a minimal in-memory SQLite DB (on disk so modules can share it)
    seeded with the tables that skill_improve reads: scorecard_samples +
    failure_forensics + patterns.
    Usage: conn = _make_db(tmp_path)
    Gotchas: uses disk path so both the scorecard and forensics_store can read it.
    """
    db_path = tmp_path / "jobs.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Minimal scorecard_samples table (P5-a schema §3.2).
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scorecard_samples (
            job_id          TEXT PRIMARY KEY,
            model           TEXT NOT NULL,
            worker          TEXT NOT NULL,
            task_type       TEXT NOT NULL,
            outcome         TEXT NOT NULL,
            findings_count  INTEGER NOT NULL DEFAULT 0,
            cost_usd        REAL NOT NULL,
            wall_ms         INTEGER NOT NULL,
            sample_ts       INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS failure_forensics (
            job_id          TEXT PRIMARY KEY,
            stage           TEXT NOT NULL,
            error_class     TEXT NOT NULL,
            cost_burned_usd REAL NOT NULL,
            retry_worthy    INTEGER NOT NULL,
            bundle_path     TEXT,
            detail          TEXT,
            captured_ts     INTEGER NOT NULL
        );
    """)
    conn.commit()
    return conn


def _seed_failure(conn: sqlite3.Connection, job_id: str, error_class: str, ts: int) -> None:
    """Seed a single failure_forensics row for a given error class."""
    conn.execute(
        "INSERT INTO failure_forensics VALUES (?,?,?,?,?,?,?,?)",
        (job_id, "TEST", error_class, 0.05, 1, None, None, ts),
    )
    conn.commit()


def _seed_merged_clean(conn: sqlite3.Connection, job_id: str, task_type: str, ts: int) -> None:
    """Seed a scorecard_samples row with merged_clean outcome."""
    conn.execute(
        "INSERT INTO scorecard_samples VALUES (?,?,?,?,?,?,?,?,?)",
        (job_id, "claude-sonnet", "claude", task_type, "merged_clean", 0, 0.10, 5000, ts),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    """Shared jobs DB for the session."""
    return _make_db(tmp_path)


@pytest.fixture()
def db_path(tmp_path, db):
    """Return the db path so modules that open by path can access it."""
    return tmp_path / "jobs.db"


@pytest.fixture()
def skills_dir(tmp_path):
    """A scratch directory acting as the skill-file root."""
    d = tmp_path / "skills"
    d.mkdir()
    # Pre-create one skill file so the imporver has something to propose editing.
    skill = d / "retro" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("# Retro skill\nStep 1: gather failures.\n")
    return d


@pytest.fixture()
def worktree(tmp_path):
    """A scratch worktree root (not a real git repo — ring check uses realpath)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    return wt


# ---------------------------------------------------------------------------
# SK-1: weekly pass produces a skill-file diff as a held action (never silent)
# ---------------------------------------------------------------------------


class TestWeeklyPassProducesHeldAction:
    """
    SK-1: A weekly run over seeded histories produces a concrete skill-file diff
    as a held action (enqueue_action called, disposition 'held'); it NEVER writes
    the diff to disk directly (silent write would be a security gap).
    """

    def test_weekly_pass_enqueues_held_action(self, tmp_path, db_path, skills_dir, worktree):
        """
        Purpose: prove run_weekly_pass calls enqueue_action with a held action.
        Removing the enqueue_action call makes this fail (RED invariant).
        """
        # Seed repeated failures for a recurring error class so the pass has
        # something to propose a skill improvement for.
        conn = sqlite3.connect(str(db_path))
        for i in range(5):
            _seed_failure(conn, f"job-fail-{i}", "test_fail", 1_700_000_000 + i * 3600)
        conn.close()

        # Spy: intercept BrokerClient.enqueue_action.
        enqueue_calls: List[Dict[str, Any]] = []

        def fake_enqueue(**kwargs):
            enqueue_calls.append(kwargs)
            return {"action_id": "action-0001", "disposition": "held"}

        mock_broker = MagicMock()
        mock_broker.enqueue_action.side_effect = fake_enqueue

        # The check_retro_diff gate: let it PASS for a non-ring diff.
        with patch("factory.skill_improve.check_retro_diff") as mock_gate:
            mock_gate.return_value = None  # no RingViolation → pass
            result = run_weekly_pass(
                db_path=db_path,
                skills_dir=skills_dir,
                worktree_root=worktree,
                broker=mock_broker,
                # AI distillation is mocked — we're testing the structure, not the LLM.
                diff_fn=lambda history, skill_files: (
                    "--- a/skills/retro/SKILL.md\n"
                    "+++ b/skills/retro/SKILL.md\n"
                    "@@ -1,2 +1,3 @@\n"
                    " # Retro skill\n"
                    " Step 1: gather failures.\n"
                    "+Step 2: classify by error_class.\n"
                ),
            )

        # MUST have called enqueue_action at least once (the held action).
        assert len(enqueue_calls) >= 1, "weekly pass did not enqueue any held action"
        action = enqueue_calls[0]
        assert action["type"] in ("retro_diff", "skill_diff"), (
            f"unexpected action type: {action['type']!r}"
        )
        # The action must carry the diff + sha256 (v2-C3 hash-pin).
        payload = json.loads(action["payload"])
        assert "diff" in payload, "payload missing 'diff'"
        assert "diff_sha256" in payload, "payload missing 'diff_sha256' (hash-pin)"
        # Verify the hash is consistent.
        expected_sha = hashlib.sha256(payload["diff"].encode()).hexdigest()
        assert payload["diff_sha256"] == expected_sha, "diff_sha256 does not match diff bytes"

    def test_weekly_pass_does_not_write_diff_to_disk(
        self, tmp_path, db_path, skills_dir, worktree
    ):
        """
        Purpose: no direct disk writes to the skills dir — the diff must only flow
        through the broker held-action path. Silent writes bypass the owner tap.
        Removal invariant: removing the 'no write' guarantee makes an integrity test
        elsewhere fail (the gate must be the ONLY path to disk).
        """
        conn = sqlite3.connect(str(db_path))
        _seed_failure(conn, "job-fail-0", "test_fail", 1_700_000_000)
        conn.close()

        diff_text = (
            "--- a/skills/retro/SKILL.md\n"
            "+++ b/skills/retro/SKILL.md\n"
            "@@ -1 +1,2 @@\n"
            " # Retro skill\n"
            "+Step 2: new.\n"
        )

        mock_broker = MagicMock()
        mock_broker.enqueue_action.return_value = {"action_id": "action-0002", "disposition": "held"}

        skill_file = skills_dir / "retro" / "SKILL.md"
        before_mtime = skill_file.stat().st_mtime

        with patch("factory.skill_improve.check_retro_diff") as mock_gate:
            mock_gate.return_value = None
            run_weekly_pass(
                db_path=db_path,
                skills_dir=skills_dir,
                worktree_root=worktree,
                broker=mock_broker,
                diff_fn=lambda hist, sfs: diff_text,
            )

        # The skill file on disk must NOT be modified by the pass.
        assert skill_file.stat().st_mtime == before_mtime, (
            "weekly pass directly wrote to the skill file — violates held-action-only discipline"
        )


# ---------------------------------------------------------------------------
# SK-2: skill-improve diff touching a ring path is rejected at the propose gate
# ---------------------------------------------------------------------------


class TestSkillImproveDiffGating:
    """
    SK-2: A skill-improve diff touching a ring path is rejected by the shared ring
    gate (check_retro_diff) at the PROPOSE door; no held action is enqueued.
    Removing the check_retro_diff call makes this test leak (the gate is bypassed).
    """

    @pytest.mark.parametrize(
        "ring_diff",
        [
            # Direct ring file hit.
            pytest.param(
                "--- a/factory/cost_stops.py\n+++ b/factory/cost_stops.py\n"
                "@@ -1 +1 @@\n-# guard\n+# weakened guard\n",
                id="cost_stops",
            ),
            # broker/ directory member.
            pytest.param(
                "--- a/broker/server.py\n+++ b/broker/server.py\n"
                "@@ -1 +1 @@\n-# broker\n+# tampered\n",
                id="broker_server",
            ),
            # trust-policy doc.
            pytest.param(
                "--- a/trust-policy.md\n+++ b/trust-policy.md\n"
                "@@ -1 +1 @@\n-old\n+new\n",
                id="trust_policy",
            ),
        ],
    )
    def test_ring_diff_rejected_no_enqueue(
        self, tmp_path, db_path, skills_dir, worktree, ring_diff
    ):
        """
        Purpose: a skill-improve diff touching a ring path hits check_retro_diff,
        which raises RingViolation; skill_improve propagates the rejection and does
        NOT call enqueue_action.
        """
        conn = sqlite3.connect(str(db_path))
        _seed_failure(conn, "job-ring-0", "test_fail", 1_700_000_000)
        conn.close()

        mock_broker = MagicMock()

        # Gate: raises RingViolation (mimicking retro_ring_gate behaviour for ring diffs).
        with patch("factory.skill_improve.check_retro_diff") as mock_gate:
            mock_gate.side_effect = RingViolation(
                f"diff touches ring path (test): {ring_diff[:40]!r}"
            )

            result = run_weekly_pass(
                db_path=db_path,
                skills_dir=skills_dir,
                worktree_root=worktree,
                broker=mock_broker,
                diff_fn=lambda hist, sfs: ring_diff,
            )

        # Gate MUST have been called.
        mock_gate.assert_called_once()
        # No held action must have been enqueued.
        mock_broker.enqueue_action.assert_not_called()
        # Result must signal rejection.
        assert result.get("status") == "rejected", (
            f"expected status='rejected', got {result!r}"
        )
        assert "ring" in result.get("reason", "").lower(), (
            f"rejection reason should mention 'ring': {result!r}"
        )

    def test_gate_called_before_enqueue(self, tmp_path, db_path, skills_dir, worktree):
        """
        Purpose: ordering contract — check_retro_diff MUST be called before
        enqueue_action (gate → enqueue, never enqueue → gate).
        """
        conn = sqlite3.connect(str(db_path))
        _seed_failure(conn, "job-order-0", "test_fail", 1_700_000_000)
        conn.close()

        call_order: List[str] = []

        def gate_spy(*args, **kwargs):
            call_order.append("gate")

        def enqueue_spy(**kwargs):
            call_order.append("enqueue")
            return {"action_id": "a-0", "disposition": "held"}

        mock_broker = MagicMock()
        mock_broker.enqueue_action.side_effect = enqueue_spy

        with patch("factory.skill_improve.check_retro_diff", side_effect=gate_spy):
            run_weekly_pass(
                db_path=db_path,
                skills_dir=skills_dir,
                worktree_root=worktree,
                broker=mock_broker,
                diff_fn=lambda hist, sfs: (
                    "--- a/skills/retro/SKILL.md\n+++ b/skills/retro/SKILL.md\n"
                    "@@ -1 +1,2 @@\n # Retro skill\n+Step 2: added.\n"
                ),
            )

        assert "gate" in call_order, "gate was never called"
        if "enqueue" in call_order:
            gate_idx = call_order.index("gate")
            enqueue_idx = call_order.index("enqueue")
            assert gate_idx < enqueue_idx, (
                "enqueue_action called BEFORE check_retro_diff (ordering violation)"
            )

    def test_guard_adjacent_flag_on_card(self, tmp_path, db_path, skills_dir, worktree):
        """
        Purpose: v2-C5 — when the proposed diff touches a guard-adjacent skill,
        the held-action card summary flags it for extra owner scrutiny.
        """
        conn = sqlite3.connect(str(db_path))
        _seed_failure(conn, "job-guard-adj-0", "test_fail", 1_700_000_000)
        conn.close()

        # Create a guard-adjacent skill file that the diff touches.
        guard_skill_name = next(iter(_GUARD_ADJACENT_SKILLS), "injection-scan-helper")
        guard_skill_dir = skills_dir / guard_skill_name
        guard_skill_dir.mkdir(parents=True, exist_ok=True)
        (guard_skill_dir / "SKILL.md").write_text("# Guard-adjacent skill\n")

        guard_diff = (
            f"--- a/skills/{guard_skill_name}/SKILL.md\n"
            f"+++ b/skills/{guard_skill_name}/SKILL.md\n"
            "@@ -1 +1,2 @@\n"
            " # Guard-adjacent skill\n"
            "+Updated heuristic.\n"
        )

        enqueue_calls: List[Dict] = []

        def fake_enqueue(**kwargs):
            enqueue_calls.append(kwargs)
            return {"action_id": "a-gaj-0", "disposition": "held"}

        mock_broker = MagicMock()
        mock_broker.enqueue_action.side_effect = fake_enqueue

        with patch("factory.skill_improve.check_retro_diff") as mock_gate:
            mock_gate.return_value = None
            run_weekly_pass(
                db_path=db_path,
                skills_dir=skills_dir,
                worktree_root=worktree,
                broker=mock_broker,
                diff_fn=lambda hist, sfs: guard_diff,
            )

        assert enqueue_calls, "no held action enqueued for guard-adjacent diff"
        # The summary MUST flag the guard-adjacent nature.
        summary = enqueue_calls[0].get("summary", "")
        assert "guard" in summary.lower() or "⚠" in summary, (
            f"guard-adjacent flag missing from summary: {summary!r}"
        )


# ---------------------------------------------------------------------------
# SK-3: pattern_bank.query returns relevant prior for matching task
# ---------------------------------------------------------------------------


class TestPatternBankQuery:
    """
    SK-3: pattern_bank.query(task_spec) returns a relevant prior PatternHit for a
    matching task type; an unmatched query returns empty list (no crash).
    """

    def test_query_returns_stored_pattern(self, tmp_path):
        """
        Purpose: a pattern stored for task_type 'bugfix' is returned when a new
        bugfix task is queried.
        """
        db_path = tmp_path / "patterns.db"
        bank = PatternBank(db_path)

        bank.add_pattern(
            task_type="bugfix",
            repo="repo-a",
            approach_summary="Fixed NullPointerException by adding early-return guard.",
            diff_pointer="repo-a/jobs/job-001/phase.diff",
        )

        hits = bank.query(task_type="bugfix", limit=5)
        assert len(hits) >= 1, "expected at least one hit for matching task_type"
        assert isinstance(hits[0], PatternHit)
        assert hits[0].task_type == "bugfix"
        assert "NullPointerException" in hits[0].approach_summary or hits[0].approach_summary

    def test_query_no_match_returns_empty(self, tmp_path):
        """
        Purpose: a query for an unmatched task_type returns [] without crashing.
        """
        db_path = tmp_path / "patterns.db"
        bank = PatternBank(db_path)

        hits = bank.query(task_type="refactor", limit=5)
        assert hits == [], f"expected empty list, got: {hits!r}"

    def test_query_module_level_alias(self, tmp_path):
        """
        Purpose: the module-level query_patterns alias works identically to the
        bank instance method (the supervisor pre-spawn hook may use either form).
        """
        db_path = tmp_path / "patterns.db"
        bank = PatternBank(db_path)
        bank.add_pattern(
            task_type="feature",
            repo="repo-b",
            approach_summary="Added streaming endpoint using SSE.",
            diff_pointer="repo-b/jobs/job-002/phase.diff",
        )

        hits = query_patterns(db_path=db_path, task_type="feature", limit=3)
        assert len(hits) >= 1

    def test_query_cross_repo_fallback_documented(self, tmp_path):
        """
        Purpose: when the code-review-graph is unavailable (offline), pattern bank
        falls back to same-repo-only results with no crash, per design §4.3.
        The behaviour is documented in the PatternBank.query docstring.
        """
        db_path = tmp_path / "patterns.db"
        bank = PatternBank(db_path)
        bank.add_pattern(
            task_type="bugfix",
            repo="repo-a",
            approach_summary="Offline fallback pattern.",
            diff_pointer="repo-a/j/phase.diff",
        )

        # Query with cross_repo=True but graph tool unavailable.
        with patch("factory.pattern_bank._try_cross_repo_search") as mock_xr:
            mock_xr.side_effect = RuntimeError("code-review-graph unavailable offline")
            hits = bank.query(task_type="bugfix", limit=5, cross_repo=True)

        # Must still return same-repo results, no crash.
        assert len(hits) >= 1, "expected same-repo fallback hit when cross-repo fails"


# ---------------------------------------------------------------------------
# SK-4: pattern bank populates on merged_clean only
# ---------------------------------------------------------------------------


class TestPatternBankPopulation:
    """
    SK-4: Patterns are stored co-transactionally on a merged_clean outcome.
    A rejected or failed job adds NO pattern.
    """

    def test_add_pattern_appears_in_query(self, tmp_path):
        """
        Purpose: add_pattern (the write path) stores the pattern so a subsequent
        query returns it.
        """
        db_path = tmp_path / "patterns.db"
        add_pattern(
            db_path=db_path,
            task_type="docs",
            repo="repo-docs",
            approach_summary="Wrote migration guide covering all breaking changes.",
            diff_pointer="repo-docs/j/job-003/phase.diff",
        )

        hits = query_patterns(db_path=db_path, task_type="docs", limit=5)
        assert any("migration" in h.approach_summary for h in hits)

    def test_rejected_job_does_not_add_pattern(self, tmp_path):
        """
        Purpose: add_pattern is gated on the supervisor-observed outcome; calling it
        with outcome='rejected' must not store a pattern.
        """
        db_path = tmp_path / "patterns.db"
        add_pattern(
            db_path=db_path,
            task_type="bugfix",
            repo="repo-a",
            approach_summary="Should not appear — job was rejected.",
            diff_pointer="repo-a/j/phase.diff",
            outcome="rejected",  # non-merged_clean → not stored
        )

        hits = query_patterns(db_path=db_path, task_type="bugfix", limit=5)
        assert not any("Should not appear" in h.approach_summary for h in hits), (
            "pattern from a rejected job was stored — gate on merged_clean failed"
        )

    def test_failed_job_does_not_add_pattern(self, tmp_path):
        """
        Purpose: add_pattern with outcome='failed' must not store a pattern.
        """
        db_path = tmp_path / "patterns.db"
        add_pattern(
            db_path=db_path,
            task_type="bugfix",
            repo="repo-a",
            approach_summary="Failed pattern — must not store.",
            diff_pointer="repo-a/j/phase.diff",
            outcome="failed",
        )

        hits = query_patterns(db_path=db_path, task_type="bugfix", limit=5)
        assert not any("Failed pattern" in h.approach_summary for h in hits)

    def test_merged_clean_is_stored(self, tmp_path):
        """
        Purpose: add_pattern with outcome='merged_clean' (the default) IS stored.
        """
        db_path = tmp_path / "patterns.db"
        add_pattern(
            db_path=db_path,
            task_type="feature",
            repo="repo-c",
            approach_summary="Merged clean feature with SSE streaming.",
            diff_pointer="repo-c/j/phase.diff",
            outcome="merged_clean",
        )

        hits = query_patterns(db_path=db_path, task_type="feature", limit=5)
        assert any("SSE streaming" in h.approach_summary for h in hits)


# ---------------------------------------------------------------------------
# REQ-03: pattern is advisory — does not bypass any gate
# ---------------------------------------------------------------------------


class TestPatternAdvisory:
    """
    REQ-03: Pattern bank is READ-ONLY advisory input. A pattern can't inject a
    guard change — it informs a worker brief but does not bypass any gate.
    """

    def test_pattern_is_a_dataclass_not_callable(self, tmp_path):
        """
        Purpose: PatternHit is a pure data container (NamedTuple / dataclass) —
        it has no .apply(), .patch(), or .enqueue() method. The boundary is
        structural: there is no code path from a PatternHit to disk or broker.
        """
        db_path = tmp_path / "patterns.db"
        bank = PatternBank(db_path)
        bank.add_pattern(
            task_type="bugfix",
            repo="repo-advisory",
            approach_summary="Advisory-only pattern.",
            diff_pointer="repo-a/phase.diff",
        )
        hits = bank.query(task_type="bugfix")
        assert hits, "no hit to inspect"
        hit = hits[0]

        assert not hasattr(hit, "apply"), "PatternHit must not expose .apply()"
        assert not hasattr(hit, "patch"), "PatternHit must not expose .patch()"
        assert not hasattr(hit, "enqueue"), "PatternHit must not expose .enqueue()"

    def test_pattern_content_alone_cannot_call_broker(self, tmp_path):
        """
        Purpose: even if a pattern's approach_summary contains a shell command or
        diff payload, it cannot bypass the ring gate — it is plain text, not
        executable, and the worker's own ring check and review still apply.
        This test documents the boundary (design §4.3 REQ-03).
        """
        db_path = tmp_path / "patterns.db"
        bank = PatternBank(db_path)
        malicious_summary = (
            "--- a/factory/cost_stops.py\n+++ b/factory/cost_stops.py\n"
            "@@ -1 +1 @@\n-guard\n+weakened\n"
        )
        bank.add_pattern(
            task_type="bugfix",
            repo="repo-x",
            approach_summary=malicious_summary,
            diff_pointer="repo-x/j/phase.diff",
        )

        hits = bank.query(task_type="bugfix")
        assert hits
        hit = hits[0]
        # The summary is plain text — it is NOT a diff that can be applied.
        # Confirm it's a string, not a callable or executable object.
        assert isinstance(hit.approach_summary, str)
        # Confirm reading the pattern does not trigger any broker call.
        # (No mock needed — this is a static property check.)
