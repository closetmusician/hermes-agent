# ABOUTME: The P2 SUPERVISOR — the thin, plain-code (NO AI) driver that chains the
# ABOUTME: built P2 pieces into one end-to-end loop: intake → one job (sole writer) →
# ABOUTME: scrubbed worker in a worktree → gauntlet (test/review/ring/injection) →
# ABOUTME: held merge card on the broker approval surface → nonce approval → merge.
# ABOUTME: It CALLS the pieces; it never re-implements them. The AI lives in the worker.
"""
Supervisor — the plain-code control loop for "one worker, end to end" (design §1/§4).

This closes the STAGED positive half the verifier found (P2-verification §REQ-06):
the pieces were each proven, but no single process drove intake→gauntlet→merge with
one approval. The Supervisor is that process — plain code, no AI in the loop.

What it composes (public interfaces only — none of these are modified here):
  * factory.intake            — parse a /factory command or tasks.md tag → job-spec.
  * factory.job_store.JobStore — the SOLE-WRITER durable store + one-way state machine.
  * factory.two_stage         — kind:feature vs quick → spec-first or straight-to-implement.
  * factory.worker_runner     — worktree-first, scrubbed-env worker launch + schema parse.
  * factory.gauntlet.Gauntlet — test → review → ring/injection gate → AWAITING_APPROVAL.
  * factory.injection_scan    — chokepoint 1 (INPUT) fence-before-dispatch.
  * broker approval surface   — HeldStore + ApprovalAuthority + merge_executor: the merge
                                is a held action, nonce-approved, the ONLY human touch.

Design non-negotiables enforced here:
  * NO AI in this loop (the AI is inside the worker subprocess only).
  * The supervisor is the SOLE writer of the job store (all INSERT/transition here).
  * The merge rides the EXISTING broker approval machinery (a held 'merge' action);
    the supervisor holds no nonce and cannot self-approve — one human tap merges.
  * Workers never push; the broker merge executor is the only push path.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from broker.approval import ApprovalAuthority
from broker.held_store import HeldStore
from factory.gauntlet import Gauntlet, GauntletOutcome
from factory.injection_scan import fence, scan
from factory.intake import parse_telegram_command, parse_tasks_md_line
from factory.job_schema import WorkerResultInvalid
from factory.job_store import IllegalTransition, JobStore
from factory.two_stage import (
    Stage,
    persist_spec_artifact,
    plan_stages,
)
from factory.worker_runner import WorkerRunner, WorkerRunSpec

logger = logging.getLogger(__name__)

# The merge action's safe-lane record. Empty allowed_origins ⇒ SafeLane C4 fails ⇒
# every merge is HELD (design §4.2: P2 has no trust tiers yet — every merge held).
_MERGE_SAFE_LANE = json.dumps({"disposition": "held", "allowed_origins": []})


@dataclass
class IntakeItem:
    """
    One raw intake source for the supervisor to normalize into a job.

    Purpose: a uniform envelope over the two intake seams (Telegram /factory and a
    tasks.md factory: line) so run_until_approval / enqueue_from_intake take one type.
    Usage: IntakeItem(source="telegram", text="/factory repo: add X", kind="quick").
    Gotchas: kind overrides the parsed default when set — the caller chooses quick vs
    feature routing; source selects which intake parser runs.
    """

    source: str  # 'telegram' | 'tasks_md'
    text: str
    kind: Optional[str] = None
    base_branch: str = "main"
    worker: str = "claude"
    model: str = "claude-opus-4-5"
    budget_usd: float = 1.0
    timeout_min: int = 30
    model_key: Optional[str] = None


class Supervisor:
    """
    The plain-code driver that runs ONE factory job end to end (design §1/§4).

    Purpose: chain the built P2 pieces — intake, job store (sole writer), worker
    runner, two-stage router, gauntlet, and the broker merge approval — into a
    single loop with exactly one human touch (the merge nonce). No AI here.
    Usage:
        sup = Supervisor(store=..., repo_root=..., worktrees_root=...,
                         held_store=..., authority=..., merge_executor=...,
                         worker_impl=<worker CLI backend>)
        job_id = sup.run_until_approval(IntakeItem(...))   # → AWAITING_APPROVAL
        nonce = authority.mint_nonce(action_id)            # broker mints (Telegram)
        sup.approve_merge(job_id, action_id, nonce=nonce)  # → DONE
    Gotchas:
      * run_until_approval drives intake→gauntlet and enqueues the held merge card;
        it STOPS at AWAITING_APPROVAL (the single approval gate) or NEEDS_ATTENTION.
      * A ring/injection/gauntlet failure parks the job NEEDS_ATTENTION and NO merge
        card is enqueued — the gate is inside the loop, not decorative.
      * The supervisor NEVER pushes and holds no nonce; the merge executor (broker)
        is the only push path and one human tap is the only approval.
    """

    def __init__(
        self,
        *,
        store: JobStore,
        repo_root: str,
        worktrees_root: str,
        held_store: HeldStore,
        authority: ApprovalAuthority,
        merge_executor: Callable[[Dict[str, Any]], Dict[str, Any]],
        worker_impl: Any = None,
        git_env: Optional[Dict[str, str]] = None,
        test_verdict: Optional[Callable[[str], str]] = None,
        codex_review: Optional[Callable[[str, str], List[str]]] = None,
        fix_worker: Optional[Callable[[str, str, str], None]] = None,
    ) -> None:
        """
        Purpose: wire the (already-built) pieces the supervisor drives.
        Usage: see the class docstring.
        Gotchas: worker_impl is the worker CLI backend (a stub at the subprocess
        boundary in tests; the real ReplacementEnvWorker in production). merge_executor
        is the broker's real merge executor — approve_merge runs it via the nonce-gated
        ApprovalAuthority. test_verdict/codex_review are the gauntlet's injectable
        stage functions (real suite / codex CLI in production).
        """
        self._store = store
        self._repo_root = repo_root
        self._worktrees_root = worktrees_root
        self._held = held_store
        self._authority = authority
        self._merge_executor = merge_executor
        self._git_env = git_env

        self._runner = WorkerRunner(
            repo_root=repo_root,
            worktrees_root=worktrees_root,
            worker_impl=worker_impl,
            git_env=git_env,
        )
        self._gauntlet = Gauntlet(
            store,
            test_verdict=test_verdict,
            codex_review=codex_review,
            fix_worker=fix_worker,
        )

    # ------------------------------------------------------------------
    # Intake → exactly one job row (the supervisor is the SOLE writer).
    # ------------------------------------------------------------------

    def _normalize_intake(self, item: IntakeItem) -> Optional[Dict[str, Any]]:
        """
        Purpose: run the right intake parser and apply the item's overrides.
        Usage: spec = self._normalize_intake(item)
        Gotchas: returns None for a tasks.md line that is not a factory tag (not an
        error). INPUT injection scanning happens in enqueue_from_intake, not here.
        """
        defaults = {
            "base_branch": item.base_branch,
            "kind": item.kind or "feature",
            "worker": item.worker,
            "model": item.model,
            "budget_usd": item.budget_usd,
            "timeout_min": item.timeout_min,
        }
        if item.source == "telegram":
            spec = parse_telegram_command(item.text, defaults=defaults)
        elif item.source == "tasks_md":
            spec = parse_tasks_md_line(item.text, defaults=defaults)
        else:
            raise ValueError(f"unknown intake source {item.source!r}")
        if spec is None:
            return None
        if item.kind:
            spec["kind"] = item.kind
        return spec

    def enqueue_from_intake(self, item: IntakeItem) -> Optional[str]:
        """
        Normalize intake → fence any injected INPUT → write exactly one job row.

        Purpose: the SOLE-WRITER INSERT path (design §1.4/§1.6). Both intake seams
        converge here; the supervisor performs the single enqueue_job. An intake
        source already turned into a job (same intake_source_hash) is idempotent —
        a re-tick does not create a duplicate row.
        Usage: job_id = sup.enqueue_from_intake(item)
        Gotchas:
          * INPUT injection scan (chokepoint 1, design §3.1): the spec text is scanned
            and FENCED before it ever becomes a WorkerSpec — the worker never receives
            raw un-scanned supervisor-supplied external content.
          * returns None if the intake line is not a factory job (tasks.md non-tag).
        """
        spec = self._normalize_intake(item)
        if spec is None:
            return None

        # Chokepoint 1 (INPUT): scan + fence the spec before it enters a job/WorkerSpec.
        findings = scan(spec["spec"])
        if findings:
            spec["spec"] = fence(spec["spec"], findings)
            rules = ",".join(sorted({f.rule for f in findings}))
            logger.warning("intake injection flagged [%s]; fenced before dispatch", rules)

        # Idempotency guard — the supervisor never double-inserts one source.
        source_hash = spec.get("intake_source_hash")
        if source_hash and self._store.has_job_for_intake_hash(source_hash):
            existing = [
                j for j in self._store.list_jobs()
                if j.get("intake_source_hash") == source_hash
            ]
            return existing[0]["id"] if existing else None

        return self._store.enqueue_job(spec)

    # ------------------------------------------------------------------
    # The end-to-end driver (up to the single approval gate).
    # ------------------------------------------------------------------

    def run_until_approval(self, item: IntakeItem) -> str:
        """
        Drive one job intake → worker → gauntlet → held merge card.

        Purpose: the positive first-magic half — everything autonomous UP TO the
        single approval. Returns the job id; the job ends AWAITING_APPROVAL (with a
        held merge card enqueued) on the happy path, or NEEDS_ATTENTION on any gate
        failure (no merge card enqueued).
        Usage: job_id = sup.run_until_approval(item); then approve_merge(...).
        Gotchas: no AI runs here — plain sequencing of the built pieces. A worker
        that will not speak the result schema, a ring/injection hit, or a gauntlet
        failure all park the job NEEDS_ATTENTION without a merge card.
        """
        job_id = self.enqueue_from_intake(item)
        if job_id is None:
            raise ValueError("intake produced no job")

        row = self._store.get(job_id)
        plan = plan_stages(row["kind"])

        # Base run spec for the worker (shared across stages).
        base_run = WorkerRunSpec(
            job_id=job_id,
            repo=row["repo"],
            base_branch=row["base_branch"],
            spec_text=row["spec"],
            worker=row["worker"],
            model=row["model"],
            budget_usd=row["budget_usd"],
            timeout_min=row["timeout_min"],
            slug=self._slug(row["spec"]),
            model_key=item.model_key,
        )

        # Two-stage routing (design §2.4): feature → spec-first; quick → implement.
        approved_spec_text: Optional[str] = None
        if Stage.SPEC in plan.stages:
            approved_spec_text = self._run_spec_stage(job_id, base_run)

        # Implement stage — the worker builds the feature in its worktree.
        try:
            launch, result = self._run_implement_stage(
                job_id, base_run, approved_spec_text
            )
        except WorkerResultInvalid as exc:
            self._park(job_id, "RUNNING", f"worker result invalid: {exc}")
            return job_id

        # Gauntlet: test → review → ring/injection gate → AWAITING_APPROVAL.
        gres = self._gauntlet.run(
            job_id,
            worktree=launch.worktree_path,
            base=row["base_branch"],
            branch=launch.branch,
        )
        if gres.outcome != GauntletOutcome.CLEARED:
            return job_id  # gauntlet already parked NEEDS_ATTENTION; no merge card

        # Cleared → build + enqueue the held merge card (the single approval gate).
        self._enqueue_merge_card(
            job_id,
            launch,
            row,
            test_result=gres.test_result or "pass",
            review_findings=gres.review_findings,
        )
        return job_id

    def _run_spec_stage(self, job_id: str, base_run: WorkerRunSpec) -> str:
        """
        Purpose: Stage-1 (feature only) — run the spec-draft worker, persist the
        schema'd stage-1 spec artifact BEFORE the implement stage may run (§2.4).
        Usage: approved = self._run_spec_stage(job_id, base_run)
        Gotchas: this uses a SEPARATE worktree/branch (spec_run_spec suffixes '-spec')
        so the spec draft does not pollute the implement worktree. The persisted
        artifact is the reviewable record; a malformed draft parks the job.
        """
        from factory.two_stage import spec_run_spec

        s1 = spec_run_spec(base_run)
        launch = self._runner.launch(s1)
        worker_result = self._runner._worker.collect_result(launch.handle)
        # Persist + validate the stage-1 spec (fail-closed on malformed output).
        path = persist_spec_artifact(self._worktrees_root, job_id, worker_result.output)
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    def _run_implement_stage(self, job_id: str, base_run: WorkerRunSpec,
                             approved_spec_text: Optional[str]):
        """
        Purpose: launch the implement worker (worktree-first, scrubbed env) and
        collect + schema-validate its result; move the job QUEUED→RUNNING.
        Usage: launch, result = self._run_implement_stage(job_id, base_run, spec)
        Gotchas: raises WorkerResultInvalid (caught by the caller → NEEDS_ATTENTION)
        on non-conforming output. The QUEUED→RUNNING transition is recorded with the
        worktree/branch/pgid so the integrity check can reconcile a dead worker.
        """
        from factory.two_stage import implement_run_spec

        impl = implement_run_spec(base_run, approved_spec_text=approved_spec_text)
        launch = self._runner.launch(impl)
        # Record worktree/branch/pgid on the row and move to RUNNING.
        self._store.transition(
            job_id, "QUEUED", "RUNNING",
            extra={
                "worktree_path": launch.worktree_path,
                "branch": launch.branch,
                "pgid": launch.handle.pgid,
            },
        )
        result = self._runner.collect(launch.handle, expected_branch=launch.branch)
        return launch, result

    def _prepare_merge_worktree(self, launch, base: str) -> str:
        """
        Create a dedicated, checkout-capable clone for the merge executor (§4.2).

        Purpose: give the broker merge executor a git checkout where it CAN
        ``checkout base`` (the executor does this internally before ``merge --ff-only``).
        A git LINKED worktree — which is what the worker ran in — cannot check out
        ``base`` when ``base`` is already checked out in the primary tree ("'main' is
        already used by worktree at ..."), so pointing the executor at the worker's
        worktree makes its merge a silent no-op. This clone is where base is free.
        Usage: merge_wt = self._prepare_merge_worktree(launch)
        Gotchas:
          * INTERFACE FINDING (design REQ-01 escalate_if): the merge executor assumes
            its ``worktree`` can ``checkout base``. It cannot for a linked worktree.
            Rather than edit the executor (it is a wired piece), the supervisor
            prepares a clone that satisfies the assumption. Documented in the report.
          * The job branch's commits transfer via a LOCAL clone of the worker's
            worktree + a local fetch of the branch — no worker/supervisor NETWORK push
            (the M2 no-egress-token invariant holds; the executor's own push to the
            remote is the only network egress, and it runs in the broker).
          * origin is repointed to the real remote so the executor's fetch+push
            target the true remote, not the intermediate clone.
        """
        worker_worktree = launch.worktree_path
        branch = launch.branch
        merge_wt = os.path.join(
            self._worktrees_root, "merge", os.path.basename(worker_worktree)
        )
        os.makedirs(os.path.dirname(merge_wt), exist_ok=True)

        # 1. Resolve the real remote URL from the worker's worktree (its origin).
        remote_url = self._git_out(
            ["config", "--get", "remote.origin.url"], cwd=worker_worktree
        ).strip()

        # 2. Clone the worker's worktree locally. The worktree's HEAD is the job
        #    branch, so the clone lands with that branch checked out AND base fetched
        #    as a remote-tracking ref — no worker/supervisor network push involved.
        self._git(["clone", "--branch", branch, worker_worktree, merge_wt])
        # 3. Materialize a LOCAL base branch, so the executor's ``rebase base`` /
        #    ``checkout base`` resolve it (the clone has base only as origin/<base>).
        self._git(["branch", "--force", base, f"origin/{base}"], cwd=merge_wt)
        # 4. Repoint origin at the TRUE remote so the executor's fetch+push are real.
        if remote_url:
            self._git(["remote", "set-url", "origin", remote_url], cwd=merge_wt)
        return merge_wt

    def _enqueue_merge_card(self, job_id: str, launch, row: Dict[str, Any], *,
                            test_result: str, review_findings: List[str]) -> str:
        """
        Build the merge card payload and enqueue it as a HELD broker action (§4.2).

        Purpose: the single approval gate — the merge rides the existing broker
        approval surface (HeldStore held action). The payload is the merge_executor's
        contract: {repo, branch, base, worktree, remote}. Review findings ride the
        card (design §4.1 — the owner sees them on approval).
        Usage: action_id = self._enqueue_merge_card(job_id, launch, row, ...)
        Gotchas:
          * the supervisor does NOT approve — it only enqueues held. It holds no
            nonce; the merge cannot execute until a human taps approve (approve_merge).
          * the card's ``worktree`` is a DEDICATED merge checkout (not the worker's
            linked worktree). The merge executor does ``checkout base`` internally,
            which git REFUSES in a linked worktree whose base is checked out in the
            primary tree (interface finding, see _prepare_merge_worktree). The
            dedicated clone is where base IS checkable out — the executor is unmodified.
        """
        merge_worktree = self._prepare_merge_worktree(launch, row["base_branch"])
        card = {
            "repo": row["repo"],
            "branch": launch.branch,
            "base": row["base_branch"],
            "worktree": merge_worktree,
            "remote": "origin",
            "summary": f"factory job {job_id}: {row['spec'][:80]}",
            "test_result": test_result,
            "review_findings": review_findings,
        }
        action_id = self._held.enqueue(
            type="merge",
            summary=card["summary"],
            payload=json.dumps(card),
            origin=f"factory:{job_id}",
            safe_lane_json=_MERGE_SAFE_LANE,
            state="held",
        )
        # The job is already in AWAITING_APPROVAL (the gauntlet moved it there); the
        # held merge card is now enqueued awaiting the single nonce approval.
        return action_id

    # ------------------------------------------------------------------
    # The single human touch: nonce-gated merge approval.
    # ------------------------------------------------------------------

    def approve_merge(self, job_id: str, action_id: str, *, nonce: Optional[str]) -> Dict[str, Any]:
        """
        Approve the held merge with a broker-minted nonce → run the merge → DONE.

        Purpose: the ONLY human touch in the happy path. The nonce-gated
        ApprovalAuthority runs the real broker merge executor (rebase → re-test →
        merge → push under a per-repo lock). On a merged result the job → DONE; a
        conflict / re-test fail from the executor → NEEDS_ATTENTION (never forced).
        Usage: sup.approve_merge(job_id, action_id, nonce=authority.mint_nonce(action_id)).
        Gotchas:
          * WITHOUT a valid nonce, ApprovalAuthority.approve raises ApprovalRejected —
            the merge never runs and the remote never moves (the self-approval wall).
          * The supervisor passes the merge executor to the authority; it does not
            merge itself and holds no nonce.
        """
        # The nonce wall lives in ApprovalAuthority.approve — an invalid/missing nonce
        # raises ApprovalRejected here, before the executor ever runs.
        result = self._authority.approve(
            action_id, nonce, executor=self._merge_executor
        )

        if result.get("status") == "merged":
            self._store.transition(job_id, "AWAITING_APPROVAL", "MERGING")
            self._store.transition(job_id, "MERGING", "DONE")
        else:
            # Conflict / re-test fail / push fail — never forced; park for a human.
            reason = result.get("reason") or result.get("status") or "merge_failed"
            self._park(job_id, "AWAITING_APPROVAL", f"merge: {reason}")
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _park(self, job_id: str, expected: str, reason: str) -> None:
        """
        Purpose: park a job to NEEDS_ATTENTION with a fail reason, tolerating a row
        that already moved (defensive — never crash the driver).
        Usage: self._park(job_id, "RUNNING", "worker result invalid: ...")
        Gotchas: expected is the current state the caller believes the job is in; an
        IllegalTransition (already moved) is logged, not raised.
        """
        try:
            self._store.transition(job_id, expected, "NEEDS_ATTENTION", fail_reason=reason)
        except IllegalTransition:
            logger.warning("supervisor: could not park job %s from %s (already moved)",
                           job_id, expected)

    def _git(self, args: List[str], *, cwd: Optional[str] = None) -> None:
        """
        Purpose: run a supervisor-side git subcommand (worktree/clone prep), raising
        on failure. This is the SUPERVISOR's git (repo access), never the scrubbed
        worker — it prepares the merge checkout, it does not do network egress.
        Usage: self._git(["clone", src, dst])
        Gotchas: uses the configured git_env (hermetic in tests); None inherits the
        supervisor's real git config. Never passes a push token — the only network
        push is the broker merge executor's, not this.
        """
        import subprocess

        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            env=dict(self._git_env) if self._git_env is not None else None,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed (rc={proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )

    def _git_out(self, args: List[str], *, cwd: Optional[str] = None) -> str:
        """
        Purpose: run a supervisor-side git subcommand and return its stdout.
        Usage: url = self._git_out(["config", "--get", "remote.origin.url"], cwd=wt)
        Gotchas: returns "" on a non-zero exit (e.g. an unset config key) rather than
        raising — the caller treats an empty result as "not set".
        """
        import subprocess

        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            env=dict(self._git_env) if self._git_env is not None else None,
        )
        return proc.stdout if proc.returncode == 0 else ""

    @staticmethod
    def _slug(spec_text: str) -> str:
        """
        Purpose: a short, filesystem/branch-safe slug from the spec text.
        Usage: slug = Supervisor._slug(row["spec"])
        Gotchas: lowercased, non-alnum → '-', trimmed to a few words so the branch
        name stays short; empty spec falls back to 'job'.
        """
        import re

        words = re.sub(r"[^a-z0-9]+", "-", (spec_text or "").lower()).strip("-")
        slug = "-".join(words.split("-")[:4])
        return slug or "job"
