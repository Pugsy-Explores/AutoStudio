"""
Phase 12 — Execution graph builder.

Converts agent_v2.schemas.trace.Trace → ExecutionGraph (nodes + edges).

v1: Linear chain of step nodes linked by "next" edges.
v2 (Step 9): Add retry edges (self-loop or synthetic retry node) and replan edges.

This is NOT repo_graph/graph_builder.py (symbol graph).
This is the EXECUTION graph builder.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agent_v2.schemas.trace import Trace
    from agent_v2.schemas.plan import PlanDocument

from agent_v2.observability.graph_model import ExecutionGraph, GraphNode, GraphEdge


def build_graph(trace: Trace, plan: Optional[PlanDocument] = None) -> ExecutionGraph:
    """
    Build execution graph from internal Trace.

    v1 behavior (Phase 12 Steps 1-3):
    - One node per TraceStep
    - Linear chain (next edges)
    - Optional retry edges when plan is provided

    v2 behavior (Phase 12 Step 9):
    - Retry edges: Synthetic retry event nodes when step.execution.attempts > 1
    - Replan edges: Detect replan boundaries and add type="replan" edges

    Args:
        trace: Internal trace from agent_v2.schemas.trace
        plan: Optional PlanDocument to extract retry/replan metadata

    Returns:
        ExecutionGraph with nodes and edges ready for UI serialization
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    step_id_to_plan_step = {}
    if plan is not None:
        for ps in plan.steps:
            step_id_to_plan_step[ps.step_id] = ps

    prev_node_id: str | None = None
    prev_tool_step_failed = False

    for idx, step in enumerate(trace.steps):
        node_id = step.step_id
        plan_step = step_id_to_plan_step.get(node_id)

        if step.kind == "llm":
            meta = {
                "duration_ms": step.duration_ms,
                "task_name": step.metadata.get("task_name") or step.action,
                "model": step.metadata.get("model"),
                "tokens_input": step.metadata.get("tokens_input"),
                "tokens_output": step.metadata.get("tokens_output"),
            }
            node = GraphNode(
                id=node_id,
                type="llm",
                label=step.action,
                status="success" if step.success else "failure",
                input=dict(step.input) if step.input else {},
                output=dict(step.output) if step.output else {},
                error=step.error.message if step.error else None,
                metadata={k: v for k, v in meta.items() if v is not None},
            )
        else:
            node = GraphNode(
                id=node_id,
                type="step",
                label=f"{step.action}",
                status="success" if step.success else "failure",
                input=dict(step.input) if step.input else {},
                output=dict(step.output) if step.output else {"target": step.target},
                error=step.error.message if step.error else None,
                metadata={
                    "duration_ms": step.duration_ms,
                    "plan_step_index": step.plan_step_index,
                    "action": step.action,
                },
            )

        if step.kind == "tool" and plan_step is not None and hasattr(plan_step, "execution"):
            attempts = getattr(plan_step.execution, "attempts", 0)
            node.metadata["attempts"] = attempts

            if attempts > 1:
                retry_node_id = f"{node_id}_retry"
                retry_node = GraphNode(
                    id=retry_node_id,
                    type="event",
                    label=f"retry ({attempts - 1}x)",
                    status="retry",
                    metadata={
                        "retry_count": attempts - 1,
                        "parent_step_id": node_id,
                    },
                )
                nodes.append(retry_node)

                if prev_node_id:
                    edges.append(GraphEdge(source=prev_node_id, target=retry_node_id, type="next"))
                edges.append(GraphEdge(source=retry_node_id, target=node_id, type="retry"))
                prev_node_id = node_id
                nodes.append(node)
                if step.kind == "tool":
                    prev_tool_step_failed = not step.success
                continue

        nodes.append(node)

        if prev_node_id:
            edge_type = "next"
            if (
                step.kind == "tool"
                and prev_tool_step_failed
                and step.plan_step_index == 1
            ):
                edge_type = "replan"

            edges.append(
                GraphEdge(
                    source=prev_node_id,
                    target=node_id,
                    type=edge_type,
                )
            )

        prev_node_id = node_id
        if step.kind == "tool":
            prev_tool_step_failed = not step.success

    return ExecutionGraph(
        trace_id=trace.trace_id,
        nodes=nodes,
        edges=edges,
    )
