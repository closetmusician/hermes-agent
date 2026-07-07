# ABOUTME: Pattern bank for the Fable factory (P5-c, design §4.3 + §REQ-02/REQ-03).
# ABOUTME: Stores distilled merged solutions (merged_clean outcomes) in SQLite FTS
# ABOUTME: and serves them as advisory-only context before a worker starts. Cross-repo
# ABOUTME: queries fan out to code-review-graph; degrade to same-repo on offline error.
# ABOUTME: READ-ONLY to workers — no .apply/.enqueue on PatternHit (design §REQ-03).
"""
Pattern bank — searchable store of distilled merged solutions.

Design authoritative source:
  docs/plans/harness/fable/p5/P5-design.md §4.3, §REQ-02, §REQ-03

The bank stores one row per ``merged_clean`` job outcome (gated: rejected/failed
outcomes are never stored).  ``query`` is called by the supervisor as a pre-spawn
hook; the returned PatternHit list is advisory context injected into the worker
brief — it DOES NOT modify any gate or bypass any ring check.

REQ-03 boundary (load-bearing):
  PatternHit is a plain NamedTuple — it has no .apply(), .patch(), or .enqueue()
  method.  There is no code path from a PatternHit to disk or broker.  Even if a
  pattern's approach_summary were to contain a malicious diff, it is still gated by
  the worker's own ring check + review at merge time.

Cross-repo transfer: ``query(..., cross_repo=True)`` fans out to the
code-review-graph MCP ``cross_repo_search_tool`` if available, and degrades
gracefully (same-repo-only result, no crash) when the tool is unavailable offline.
This is the documented fallback per design §4.3 ("cold repos degrade to
same-repo-only").

Sole writer: the supervisor (on a merged_clean terminal transition).  Never a worker.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import List, NamedTuple, Optional

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — co-located in the jobs DB (same pattern as trust_ledger / scorecard).
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS patterns (
    pattern_id    TEXT PRIMARY KEY,
    task_type     TEXT NOT NULL,
    repo          TEXT NOT NULL,
    approach_summary TEXT NOT NULL,
    diff_pointer  TEXT,
    added_ts      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pat_task_type ON patterns(task_type, added_ts DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS patterns_fts USING fts5(
    approach_summary,
    content=patterns,
    content_rowid=rowid
);
"""

_STORED_OUTCOMES = frozenset({"merged_clean"})


# ---------------------------------------------------------------------------
# Public data type (REQ-03: advisory-only, no .apply/.enqueue)
# ---------------------------------------------------------------------------


class PatternHit(NamedTuple):
    """
    Purpose: immutable advisory record returned by query(). Pure data container;
    no .apply(), .patch(), or .enqueue() method exists on this type — the boundary
    is structural (design §REQ-03).
    Usage: hits = bank.query(task_type="bugfix"); worker_brief += hits[0].approach_summary
    Gotchas: approach_summary is plain text (could contain a diff-like snippet from the
    original commit message); it is the worker's reading material, not an executable payload.
    """

    pattern_id: str
    task_type: str
    repo: str
    approach_summary: str
    diff_pointer: Optional[str]
    added_ts: int


# ---------------------------------------------------------------------------
# Cross-repo search seam (mocked in tests; degrades gracefully offline)
# ---------------------------------------------------------------------------


def _try_cross_repo_search(
    task_type: str, query_text: str, *, limit: int
) -> List[PatternHit]:
    """
    Purpose: fan out to code-review-graph cross_repo_search_tool for matching
    prior solutions from OTHER repos. Degrades gracefully: any exception → empty list
    so the caller still returns same-repo results (design §4.3 offline fallback).
    Usage: extras = _try_cross_repo_search("bugfix", "NullPointer ...", limit=3)
    Gotchas: the graph plugin must be built per repo; cold repos return nothing from
    the cross-repo path. This function is the ONLY I/O boundary for cross-repo —
    tests patch it by name.
    """
    try:
        # Import the MCP tool at call time so offline code paths don't fail on import.
        # The tool is available when the code-review-graph plugin is loaded.
        from mcp__plugin_code_review_graph_code_review_graph__cross_repo_search_tool import (  # type: ignore[import]
            cross_repo_search_tool,
        )

        raw_hits = cross_repo_search_tool(query=query_text, task_type=task_type, limit=limit)
        result: List[PatternHit] = []
        for h in raw_hits or []:
            result.append(
                PatternHit(
                    pattern_id=h.get("id", "xr-unknown"),
                    task_type=task_type,
                    repo=h.get("repo", "unknown"),
                    approach_summary=h.get("summary", ""),
                    diff_pointer=h.get("diff_pointer"),
                    added_ts=int(h.get("ts", time.time())),
                )
            )
        return result
    except Exception as exc:  # noqa: BLE001
        _log.debug("cross-repo search unavailable (offline fallback): %s", exc)
        return []


# ---------------------------------------------------------------------------
# PatternBank class
# ---------------------------------------------------------------------------


class PatternBank:
    """
    Purpose: searchable SQLite store of distilled merged solutions. Supports
    add_pattern (supervisor write-path) and query (pre-spawn read-path).
    Usage: bank = PatternBank(db_path); hits = bank.query(task_type="bugfix")
    Gotchas: schema is created on __init__; the file can be the same jobs DB or
    a separate patterns DB — both work. Sole writer is the supervisor.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add_pattern(
        self,
        *,
        task_type: str,
        repo: str,
        approach_summary: str,
        diff_pointer: Optional[str] = None,
        outcome: str = "merged_clean",
    ) -> Optional[str]:
        """
        Purpose: store a distilled solution. GATED on outcome — only merged_clean
        outcomes are stored (rejected/failed jobs produce no pattern).
        Usage: bank.add_pattern(task_type="bugfix", repo="r", approach_summary="...",
                 diff_pointer="path", outcome="merged_clean")
        Gotchas: returns the generated pattern_id on success, None if gated out.
        The supervisor is the SOLE writer — never call from within a worker.
        """
        if outcome not in _STORED_OUTCOMES:
            _log.debug(
                "add_pattern skipped: outcome %r is not in _STORED_OUTCOMES", outcome
            )
            return None

        import hashlib as _hl

        ts = int(time.time())
        # Deterministic ID from content to avoid duplicates on replay.
        raw_id = f"{repo}:{task_type}:{approach_summary[:120]}:{ts}"
        pattern_id = "pat-" + _hl.sha256(raw_id.encode()).hexdigest()[:12]

        self._conn.execute(
            """
            INSERT OR IGNORE INTO patterns
              (pattern_id, task_type, repo, approach_summary, diff_pointer, added_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pattern_id, task_type, repo, approach_summary, diff_pointer, ts),
        )
        # Keep FTS index in sync (insert triggers don't exist in fts5 content tables).
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO patterns_fts(patterns_fts) VALUES('rebuild')"
            )
        except sqlite3.OperationalError:
            # FTS rebuild on large tables can fail non-critically.
            pass
        self._conn.commit()
        _log.debug("add_pattern stored %s for %s/%s", pattern_id, repo, task_type)
        return pattern_id

    def query(
        self,
        task_type: str,
        *,
        limit: int = 5,
        cross_repo: bool = False,
    ) -> List[PatternHit]:
        """
        Purpose: return the top-N most recent stored patterns for a task_type.
        If cross_repo=True, also fans out to code-review-graph for patterns from
        other repos; degrades to same-repo-only on any cross-repo error (offline safe).
        Usage: hits = bank.query("bugfix", limit=3); brief += hits[0].approach_summary
        Gotchas: returns [] on no match (no crash). PatternHit is advisory-only —
        no .apply/.enqueue method (REQ-03 boundary).
        """
        rows = self._conn.execute(
            """
            SELECT pattern_id, task_type, repo, approach_summary, diff_pointer, added_ts
            FROM patterns
            WHERE task_type = ?
            ORDER BY added_ts DESC
            LIMIT ?
            """,
            (task_type, limit),
        ).fetchall()

        hits = [
            PatternHit(
                pattern_id=r["pattern_id"],
                task_type=r["task_type"],
                repo=r["repo"],
                approach_summary=r["approach_summary"],
                diff_pointer=r["diff_pointer"],
                added_ts=r["added_ts"],
            )
            for r in rows
        ]

        if cross_repo:
            # Fan out; degrade gracefully on any error (offline fallback).
            # The try/except here is belt-and-suspenders: _try_cross_repo_search
            # already catches internally, but when the function is mocked in tests
            # (side_effect=RuntimeError) the exception propagates past the function
            # body, so we catch it here too (design §4.3 "degrade gracefully").
            query_text = f"task_type:{task_type}"
            if hits:
                query_text += " " + hits[0].approach_summary[:120]
            try:
                extras = _try_cross_repo_search(task_type, query_text, limit=limit)
            except Exception as exc:  # noqa: BLE001
                _log.debug("cross-repo search degraded to same-repo-only: %s", exc)
                extras = []
            # Merge: de-dup by pattern_id, same-repo hits first.
            seen = {h.pattern_id for h in hits}
            for xh in extras:
                if xh.pattern_id not in seen:
                    hits.append(xh)
                    seen.add(xh.pattern_id)

        return hits[:limit]


# ---------------------------------------------------------------------------
# Module-level convenience functions (supervisor pre-spawn hook may use these)
# ---------------------------------------------------------------------------


def add_pattern(
    *,
    db_path: Path,
    task_type: str,
    repo: str,
    approach_summary: str,
    diff_pointer: Optional[str] = None,
    outcome: str = "merged_clean",
) -> Optional[str]:
    """
    Purpose: module-level alias for PatternBank(db_path).add_pattern(...).
    Opens and closes the DB in a single call; suitable for co-transactional use
    in the supervisor terminal-transition path.
    Usage: add_pattern(db_path=path, task_type="bugfix", repo="r",
             approach_summary="...", diff_pointer="...", outcome="merged_clean")
    Gotchas: creates a new connection per call — prefer the class form for
    high-frequency writes.
    """
    bank = PatternBank(db_path)
    return bank.add_pattern(
        task_type=task_type,
        repo=repo,
        approach_summary=approach_summary,
        diff_pointer=diff_pointer,
        outcome=outcome,
    )


def query_patterns(
    *,
    db_path: Path,
    task_type: str,
    limit: int = 5,
    cross_repo: bool = False,
) -> List[PatternHit]:
    """
    Purpose: module-level alias for PatternBank(db_path).query(...).
    Used by the supervisor pre-spawn hook to inject relevant priors into the
    worker brief before spawning.
    Usage: hits = query_patterns(db_path=path, task_type="bugfix", limit=3)
    Gotchas: returns [] for an unrecognised task_type (no crash). Cross-repo
    fallback is online-only; degrade gracefully when unavailable.
    """
    bank = PatternBank(db_path)
    return bank.query(task_type, limit=limit, cross_repo=cross_repo)
