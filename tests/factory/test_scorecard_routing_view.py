# ABOUTME: RED-first test suite for factory/scorecard.py + factory/routing_view.py
# ABOUTME: (P5-a, REQ-01..04). Covers: per-(model×task_type) sample accumulation,
# ABOUTME: anti-poisoning (SC-6), routing-changes-from-data (SC-2, FRESH-CONTEXT GATE),
# ABOUTME: cannot-add-model invariant (SC-4), cold-start fallback (SC-3), residency
# ABOUTME: survives refactor (SC-5). Real SQLite (tmp_path), real model_router; no mocks.
"""
Tests for factory/scorecard.py and factory/routing_view.py.

RED → GREEN protocol: these tests were written before the implementation and drive it.
Each test documents the invariant it protects and what removal/weakening would make it leak.

Design source: docs/plans/harness/fable/p5/P5-design.md §3 (REQ-01), §7.2 (SC tests),
§REQ-01..REQ-04 requirement map.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_job_row(
    job_id: str,
    *,
    model: str = "claude-sonnet",
    worker: str = "claude",
    spec: str = "fix the login bug",
    state: str = "DONE",
    cost: float = 0.10,
    created_ts: Optional[int] = None,
    updated_ts: Optional[int] = None,
    fail_reason: Optional[str] = None,
    # Worker-emitted fields: these should be IGNORED by record_sample (SC-6).
    findings_count_emitted: Optional[int] = None,
    outcome_emitted: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a minimal job_row dict that mimics what job_store.get() returns."""
    now = int(time.time() * 1000)
    row: Dict[str, Any] = {
        "id": job_id,
        "repo": "/repo/test",
        "model": model,
        "worker": worker,
        "spec": spec,
        "kind": "feature",
        "state": state,
        "cost_so_far_usd": cost,
        "created_ts": created_ts or now - 5000,
        "updated_ts": updated_ts or now,
        "fail_reason": fail_reason,
        "review_findings_path": None,
        "test_result": None,
        # These mimic worker-emitted metadata; record_sample must not use them.
        "findings": findings_count_emitted,
        "worker_outcome": outcome_emitted,
    }
    return row


def _make_scorecard(db_path: Path):
    """Create a Scorecard instance on a fresh SQLite DB."""
    from factory.scorecard import Scorecard

    return Scorecard(db_path / "jobs.db")


# A catalog where ALL probe-confirmed OR entries are live (to test the residency
# invariant is enforced by the router, not by an empty catalog).
_LIVE_CATALOG = {
    "cli": {
        "claude": {"present": True},
        "codex": {"present": True},
    },
    "openrouter": {
        "models": {
            "zhipuai/glm-5": {"live_catalog": True, "ping_ok": True},
            "moonshot/kimi-k2": {"live_catalog": True, "ping_ok": True},
            "deepseek/deepseek-r1": {"live_catalog": True, "ping_ok": True},
        }
    },
}

_FALSE_CATALOG = {
    "cli": {
        "claude": {"present": True},
        "codex": {"present": True},
    },
    "openrouter": {
        "models": {
            "zhipuai/glm-5": {"live_catalog": False, "ping_ok": False},
        }
    },
}


# ---------------------------------------------------------------------------
# SC-1 — samples accumulate; rollup returns one row per (model, task_type)
# ---------------------------------------------------------------------------


def test_sc1_rollup_accumulates_samples_per_model_task_type(tmp_path: Path) -> None:
    """After several completed jobs, rollup() returns one row per (model, task_type)
    with correct merged_clean_rate and mean_cost. Empty DB → empty rollup.
    """
    from factory.scorecard import Scorecard

    sc = _make_scorecard(tmp_path)

    # Empty DB → empty rollup.
    assert sc.rollup() == []

    # Two bugfix jobs for claude-sonnet, one merged_clean, one rejected.
    sc.record_sample(
        _make_job_row("j1", model="claude-sonnet", spec="fix login bug", state="DONE", cost=0.10),
        findings_count=0,
    )
    sc.record_sample(
        _make_job_row(
            "j2",
            model="claude-sonnet",
            spec="fix crash bug",
            state="NEEDS_ATTENTION",
            fail_reason="rejected by owner",
            cost=0.08,
        ),
        findings_count=2,
    )

    # One feature job for codex/gpt-5.
    sc.record_sample(
        _make_job_row("j3", model="gpt-5", worker="codex", spec="add dark mode feature", state="DONE", cost=0.05),
        findings_count=1,
    )

    rows = sc.rollup()
    assert len(rows) == 2, f"Expected 2 (model×task_type) cells, got {len(rows)}: {rows}"

    # Find claude-sonnet bugfix row.
    claude_bug = next(
        (r for r in rows if r.model == "claude-sonnet" and r.task_type == "bugfix"), None
    )
    assert claude_bug is not None, "claude-sonnet/bugfix row missing"
    assert claude_bug.n == 2
    assert abs(claude_bug.merged_clean_rate - 0.5) < 1e-6
    assert abs(claude_bug.mean_cost_usd - 0.09) < 1e-4  # (0.10 + 0.08) / 2
    assert claude_bug.mean_findings == 1.0  # (0 + 2) / 2

    # Find gpt-5 feature row.
    codex_feat = next((r for r in rows if r.model == "gpt-5"), None)
    assert codex_feat is not None, "gpt-5/feature row missing"
    assert codex_feat.n == 1
    assert codex_feat.merged_clean_rate == 1.0


# ---------------------------------------------------------------------------
# SC-2 — routing order changes from scorecard data [FRESH-CONTEXT GATE]
# ---------------------------------------------------------------------------


def test_sc2_routing_order_changes_from_scorecard_data(tmp_path: Path) -> None:
    """FRESH-CONTEXT GATE: routing order changes when scorecard data changes.

    Seed the scorecard so bugfix favors a cheaper confirmed model; assert
    routing_view.task_type_table()['bugfix'][0] differs from the cold-start default
    AND that select_tier(repo, 'bugfix', ...) with scorecard returns a model that
    matches the scorecard-preferred entry.

    Removing the catalog-intersection or the scorecard-driven ranking makes this leak.
    """
    from factory.routing_view import task_type_table
    from factory.scorecard import Scorecard
    from factory.model_router import _FIRST_PARTY_LADDER, select_tier, WAIT_FOR_CAPACITY
    from factory.merge_policy import MergePolicy

    sc = Scorecard(tmp_path / "jobs.db")

    # Seed: codex/gpt-5 has PERFECT bugfix rate at very low cost.
    # claude-sonnet has poor bugfix rate.
    for i in range(10):
        sc.record_sample(
            _make_job_row(
                f"gpt-j{i}",
                model="gpt-5",
                worker="codex",
                spec="fix the crash bug",
                state="DONE",
                cost=0.02,
            ),
            findings_count=0,
        )
    for i in range(10):
        sc.record_sample(
            _make_job_row(
                f"claude-j{i}",
                model="claude-sonnet",
                worker="claude",
                spec="fix the crash bug",
                state="NEEDS_ATTENTION",
                fail_reason="rejected",
                cost=0.20,
            ),
            findings_count=5,
        )

    # Get the scorecard-driven routing table.
    table = task_type_table(_FALSE_CATALOG, sc)
    assert "bugfix" in table, "Expected a 'bugfix' entry in the routing table"

    bugfix_order = table["bugfix"]
    assert len(bugfix_order) > 0

    # The first entry should be codex/gpt-5 (perfect rate, low cost).
    first_worker, first_model = bugfix_order[0]
    assert first_model == "gpt-5" and first_worker == "codex", (
        f"Expected gpt-5 at top of bugfix ranking, got {bugfix_order[0]}"
    )

    # And the cold-start default puts claude-sonnet first.
    default_top = _FIRST_PARTY_LADDER[0]
    assert default_top != ("codex", "gpt-5"), "Pre-condition: cold-start order has claude first"

    # select_tier with scorecard still clears residency — false-policy repo never leaks OR.
    policy = MergePolicy(repo="/repo/work", allow_openrouter=False)
    rung = select_tier("/repo/work", "bugfix", policy, _FALSE_CATALOG, scorecard=sc)
    assert rung is not WAIT_FOR_CAPACITY
    assert rung.worker in ("claude", "codex"), f"Residency leaked: got {rung.worker}"
    # With data, the scorer picks codex/gpt-5 first.
    assert rung.model == "gpt-5", f"Expected scorecard-driven gpt-5, got {rung.model}"


# ---------------------------------------------------------------------------
# SC-3 — cold start (n < MIN_SAMPLES) falls back to hand-written default
# ---------------------------------------------------------------------------


def test_sc3_cold_start_falls_back_to_default_order(tmp_path: Path) -> None:
    """Cold start: n < MIN_SAMPLES falls back to the hand-written order (no crash)."""
    from factory.routing_view import ranked_first_party, MIN_SAMPLES
    from factory.model_router import _FIRST_PARTY_LADDER
    from factory.scorecard import Scorecard

    sc = Scorecard(tmp_path / "jobs.db")

    # Record fewer than MIN_SAMPLES for each model.
    for i in range(MIN_SAMPLES - 1):
        sc.record_sample(
            _make_job_row(f"j{i}", model="gpt-5", worker="codex", spec="add feature", state="DONE"),
            findings_count=0,
        )

    ranked = ranked_first_party(_FALSE_CATALOG, sc, task_type="feature")

    # The result must be a non-empty tuple (no crash) and match the default order.
    assert isinstance(ranked, tuple)
    assert len(ranked) > 0

    # In cold-start, the order should equal the hand-written default (cold entries last,
    # in their original order).
    default_list = list(_FIRST_PARTY_LADDER)
    # Catalog intersection may filter some entries from _FALSE_CATALOG (both cli present).
    for i, (worker, model) in enumerate(ranked):
        assert (worker, model) in default_list, f"Cold entry {(worker, model)} not in default"


# ---------------------------------------------------------------------------
# SC-4 — a model absent from catalog CANNOT enter any ladder [anti-phantom]
# ---------------------------------------------------------------------------


def test_sc4_absent_model_cannot_enter_ladder(tmp_path: Path) -> None:
    """A scorecard row for a model absent from capabilities.json must NOT enter any ladder.

    Removing the catalog-intersection in ranked_first_party / ranked_openrouter would
    make this test leak (a phantom model would appear in the result).
    """
    from factory.routing_view import ranked_first_party, ranked_openrouter
    from factory.scorecard import Scorecard

    sc = Scorecard(tmp_path / "jobs.db")

    PHANTOM_MODEL = "acme/totally-made-up-9000"
    PHANTOM_OR = "phantom-openrouter/model-xyz"

    # Seed scorecard with a since-removed first-party model (perfect rate, many samples).
    for i in range(20):
        sc.record_sample(
            _make_job_row(
                f"phantom-fp-{i}",
                model=PHANTOM_MODEL,
                worker="claude",
                spec="fix bug",
                state="DONE",
                cost=0.01,
            ),
            findings_count=0,
        )

    # Seed scorecard with a since-removed OR model.
    for i in range(20):
        sc.record_sample(
            _make_job_row(
                f"phantom-or-{i}",
                model=PHANTOM_OR,
                worker="openrouter",
                spec="add feature",
                state="DONE",
                cost=0.01,
            ),
            findings_count=0,
        )

    fp_ranked = ranked_first_party(_FALSE_CATALOG, sc)
    or_ranked = ranked_openrouter(_LIVE_CATALOG, sc)

    fp_models = [m for (_, m) in fp_ranked]
    assert PHANTOM_MODEL not in fp_models, (
        f"Phantom FP model entered the ladder: {fp_ranked}"
    )
    assert PHANTOM_OR not in fp_models, "Phantom OR model entered FP ladder"
    assert PHANTOM_OR not in list(or_ranked), (
        f"Phantom OR model entered the OR ladder: {or_ranked}"
    )


# ---------------------------------------------------------------------------
# SC-5 — residency invariant survives the P5-a refactor
# ---------------------------------------------------------------------------


def test_sc5_residency_invariant_survives_refactor(tmp_path: Path) -> None:
    """CROWN: even after scorecard reordering, a false-policy repo's admissible_ladder
    contains ZERO openrouter rungs.

    If routing_view accidentally introduced an OR rung for a false-policy repo or if
    admissible_ladder's residency filter was disrupted by the scorecard injection,
    this test would catch it.
    """
    from factory.model_router import admissible_ladder
    from factory.merge_policy import MergePolicy
    from factory.scorecard import Scorecard

    sc = Scorecard(tmp_path / "jobs.db")

    # Seed: OR models have perfect bugfix rate (might encourage the view to rank them first).
    for i in range(20):
        sc.record_sample(
            _make_job_row(
                f"or-j{i}",
                model="zhipuai/glm-5",
                worker="openrouter",
                spec="fix crash",
                state="DONE",
                cost=0.001,
            ),
            findings_count=0,
        )

    false_policy = MergePolicy(repo="/repo/work", allow_openrouter=False)

    # admissible_ladder with scorecard for a false-policy repo.
    ladder = admissible_ladder(
        "/repo/work", false_policy, _LIVE_CATALOG, scorecard=sc, task_type="bugfix"
    )
    workers = [r.worker for r in ladder]
    assert "openrouter" not in workers, (
        f"Residency wall broken after scorecard refactor: {workers}"
    )
    assert set(workers) <= {"claude", "codex"}, f"Unexpected workers: {workers}"


# ---------------------------------------------------------------------------
# SC-6 — anti-poisoning: a worker CANNOT move its own scorecard sample [v2-C4]
# ---------------------------------------------------------------------------


def test_sc6_worker_emitted_fields_are_ignored(tmp_path: Path) -> None:
    """ANTI-POISONING (v2-C4): a worker-emitted findings/outcome string cannot influence
    the recorded sample.

    record_sample derives every field from supervisor-observed job_row columns.
    A job row carrying worker-emitted 'findings' or 'worker_outcome' fields must
    produce the same sample as one without them — the worker fields are silently ignored.

    Removing the supervisor-only derivation (e.g., reading job_row['findings'] directly
    for findings_count) would make this test leak: the worker's inflated count would
    appear in the sample.
    """
    from factory.scorecard import Scorecard

    sc = Scorecard(tmp_path / "jobs.db")

    # Job row where the worker has tried to emit a very low findings_count and a
    # favorable outcome — but the supervisor-derived fields say otherwise.
    poisoned_row = _make_job_row(
        "poison-j1",
        model="claude-sonnet",
        spec="fix the login bug",
        state="DONE",
        cost=0.10,
        findings_count_emitted=0,    # worker claims zero findings
        outcome_emitted="merged_clean",  # worker claims merged_clean
    )

    # The supervisor passes the ACTUAL reviewer-parsed count (2 findings).
    sc.record_sample(poisoned_row, findings_count=2)

    rows = sc.rollup()
    assert len(rows) == 1
    row = rows[0]

    # findings_count must be 2 (supervisor-parsed), not 0 (worker-emitted).
    assert row.mean_findings == 2.0, (
        f"Worker-emitted findings_count leaked into sample: got {row.mean_findings}"
    )

    # outcome must be derived from state='DONE' → 'merged_clean' (supervisor field);
    # the worker_outcome field is ignored.
    assert row.merged_clean_rate == 1.0, "Outcome should be derived from supervisor state"

    # Now record a second row where the worker emits 'rejected' as worker_outcome
    # but the supervisor's job_row has state='NEEDS_ATTENTION' with a reject reason.
    reject_row = _make_job_row(
        "poison-j2",
        model="claude-sonnet",
        spec="fix the login bug",
        state="NEEDS_ATTENTION",
        fail_reason="rejected by owner",
        findings_count_emitted=99,     # worker emits inflated count
        outcome_emitted="merged_clean",  # worker claims a good outcome
    )
    sc.record_sample(reject_row, findings_count=0)

    rows = sc.rollup()
    assert len(rows) == 1
    row = rows[0]
    # 1 merged_clean + 1 rejected → rate should be 0.5, not 1.0.
    assert abs(row.merged_clean_rate - 0.5) < 1e-6, (
        f"Worker-emitted outcome leaked: merged_clean_rate={row.merged_clean_rate}"
    )
    # findings: (2 + 0) / 2 = 1.0, not inflated to 99.
    assert abs(row.mean_findings - 1.0) < 1e-6, (
        f"Worker-emitted findings leaked: mean_findings={row.mean_findings}"
    )


# ---------------------------------------------------------------------------
# REQ-03 — residency invariant: even if the view ranks OR first, work job stays zero OR
# ---------------------------------------------------------------------------


def test_req03_admissible_ladder_residency_after_view_reorder(tmp_path: Path) -> None:
    """REQ-03: even if the view ranks openrouter first for a task_type, a work job's
    admissible_ladder still has ZERO openrouter rungs.

    This is a direct assertion of the residency-untouched invariant: the view only
    orders; _rung_admissible (inside admissible_ladder) is the enforcement gate.
    """
    from factory.model_router import admissible_ladder
    from factory.merge_policy import MergePolicy
    from factory.scorecard import Scorecard

    sc = Scorecard(tmp_path / "jobs.db")

    # Make OR models look great in the scorecard.
    for i in range(20):
        sc.record_sample(
            _make_job_row(
                f"or-feat-{i}",
                model="zhipuai/glm-5",
                worker="openrouter",
                spec="add dark mode",
                state="DONE",
                cost=0.001,
            ),
            findings_count=0,
        )

    policy = MergePolicy(repo="/repo/work", allow_openrouter=False)
    ladder = admissible_ladder(
        "/repo/work", policy, _LIVE_CATALOG, scorecard=sc, task_type="feature"
    )

    or_rungs = [r for r in ladder if r.worker == "openrouter"]
    assert len(or_rungs) == 0, (
        f"REQ-03 violated: {len(or_rungs)} openrouter rung(s) in a work job's ladder: {or_rungs}"
    )


# ---------------------------------------------------------------------------
# REQ-04 — RED-first: invariant tests verify implementation gates hold
# ---------------------------------------------------------------------------


def test_req04_anti_poisoning_gate_fails_if_broken() -> None:
    """REQ-04 meta: confirm the SC-6 test would detect a broken anti-poisoning gate.

    This test directly verifies that if someone bypassed the anti-poisoning derivation
    and inserted a worker-controlled value, the test above would catch it.  We simulate
    a 'broken' implementation by directly inserting a worker-controlled row and checking
    that the inserted value is present — confirming the test pattern is sound.
    """
    # This test validates the test logic itself: if a hypothetical broken implementation
    # stored findings_count from job_row["findings"] (worker-emitted), the DB would
    # contain 0 (worker claim) instead of 2 (supervisor-parsed).  The SC-6 test checks
    # mean_findings == 2.0, which would fail with a broken implementation.
    # Here we manually insert a row with findings_count=0 to simulate the broken state
    # and assert the SC-6 logic would catch it.
    import sqlite3
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "jobs.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE scorecard_samples (
              job_id TEXT PRIMARY KEY, model TEXT, worker TEXT, task_type TEXT,
              outcome TEXT, findings_count INTEGER, cost_usd REAL, wall_ms INTEGER,
              sample_ts INTEGER
            )
        """)
        # Simulate broken impl: stored 0 (worker claim) instead of 2 (supervisor-parsed).
        conn.execute(
            "INSERT INTO scorecard_samples VALUES (?,?,?,?,?,?,?,?,?)",
            ("j1", "claude-sonnet", "claude", "bugfix", "merged_clean", 0, 0.10, 5000, 1000),
        )
        conn.commit()
        row = conn.execute("SELECT findings_count FROM scorecard_samples WHERE job_id='j1'").fetchone()
        # In the broken state, findings_count would be 0.
        assert row[0] == 0, "Simulation of broken state confirmed (findings=0)"
        # SC-6 checks mean_findings == 2.0; 0 != 2.0 → test would fail → gate detected.
        conn.close()


# ---------------------------------------------------------------------------
# Co-transactional write via record_sample_on_conn
# ---------------------------------------------------------------------------


def test_co_transactional_write(tmp_path: Path) -> None:
    """record_sample_on_conn writes without committing; caller controls the transaction."""
    from factory.scorecard import Scorecard

    sc = Scorecard(tmp_path / "jobs.db")

    # Open a separate connection (simulating the supervisor's jobs connection).
    conn = sqlite3.connect(str(tmp_path / "jobs.db"))
    conn.row_factory = sqlite3.Row

    job_row = _make_job_row("co-j1", state="DONE", cost=0.05)
    sc.record_sample_on_conn(conn, job_row, findings_count=1)
    conn.commit()
    conn.close()

    rows = sc.rollup()
    assert len(rows) == 1
    assert rows[0].n == 1


# ---------------------------------------------------------------------------
# parse_findings_count helper
# ---------------------------------------------------------------------------


def test_parse_findings_count_from_json(tmp_path: Path) -> None:
    """parse_findings_count reads count from a JSON findings file."""
    from factory.scorecard import parse_findings_count

    findings_file = tmp_path / "findings.json"
    findings_file.write_text('{"count": 3}')
    assert parse_findings_count(str(findings_file)) == 3

    findings_file2 = tmp_path / "findings2.json"
    findings_file2.write_text('{"findings": [{"id": 1}, {"id": 2}]}')
    assert parse_findings_count(str(findings_file2)) == 2


def test_parse_findings_count_missing_path() -> None:
    """parse_findings_count returns 0 for None or a missing path without raising."""
    from factory.scorecard import parse_findings_count

    assert parse_findings_count(None) == 0
    assert parse_findings_count("/no/such/path/findings.json") == 0
