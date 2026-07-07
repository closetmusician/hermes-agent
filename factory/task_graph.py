# ABOUTME: TaskGraph schema + validator for the P3-a task-splitter output contract.
# ABOUTME: Defines TaskNode + TaskGraph dataclasses and the fail-closed validate()
# ABOUTME: function (Kahn's topological sort + dangling-edge + task_type checks).
# ABOUTME: from_json() is the entry point for AI-output parsing; malformed input
# ABOUTME: raises GraphValidationError so the splitter returns ParkedSpec, not a crash.
"""
Task graph schema and validation.

Design authoritative source:
  docs/plans/harness/fable/p3/P3-design.md §3.2 (TaskNode, TaskGraph)

The contract the AI output must satisfy before any scheduler logic sees it:
  * Every depends_on id resolves to a real node (no dangling edges).
  * The depends_on relation is acyclic (Kahn's algorithm — cycle → rejection).
  * task_type is in VALID_TASK_TYPES (GRADUATABLE_TASK_TYPES ∪ {"other"}).
  * Structural fields (id, title, acceptance, etc.) are present and typed.

Missing acceptance criteria are NOT a hard validation error — they are an
ambiguity signal for the rubric (§3.3). A node with acceptance=[] is valid at
the graph level but will score high in ambiguity_rubric.grade().

``validate()`` is the single fail-closed gate. A malformed result raises
``GraphValidationError``; the splitter catches that and returns a ``ParkedSpec``
so the scheduler never receives an invalid graph.
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from factory.trust_ledger import GRADUATABLE_TASK_TYPES

# Allowed task_type values: the graduatable set plus the safe catch-all.
VALID_TASK_TYPES: frozenset = GRADUATABLE_TASK_TYPES | frozenset({"other"})


class GraphValidationError(Exception):
    """
    Raised by validate() or from_json() when the task graph fails a structural invariant.

    Purpose: a single, catchable exception so the splitter can convert any
    validation failure into a ParkedSpec without crash-surfacing the detail.
    Usage: try: validate(graph) except GraphValidationError as exc: return ParkedSpec(...)
    Gotchas: the message is human-readable and intended for the questions.md writer;
    it names the failing node id and the failing invariant.
    """


@dataclass
class TaskNode:
    """
    One unit of work in the task graph.

    Fields mirror the AI output contract (design §3.2):
      id          — stable within-graph identifier (e.g. "T1")
      title       — short human-readable description
      task_type   — must be in VALID_TASK_TYPES
      acceptance  — list of acceptance criteria (≥0; empty flags high ambiguity)
      depends_on  — list of node ids that must reach DONE before this one starts
      est_files   — declared file footprint (feeds P5 scope guard later)
      ambiguity   — [0.0, 1.0] filled by ambiguity_rubric.grade(); the AI's
                    self-reported value is advisory and overwritten by the splitter
    """

    id: str
    title: str
    task_type: str
    acceptance: List[str]
    depends_on: List[str]
    est_files: List[str]
    ambiguity: float = 0.0


@dataclass
class TaskGraph:
    """
    The validated output of one AI splitting pass.

    Fields:
      spec_hash     — sha256 of the source spec (intake idempotency key)
      repo          — repository slug the tasks will run against
      nodes         — ordered list of TaskNode (the splitter emits them; the
                      scheduler fans them out according to depends_on edges)
      max_ambiguity — max(node.ambiguity) after the rubric pass; the spec-review
                      gate uses this for park vs hold decisions
    """

    spec_hash: str
    repo: str
    nodes: List[TaskNode]
    max_ambiguity: float = 0.0


@dataclass
class ParkedSpec:
    """
    Sentinel returned by the splitter when the AI output is unusable.

    Purpose: the splitter never raises to the caller — it either returns a
    TaskGraph (valid, scored) or a ParkedSpec (failed validation/parse).
    Usage: result = split(...); if isinstance(result, ParkedSpec): handle park.
    Gotchas: reason is a short diagnostic string for the questions.md writer;
    spec_text is the original (pre-fence) spec for audit purposes.
    """

    reason: str
    spec_text: str


def validate(graph: TaskGraph) -> List[str]:
    """
    Fail-closed validation of a TaskGraph.  Raises GraphValidationError on any violation.

    Purpose: run as the SOLE validator before any scheduler logic touches the graph.
    The four invariants checked (in order):
      1. All depends_on ids resolve to real nodes (no dangling edges).
      2. All task_type values are in VALID_TASK_TYPES.
      3. The depends_on relation is acyclic (Kahn's topological sort).
    Returns the topological ordering (list of node ids, leaves first) so the
    scheduler can consume it without recomputing.

    Usage:
        topo_order = validate(graph)   # raises on failure
    Gotchas:
      * Duplicate node ids in graph.nodes cause the graph to fail the Kahn step
        because in-degree accounting will detect an unreachable phantom node.
      * Kahn's algorithm: build in-degree map, process zero-in-degree frontier;
        if any node is never processed, a cycle (or impossible dep) exists.
    """
    if not isinstance(graph.nodes, list):
        raise GraphValidationError("graph.nodes must be a list")

    # Build id → node map for O(1) lookups.
    id_to_node: Dict[str, TaskNode] = {}
    for node in graph.nodes:
        if not isinstance(node, TaskNode):
            raise GraphValidationError(f"graph.nodes contains a non-TaskNode entry: {node!r}")
        if node.id in id_to_node:
            raise GraphValidationError(f"duplicate node id {node.id!r}")
        id_to_node[node.id] = node

    node_ids = set(id_to_node.keys())

    # --- Invariant 1: task_type validity ---
    for node in graph.nodes:
        if node.task_type not in VALID_TASK_TYPES:
            raise GraphValidationError(
                f"node {node.id!r}: invalid task_type {node.task_type!r}; "
                f"must be one of {sorted(VALID_TASK_TYPES)}"
            )

    # --- Invariant 2: dangling edge check ---
    for node in graph.nodes:
        for dep_id in node.depends_on:
            if dep_id not in node_ids:
                raise GraphValidationError(
                    f"node {node.id!r}: dangling depends_on reference {dep_id!r} "
                    f"(no node with that id in the graph)"
                )

    # --- Invariant 3: cycle detection via Kahn's algorithm ---
    # in_degree[id] = number of unresolved predecessors for this node.
    in_degree: Dict[str, int] = {nid: 0 for nid in node_ids}
    # successors[id] = list of node ids that depend ON this node.
    successors: Dict[str, List[str]] = {nid: [] for nid in node_ids}

    for node in graph.nodes:
        for dep_id in node.depends_on:
            # dep_id must finish before node.id — dep_id is a predecessor of node.id
            in_degree[node.id] += 1
            successors[dep_id].append(node.id)

    # Initialize the frontier with nodes that have no predecessors.
    frontier: deque[str] = deque(
        nid for nid in node_ids if in_degree[nid] == 0
    )
    topo_order: List[str] = []

    while frontier:
        nid = frontier.popleft()
        topo_order.append(nid)
        for successor_id in successors[nid]:
            in_degree[successor_id] -= 1
            if in_degree[successor_id] == 0:
                frontier.append(successor_id)

    if len(topo_order) != len(node_ids):
        # Some nodes were never reachable → there is a cycle.
        in_cycle = sorted(node_ids - set(topo_order))
        raise GraphValidationError(
            f"cycle detected in depends_on graph; nodes involved: {in_cycle}"
        )

    return topo_order


def from_json(payload: Any) -> TaskGraph:
    """
    Parse a raw dict/JSON-decoded value into a TaskGraph and validate it.

    Purpose: the single entry point for AI output → TaskGraph conversion.
    Malformed input (missing fields, wrong types, failed validation) always
    raises GraphValidationError so the splitter can return ParkedSpec.

    Usage:
        graph = from_json(json.loads(ai_output))
    Gotchas:
      * Does NOT set node.ambiguity — the splitter does that after calling
        ambiguity_rubric.grade() on each node (the AI's self-reported ambiguity
        is overwritten by the pure rubric pass).
      * Raises GraphValidationError on any structural problem — callers must
        not catch KeyError/TypeError separately.
    """
    try:
        if not isinstance(payload, dict):
            raise GraphValidationError(
                f"expected a JSON object at the top level, got {type(payload).__name__!r}"
            )

        nodes_raw = payload.get("nodes")
        if not isinstance(nodes_raw, list):
            raise GraphValidationError(
                f"'nodes' must be a list, got {type(nodes_raw).__name__!r}"
            )

        nodes: List[TaskNode] = []
        for i, raw_node in enumerate(nodes_raw):
            if not isinstance(raw_node, dict):
                raise GraphValidationError(
                    f"nodes[{i}] must be an object, got {type(raw_node).__name__!r}"
                )
            try:
                node = TaskNode(
                    id=str(raw_node["id"]),
                    title=str(raw_node["title"]),
                    task_type=str(raw_node.get("task_type", "other")),
                    acceptance=list(raw_node.get("acceptance") or []),
                    depends_on=list(raw_node.get("depends_on") or []),
                    est_files=list(raw_node.get("est_files") or []),
                    ambiguity=float(raw_node.get("ambiguity", 0.0)),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise GraphValidationError(
                    f"nodes[{i}]: cannot construct TaskNode — {exc}"
                ) from exc
            nodes.append(node)

        spec_hash = str(payload.get("spec_hash", ""))
        repo = str(payload.get("repo", ""))
        max_ambiguity = float(payload.get("max_ambiguity", 0.0))

        graph = TaskGraph(
            spec_hash=spec_hash,
            repo=repo,
            nodes=nodes,
            max_ambiguity=max_ambiguity,
        )

    except GraphValidationError:
        raise
    except Exception as exc:
        raise GraphValidationError(f"unexpected parse error: {exc}") from exc

    # Run the structural invariants.
    validate(graph)
    return graph


def spec_hash(spec_text: str) -> str:
    """
    Compute the sha256 hex digest of spec_text — the intake idempotency key.

    Purpose: stable, collision-resistant hash for deduplicating re-split requests
    on the same spec text, and for naming the per-spec questions.md file.
    Usage: h = spec_hash(spec_text)
    Gotchas: uses UTF-8 encoding; the same text always produces the same hash.
    """
    return hashlib.sha256(spec_text.encode("utf-8")).hexdigest()
