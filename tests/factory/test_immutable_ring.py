# ABOUTME: RED-first tests for the immutable-ring submission gate (P2-h, design §3.2).
# ABOUTME: check_diff() rejects any worker diff touching a ring path — canonicalized so
# ABOUTME: rename, symlink, and ../ traversal cannot evade it. Anti-weakening: removing
# ABOUTME: realpath canonicalization makes the traversal test PASS through (a leak), so
# ABOUTME: that test FAILS — proving the wall is real, not decorative.
"""
Tests for factory.immutable_ring (design §3.2, review P1-R2/P1-R3).

The gate runs at merge-submission on a factory job's real ``git diff
<base>...<branch>`` — worker-produced worktrees only. These tests build REAL git
diff text (via a throwaway repo) so nothing here validates a mocked diff parser.

Attack coverage: direct hit, rename in/out (old+new path), ../ traversal,
symlink-into-ring, self-protection (the gate lists its own files), fail-closed on
un-parseable diff, and a benign negative control.
"""

import subprocess

import pytest

from factory.immutable_ring import (
    RING_PATHS,
    check_diff,
    RingViolation,
    fs_scope_excludes,
    is_ring_path,
)


def _git(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True,
        capture_output=True, text=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "HOME": str(cwd), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )


@pytest.fixture
def repo(tmp_path):
    """A throwaway git repo with a broker/ ring file and an app/ file committed."""
    root = tmp_path / "wt"
    root.mkdir()
    _git(["init", "-q"], root)
    (root / "broker").mkdir()
    (root / "broker" / "approval.py").write_text("# ring file\n")
    (root / "broker" / "server.py").write_text("# ring file\n")
    (root / "app").mkdir()
    (root / "app" / "x.py").write_text("# app file\n")
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "base"], root)
    _git(["branch", "base"], root)
    return root


def _diff_since_base(root):
    return _git(["diff", "base", "HEAD"], root).stdout


# --- Direct hit --------------------------------------------------------------

def test_direct_ring_edit_rejected(repo):
    (repo / "broker" / "approval.py").write_text("# tampered\n")
    _git(["commit", "-aqm", "tamper"], repo)
    with pytest.raises(RingViolation):
        check_diff(_diff_since_base(repo), worktree_root=str(repo))


# --- Rename (P1-R2): both old-path and new-path checked -----------------------

def test_rename_ring_file_out_rejected_on_old_path(repo):
    """Renaming broker/server.py -> app/harmless.py trips the OLD-path check."""
    _git(["mv", "broker/server.py", "app/harmless.py"], repo)
    _git(["commit", "-qm", "smuggle out"], repo)
    with pytest.raises(RingViolation):
        check_diff(_diff_since_base(repo), worktree_root=str(repo))


def test_rename_app_onto_ring_rejected_on_new_path(repo):
    """Renaming app/x.py -> broker/x.py trips the NEW-path check."""
    _git(["mv", "app/x.py", "broker/x.py"], repo)
    _git(["commit", "-qm", "smuggle in"], repo)
    with pytest.raises(RingViolation):
        check_diff(_diff_since_base(repo), worktree_root=str(repo))


# --- Traversal (P1-R3): realpath canonicalization is load-bearing ------------

def test_traversal_path_canonicalizes_onto_ring_rejected(repo):
    """A ../ path that resolves onto a ring file is rejected (realpath match)."""
    # Synthesize a diff header with a traversal path that resolves onto the ring.
    diff = (
        "diff --git a/sub/../../broker/server.py b/sub/../../broker/server.py\n"
        "--- a/sub/../../broker/server.py\n"
        "+++ b/sub/../../broker/server.py\n"
        "@@ -1 +1 @@\n"
        "-x\n+y\n"
    )
    with pytest.raises(RingViolation):
        check_diff(diff, worktree_root=str(repo))


def test_path_escaping_worktree_rejected(repo):
    """A path that resolves OUTSIDE the worktree is rejected outright."""
    diff = (
        "diff --git a/../../../etc/evil b/../../../etc/evil\n"
        "--- a/../../../etc/evil\n"
        "+++ b/../../../etc/evil\n"
        "@@ -1 +1 @@\n"
        "-x\n+y\n"
    )
    with pytest.raises(RingViolation):
        check_diff(diff, worktree_root=str(repo))


# --- Symlink (P1-R3) ---------------------------------------------------------

def test_symlink_into_ring_rejected(repo):
    """A diff adding a symlink whose realpath target is a ring file is rejected."""
    import os
    os.symlink("broker/approval.py", repo / "app" / "link.py")
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "symlink into ring"], repo)
    with pytest.raises(RingViolation):
        check_diff(_diff_since_base(repo), worktree_root=str(repo))


# --- Self-protection ---------------------------------------------------------

def test_gate_protects_its_own_files():
    """RING_PATHS lists the gate's own module + siblings (a diff weakening the
    guard is the worst diff)."""
    assert "factory/immutable_ring.py" in RING_PATHS
    assert "factory/injection_scan.py" in RING_PATHS
    assert "factory/cost_stops.py" in RING_PATHS
    assert "factory/worker_env.py" in RING_PATHS


def test_diff_touching_gate_module_rejected(repo):
    (repo / "factory").mkdir()
    (repo / "factory" / "immutable_ring.py").write_text("# weakened\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "weaken gate"], repo)
    with pytest.raises(RingViolation):
        check_diff(_diff_since_base(repo), worktree_root=str(repo))


# --- Fail-closed on un-parseable diff ----------------------------------------

def test_unparseable_diff_fails_closed(repo):
    """A diff header that cannot be parsed into old/new paths is REJECTED."""
    with pytest.raises(RingViolation):
        check_diff("diff --git THIS IS NOT A VALID HEADER\n@@ garbage\n",
                   worktree_root=str(repo))


# --- Negative control --------------------------------------------------------

def test_app_only_diff_allowed(repo):
    (repo / "app" / "x.py").write_text("# app change\n")
    _git(["commit", "-aqm", "app change"], repo)
    # Should not raise.
    check_diff(_diff_since_base(repo), worktree_root=str(repo))


def test_empty_diff_allowed(repo):
    check_diff("", worktree_root=str(repo))


# --- FS-scope-exclude companion (REQ-04) -------------------------------------

def test_fs_scope_excludes_contains_ring_prefixes():
    excludes = fs_scope_excludes()
    assert "broker/" in excludes or any(e.startswith("broker") for e in excludes)
    assert "factory/immutable_ring.py" in excludes


def test_is_ring_path_helper():
    assert is_ring_path("broker/approval.py") is True
    assert is_ring_path("app/users.py") is False
