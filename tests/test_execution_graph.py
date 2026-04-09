"""
Phase 12 — Execution graph UI tests.

Validates:
1. Graph model (GraphNode, GraphEdge, ExecutionGraph)
2. Graph builder (build_graph from Trace)
3. Retry edges (synthetic retry event nodes)
4. Replan edges (failure → new plan segment)
5. Runtime integration (graph in output)
"""
from __future__ import annotations

import pytest

from agent_v2.observability.graph_model import GraphNode, GraphEdge, ExecutionGraph
from agent_v2.observability.graph_builder import build_graph
from agent_v2.schemas.trace import Trace, TraceStep, TraceMetadata, TraceError
from agent_v2.schemas.execution import ErrorType
from agent_v2.schemas.plan import (
    PlanDocument,
    PlanStep,
    PlanSource,
    PlanRisk,
    PlanMetadata,
)


class TestGraphModel:
    """Phase 12 Step 1 — Graph model schemas."""

    def test_graph_node_basic(self):
        """GraphNode captures id, type, label, status."""
        node = GraphNode(
            id="step_1",
            type="step",
            label="search",
            status="success",
        )
        assert node.id == "step_1"
        assert node.type == "step"
        assert node.label == "search"
        assert node.status == "success"
        assert node.input is None
        assert node.output is None
        assert node.error is None
        assert node.metadata == {}

    def test_graph_node_with_details(self):
        """GraphNode can include input, output, error, metadata."""
        node = GraphNode(
            id="step_2",
            type="step",
            label="edit",
            status="failure",
            input={"path": "file.py"},
            output={"target": "file.py"},
            error="patch failed",
            metadata={"duration_ms": 1500, "attempts": 2},
        )
        assert node.error == "patch failed"
        assert node.metadata["attempts"] == 2

    def test_graph_edge_basic(self):
        """GraphEdge links source → target with type."""
        edge = GraphEdge(source="step_1", target="step_2", type="next")
        assert edge.source == "step_1"
        assert edge.target == "step_2"
        assert edge.type == "next"

    def test_execution_graph_basic(self):
        """ExecutionGraph contains trace_id, nodes, edges."""
        graph = ExecutionGraph(
            trace_id="trace_123",
            nodes=[
                GraphNode(id="s1", type="step", label="search", status="success"),
            ],
            edges=[],
        )
        assert graph.trace_id == "trace_123"
        assert len(graph.nodes) == 1
        assert len(graph.edges) == 0

    def test_execution_graph_serializable(self):
        """ExecutionGraph.model_dump() produces JSON-serializable dict."""
        graph = ExecutionGraph(
            trace_id="trace_456",
            nodes=[
                GraphNode(id="s1", type="step", label="search", status="success"),
                GraphNode(id="s2", type="step", label="edit", status="failure"),
            ],
            edges=[
                GraphEdge(source="s1", target="s2", type="next"),
            ],
        )
        data = graph.model_dump()
        assert isinstance(data, dict)
        assert data["trace_id"] == "trace_456"
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1


class TestGraphBuilderBasic:
    """Phase 12 Steps 2-3 — Build graph from Trace."""

    def test_empty_trace(self):
        """Empty trace produces graph with no nodes/edges."""
        trace = Trace(
            trace_id="t1",
            instruction="test",
            plan_id="p1",
            steps=[],
            status="failure",
            metadata=TraceMetadata(total_steps=0, total_duration_ms=0),
        )
        graph = build_graph(trace)
        assert graph.trace_id == "t1"
        assert len(graph.nodes) == 0
        assert len(graph.edges) == 0

    def test_single_step_trace(self):
        """Single step produces one node, no edges."""
        trace = Trace(
            trace_id="t2",
            instruction="test",
            plan_id="p2",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="search",
                    target="test query",
                    success=True,
                    error=None,
                    duration_ms=100,
                )
            ],
            status="success",
            metadata=TraceMetadata(total_steps=1, total_duration_ms=100),
        )
        graph = build_graph(trace)
        assert len(graph.nodes) == 1
        assert graph.nodes[0].id == "s1"
        assert graph.nodes[0].type == "step"
        assert graph.nodes[0].label == "search"
        assert graph.nodes[0].status == "success"
        assert len(graph.edges) == 0

    def test_linear_chain(self):
        """Multiple steps produce linear chain with next edges."""
        trace = Trace(
            trace_id="t3",
            instruction="test",
            plan_id="p3",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="search",
                    target="query",
                    success=True,
                    error=None,
                    duration_ms=100,
                ),
                TraceStep(
                    step_id="s2",
                    plan_step_index=2,
                    action="open_file",
                    target="file.py",
                    success=True,
                    error=None,
                    duration_ms=50,
                ),
                TraceStep(
                    step_id="s3",
                    plan_step_index=3,
                    action="edit",
                    target="file.py",
                    success=True,
                    error=None,
                    duration_ms=200,
                ),
            ],
            status="success",
            metadata=TraceMetadata(total_steps=3, total_duration_ms=350),
        )
        graph = build_graph(trace)

        assert len(graph.nodes) == 3
        assert len(graph.edges) == 2

        assert graph.edges[0].source == "s1"
        assert graph.edges[0].target == "s2"
        assert graph.edges[0].type == "next"

        assert graph.edges[1].source == "s2"
        assert graph.edges[1].target == "s3"
        assert graph.edges[1].type == "next"

    def test_step_with_error(self):
        """Failed step includes error message in node."""
        trace = Trace(
            trace_id="t4",
            instruction="test",
            plan_id="p4",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="edit",
                    target="file.py",
                    success=False,
                    error=TraceError(type=ErrorType.tool_error, message="patch failed"),
                    duration_ms=150,
                )
            ],
            status="failure",
            metadata=TraceMetadata(total_steps=1, total_duration_ms=150),
        )
        graph = build_graph(trace)

        assert len(graph.nodes) == 1
        node = graph.nodes[0]
        assert node.status == "failure"
        assert node.error == "patch failed"


class TestGraphBuilderRetryEdges:
    """Phase 12 Step 9 — Retry edge support."""

    def test_retry_edges_when_plan_provided(self):
        """When plan provided, attempts > 1 creates retry event node."""
        plan = PlanDocument(
            plan_id="p5",
            instruction="test",
            understanding="test",
            sources=[PlanSource(type="other", ref="test", summary="test")],
            steps=[
                PlanStep(
                    step_id="s1",
                    index=1,
                    type="modify",
                    goal="edit file",
                    action="edit",
                )
            ],
            risks=[PlanRisk(risk="test", impact="low", mitigation="test")],
            completion_criteria=["test"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )

        trace = Trace(
            trace_id="t5",
            instruction="test",
            plan_id="p5",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="edit",
                    target="file.py",
                    success=True,
                    error=None,
                    duration_ms=200,
                    metadata={"attempts": 3},
                )
            ],
            status="success",
            metadata=TraceMetadata(total_steps=1, total_duration_ms=200),
        )

        graph = build_graph(trace, plan=plan)

        assert len(graph.nodes) == 2
        assert len(graph.edges) == 1

        retry_node = next(n for n in graph.nodes if n.type == "event")
        assert retry_node.label == "retry (2x)"
        assert retry_node.metadata["retry_count"] == 2

        retry_edge = graph.edges[0]
        assert retry_edge.type == "retry"
        assert retry_edge.target == "s1"

    def test_no_retry_edges_when_single_attempt(self):
        """Single attempt (no retries) produces simple linear chain."""
        plan = PlanDocument(
            plan_id="p6",
            instruction="test",
            understanding="test",
            sources=[PlanSource(type="other", ref="test", summary="test")],
            steps=[
                PlanStep(
                    step_id="s1",
                    index=1,
                    type="explore",
                    goal="search",
                    action="search",
                )
            ],
            risks=[PlanRisk(risk="test", impact="low", mitigation="test")],
            completion_criteria=["test"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )

        trace = Trace(
            trace_id="t6",
            instruction="test",
            plan_id="p6",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="search",
                    target="query",
                    success=True,
                    error=None,
                    duration_ms=100,
                )
            ],
            status="success",
            metadata=TraceMetadata(total_steps=1, total_duration_ms=100),
        )

        graph = build_graph(trace, plan=plan)

        assert len(graph.nodes) == 1
        assert len(graph.edges) == 0
        assert graph.nodes[0].type == "step"


class TestGraphBuilderReplanEdges:
    """Phase 12 Step 9 — Replan edge support."""

    def test_replan_edge_when_failure_followed_by_step_1(self):
        """Failure followed by plan_step_index=1 creates replan edge."""
        trace = Trace(
            trace_id="t7",
            instruction="test",
            plan_id="p7",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="search",
                    target="query",
                    success=True,
                    error=None,
                    duration_ms=100,
                ),
                TraceStep(
                    step_id="s2",
                    plan_step_index=2,
                    action="edit",
                    target="file.py",
                    success=False,
                    error=TraceError(type=ErrorType.tool_error, message="patch failed"),
                    duration_ms=150,
                ),
                TraceStep(
                    step_id="s3",
                    plan_step_index=1,
                    action="search",
                    target="new query",
                    success=True,
                    error=None,
                    duration_ms=100,
                ),
            ],
            status="success",
            metadata=TraceMetadata(total_steps=3, total_duration_ms=350),
        )

        graph = build_graph(trace)

        assert len(graph.nodes) == 3
        assert len(graph.edges) == 2

        assert graph.edges[0].source == "s1"
        assert graph.edges[0].target == "s2"
        assert graph.edges[0].type == "next"

        assert graph.edges[1].source == "s2"
        assert graph.edges[1].target == "s3"
        assert graph.edges[1].type == "replan"


class TestGraphBuilderIntegration:
    """Phase 12 — Integration with runtime output."""

    def test_runtime_output_includes_graph(self):
        """Runtime normalize_run_result builds graph from trace."""
        from agent_v2.runtime.runtime import normalize_run_result
        from agent_v2.state.agent_state import AgentState

        trace = Trace(
            trace_id="t8",
            instruction="test",
            plan_id="p8",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="search",
                    target="query",
                    success=True,
                    error=None,
                    duration_ms=100,
                )
            ],
            status="success",
            metadata=TraceMetadata(total_steps=1, total_duration_ms=100),
        )

        state = AgentState(instruction="test")
        mgr_out = {"status": "success", "trace": trace, "state": state}

        result = normalize_run_result(mgr_out, state)

        assert "graph" in result
        assert result["graph"] is not None
        assert result["graph"]["trace_id"] == "t8"
        assert len(result["graph"]["nodes"]) == 1
        assert result["graph"]["nodes"][0]["id"] == "s1"

    def test_runtime_output_no_graph_when_no_trace(self):
        """No trace → no graph."""
        from agent_v2.runtime.runtime import normalize_run_result
        from agent_v2.state.agent_state import AgentState

        state = AgentState(instruction="test")
        result = normalize_run_result(state, state)

        assert result["status"] == "plan_ready"
        assert result["trace"] is None
        assert result["graph"] is None


class TestGraphBuilderEdgeCases:
    """Phase 12 — Edge cases and error handling."""

    def test_graph_from_trace_without_plan(self):
        """build_graph works without plan parameter (no retry edges)."""
        trace = Trace(
            trace_id="t9",
            instruction="test",
            plan_id="p9",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="search",
                    target="query",
                    success=True,
                    error=None,
                    duration_ms=100,
                ),
                TraceStep(
                    step_id="s2",
                    plan_step_index=2,
                    action="edit",
                    target="file.py",
                    success=True,
                    error=None,
                    duration_ms=200,
                ),
            ],
            status="success",
            metadata=TraceMetadata(total_steps=2, total_duration_ms=300),
        )

        graph = build_graph(trace, plan=None)

        assert len(graph.nodes) == 2
        assert len(graph.edges) == 1
        assert graph.edges[0].type == "next"

    def test_multiple_retries_multiple_steps(self):
        """Multiple steps with retries produce multiple retry nodes."""
        plan = PlanDocument(
            plan_id="p10",
            instruction="test",
            understanding="test",
            sources=[PlanSource(type="other", ref="test", summary="test")],
            steps=[
                PlanStep(
                    step_id="s1",
                    index=1,
                    type="explore",
                    goal="search",
                    action="search",
                ),
                PlanStep(
                    step_id="s2",
                    index=2,
                    type="modify",
                    goal="edit",
                    action="edit",
                ),
            ],
            risks=[PlanRisk(risk="test", impact="low", mitigation="test")],
            completion_criteria=["test"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )

        trace = Trace(
            trace_id="t10",
            instruction="test",
            plan_id="p10",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="search",
                    target="query",
                    success=True,
                    error=None,
                    duration_ms=100,
                    metadata={"attempts": 2},
                ),
                TraceStep(
                    step_id="s2",
                    plan_step_index=2,
                    action="edit",
                    target="file.py",
                    success=True,
                    error=None,
                    duration_ms=200,
                    metadata={"attempts": 3},
                ),
            ],
            status="success",
            metadata=TraceMetadata(total_steps=2, total_duration_ms=300),
        )

        graph = build_graph(trace, plan=plan)

        retry_nodes = [n for n in graph.nodes if n.type == "event"]
        assert len(retry_nodes) == 2
        assert retry_nodes[0].label == "retry (1x)"
        assert retry_nodes[1].label == "retry (2x)"

        retry_edges = [e for e in graph.edges if e.type == "retry"]
        assert len(retry_edges) == 2


class TestGraphBuilderComplexFlow:
    """Phase 12 — Complex execution flows (retry + replan)."""

    def test_retry_then_replan(self):
        """Step with retries fails, triggers replan."""
        plan = PlanDocument(
            plan_id="p11",
            instruction="test",
            understanding="test",
            sources=[PlanSource(type="other", ref="test", summary="test")],
            steps=[
                PlanStep(
                    step_id="s1",
                    index=1,
                    type="modify",
                    goal="edit",
                    action="edit",
                ),
                PlanStep(
                    step_id="s2",
                    index=2,
                    type="explore",
                    goal="search again",
                    action="search",
                ),
            ],
            risks=[PlanRisk(risk="test", impact="low", mitigation="test")],
            completion_criteria=["test"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )

        trace = Trace(
            trace_id="t11",
            instruction="test",
            plan_id="p11",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="edit",
                    target="file.py",
                    success=False,
                    error=TraceError(type=ErrorType.tool_error, message="patch failed"),
                    duration_ms=200,
                    metadata={"attempts": 3},
                ),
                TraceStep(
                    step_id="s2",
                    plan_step_index=1,
                    action="search",
                    target="new query",
                    success=True,
                    error=None,
                    duration_ms=100,
                ),
            ],
            status="success",
            metadata=TraceMetadata(total_steps=2, total_duration_ms=300),
        )

        graph = build_graph(trace, plan=plan)

        retry_nodes = [n for n in graph.nodes if n.type == "event"]
        assert len(retry_nodes) == 1
        assert retry_nodes[0].label == "retry (2x)"

        step_nodes = [n for n in graph.nodes if n.type == "step"]
        assert len(step_nodes) == 2

        replan_edge = next((e for e in graph.edges if e.type == "replan"), None)
        assert replan_edge is not None
        assert replan_edge.source == "s1"
        assert replan_edge.target == "s2"


class TestGraphStatusColors:
    """Phase 12 Step 7 — Status field for UI styling."""

    def test_status_success(self):
        """Success step has status='success'."""
        trace = Trace(
            trace_id="t12",
            instruction="test",
            plan_id="p12",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="search",
                    target="query",
                    success=True,
                    error=None,
                    duration_ms=100,
                )
            ],
            status="success",
            metadata=TraceMetadata(total_steps=1, total_duration_ms=100),
        )
        graph = build_graph(trace)
        assert graph.nodes[0].status == "success"

    def test_status_failure(self):
        """Failed step has status='failure'."""
        trace = Trace(
            trace_id="t13",
            instruction="test",
            plan_id="p13",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="edit",
                    target="file.py",
                    success=False,
                    error=TraceError(type=ErrorType.tool_error, message="failed"),
                    duration_ms=100,
                )
            ],
            status="failure",
            metadata=TraceMetadata(total_steps=1, total_duration_ms=100),
        )
        graph = build_graph(trace)
        assert graph.nodes[0].status == "failure"

    def test_status_retry(self):
        """Retry event node has status='retry'."""
        plan = PlanDocument(
            plan_id="p14",
            instruction="test",
            understanding="test",
            sources=[PlanSource(type="other", ref="test", summary="test")],
            steps=[
                PlanStep(
                    step_id="s1",
                    index=1,
                    type="modify",
                    goal="edit",
                    action="edit",
                )
            ],
            risks=[PlanRisk(risk="test", impact="low", mitigation="test")],
            completion_criteria=["test"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )

        trace = Trace(
            trace_id="t14",
            instruction="test",
            plan_id="p14",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="edit",
                    target="file.py",
                    success=True,
                    error=None,
                    duration_ms=200,
                    metadata={"attempts": 3},
                )
            ],
            status="success",
            metadata=TraceMetadata(total_steps=1, total_duration_ms=200),
        )

        graph = build_graph(trace, plan=plan)
        retry_node = next(n for n in graph.nodes if n.type == "event")
        assert retry_node.status == "retry"


class TestGraphMetadata:
    """Phase 12 — Metadata for drill-down (Step 6)."""

    def test_node_metadata_includes_duration_and_index(self):
        """Node metadata includes duration_ms and plan_step_index."""
        trace = Trace(
            trace_id="t15",
            instruction="test",
            plan_id="p15",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="search",
                    target="query",
                    success=True,
                    error=None,
                    duration_ms=150,
                )
            ],
            status="success",
            metadata=TraceMetadata(total_steps=1, total_duration_ms=150),
        )
        graph = build_graph(trace)

        assert graph.nodes[0].metadata["duration_ms"] == 150
        assert graph.nodes[0].metadata["plan_step_index"] == 1
        assert graph.nodes[0].metadata["action"] == "search"

    def test_node_output_includes_target(self):
        """Node output includes target from trace step."""
        trace = Trace(
            trace_id="t16",
            instruction="test",
            plan_id="p16",
            steps=[
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="open_file",
                    target="src/main.py",
                    success=True,
                    error=None,
                    duration_ms=50,
                )
            ],
            status="success",
            metadata=TraceMetadata(total_steps=1, total_duration_ms=50),
        )
        graph = build_graph(trace)

        assert graph.nodes[0].output == {"target": "src/main.py"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
