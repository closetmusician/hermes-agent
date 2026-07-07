# ABOUTME: RED-first tests for the factory worker runner + env scrub + result schema
# ABOUTME: (P2-c). The headline test is the P0-1 replacement-env wall: a GITHUB_TOKEN
# ABOUTME: seeded in the supervisor os.environ must be ABSENT from the constructed
# ABOUTME: worker env. Only the SUBPROCESS BOUNDARY is stubbed (a Worker that records
# ABOUTME: the spec); the scrub / worktree / argv / schema logic runs for real.
"""
Tests for factory.worker_env, factory.job_schema, factory.worker_runner.

Design source: docs/plans/harness/fable/p2/P2-design.md §2 + §5 (P2-c RED tests).

The subprocess boundary (claude -p / codex exec) is a legitimate third-party
process; a RecordingWorker stub stands in for it so we never spawn a real model
call in a unit test. Everything else — the env replacement wall, the worktree-
first ordering, the argv safety grep, the JSON schema — is exercised against real
plain code, so none of these tests validate mocked behaviour.
"""

import json
import os
import subprocess

import pytest

from factory.job_schema import (
    WorkerResultInvalid,
    parse_worker_output,
    validate_worker_result,
)
from factory.worker_env import (
    EgressKeyLeaked,
    allowed_keys,
    assert_no_egress,
    is_egress_name,
    scrub_env,
)
from factory.worker_runner import (
    WorkerRunner,
    WorkerRunSpec,
    assert_argv_safe,
    build_argv,
)
from worker.base import WorkerHandle, WorkerResult, WorkerSpec
from worker.local_subprocess import LocalSubprocessWorker


# ---------------------------------------------------------------------------
# Subprocess-boundary stub — records the WorkerSpec instead of spawning claude.
# ---------------------------------------------------------------------------
class RecordingWorker(LocalSubprocessWorker):
    """A Worker that records the launched spec and returns a canned result."""

    def __init__(self, canned_output=""):
        super().__init__()
        self.launched_spec = None
        self._canned_output = canned_output

    def launch(self, spec: WorkerSpec) -> WorkerHandle:
        self.launched_spec = spec
        return WorkerHandle(
            worker_id="rec-1",
            pid=0,
            pgid=0,
            output_path=spec.output_path,
            worktree=spec.cwd,
        )

    def collect_result(self, handle: WorkerHandle) -> WorkerResult:
        return WorkerResult(returncode=0, output=self._canned_output, timed_out=False)


# Hermetic git env: ignore ambient global/system config (which may be absent or
# reference unreachable helpers under the test runner's env -i) and pin identity.
_GIT_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", "/tmp"),
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(args, **kw):
    """Run git with the hermetic env; raise on failure with captured stderr."""
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, env=_GIT_ENV, **kw
    )


def _init_git_repo(path):
    """Create a real git repo with one commit on main, for worktree tests."""
    # --template= (empty) skips copying git-core template hooks — avoids a
    # sandbox "Operation not permitted" on the hooks copy and keeps the repo clean.
    _git(["init", "-b", "main", "--template=", str(path)])
    (path / "README.md").write_text("hi\n")
    _git(["-C", str(path), "add", "README.md"])
    _git(["-C", str(path), "commit", "-m", "init"])


def _run_spec(tmp_path, **over):
    base = dict(
        job_id="job1",
        repo="acme",
        base_branch="main",
        spec_text="add a widget",
        worker="claude",
        model="claude-x",
        budget_usd=2.0,
        timeout_min=20,
        slug="add-widget",
        model_key="sk-ant-fake",
    )
    base.update(over)
    return WorkerRunSpec(**base)


# ===========================================================================
# P2-c #3 (★ headline): the P0-1 replacement-env wall.
# ===========================================================================
def test_worker_env_lacks_seeded_egress_tokens(tmp_path, monkeypatch):
    """Seed GITHUB_TOKEN + TELEGRAM_BOT_TOKEN in supervisor env → worker env has neither."""
    # Simulate a DEFAULT (non-starved) host: creds live in the supervisor os.environ.
    monkeypatch.setenv("GITHUB_TOKEN", "leak-me")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "leak2")

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    runner = WorkerRunner(repo_root=str(repo), worktrees_root=str(tmp_path / "wt"))

    env = runner.build_worker_env(_run_spec(tmp_path))

    # (a) neither egress key present
    assert "GITHUB_TOKEN" not in env
    assert "TELEGRAM_BOT_TOKEN" not in env
    # (b) whitelist subset
    assert set(env).issubset(allowed_keys("ANTHROPIC_API_KEY"))
    # (c) exactly one model key, *_API_KEY, not egress
    model_keys = [k for k in env if k.endswith("_API_KEY")]
    assert model_keys == ["ANTHROPIC_API_KEY"]
    assert not is_egress_name(model_keys[0])
    # (d) HOME is a temp HOME, not the real one
    assert env["HOME"] != os.path.expanduser("~")


def test_scrub_env_never_inherits_os_environ(monkeypatch):
    """scrub_env built from {} — an arbitrary inherited var never appears."""
    monkeypatch.setenv("SOME_RANDOM_INHERITED", "x")
    monkeypatch.setenv("GH_TOKEN", "leak")
    env = scrub_env(model_env={"ANTHROPIC_API_KEY": "k"}, worker_cli="claude")
    assert "SOME_RANDOM_INHERITED" not in env
    assert "GH_TOKEN" not in env


# ===========================================================================
# P2-c #4 (★): assert_no_egress is the whitelist wall (stronger than denylist).
# ===========================================================================
def test_assert_no_egress_raises_on_denylisted_key():
    bad = {"PATH": "/bin", "HOME": "/tmp/h", "GITHUB_TOKEN": "x"}
    with pytest.raises(EgressKeyLeaked):
        assert_no_egress(bad)


def test_assert_no_egress_raises_on_out_of_whitelist_key():
    """A novel, non-denylisted key still fails the positive whitelist guard."""
    bad = {"PATH": "/bin", "HOME": "/tmp/h", "NOVEL_UNKNOWN_VAR": "x"}
    with pytest.raises(EgressKeyLeaked):
        assert_no_egress(bad)


def test_assert_no_egress_allows_clean_env():
    good = {
        "PATH": "/bin",
        "HOME": "/tmp/h",
        "TMPDIR": "/tmp/h/t",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "ANTHROPIC_API_KEY": "k",
    }
    assert_no_egress(good, model_key_name="ANTHROPIC_API_KEY")  # no raise


def test_scrub_env_rejects_egress_model_key():
    """A mis-shaped model_env carrying an egress name fails closed at construction."""
    with pytest.raises(EgressKeyLeaked):
        scrub_env(model_env={"GITHUB_TOKEN": "x"})


def test_scrub_env_rejects_multi_key_model_env():
    with pytest.raises(EgressKeyLeaked):
        scrub_env(model_env={"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "b"})


# ===========================================================================
# P2-c #2 (★): argv has no --dangerously-skip-permissions and no git push.
# ===========================================================================
def test_argv_excludes_dangerous_flag_and_push():
    argv = build_argv(_run_spec(None))
    joined = " ".join(argv)
    assert "--dangerously-skip-permissions" not in joined
    assert "git push" not in joined
    assert_argv_safe(argv)  # no raise


def test_argv_allowlist_strips_smuggled_push_tool():
    argv = build_argv(_run_spec(None, allowed_tools=["Edit", "Bash(git push)", "Read"]))
    assert "git push" not in " ".join(argv)


def test_assert_argv_safe_rejects_dangerous_flag():
    with pytest.raises(ValueError):
        assert_argv_safe(["claude", "-p", "--dangerously-skip-permissions"])


# ===========================================================================
# P2-c #5: --strict-mcp-config present.
# ===========================================================================
def test_argv_has_strict_mcp_config():
    argv = build_argv(_run_spec(None))
    assert "--strict-mcp-config" in argv


# ===========================================================================
# P2-c #1 (★): worktree created BEFORE launch; branch/worktree recorded.
# ===========================================================================
def test_worktree_created_before_launch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    rec = RecordingWorker()
    runner = WorkerRunner(
        repo_root=str(repo), worktrees_root=str(tmp_path / "wt"), worker_impl=rec, git_env=_GIT_ENV
    )

    launch = runner.launch(_run_spec(tmp_path))

    # The worktree directory exists (git worktree add ran) and matches the record.
    assert os.path.isdir(launch.worktree_path)
    assert launch.branch == "factory/acme/job1-add-widget"
    # The stub was launched INTO that worktree (proves order: worktree then launch).
    assert rec.launched_spec is not None
    assert rec.launched_spec.cwd == launch.worktree_path
    # The launched env is the scrubbed replacement (no egress), asserted for real.
    assert "GITHUB_TOKEN" not in rec.launched_spec.env
    assert set(rec.launched_spec.env).issubset(allowed_keys("ANTHROPIC_API_KEY"))
    # A real branch was created in the repo.
    branches = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", launch.branch],
        capture_output=True, text=True, env=_GIT_ENV,
    ).stdout
    assert "factory/acme/job1-add-widget" in branches


def test_worker_push_attempt_fails_no_token_no_tool(tmp_path):
    """A worker in its worktree cannot push: no egress token in env, no push tool."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    rec = RecordingWorker()
    runner = WorkerRunner(
        repo_root=str(repo), worktrees_root=str(tmp_path / "wt"), worker_impl=rec, git_env=_GIT_ENV
    )
    launch = runner.launch(_run_spec(tmp_path))

    # No push tool in argv, and no *_TOKEN in the worker env.
    assert "git push" not in " ".join(launch.argv)
    assert not any(k.endswith("_TOKEN") for k in launch.env)
    # A real git push from the worktree fails (no remote configured, no creds).
    proc = subprocess.run(
        ["git", "-C", launch.worktree_path, "push", "origin", "HEAD"],
        capture_output=True, text=True, env=launch.env,
    )
    assert proc.returncode != 0


# ===========================================================================
# P2-c #6/#7: schema validation catches malformed output; well-formed parses.
# ===========================================================================
def test_wellformed_result_parses():
    raw = json.dumps(
        {
            "status": "completed",
            "branch": "factory/acme/job1-add-widget",
            "summary": "did it",
            "test_result": "pass",
        }
    )
    obj = parse_worker_output(raw, expected_branch="factory/acme/job1-add-widget")
    assert obj["status"] == "completed"
    assert obj["test_result"] == "pass"


def test_result_embedded_in_log_noise_parses():
    raw = "progress: starting\nsome log line\n" + json.dumps(
        {"status": "completed", "branch": "factory/x", "summary": "s", "test_result": "pass"}
    ) + "\ndone\n"
    obj = parse_worker_output(raw)
    assert obj["branch"] == "factory/x"


def test_malformed_output_raises():
    with pytest.raises(WorkerResultInvalid):
        parse_worker_output("this is not json at all")


def test_missing_required_field_raises():
    raw = json.dumps({"status": "completed", "branch": "factory/x", "summary": "s"})
    with pytest.raises(WorkerResultInvalid):
        parse_worker_output(raw)


def test_bad_branch_pattern_raises():
    raw = json.dumps(
        {"status": "completed", "branch": "main", "summary": "s", "test_result": "pass"}
    )
    with pytest.raises(WorkerResultInvalid):
        parse_worker_output(raw)


def test_branch_mismatch_raises():
    raw = json.dumps(
        {"status": "completed", "branch": "factory/other", "summary": "s", "test_result": "pass"}
    )
    with pytest.raises(WorkerResultInvalid):
        parse_worker_output(raw, expected_branch="factory/acme/job1-add-widget")


def test_collect_maps_bad_output_to_error(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    rec = RecordingWorker(canned_output="garbage not json")
    runner = WorkerRunner(
        repo_root=str(repo), worktrees_root=str(tmp_path / "wt"), worker_impl=rec, git_env=_GIT_ENV
    )
    launch = runner.launch(_run_spec(tmp_path))
    with pytest.raises(WorkerResultInvalid):
        runner.collect(launch.handle, expected_branch=launch.branch)


# ===========================================================================
# P3-0: DATA-RESIDENCY WALL (design §6.2 / §13.1). The residency guard is WIRED
# into build_worker_env — an OpenRouter key CANNOT be injected for a work/
# allow_openrouter:false job even if _PROVIDER_MODEL_KEY later maps openrouter.
# These run against the REAL build_worker_env seam (no git worktree needed, so
# they execute in the sandbox where the git-init tests cannot).
# ===========================================================================
from unittest import mock  # noqa: E402

from factory.merge_policy import MergePolicy, default_policy  # noqa: E402
from factory.residency_guard import (  # noqa: E402
    ResidencyViolation,
    assert_openrouter_allowed,
)


def _runner_no_git():
    """A WorkerRunner whose build_worker_env we call directly (no worktree)."""
    return WorkerRunner(repo_root="/nonexistent", worktrees_root="/nonexistent")


# --- REQ-01: the guard in isolation (denied / allowed / fail-closed) ----------
def test_guard_denies_work_openrouter():
    """work/Diligent + allow_openrouter:false + openrouter provider → denied."""
    policy = default_policy("diligent-platform")  # allow_openrouter defaults False
    with pytest.raises(ResidencyViolation):
        assert_openrouter_allowed("diligent-platform", "openrouter", policy)


def test_guard_allows_personal_openrouter_when_opted_in():
    """personal + allow_openrouter:true + openrouter → allowed (no raise)."""
    policy = MergePolicy(repo="myproj", allow_openrouter=True)
    assert_openrouter_allowed("myproj", "openrouter", policy)  # no raise


def test_guard_fails_closed_on_unknown_policy():
    """policy=None (unknown/unmapped residency) + openrouter → denied."""
    with pytest.raises(ResidencyViolation):
        assert_openrouter_allowed("mystery-repo", "openrouter", None)


def test_guard_ignores_first_party_workers():
    """claude/codex are first-party — never gated, even with openrouter off."""
    policy = default_policy("diligent-platform")  # allow_openrouter False
    assert_openrouter_allowed("diligent-platform", "claude", policy)  # no raise
    assert_openrouter_allowed("diligent-platform", "codex", policy)  # no raise


# --- REQ-02/03: the WIRING — build_worker_env CALLS the guard at the seam -----
def test_build_worker_env_calls_residency_guard():
    """
    The CALL proof (design §13.1, review P0-1): build_worker_env must invoke
    assert_openrouter_allowed with the run_spec's repo/worker/policy. Spy on the
    guard as imported by worker_runner and assert it was called.
    """
    runner = _runner_no_git()
    spec = _run_spec(
        None,
        repo="diligent-platform",
        worker="claude",
        model_key="sk-ant-fake",
        policy=default_policy("diligent-platform"),
    )
    with mock.patch(
        "factory.worker_runner.assert_openrouter_allowed"
    ) as spy:
        runner.build_worker_env(spec)
    spy.assert_called_once()
    call = spy.call_args
    assert call.args[0] == "diligent-platform"  # repo
    assert call.args[1] == "claude"  # worker
    assert isinstance(call.args[2], MergePolicy)  # policy


def test_work_openrouter_job_gets_no_key_at_the_seam(monkeypatch):
    """
    The OUTCOME proof (design §13.1, not theater): even if _PROVIDER_MODEL_KEY is
    made to map openrouter, a work/allow_openrouter:false job requesting openrouter
    raises ResidencyViolation from build_worker_env BEFORE any key is placed — so
    no env with an OpenRouter key can ever be returned.
    """
    # Stub the provider→key map to DO carry openrouter (the P4 future state).
    monkeypatch.setattr(
        "factory.worker_runner._PROVIDER_MODEL_KEY",
        {"claude": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
    )
    runner = _runner_no_git()
    spec = _run_spec(
        None,
        repo="diligent-platform",
        worker="openrouter",
        model_key="sk-or-fake",
        policy=default_policy("diligent-platform"),  # allow_openrouter False
    )
    with pytest.raises(ResidencyViolation):
        runner.build_worker_env(spec)


def test_work_env_defaults_to_failclosed_policy(monkeypatch):
    """
    A WorkerRunSpec with NO policy set (default None) + openrouter worker → the
    seam treats unknown residency as allow_openrouter:false and refuses the key.
    """
    monkeypatch.setattr(
        "factory.worker_runner._PROVIDER_MODEL_KEY",
        {"claude": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
    )
    runner = _runner_no_git()
    spec = _run_spec(None, worker="openrouter", model_key="sk-or-fake")  # policy=None
    with pytest.raises(ResidencyViolation):
        runner.build_worker_env(spec)


def test_personal_openrouter_admitted_at_seam(monkeypatch):
    """
    Positive path: personal repo with allow_openrouter:true + openrouter worker
    builds a clean env carrying the OpenRouter key (the guard does not over-block).
    """
    monkeypatch.setattr(
        "factory.worker_runner._PROVIDER_MODEL_KEY",
        {"claude": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
    )
    runner = _runner_no_git()
    spec = _run_spec(
        None,
        repo="myproj",
        worker="openrouter",
        model_key="sk-or-fake",
        policy=MergePolicy(repo="myproj", allow_openrouter=True),
    )
    env = runner.build_worker_env(spec)
    assert env["OPENROUTER_API_KEY"] == "sk-or-fake"


# --- REQ-04: anti-weakening — removing the guard call must break a test -------
def test_antiweakening_guard_call_is_load_bearing(monkeypatch):
    """
    Neutralize the guard (make it a no-op) and stub in an openrouter key mapping:
    build_worker_env would then wrongly build a work-repo OpenRouter env. This
    test asserts that WITH the guard active the work job is refused — so if the
    guard call is ever deleted from build_worker_env, test_work_openrouter_job_
    gets_no_key_at_the_seam flips to returning a key and FAILS. This test proves
    the guard, not its bypass, is what blocks the key.
    """
    monkeypatch.setattr(
        "factory.worker_runner._PROVIDER_MODEL_KEY",
        {"claude": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"},
    )
    runner = _runner_no_git()
    spec = _run_spec(
        None,
        repo="diligent-platform",
        worker="openrouter",
        model_key="sk-or-fake",
        policy=default_policy("diligent-platform"),
    )
    # Guard active → refused (the load-bearing behavior).
    with pytest.raises(ResidencyViolation):
        runner.build_worker_env(spec)
    # Guard neutralized → a key WOULD be injected (documents what the guard prevents).
    monkeypatch.setattr("factory.worker_runner.assert_openrouter_allowed", lambda *a, **k: None)
    env = runner.build_worker_env(spec)
    assert env["OPENROUTER_API_KEY"] == "sk-or-fake"
