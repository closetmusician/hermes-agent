# ABOUTME: The factory worker runner (P2 design §2). Runs one one-shot claude -p /
# ABOUTME: codex exec job in a git worktree with a REPLACEMENT-scrubbed env (the
# ABOUTME: P0-1 fix), builds the least-privilege argv (no git push, no
# ABOUTME: --dangerously-skip-permissions, --strict-mcp-config), launches via the
# ABOUTME: Worker contract, and parses the schema'd JSON result. Workers never push.
"""
Factory worker runner — worktree-first, scrubbed-env, schema-validated.

Design authoritative source: docs/plans/harness/fable/p2/P2-design.md §2

Composition (does NOT modify worker/base.py or worker/local_subprocess.py):
  * ReplacementEnvWorker subclasses LocalSubprocessWorker and overrides ONLY the
    env-merge step of launch(), so the subprocess env == the scrubbed replacement
    dict (never ``{**os.environ, **spec.env}``). check/kill/collect_result are the
    parent's, unchanged — the whole point of extending via composition.
  * WorkerRunner orchestrates: git worktree add FIRST, build argv + scrubbed env,
    pre-launch egress assertion, launch, and (on collect) schema validation.

The security crux is the env: scrub_env() (factory/worker_env.py) returns a
COMPLETE replacement built from {}, and assert_no_egress() is a hard pre-launch
wall. A GITHUB_TOKEN in the supervisor's os.environ can never reach the worker.
"""

from __future__ import annotations

import logging
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from factory.job_schema import WorkerResultInvalid, parse_worker_output
from factory.worker_env import assert_no_egress, scrub_env
from worker.base import WorkerHandle, WorkerResult, WorkerSpec
from worker.local_subprocess import LocalSubprocessWorker

logger = logging.getLogger(__name__)

# Flags/tools that must NEVER appear in a worker argv (bypass-tested invariants).
# --dangerously-skip-permissions disables the acceptEdits gate (the worst escape);
# Bash(git push) would let the worker push (workers never push — the broker does).
_FORBIDDEN_ARGV_SUBSTRINGS = (
    "--dangerously-skip-permissions",
    "git push",
)

# Provider → the model *_API_KEY name the worker carries (inference-only).
_PROVIDER_MODEL_KEY = {
    "claude": "ANTHROPIC_API_KEY",
    "codex": "OPENAI_API_KEY",
}

# Default least-privilege tool allowlist for the claude worker. No shell-general
# Bash(...), no network tools, and crucially NO Bash(git push): the worker commits
# locally only; every push rides the broker git_push_executor.
_DEFAULT_CLAUDE_TOOLS = (
    "Edit",
    "Write",
    "Read",
    "Bash(git add)",
    "Bash(git commit)",
    "Bash(git status)",
    "Bash(git diff)",
)


class ReplacementEnvWorker(LocalSubprocessWorker):
    """
    A LocalSubprocessWorker whose launch uses REPLACEMENT env semantics.

    Purpose: close P0-1 without editing worker/local_subprocess.py. The parent's
    launch builds ``{**os.environ, **spec.env}`` (an overlay that can add but
    never remove inherited egress creds). This subclass overrides launch so the
    subprocess env is EXACTLY spec.env — the scrubbed replacement dict.
    Usage: internal to WorkerRunner; not a general-purpose worker.
    Gotchas: check/kill/collect_result are inherited unchanged; only the env
    construction differs. The process-group (setsid) behavior is identical.
    """

    def launch(self, spec: WorkerSpec) -> WorkerHandle:
        """
        Spawn the subprocess with spec.env as the COMPLETE environment.

        Purpose: identical to the parent launch except the env is a replacement
                 (env=spec.env), not an overlay on os.environ.
        Usage:   handle = ReplacementEnvWorker().launch(spec)
        Gotchas: spec.env must already be the full scrubbed env (PATH/HOME/... +
                 one model key). Passing a partial env here would starve the
                 worker of PATH — the runner always builds it via scrub_env.
        """
        output_path = Path(spec.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # REPLACEMENT env — the wall. os.environ is never folded in.
        replacement_env = dict(spec.env)

        output_fh = open(output_path, "wb")  # noqa: WPS515
        try:
            proc = subprocess.Popen(
                spec.cmd,
                cwd=spec.cwd,
                env=replacement_env,
                stdout=output_fh,
                stderr=output_fh,
                preexec_fn=os.setsid,  # child is a session + process-group leader
                close_fds=True,
            )
        except Exception:
            output_fh.close()
            raise
        output_fh.close()

        worker_id = str(uuid.uuid4())
        pgid = proc.pid
        try:
            actual_pgid = os.getpgid(proc.pid)
            if actual_pgid != proc.pid:
                logger.warning(
                    "worker %s: expected pgid=%d but got pgid=%d",
                    worker_id,
                    proc.pid,
                    actual_pgid,
                )
                pgid = actual_pgid
        except ProcessLookupError:
            pass

        handle = WorkerHandle(
            worker_id=worker_id,
            pid=proc.pid,
            pgid=pgid,
            output_path=str(output_path),
            worktree=spec.cwd,
        )
        handle._proc = proc  # type: ignore[attr-defined]
        handle._timeout_s = spec.timeout_s  # type: ignore[attr-defined]
        return handle


@dataclass
class WorkerRunSpec:
    """
    The runner's input for one worker invocation.

    Purpose: everything WorkerRunner needs to stand up one worktree job.
    Usage: WorkerRunSpec(job_id=..., repo=..., base_branch="main",
                         spec_text="add X", worker="claude", model="...",
                         budget_usd=2.0, timeout_min=20, slug="add-x")
    Gotchas: model_key is the broker-injected value (inference-only). It is set by
    the supervisor from the broker's resolve_model_key RPC (design §2.3A) and is
    never read from os.environ here.
    """

    job_id: str
    repo: str
    base_branch: str
    spec_text: str
    worker: str  # 'claude' | 'codex'
    model: str
    budget_usd: float
    timeout_min: int
    slug: str = "job"
    model_key: Optional[str] = None
    allowed_tools: Optional[List[str]] = None  # per-repo override; default least-priv
    extra_prompt: Optional[str] = None  # e.g. an approved stage-1 spec for stage-2


@dataclass
class WorkerLaunch:
    """
    The result of a launch: the live handle plus the recorded worktree metadata.

    Purpose: hand the supervisor the handle to poll AND the branch/worktree it must
    persist to the job row (design §2.2 step 1-3).
    Usage: launch = runner.launch(run_spec); persist launch.branch, launch.worktree_path.
    Gotchas: worktree_path/branch are recorded BEFORE the worker is launched, so
    even a launch that immediately dies leaves the supervisor a cleanup anchor.
    """

    handle: WorkerHandle
    branch: str
    worktree_path: str
    argv: List[str]
    env: Dict[str, str] = field(default_factory=dict)


def _run_git(
    args: List[str],
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> None:
    """
    Purpose: run a git subcommand, raising on failure with captured output.
    Usage: _run_git(["worktree", "add", "-b", branch, path, base], cwd=repo)
    Gotchas: uses the ambient git (SUPERVISOR context, which has repo access) — it
    is NOT the scrubbed worker. The worker's scrubbed env applies only to the
    claude/codex subprocess. env is an optional override for hermetic tests; None
    inherits the supervisor's real environment (its git config).
    """
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=dict(env) if env is not None else None,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (rc={proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )


def build_argv(run_spec: WorkerRunSpec) -> List[str]:
    """
    Build the least-privilege one-shot worker argv (design §2.2).

    Purpose: construct the claude -p / codex exec command with a JSON-schema output
    contract, an acceptEdits permission mode, a tool allowlist that EXCLUDES git
    push, --strict-mcp-config (no ambient MCP servers), and the per-job budget cap.
    Usage: argv = build_argv(run_spec)
    Gotchas:
      * NEVER emits --dangerously-skip-permissions (asserted absent downstream).
      * Bash(git push) is never in the allowlist — the tool-layer half of "workers
        never push" (the env half is no token).
      * --strict-mcp-config is always present so only an explicit/empty MCP config
        loads (blocks Gmail/Drive/arbitrary ambient tools).
    """
    if run_spec.worker == "claude":
        tools = run_spec.allowed_tools or list(_DEFAULT_CLAUDE_TOOLS)
        # Guard: a per-repo allowlist must not smuggle in a push tool.
        tools = [t for t in tools if "git push" not in t.replace("  ", " ")]
        argv = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            "acceptEdits",
            "--strict-mcp-config",
            "--allowedTools",
            " ".join(tools),
            "--model",
            run_spec.model,
            "--max-budget-usd",
            str(run_spec.budget_usd),
        ]
    elif run_spec.worker == "codex":
        argv = [
            "codex",
            "exec",
            "-s",
            "workspace-write",
            "-a",
            "never",
            "--json",
            "--model",
            run_spec.model,
        ]
    else:
        raise ValueError(f"unknown worker kind {run_spec.worker!r}")

    prompt = run_spec.spec_text
    if run_spec.extra_prompt:
        prompt = f"{run_spec.extra_prompt}\n\n{prompt}"
    argv.append(prompt)
    return argv


def assert_argv_safe(argv: List[str]) -> None:
    """
    Purpose: bypass-tested invariant — the constructed argv must contain no
    forbidden flag/tool (--dangerously-skip-permissions, any git push).
    Usage: assert_argv_safe(argv) before launch.
    Gotchas: raises ValueError if any forbidden substring appears in any token —
    this is the falsifiable grep the design §2.3 mandates.
    """
    joined = " ".join(argv)
    for forbidden in _FORBIDDEN_ARGV_SUBSTRINGS:
        if forbidden in joined:
            raise ValueError(
                f"worker argv contains forbidden token {forbidden!r}: {argv}"
            )


class WorkerRunner:
    """
    Orchestrates one factory worker run: worktree → scrub → launch → parse.

    Purpose: the plain-code (no AI) supervisor-side runner. It creates the git
    worktree FIRST, builds the least-privilege argv + the replacement-scrubbed env,
    asserts no egress key leaked, launches via ReplacementEnvWorker, and validates
    the worker's JSON result against the schema on collect.
    Usage:
        runner = WorkerRunner(repo_root="/path/to/repo",
                              worktrees_root="/path/to/worktrees")
        launch = runner.launch(run_spec)     # worktree created, worker running
        result = runner.collect(launch.handle, expected_branch=launch.branch)
    Gotchas: launch() records worktree_path/branch before spawning; collect()
    raises WorkerResultInvalid (caught by the supervisor → NEEDS_ATTENTION) on any
    non-conforming worker output.
    """

    def __init__(
        self,
        *,
        repo_root: str,
        worktrees_root: str,
        worker_impl: Optional[LocalSubprocessWorker] = None,
        git_env: Optional[Mapping[str, str]] = None,
    ) -> None:
        """
        Purpose: configure the repo + worktree layout and the worker backend.
        Usage: WorkerRunner(repo_root=..., worktrees_root=...)
        Gotchas: worker_impl defaults to ReplacementEnvWorker (the replacement-env
        launcher); tests may inject a stub that records the WorkerSpec instead of
        spawning a real subprocess (the SUBPROCESS BOUNDARY is the only mock).
        git_env overrides the env for the supervisor-side git worktree call
        (hermetic tests); None inherits the supervisor's real git environment.
        """
        self._repo_root = repo_root
        self._worktrees_root = worktrees_root
        self._worker = worker_impl or ReplacementEnvWorker()
        self._git_env = git_env

    def _branch_name(self, run_spec: WorkerRunSpec) -> str:
        """
        Purpose: the scoped factory branch name (design §2.2): factory/<repo>/<id>-<slug>.
        Usage: branch = self._branch_name(run_spec)
        Gotchas: the immutable-ring/scope checks rely on this exact prefix, so the
        repo slug is basename-only (no path separators leaking into the branch).
        """
        repo_slug = os.path.basename(run_spec.repo.rstrip("/")) or run_spec.repo
        return f"factory/{repo_slug}/{run_spec.job_id}-{run_spec.slug}"

    def build_worker_env(self, run_spec: WorkerRunSpec) -> Dict[str, str]:
        """
        Build + validate the scrubbed replacement env for this run (design §2.3).

        Purpose: produce the COMPLETE worker env via scrub_env (from {}), then run
        assert_no_egress on it — the falsifiable pre-launch wall. Returns the env
        the subprocess will see.
        Usage: env = runner.build_worker_env(run_spec)
        Gotchas: raises EgressKeyLeaked if any egress key or out-of-whitelist key is
        present — which is exactly what happens if scrub_env is ever weakened to
        inherit os.environ. model_key must be the broker-injected inference key.
        """
        model_key_name = _PROVIDER_MODEL_KEY.get(run_spec.worker)
        model_env: Optional[Mapping[str, str]] = None
        if run_spec.model_key and model_key_name:
            model_env = {model_key_name: run_spec.model_key}

        env = scrub_env(model_env=model_env, worker_cli=run_spec.worker)
        # The falsifiable wall — run on the EXACT dict about to be the subprocess env.
        assert_no_egress(env, model_key_name=model_key_name if model_env else None)
        return env

    def launch(self, run_spec: WorkerRunSpec) -> WorkerLaunch:
        """
        Create the worktree FIRST, then launch the scrubbed worker (design §2.2).

        Purpose: the ordered launch — worktree add BEFORE any worker work, argv +
        env built and asserted safe, then ReplacementEnvWorker.launch.
        Usage: launch = runner.launch(run_spec)
        Gotchas:
          * git worktree add runs before build_argv/launch (order is load-bearing;
            the supervisor records worktree_path/branch from the return).
          * argv safety + env egress assertions run BEFORE the subprocess spawns —
            a violation parks the job, no worker starts.
        """
        branch = self._branch_name(run_spec)
        worktree_path = os.path.join(self._worktrees_root, f"{run_spec.job_id}-{run_spec.slug}")

        # 1. WORKTREE FIRST — before argv, env, or any worker launch.
        _run_git(
            ["worktree", "add", "-b", branch, worktree_path, run_spec.base_branch],
            cwd=self._repo_root,
            env=self._git_env,
        )

        # 2. Build the least-privilege argv and assert it carries no forbidden token.
        argv = build_argv(run_spec)
        assert_argv_safe(argv)

        # 3. Build + validate the scrubbed replacement env (the P0-1 wall).
        env = self.build_worker_env(run_spec)

        # 4. Launch through the Worker contract with REPLACEMENT env semantics.
        output_path = os.path.join(worktree_path, ".factory", "worker.out")
        spec = WorkerSpec(
            cmd=argv,
            cwd=worktree_path,
            output_path=output_path,
            env=env,
            timeout_s=float(run_spec.timeout_min * 60),
        )
        handle = self._worker.launch(spec)
        return WorkerLaunch(
            handle=handle,
            branch=branch,
            worktree_path=worktree_path,
            argv=argv,
            env=env,
        )

    def collect(
        self,
        handle: WorkerHandle,
        *,
        expected_branch: Optional[str] = None,
    ) -> Dict[str, object]:
        """
        Collect the worker result and validate it against the schema (design §2.5).

        Purpose: read the worker output via the Worker contract, then parse+validate
        the JSON result. Non-conforming output raises WorkerResultInvalid (the
        supervisor maps that to NEEDS_ATTENTION — no crash).
        Usage: result = runner.collect(handle, expected_branch=launch.branch)
        Gotchas: expected_branch, when given, must match the worker's claimed
        branch — a worker claiming a different branch is not trusted.
        """
        worker_result: WorkerResult = self._worker.collect_result(handle)
        return parse_worker_output(worker_result.output, expected_branch=expected_branch)
