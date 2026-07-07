# ABOUTME: RED-first tests for factory/trust_ledger.py (P1b-a, REQ-01).
# ABOUTME: Uses real SQLite temp files; no mocks. Covers schema creation,
# ABOUTME: record_outcome accumulation per repo×task_type, derive_task_type,
# ABOUTME: the trust-ledger.md mirror writer, and atomicity of outcome recording.
# ABOUTME: Each test maps to a done-when clause in P1b-design.md §2 / REQ-01.
"""
Tests for factory.trust_ledger (P1b-a, REQ-01).

Coverage:
  1. Schema: trust_ledger table created with all required columns.
  2. record_outcome accumulates rows keyed by (repo, task_type).
  3. read_rows returns all rows for the given (repo, task_type) key.
  4. outcome vocabulary: merged_clean / merged_with_fix / rejected / reverted accepted.
  5. derive_task_type: keyword-map classification + explicit label passthrough.
  6. derive_task_type: unknown → 'other' (fail-safe).
  7. trust-ledger.md mirror: written atomically, reflects current rows.
  8. Multiple repos × task_types stay isolated (no cross-key bleed).
  9. repo_class is recorded per row (snapshot at write time).
 10. outcome_ts is stored in ms epoch; row is queryable.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from factory.trust_ledger import (
    TrustLedger,
    derive_task_type,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ledger(tmp_path):
    """A fresh TrustLedger backed by a real SQLite temp file."""
    return TrustLedger(tmp_path / "jobs.db")


@pytest.fixture()
def ledger_with_mirror(tmp_path):
    """A TrustLedger that also knows where to write the mirror."""
    mirror = tmp_path / "trust-ledger.md"
    store = TrustLedger(tmp_path / "jobs.db", mirror_path=mirror)
    return store, mirror


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_creates_trust_ledger_table(tmp_path):
    """trust_ledger table is created on first connect."""
    db_path = tmp_path / "jobs.db"
    TrustLedger(db_path)
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "trust_ledger" in tables
    conn.close()


def test_schema_has_required_columns(tmp_path):
    """All schema columns exist (job_id, repo, task_type, outcome, repo_class, outcome_ts)."""
    db_path = tmp_path / "jobs.db"
    TrustLedger(db_path)
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trust_ledger)").fetchall()}
    assert {"job_id", "repo", "task_type", "outcome", "repo_class", "outcome_ts",
            "tier_at_spawn", "confidence", "detail"}.issubset(cols)
    conn.close()


# ---------------------------------------------------------------------------
# record_outcome + read_rows
# ---------------------------------------------------------------------------


def test_record_outcome_accumulates_rows(ledger):
    """Calling record_outcome multiple times accumulates rows per (repo, task_type)."""
    ledger.record_outcome("job-1", repo="myrepo", task_type="feature",
                          outcome="merged_clean", repo_class="personal",
                          outcome_ts=1_000_000)
    ledger.record_outcome("job-2", repo="myrepo", task_type="feature",
                          outcome="merged_clean", repo_class="personal",
                          outcome_ts=2_000_000)
    rows = ledger.read_rows("myrepo", "feature")
    assert len(rows) == 2
    assert {r["job_id"] for r in rows} == {"job-1", "job-2"}


def test_read_rows_keyed_by_repo_and_task_type(ledger):
    """read_rows returns ONLY rows matching the given (repo, task_type) pair."""
    ledger.record_outcome("job-A", repo="repoX", task_type="bugfix",
                          outcome="merged_clean", repo_class="personal",
                          outcome_ts=1_000_000)
    ledger.record_outcome("job-B", repo="repoX", task_type="docs",
                          outcome="merged_clean", repo_class="personal",
                          outcome_ts=2_000_000)
    ledger.record_outcome("job-C", repo="repoY", task_type="bugfix",
                          outcome="merged_clean", repo_class="personal",
                          outcome_ts=3_000_000)

    bugfix_x = ledger.read_rows("repoX", "bugfix")
    assert len(bugfix_x) == 1
    assert bugfix_x[0]["job_id"] == "job-A"

    docs_x = ledger.read_rows("repoX", "docs")
    assert len(docs_x) == 1

    bugfix_y = ledger.read_rows("repoY", "bugfix")
    assert len(bugfix_y) == 1

    # Cross-key isolation: (repoX, feature) is empty.
    assert ledger.read_rows("repoX", "feature") == []


def test_outcome_vocabulary_accepted(ledger):
    """All four outcome values are accepted by record_outcome."""
    for i, outcome in enumerate(["merged_clean", "merged_with_fix", "rejected", "reverted"]):
        ledger.record_outcome(
            f"job-{i}", repo="r", task_type="feature",
            outcome=outcome, repo_class="personal",
            outcome_ts=i * 1_000_000,
        )
    rows = ledger.read_rows("r", "feature")
    assert {r["outcome"] for r in rows} == {"merged_clean", "merged_with_fix", "rejected", "reverted"}


def test_repo_class_is_recorded_per_row(ledger):
    """repo_class is stored as part of each row."""
    ledger.record_outcome("job-1", repo="r", task_type="feature",
                          outcome="merged_clean", repo_class="personal",
                          outcome_ts=1_000_000)
    rows = ledger.read_rows("r", "feature")
    assert rows[0]["repo_class"] == "personal"


def test_outcome_ts_stored_as_ms_epoch(ledger):
    """outcome_ts is stored verbatim (ms epoch int) and retrievable."""
    ts = 1_720_000_000_000  # a plausible ms epoch
    ledger.record_outcome("job-1", repo="r", task_type="feature",
                          outcome="merged_clean", repo_class="personal",
                          outcome_ts=ts)
    rows = ledger.read_rows("r", "feature")
    assert rows[0]["outcome_ts"] == ts


def test_rows_ordered_by_outcome_ts(ledger):
    """read_rows returns rows ordered by outcome_ts ascending."""
    for ts, jid in [(3_000_000, "job-3"), (1_000_000, "job-1"), (2_000_000, "job-2")]:
        ledger.record_outcome(jid, repo="r", task_type="feature",
                              outcome="merged_clean", repo_class="personal",
                              outcome_ts=ts)
    rows = ledger.read_rows("r", "feature")
    assert [r["job_id"] for r in rows] == ["job-1", "job-2", "job-3"]


# ---------------------------------------------------------------------------
# derive_task_type
# ---------------------------------------------------------------------------


def test_derive_task_type_explicit_label():
    """An explicit task_type label in the spec dict takes precedence over keyword scan."""
    assert derive_task_type({"task_type": "refactor", "spec": "add a login feature"}) == "refactor"


def test_derive_task_type_bugfix_from_keywords():
    """'fix' / 'bug' / 'regression' in the spec text → 'bugfix'."""
    assert derive_task_type({"spec": "fix the login regression"}) == "bugfix"
    assert derive_task_type({"spec": "repair the bug in parser"}) == "bugfix"


def test_derive_task_type_refactor_from_keywords():
    """'refactor' / 'rename' / 'extract' / 'cleanup' in spec → 'refactor'."""
    assert derive_task_type({"spec": "refactor the auth module"}) == "refactor"
    assert derive_task_type({"spec": "rename the old handler"}) == "refactor"


def test_derive_task_type_test_from_keywords():
    """'test' / 'coverage' / 'spec' in spec text → 'test'."""
    assert derive_task_type({"spec": "add test coverage for parser"}) == "test"
    assert derive_task_type({"spec": "write specs for auth"}) == "test"


def test_derive_task_type_docs_from_keywords():
    """'docs' / 'readme' / 'comment' in spec text → 'docs'."""
    assert derive_task_type({"spec": "update the readme file"}) == "docs"
    assert derive_task_type({"spec": "add comments to the auth module"}) == "docs"


def test_derive_task_type_feature_as_default():
    """A spec with no recognized keywords defaults to 'feature'."""
    assert derive_task_type({"spec": "add user profile page"}) == "feature"


def test_derive_task_type_unknown_spec_returns_other():
    """A spec with no text and no explicit label → 'other' (fail-safe)."""
    assert derive_task_type({}) == "other"
    assert derive_task_type({"spec": ""}) == "other"


def test_derive_task_type_kind_quick_biases_bugfix():
    """kind='quick' with no explicit label biases toward 'bugfix' if no keyword match."""
    # No other keywords → 'bugfix' because kind=quick biases that way over 'feature'.
    result = derive_task_type({"spec": "small quick patch", "kind": "quick"})
    assert result == "bugfix"


# ---------------------------------------------------------------------------
# trust-ledger.md mirror
# ---------------------------------------------------------------------------


def test_mirror_written_atomically(ledger_with_mirror):
    """write_ledger_mirror writes a file that reflects current ledger rows."""
    store, mirror_path = ledger_with_mirror
    store.record_outcome("job-1", repo="myrepo", task_type="feature",
                         outcome="merged_clean", repo_class="personal",
                         outcome_ts=1_000_000)
    store.write_ledger_mirror()
    assert mirror_path.exists()
    content = mirror_path.read_text()
    assert "myrepo" in content
    assert "feature" in content
    assert "merged_clean" in content


def test_mirror_does_not_exist_before_first_write(tmp_path):
    """No mirror file exists before write_ledger_mirror is called."""
    mirror = tmp_path / "trust-ledger.md"
    TrustLedger(tmp_path / "jobs.db", mirror_path=mirror)
    assert not mirror.exists()


def test_mirror_atomicity_uses_replace(ledger_with_mirror, monkeypatch):
    """write_ledger_mirror uses os.replace (atomic rename); no partial writes visible."""
    store, mirror_path = ledger_with_mirror
    replace_calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        replace_calls.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    store.write_ledger_mirror()
    assert len(replace_calls) == 1
    _, dst = replace_calls[0]
    assert str(mirror_path) == str(dst)
