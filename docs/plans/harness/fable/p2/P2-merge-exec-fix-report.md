# ABOUTME: Post-fix report for MERGE-EXEC-SILENT-FAIL (live-debug MERGE-EXEC-FIX).
# ABOUTME: Documents the root cause, RED regression test, fix diff, and GREEN results
# ABOUTME: for the silent false-'merged' bug in broker/executors/merge_executor.py.

# P2-merge-exec-fix-report — MERGE-EXEC-SILENT-FAIL

**Task:** MERGE-EXEC-FIX (live-debug, GOVERNANCE_EXEMPT)
**Date:** 2026-07-07
**Fixer:** Claude Sonnet 4.6 (subagent)

---

## Root Cause

`broker/executors/merge_executor.py` line 207 (pre-fix):

```python
_git(["checkout", base], worktree)   # ← return value DISCARDED
merge = _git(["merge", "--ff-only", branch], worktree)
```

`git checkout <base>` returns rc=1 when `<base>` is already checked out in a
linked worktree (the primary tree).  The executor discarded the `CompletedProcess`
return value and fell through to `merge --ff-only <branch>` with HEAD still on
the job branch.  `git merge --ff-only feature` while HEAD IS feature is a trivial
no-op (rc=0).  The push then reported "Everything up-to-date" (rc=0).  The
executor returned `status: 'merged'` while the protected ref (main) never moved.

All other git steps already check their return codes; this was the only unchecked
one in the critical path.

---

## RED Regression (REQ-01)

Test: `tests/broker/test_merge_executor.py::test_checkout_failure_returns_needs_attention_not_merged`

Injected a git runner whose `checkout <base>` always returns rc=1 with
`fatal: 'main' is already checked out`, while all other git calls run for real
against a local bare-repo fixture.

**RED output (pre-fix):**
```
AssertionError: BUG REPRODUCED: executor returned 'merged' even though
'git checkout main' failed — silent false-merge detected
```
Confirms the false-merged path is exactly as described.

---

## Fix (REQ-02)

**File:** `broker/executors/merge_executor.py`, lines 214–222 (post-fix)

```python
checkout = _git(["checkout", base], worktree)
if checkout.returncode != 0:
    return {
        "status": "needs_attention",
        "reason": "checkout_failed",
        "detail": (checkout.stderr or checkout.stdout)[-1000:],
        "command": f"git checkout {base}",
        "forced": False,
    }
merge = _git(["merge", "--ff-only", branch], worktree)
```

Minimal diff — one `_git(...)` call converted to a checked assignment, plus a
13-line guard block.  No force-push introduced.  No other files changed.

---

## GREEN Results (REQ-03)

```
tests/broker/   87 passed, 0 failed, 1 skipped
tests/factory/test_supervisor.py   9 passed
```

The new regression test is the 87th broker test and is now GREEN.
The supervisor happy path (dedicated-clone workaround) is unaffected.

---

## Verification Summary

| REQ | Status | Evidence |
|-----|--------|---------|
| REQ-01 RED repro | PASS | `AssertionError: BUG REPRODUCED` at line 397 pre-fix |
| REQ-02 Root-cause fix | PASS | checkout rc now checked; `merged` unreachable on failed checkout |
| REQ-03 No regressions | PASS | 87 broker + 9 supervisor tests green |
