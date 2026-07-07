# ABOUTME: P3-a task-splitter: spec_text → injection-scanned → AI worker (mocked at CLI
# ABOUTME: boundary) → schema-validated TaskGraph with per-node ambiguity from the PURE
# ABOUTME: rubric (never the AI's self-reported score). The AI call is one subprocess mock
# ABOUTME: point (_call_ai_worker); all graph/rubric logic runs real code, not mocks.
# ABOUTME: Design source: docs/plans/harness/fable/p3/P3-design.md §3.1 + §3.4.
"""
Task splitter — spec_text → TaskGraph | ParkedSpec.

Design authoritative source:
  docs/plans/harness/fable/p3/P3-design.md §3.1

Flow:
  1. Injection-scan spec_text; if findings → fence() before the AI sees it.
  2. Call _call_ai_worker with the (possibly fenced) spec prompt.
     _call_ai_worker is the sole AI boundary: it is patched in tests via
     patch("factory.task_splitter._call_ai_worker", ...).
  3. Parse + validate the AI response via task_graph.from_json().
     Malformed JSON / cycle / dangling depends_on → ParkedSpec (fail-closed).
  4. For each node: overwrite node.ambiguity with ambiguity_rubric.grade(node).
     The AI's self-reported ambiguity is advisory only (anti-gaming property).
  5. Set graph.max_ambiguity = max(node.ambiguity for all nodes).
  6. Return TaskGraph.  The spec-review gate (spec_review.py) decides park/hold/proceed.

The splitter does NOT enqueue — it returns a validated, scored TaskGraph.
The scheduler enqueues one job row per node after the gate clears.
"""

from __future__ import annotations

import json
from typing import Union

from factory.ambiguity_rubric import grade
from factory.injection_scan import fence, scan
from factory.task_graph import GraphValidationError, ParkedSpec, TaskGraph, from_json


def _call_ai_worker(prompt: str, *, repo: str, base_branch: str) -> str:
    """
    Invoke the AI subprocess to split a spec into a task graph JSON.

    Purpose: THE sole AI boundary in the splitter — every real AI call goes through
    here.  In tests this function is patched via
    ``unittest.mock.patch("factory.task_splitter._call_ai_worker", ...)``.
    In production it would launch ``claude -p --output-format json`` as a subprocess
    using the factory WorkerRunner infrastructure (not yet wired — P3-a scope).

    Usage:
        json_str = _call_ai_worker(prompt, repo=repo, base_branch=base_branch)

    Gotchas:
      * Returns the raw string output from the AI (may be malformed JSON); the
        caller must wrap json.loads + from_json in a try/except.
      * NOT called in any unit test — tests patch this function before calling
        split(), so the real subprocess is never spawned in the test suite.
    """
    # Production stub: real implementation will use WorkerRunner / subprocess.
    # This is unreachable in the test suite (always patched).
    raise NotImplementedError(  # pragma: no cover
        "_call_ai_worker must be patched in tests or wired to a real WorkerRunner "
        "for production use.  See factory/worker_runner.py for the launch pattern."
    )


def _build_prompt(fenced_spec: str, repo: str, base_branch: str) -> str:
    """
    Compose the prompt passed to the AI worker for task splitting.

    Purpose: one place that owns the prompt template so callers (and tests that
    capture the prompt) see a consistent marker.  The fenced spec is embedded as
    EXTERNAL CONTENT so the test can assert the fenced marker is present.

    Usage:
        prompt = _build_prompt(fenced_spec, repo="my-repo", base_branch="main")

    Gotchas:
      * fenced_spec already contains [EXTERNAL CONTENT …] tags when the spec was
        flagged by the injection scanner; clean specs are passed as-is.
      * The prompt instructs the model to output JSON ONLY — no prose wrapper —
        because from_json() expects the raw JSON object, not markdown-fenced output.
    """
    return (
        f"You are a senior engineer decomposing a spec into an ordered, "
        f"acceptance-test-first task graph for repo '{repo}' (base branch: {base_branch}).\n\n"
        f"Attack this spec for gaps (prd-review style), then emit an ordered task graph "
        f"as a single JSON object (no markdown fences, no prose) matching this schema:\n"
        f'{{"spec_hash": "<sha256>", "repo": "{repo}", "nodes": ['
        f'{{"id": "T1", "title": "...", "task_type": "<feature|bugfix|refactor|test|docs|other>", '
        f'"acceptance": ["...", "..."], "depends_on": [], "est_files": ["..."], "ambiguity": 0.0}}, '
        f"...], \"max_ambiguity\": 0.0}}\n\n"
        f"Rules:\n"
        f"  * One node per buildable unit of work.\n"
        f"  * depends_on lists ids of nodes that must finish before this one.\n"
        f"  * acceptance MUST have ≥1 falsifiable criterion per node.\n"
        f"  * task_type must be one of: feature, bugfix, refactor, test, docs, other.\n"
        f"  * Emit JSON ONLY — no prose, no markdown.\n\n"
        f"SPEC:\n{fenced_spec}"
    )


def split(
    spec_text: str,
    *,
    repo: str,
    base_branch: str,
) -> Union[TaskGraph, ParkedSpec]:
    """
    Split a spec into an ordered, validated TaskGraph with per-node ambiguity scores.

    Purpose: the public entry point for P3-a.  Orchestrates injection-scan, the AI
    call (mocked at the _call_ai_worker boundary in tests), graph validation, and the
    pure ambiguity rubric pass that overwrites any AI self-reported score.

    Usage:
        result = split(spec_text, repo="my-repo", base_branch="main")
        if isinstance(result, ParkedSpec):
            handle_park(result)
        else:
            handle_graph(result)   # result is a TaskGraph

    Gotchas:
      * NEVER raises — all error paths return ParkedSpec (fail-closed design).
      * The AI's ambiguity field on each node is overwritten by ambiguity_rubric.grade().
        This is the anti-gaming property: the rubric is always authoritative.
      * graph.max_ambiguity is set to max(node.ambiguity) after the rubric pass.
        The spec-review gate uses this for park/hold decisions.
      * Injection scanning runs BEFORE the AI sees the spec.  A flagged spec is
        fenced (not dropped) so the injection is visible but quarantined.
    """
    # --- Step 1: injection-scan spec_text; fence if findings ---
    findings = scan(spec_text)
    if findings:
        # Wrap the fenced content with an explicit FENCED marker so the prompt
        # carries a visible, assertable signal that the spec was quarantined.
        fenced_spec = f"[FENCED SPEC — injection signatures detected]\n{fence(spec_text, findings)}\n[/FENCED SPEC]"
    else:
        fenced_spec = spec_text

    # --- Step 2: build the prompt and call the AI worker ---
    prompt = _build_prompt(fenced_spec, repo=repo, base_branch=base_branch)
    try:
        raw_response = _call_ai_worker(prompt, repo=repo, base_branch=base_branch)
    except Exception as exc:
        return ParkedSpec(
            reason=f"AI worker call failed: {exc}",
            spec_text=spec_text,
        )

    # --- Step 3: parse + validate the AI response (fail-closed) ---
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        return ParkedSpec(
            reason=f"AI response is not valid JSON: {exc}",
            spec_text=spec_text,
        )

    try:
        graph: TaskGraph = from_json(payload)
    except GraphValidationError as exc:
        return ParkedSpec(
            reason=f"AI response failed graph validation: {exc}",
            spec_text=spec_text,
        )

    # --- Step 4: overwrite each node's ambiguity with the pure rubric score ---
    # The AI's self-reported ambiguity field is IGNORED here (anti-gaming property).
    for node in graph.nodes:
        node.ambiguity = grade(node)

    # --- Step 5: set graph.max_ambiguity ---
    graph.max_ambiguity = max(
        (node.ambiguity for node in graph.nodes), default=0.0
    )

    # --- Step 6: return the validated, scored TaskGraph ---
    return graph
