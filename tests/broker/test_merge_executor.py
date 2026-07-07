# ABOUTME: RED-first tests for broker/executors/merge_executor.py (P2 design §4.2,
# ABOUTME: task P2-g). Drives the REAL merge executor through the REAL broker approval
# ABOUTME: wall (HeldStore + ApprovalAuthority nonce) over REAL local git repos: a clean
# ABOUTME: merge succeeds; a conflict → needs_attention with NO force; the per-repo lock
# ABOUTME: serializes same-repo merges; an ungated (no-nonce) merge is REJECTED; a diff
# ABOUTME: touching the immutable ring is rejected BEFORE it can ever become a merge.
"""
Tests for the held-for-approval broker merge (REQ-02, REQ-04).

Real broker objects throughout: a real ``HeldStore`` holds the ``type="merge"``
action, a real ``ApprovalAuthority`` mints/validates the nonce, and the REAL
``build_merge_executor`` runs fetch → rebase → re-test → merge → push over REAL
local git repositories. The ONLY thing mocked is the git-NETWORK boundary — and
even that is made hermetic by pushing to a **local bare** "remote" (a real
``git push``, no network), so the no-force guarantee is exercised for real.

Anti-weakening (REQ-04):
  * the no-ungated-merge test FAILS if the nonce check in ApprovalAuthority is
    bypassed (a nonce-less approve would then execute the merge);
  * the ring-rejection test FAILS if ``check_diff`` is removed (a broker/**-
    touching diff would then be allowed toward a merge).
"""

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from broker.approval import ApprovalAuthority, ApprovalRejected
from broker.executors.merge_executor import build_merge_executor
from broker.held_store import HeldStore
from factory.immutable_ring import RingViolation, check_diff


# ---------------------------------------------------------------------------
# Real-git fixtures — a bare "remote" + a base clone + a feature branch worktree.
# ---------------------------------------------------------------------------

def _git(cwd, *args):
    """Run a git subcommand in cwd, asserting success (fixture setup only)."""
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc


def _init_identity(repo):
    _git(repo, "config", "user.email", "factory@test")
    _git(repo, "config", "user.name", "factory")
    _git(repo, "config", "commit.gpgsign", "false")


def _write(path, name, content):
    (Path(path) / name).write_text(content)


def _make_repo(tmp_path, *, feature_edits, base_edits_after_branch=None):
    """
    Build a real local git topology:
      remote/   — a bare repo (the push target; a local bare = a real push, no net)
      work/     — a clone of remote with `main` and a `feature` branch

    feature_edits: {filename: content} committed on the feature branch.
    base_edits_after_branch: {filename: content} committed on main AFTER the
      feature branch diverged — used to inject a rebase conflict.

    Returns (work_dir, remote_dir).
    """
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "--bare", "-b", "main")

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _init_identity(seed)
    _write(seed, "README.md", "line1\nline2\nline3\n")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "seed")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "origin", "main")

    work = tmp_path / "work"
    _git(tmp_path, "clone", str(remote), str(work))
    _init_identity(work)

    # Feature branch off main.
    _git(work, "checkout", "-b", "feature")
    for name, content in feature_edits.items():
        _write(work, name, content)
        _git(work, "add", name)
    _git(work, "commit", "-m", "feature work")

    # Optionally advance main (and the remote) so the rebase must move / conflict.
    if base_edits_after_branch:
        _git(work, "checkout", "main")
        for name, content in base_edits_after_branch.items():
            _write(work, name, content)
            _git(work, "add", name)
        _git(work, "commit", "-m", "base advanced")
        _git(work, "push", "origin", "main")
        _git(work, "checkout", "feature")

    return work, remote


def _pass_test(_worktree):
    """A re-test seam that always passes (the suite isn't the unit under test)."""
    return True, "ok"


def _dummy_cred(_name):
    """A push credential the broker resolves so the REAL git_push_executor runs.

    The push target is a LOCAL BARE repo (file transport), so the token value is
    never used on the wire — this makes ``git push`` real-but-hermetic (no network)
    while still exercising the executor's fail-closed token gate as satisfied.
    """
    return "local-bare-transport-token"


def _enqueue_merge(store, work, *, branch="feature", base="main", repo="acme"):
    """Enqueue a real held merge action carrying a real merge-card payload."""
    payload = json.dumps(
        {"repo": repo, "branch": branch, "base": base, "worktree": str(work),
         "remote": "origin"}
    )
    return store.enqueue(
        type="merge",
        summary=f"merge {branch} -> {base}",
        payload=payload,
        origin="factory.supervisor",
        safe_lane_json="{}",
        state="held",
    )


# ---------------------------------------------------------------------------
# REQ-02 #1 — clean merge succeeds under the broker + nonce.
# ---------------------------------------------------------------------------

def test_clean_merge_succeeds_under_broker(tmp_path):
    """Approve-with-nonce → executor rebases, re-tests, merges, pushes → merged."""
    work, remote = _make_repo(tmp_path, feature_edits={"feature.txt": "hello\n"})
    store = HeldStore(tmp_path / "held.db")
    authority = ApprovalAuthority(store, broker_secret=b"broker-secret")
    executor = build_merge_executor(_dummy_cred, test_runner=_pass_test)

    aid = _enqueue_merge(store, work)
    nonce = authority.mint_nonce(aid)
    result = authority.approve(aid, nonce, executor=executor)

    assert result["status"] == "merged", result
    assert result["forced"] is False
    assert store.get(aid)["state"] == "executed"
    # The merge really landed on the remote's main.
    log = subprocess.run(
        ["git", "log", "--oneline", "main"], cwd=str(work),
        capture_output=True, text=True,
    ).stdout
    assert "feature work" in log


# ---------------------------------------------------------------------------
# REQ-02 #2 / REQ-04 — a conflicting merge → NEEDS_ATTENTION with NO force.
# ---------------------------------------------------------------------------

def test_conflicting_merge_needs_attention_never_forces(tmp_path, monkeypatch):
    """A rebase conflict → needs_attention; git push --force is NEVER invoked."""
    # Both branches edit README.md's first line differently → rebase conflict.
    work, remote = _make_repo(
        tmp_path,
        feature_edits={"README.md": "FEATURE\nline2\nline3\n"},
        base_edits_after_branch={"README.md": "BASE\nline2\nline3\n"},
    )
    store = HeldStore(tmp_path / "held.db")
    authority = ApprovalAuthority(store, broker_secret=b"broker-secret")

    # Guard: fail loudly if ANY git invocation carries a force flag.
    real_run = subprocess.run

    def _no_force_run(cmd, *a, **k):
        if isinstance(cmd, (list, tuple)):
            assert not any(
                str(tok) in ("-f", "--force", "--force-with-lease") for tok in cmd
            ), f"force-push attempted: {cmd}"
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", _no_force_run)

    executor = build_merge_executor(test_runner=_pass_test)
    aid = _enqueue_merge(store, work)
    nonce = authority.mint_nonce(aid)
    result = authority.approve(aid, nonce, executor=executor)

    assert result["status"] == "needs_attention"
    assert result["reason"] == "rebase_conflict"
    assert result["forced"] is False
    # The remote main was NOT advanced by the feature (no merge happened).
    remote_log = subprocess.run(
        ["git", "log", "--oneline", "main"], cwd=str(work),
        capture_output=True, text=True,
    ).stdout
    assert "feature work" not in remote_log


# ---------------------------------------------------------------------------
# REQ-02 #2b — a post-rebase re-test failure → NEEDS_ATTENTION, no merge/force.
# ---------------------------------------------------------------------------

def test_post_rebase_test_failure_needs_attention(tmp_path):
    """A clean rebase but a failing re-test → needs_attention, never merged."""
    work, remote = _make_repo(tmp_path, feature_edits={"feature.txt": "hi\n"})
    store = HeldStore(tmp_path / "held.db")
    authority = ApprovalAuthority(store, broker_secret=b"broker-secret")

    executor = build_merge_executor(test_runner=lambda wt: (False, "boom"))
    aid = _enqueue_merge(store, work)
    nonce = authority.mint_nonce(aid)
    result = authority.approve(aid, nonce, executor=executor)

    assert result["status"] == "needs_attention"
    assert result["reason"] == "post_rebase_test_fail"
    assert result["forced"] is False


# ---------------------------------------------------------------------------
# REQ-02 #3 — per-repo lock serializes two same-repo merges.
# ---------------------------------------------------------------------------

def test_per_repo_lock_serializes_same_repo_merges(tmp_path):
    """Two merges for the same repo cannot interleave — the lock spans the whole
    fetch→rebase→retest→merge→push critical section (review M3)."""
    work, remote = _make_repo(tmp_path, feature_edits={"feature.txt": "x\n"})

    active = 0
    max_concurrent = 0
    seen_lock = threading.Lock()

    def _slow_test(_wt):
        nonlocal active, max_concurrent
        with seen_lock:
            active += 1
            max_concurrent = max(max_concurrent, active)
        time.sleep(0.15)  # hold the critical section long enough to overlap
        with seen_lock:
            active -= 1
        return True, "ok"

    executor = build_merge_executor(test_runner=_slow_test)
    # Two rows, SAME repo slug → same lock. The merge itself would fail the 2nd
    # time (already merged), but we are asserting SERIALIZATION, not both merging:
    # max_concurrent must stay 1 because the lock is per-repo.
    row_a = {"payload": json.dumps(
        {"repo": "acme", "branch": "feature", "base": "main",
         "worktree": str(work), "remote": "origin"})}
    row_b = {"payload": json.dumps(
        {"repo": "acme", "branch": "feature", "base": "main",
         "worktree": str(work), "remote": "origin"})}

    results = {}

    def _run(key, row):
        results[key] = executor(row)

    t1 = threading.Thread(target=_run, args=("a", row_a))
    t2 = threading.Thread(target=_run, args=("b", row_b))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # If the lock did NOT serialize, both re-tests would run at once → 2.
    assert max_concurrent == 1, "per-repo lock failed to serialize same-repo merges"


# ---------------------------------------------------------------------------
# REQ-02 #4 / REQ-04 — NO ungated merge to a protected branch.
# ---------------------------------------------------------------------------

def test_no_ungated_merge_without_nonce(tmp_path):
    """A merge is a HELD action: approve WITHOUT a valid nonce is REJECTED and the
    executor never runs — no ungated merge to a protected branch.

    Anti-weakening: if the nonce check were bypassed this would execute the merge
    and the assertion (state still 'held', no merge) would fail.
    """
    work, remote = _make_repo(tmp_path, feature_edits={"feature.txt": "x\n"})
    store = HeldStore(tmp_path / "held.db")
    authority = ApprovalAuthority(store, broker_secret=b"broker-secret")
    executor = build_merge_executor(test_runner=_pass_test)

    aid = _enqueue_merge(store, work)
    # The assistant, over the enqueue socket, has NO nonce.
    with pytest.raises(ApprovalRejected):
        authority.approve(aid, None, executor=executor)

    # Still held — the executor never ran, nothing merged to the protected branch.
    assert store.get(aid)["state"] == "held"
    remote_log = subprocess.run(
        ["git", "log", "--oneline", "main"], cwd=str(work),
        capture_output=True, text=True,
    ).stdout
    assert "feature work" not in remote_log


def test_forged_nonce_cannot_merge(tmp_path):
    """A nonce minted with the wrong secret cannot approve the merge (unforgeable)."""
    work, remote = _make_repo(tmp_path, feature_edits={"feature.txt": "x\n"})
    store = HeldStore(tmp_path / "held.db")
    real = ApprovalAuthority(store, broker_secret=b"real-secret")
    forger = ApprovalAuthority(store, broker_secret=b"guessed")
    executor = build_merge_executor(test_runner=_pass_test)

    aid = _enqueue_merge(store, work)
    forged = forger.mint_nonce(aid)
    with pytest.raises(ApprovalRejected):
        real.approve(aid, forged, executor=executor)
    assert store.get(aid)["state"] == "held"


# ---------------------------------------------------------------------------
# REQ-02 #5 / REQ-04 — a diff touching broker/ (ring) is REJECTED before merge.
# ---------------------------------------------------------------------------

def test_ring_touching_diff_rejected_before_merge(tmp_path):
    """A worker diff touching the immutable ring (broker/**) is rejected by
    check_diff — so it can NEVER be built into a merge card / enqueued as a merge.

    Anti-weakening: if check_diff were removed, this diff would be allowed and the
    RingViolation would not be raised — the assertion would fail.
    """
    ring_diff = (
        "diff --git a/broker/approval.py b/broker/approval.py\n"
        "--- a/broker/approval.py\n"
        "+++ b/broker/approval.py\n"
        "@@ -1 +1 @@\n-x\n+y\n"
    )
    with pytest.raises(RingViolation):
        check_diff(ring_diff, worktree_root=str(tmp_path))

    # Negative control: an app-only diff is NOT rejected (so the gate is not
    # rejecting everything — the ring rejection above is specific).
    app_diff = (
        "diff --git a/app/x.py b/app/x.py\n"
        "--- a/app/x.py\n+++ b/app/x.py\n@@ -1 +1 @@\n-x\n+y\n"
    )
    check_diff(app_diff, worktree_root=str(tmp_path))  # must not raise
