#!/usr/bin/env python3
"""
Phase 12 — Execution graph demo.

Demonstrates build_graph from a sample trace and shows JSON output.
"""
from agent_v2.schemas.trace import Trace, TraceStep, TraceMetadata, TraceError
from agent_v2.schemas.execution import ErrorType
from agent_v2.schemas.plan import (
    PlanDocument,
    PlanStep,
    PlanSource,
    PlanRisk,
    PlanMetadata,
)
from agent_v2.observability import build_graph
import json


def main():
    print("=" * 80)
    print("PHASE 12 — EXECUTION GRAPH DEMO")
    print("=" * 80)
    print()

    trace = Trace(
        trace_id="demo_trace_001",
        instruction="Add logging to execute_step",
        plan_id="plan_001",
        steps=[
            TraceStep(
                step_id="s1",
                plan_step_index=1,
                action="search",
                target="find execute_step",
                success=True,
                error=None,
                duration_ms=120,
            ),
            TraceStep(
                step_id="s2",
                plan_step_index=2,
                action="open_file",
                target="agent_v2/runtime/dag_executor.py",
                success=True,
                error=None,
                duration_ms=50,
            ),
            TraceStep(
                step_id="s3",
                plan_step_index=3,
                action="edit",
                target="agent_v2/runtime/dag_executor.py",
                success=True,
                error=None,
                duration_ms=250,
            ),
        ],
        status="success",
        metadata=TraceMetadata(total_steps=3, total_duration_ms=420),
    )

    print("Building graph from trace (without plan)...")
    graph = build_graph(trace)
    print(f"Graph trace_id: {graph.trace_id}")
    print(f"Nodes: {len(graph.nodes)}")
    print(f"Edges: {len(graph.edges)}")
    print()

    print("Graph structure:")
    for i, node in enumerate(graph.nodes):
        print(f"  Node {i+1}: {node.label} ({node.status}) — {node.metadata.get('duration_ms')}ms")
    for i, edge in enumerate(graph.edges):
        print(f"  Edge {i+1}: {edge.source} → {edge.target} (type={edge.type})")
    print()

    plan = PlanDocument(
        plan_id="plan_001",
        instruction="Add logging with retry",
        understanding="Add logging to execute_step with retry",
        sources=[PlanSource(type="file", ref="dag_executor.py", summary="executor")],
        steps=[
            PlanStep(
                step_id="s1",
                index=1,
                type="explore",
                goal="search",
                action="search",
                inputs={},
            ),
            PlanStep(
                step_id="s2",
                index=2,
                type="analyze",
                goal="read",
                action="open_file",
                inputs={"path": "agent_v2/runtime/dag_executor.py"},
            ),
            PlanStep(
                step_id="s3",
                index=3,
                type="modify",
                goal="edit",
                action="edit",
                inputs={"path": "agent_v2/runtime/dag_executor.py", "instruction": "log"},
            ),
        ],
        risks=[PlanRisk(risk="test", impact="low", mitigation="retry")],
        completion_criteria=["logging added"],
        metadata=PlanMetadata(created_at="2026-03-25T00:00:00Z", version=1),
    )

    print("Building graph from trace WITH plan (includes retry edges)...")
    graph_with_plan = build_graph(trace, plan=plan)
    print(f"Nodes: {len(graph_with_plan.nodes)}")
    print(f"Edges: {len(graph_with_plan.edges)}")
    print()

    print("Graph structure (with retry):")
    for i, node in enumerate(graph_with_plan.nodes):
        retry_info = f" — retry_count={node.metadata.get('retry_count')}" if node.type == "event" else ""
        print(f"  Node {i+1}: {node.label} ({node.status}){retry_info}")
    for i, edge in enumerate(graph_with_plan.edges):
        print(f"  Edge {i+1}: {edge.source} → {edge.target} (type={edge.type})")
    print()

    print("JSON output (for API/UI):")
    print(json.dumps(graph_with_plan.model_dump(), indent=2))
    print()

    print("=" * 80)
    print("DEMO COMPLETE ✅")
    print("=" * 80)
    print()
    print("Next steps:")
    print("  1. Start API: python -m agent_v2.observability.server")
    print("  2. Start UI:  cd ui && npm install && npm run dev")
    print("  3. View at:   http://localhost:3000")


if __name__ == "__main__":
    main()
