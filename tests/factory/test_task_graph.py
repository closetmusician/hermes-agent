# ABOUTME: Unit tests for factory/task_graph.py (P3-a REQ-01): TaskNode/TaskGraph schema,
# ABOUTME: validate() (Kahn's cycle detection + dangling-edge + task_type checks), and
# ABOUTME: from_json() (AI output → TaskGraph conversion with fail-closed error handling).
# ABOUTME: All tests run real validate()/from_json() code — no mocking of factory internals.
# ABOUTME: Design source: docs/plans/harness/fable/p3/P3-design.md §3.2 + §3.4 tests 1–3.
"""
Tests for factory.task_graph.

Covers validate() (topological sort, cycle detection, dangling edges, task_type) and
from_json() (parsing, structural validation, error handling).  No mocking — this is
pure validation logic tested directly.
"""

from __future__ import annotations

import pytest
from dataclasses import replace

from factory.task_graph import (
    GraphValidationError,
    ParkedSpec,
    TaskGraph,
    TaskNode,
    from_json,
    spec_hash,
    validate,
    VALID_TASK_TYPES,
)
from factory.trust_ledger import GRADUATABLE_TASK_TYPES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node(
    id: str = "T1",
    title: str = "Add login endpoint",
    task_type: str = "feature",
    acceptance=None,
    depends_on=None,
    est_files=None,
    ambiguity: float = 0.0,
) -> TaskNode:
    """Build a TaskNode with sane defaults."""
    return TaskNode(
        id=id,
        title=title,
        task_type=task_type,
        acceptance=acceptance if acceptance is not None else ["POST /login returns 200"],
        depends_on=depends_on if depends_on is not None else [],
        est_files=est_files if est_files is not None else [],
        ambiguity=ambiguity,
    )


def _graph(nodes, spec_hash_val: str = "abc123", repo: str = "test-repo") -> TaskGraph:
    """Build a TaskGraph from nodes without auto-validating."""
    return TaskGraph(
        spec_hash=spec_hash_val,
        repo=repo,
        nodes=nodes,
        max_ambiguity=max((n.ambiguity for n in nodes), default=0.0),
    )


# ---------------------------------------------------------------------------
# TaskNode + TaskGraph dataclass basics
# ---------------------------------------------------------------------------

class TestDataclassBasics:
    """Structural field tests — make sure the schema matches the design §3.2."""

    def test_tasknode_fields_accessible(self):
        """All seven TaskNode fields are present and default-constructable."""
        n = _node()
        assert n.id == "T1"
        assert n.title == "Add login endpoint"
        assert n.task_type == "feature"
        assert isinstance(n.acceptance, list)
        assert isinstance(n.depends_on, list)
        assert isinstance(n.est_files, list)
        assert isinstance(n.ambiguity, float)

    def test_taskgraph_fields_accessible(self):
        """All four TaskGraph fields are present."""
        g = _graph([_node()])
        assert isinstance(g.spec_hash, str)
        assert isinstance(g.repo, str)
        assert isinstance(g.nodes, list)
        assert isinstance(g.max_ambiguity, float)

    def test_parkedspec_fields_accessible(self):
        """ParkedSpec has reason + spec_text."""
        ps = ParkedSpec(reason="test", spec_text="some spec")
        assert ps.reason == "test"
        assert ps.spec_text == "some spec"

    def test_valid_task_types_superset_of_graduatable(self):
        """VALID_TASK_TYPES must include all GRADUATABLE_TASK_TYPES plus 'other'."""
        assert GRADUATABLE_TASK_TYPES.issubset(VALID_TASK_TYPES)
        assert "other" in VALID_TASK_TYPES


# ---------------------------------------------------------------------------
# validate() — happy paths
# ---------------------------------------------------------------------------

class TestValidateHappyPaths:
    """validate() passes on well-formed graphs and returns a topological ordering."""

    def test_single_node_validates(self):
        """A single node with no dependencies validates and appears in topo order."""
        g = _graph([_node("T1")])
        order = validate(g)
        assert order == ["T1"]

    def test_linear_chain_validates(self):
        """A linear T1→T2→T3 chain produces a correct topological ordering."""
        n1 = _node("T1", depends_on=[])
        n2 = _node("T2", depends_on=["T1"])
        n3 = _node("T3", depends_on=["T2"])
        g = _graph([n1, n2, n3])
        order = validate(g)
        assert order.index("T1") < order.index("T2") < order.index("T3")

    def test_diamond_dag_validates(self):
        """
        A diamond (T1 → T2, T1 → T3, T2 + T3 → T4) validates.
        T1 must appear before T2 and T3; both before T4.
        """
        n1 = _node("T1", depends_on=[])
        n2 = _node("T2", depends_on=["T1"])
        n3 = _node("T3", depends_on=["T1"])
        n4 = _node("T4", depends_on=["T2", "T3"])
        g = _graph([n1, n2, n3, n4])
        order = validate(g)
        assert order.index("T1") < order.index("T2")
        assert order.index("T1") < order.index("T3")
        assert order.index("T2") < order.index("T4")
        assert order.index("T3") < order.index("T4")

    def test_two_independent_nodes_both_in_order(self):
        """Two independent nodes (no edges) both appear in the topological ordering."""
        n1 = _node("T1", depends_on=[])
        n2 = _node("T2", depends_on=[])
        g = _graph([n1, n2])
        order = validate(g)
        assert set(order) == {"T1", "T2"}

    def test_node_with_empty_acceptance_validates(self):
        """
        Missing ACs are NOT a hard validation error — they are an ambiguity signal for
        the rubric.  validate() must pass a node with acceptance=[].

        Design: §3.2 'Missing acceptance criteria are NOT a hard validation error'.
        """
        n = _node("T1", acceptance=[])
        g = _graph([n])
        order = validate(g)
        assert "T1" in order

    def test_all_valid_task_types_pass(self):
        """Every task_type in VALID_TASK_TYPES passes validate()."""
        for task_type in VALID_TASK_TYPES:
            n = replace(_node("T1"), task_type=task_type)
            g = _graph([n])
            validate(g)  # must not raise


# ---------------------------------------------------------------------------
# validate() — failure paths
# ---------------------------------------------------------------------------

class TestValidateFailurePaths:
    """validate() raises GraphValidationError on each class of structural violation."""

    def test_cycle_detected_design_test_3(self):
        """
        T1→T2→T1 (cycle) must raise GraphValidationError mentioning 'cycle'.

        Design: §3.4 test #3 / §3.2 validate() invariant #2 (acyclic).
        The scheduler must NEVER receive a cyclic DAG — cycle detection is the guard.
        """
        n1 = _node("T1", depends_on=["T2"])
        n2 = _node("T2", depends_on=["T1"])
        g = _graph([n1, n2])
        with pytest.raises(GraphValidationError, match="cycle"):
            validate(g)

    def test_three_node_cycle_detected(self):
        """A longer cycle T1→T2→T3→T1 is also detected by Kahn's algorithm."""
        n1 = _node("T1", depends_on=["T3"])
        n2 = _node("T2", depends_on=["T1"])
        n3 = _node("T3", depends_on=["T2"])
        g = _graph([n1, n2, n3])
        with pytest.raises(GraphValidationError, match="cycle"):
            validate(g)

    def test_dangling_dependency_raises_design_test_2(self):
        """
        A depends_on referencing a non-existent id raises GraphValidationError
        mentioning 'dangling'.

        Design: §3.4 test #2 / §3.2 validate() invariant #1 (all deps resolve).
        The splitter must NEVER return a partial graph with dangling references.
        """
        bad_node = _node("T1", depends_on=["T99"])  # T99 does not exist
        g = _graph([bad_node])
        with pytest.raises(GraphValidationError, match="dangling"):
            validate(g)

    def test_invalid_task_type_raises(self):
        """A task_type not in VALID_TASK_TYPES raises GraphValidationError."""
        bad = replace(_node("T1"), task_type="hax0r-type")
        g = _graph([bad])
        with pytest.raises(GraphValidationError, match="task_type"):
            validate(g)

    def test_duplicate_node_id_raises(self):
        """Duplicate node ids in graph.nodes raise GraphValidationError."""
        n1 = _node("T1")
        n2 = _node("T1")  # same id
        g = _graph([n1, n2])
        with pytest.raises(GraphValidationError, match="duplicate"):
            validate(g)

    def test_non_list_nodes_raises(self):
        """graph.nodes that is not a list raises GraphValidationError immediately."""
        g = TaskGraph(spec_hash="x", repo="r", nodes="not-a-list", max_ambiguity=0.0)
        with pytest.raises(GraphValidationError):
            validate(g)

    def test_non_tasknode_entry_raises(self):
        """A non-TaskNode object in graph.nodes raises GraphValidationError."""
        g = TaskGraph(spec_hash="x", repo="r", nodes=["not-a-node"], max_ambiguity=0.0)
        with pytest.raises(GraphValidationError):
            validate(g)


# ---------------------------------------------------------------------------
# from_json() — happy paths
# ---------------------------------------------------------------------------

class TestFromJsonHappyPaths:
    """from_json() parses valid payloads into TaskGraph objects."""

    def test_minimal_valid_payload(self):
        """A minimal well-formed dict parses without error."""
        payload = {
            "spec_hash": "sha256abc",
            "repo": "my-repo",
            "nodes": [
                {
                    "id": "T1",
                    "title": "Add login",
                    "task_type": "feature",
                    "acceptance": ["POST /login returns 200"],
                    "depends_on": [],
                    "est_files": ["auth.py"],
                    "ambiguity": 0.0,
                }
            ],
            "max_ambiguity": 0.0,
        }
        graph = from_json(payload)
        assert len(graph.nodes) == 1
        assert graph.nodes[0].id == "T1"
        assert graph.repo == "my-repo"

    def test_optional_fields_have_defaults(self):
        """Missing optional fields (depends_on, est_files, ambiguity) default to empty / 0."""
        payload = {
            "spec_hash": "h",
            "repo": "r",
            "nodes": [
                {
                    "id": "T1",
                    "title": "Do something",
                    "task_type": "other",
                    "acceptance": ["It works"],
                }
            ],
        }
        graph = from_json(payload)
        assert graph.nodes[0].depends_on == []
        assert graph.nodes[0].est_files == []
        assert graph.nodes[0].ambiguity == 0.0

    def test_multi_node_chain_parsed(self):
        """A two-node linear chain parses and validate() confirms ordering."""
        payload = {
            "spec_hash": "chain",
            "repo": "r",
            "nodes": [
                {
                    "id": "T1",
                    "title": "Create migration",
                    "task_type": "feature",
                    "acceptance": ["Migration runs"],
                    "depends_on": [],
                    "est_files": [],
                    "ambiguity": 0.0,
                },
                {
                    "id": "T2",
                    "title": "Add endpoint",
                    "task_type": "feature",
                    "acceptance": ["Endpoint returns 200"],
                    "depends_on": ["T1"],
                    "est_files": [],
                    "ambiguity": 0.0,
                },
            ],
            "max_ambiguity": 0.0,
        }
        graph = from_json(payload)
        assert len(graph.nodes) == 2
        order = validate(graph)
        assert order.index("T1") < order.index("T2")


# ---------------------------------------------------------------------------
# from_json() — failure paths
# ---------------------------------------------------------------------------

class TestFromJsonFailurePaths:
    """from_json() raises GraphValidationError on every class of malformed input."""

    def test_non_dict_raises(self):
        """A non-dict top-level value raises GraphValidationError."""
        with pytest.raises(GraphValidationError):
            from_json("not a dict")

    def test_nodes_not_list_raises(self):
        """nodes that is not a list raises GraphValidationError."""
        with pytest.raises(GraphValidationError):
            from_json({"nodes": "not-a-list"})

    def test_node_missing_id_raises(self):
        """A node dict missing 'id' raises GraphValidationError (not KeyError)."""
        with pytest.raises(GraphValidationError):
            from_json({
                "spec_hash": "h",
                "repo": "r",
                "nodes": [
                    {
                        "title": "No id here",
                        "task_type": "feature",
                        "acceptance": ["Works"],
                    }
                ],
            })

    def test_node_missing_title_raises(self):
        """A node dict missing 'title' raises GraphValidationError."""
        with pytest.raises(GraphValidationError):
            from_json({
                "spec_hash": "h",
                "repo": "r",
                "nodes": [{"id": "T1", "task_type": "feature", "acceptance": ["Works"]}],
            })

    def test_cyclic_payload_raises(self):
        """A payload with a cycle raises GraphValidationError during validation."""
        with pytest.raises(GraphValidationError, match="cycle"):
            from_json({
                "spec_hash": "cyc",
                "repo": "r",
                "nodes": [
                    {
                        "id": "T1",
                        "title": "A",
                        "task_type": "feature",
                        "acceptance": ["A done"],
                        "depends_on": ["T2"],
                        "est_files": [],
                        "ambiguity": 0.0,
                    },
                    {
                        "id": "T2",
                        "title": "B",
                        "task_type": "feature",
                        "acceptance": ["B done"],
                        "depends_on": ["T1"],
                        "est_files": [],
                        "ambiguity": 0.0,
                    },
                ],
                "max_ambiguity": 0.0,
            })

    def test_dangling_payload_raises(self):
        """A payload referencing a non-existent depends_on id raises GraphValidationError."""
        with pytest.raises(GraphValidationError, match="dangling"):
            from_json({
                "spec_hash": "dang",
                "repo": "r",
                "nodes": [
                    {
                        "id": "T1",
                        "title": "A",
                        "task_type": "feature",
                        "acceptance": ["A done"],
                        "depends_on": ["T99"],
                        "est_files": [],
                        "ambiguity": 0.0,
                    }
                ],
                "max_ambiguity": 0.0,
            })

    def test_invalid_task_type_in_payload_raises(self):
        """An invalid task_type in the payload raises GraphValidationError."""
        with pytest.raises(GraphValidationError, match="task_type"):
            from_json({
                "spec_hash": "h",
                "repo": "r",
                "nodes": [
                    {
                        "id": "T1",
                        "title": "A",
                        "task_type": "INVALID_TYPE",
                        "acceptance": ["Works"],
                        "depends_on": [],
                        "est_files": [],
                        "ambiguity": 0.0,
                    }
                ],
                "max_ambiguity": 0.0,
            })


# ---------------------------------------------------------------------------
# spec_hash()
# ---------------------------------------------------------------------------

class TestSpecHash:
    """spec_hash() produces stable, deterministic SHA-256 hex digests."""

    def test_same_text_same_hash(self):
        """Same spec text always produces the same hash."""
        text = "Add OAuth2 login: 3 endpoints, token table"
        assert spec_hash(text) == spec_hash(text)

    def test_different_text_different_hash(self):
        """Different texts produce different hashes."""
        assert spec_hash("spec A") != spec_hash("spec B")

    def test_hash_is_64_hex_chars(self):
        """SHA-256 hex digest is exactly 64 characters."""
        h = spec_hash("some spec text")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_string_hashes_without_error(self):
        """spec_hash("") produces a deterministic result."""
        h1 = spec_hash("")
        h2 = spec_hash("")
        assert h1 == h2
        assert len(h1) == 64
