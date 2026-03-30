"""
Phase 12 — Execution graph model.

This defines the graph projection layer (nodes, edges) for execution visualization.

NOTE: Phase 12 spec acknowledges that v1 uses str for rapid UI iteration, with
typing discipline deferred to a follow-up pass. Production graph will use
Literal types aligned with SCHEMAS.md and ErrorType.

ExecutionGraph = nodes + edges
  nodes = steps (and later: LLM calls, events)
  edges = flow (next, retry, replan)

This is NOT the symbol graph (repo_graph/graph_builder.py).
This is the EXECUTION graph (from agent_v2.schemas.trace.Trace).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    """
    A node in the execution graph.

    v1: type is str (rapid UI iteration).
    Future: Literal["step", "llm", "event"] aligned with SCHEMAS.md.
    """

    id: str = Field(..., description="Unique node identifier (e.g., step_id)")
    type: str = Field(
        ...,
        description="Node type: step | llm | event | diff | memory (extend via SCHEMAS amendment)",
    )
    label: str = Field(..., description="Human-readable label for UI")
    status: str = Field(..., description="Execution status: success | failure | retry | pending")

    input: Optional[dict] = Field(default=None, description="Node input (structured)")
    output: Optional[dict] = Field(default=None, description="Node output (structured)")
    error: Optional[str] = Field(default=None, description="Error message if failed")

    metadata: dict = Field(default_factory=dict, description="Additional metadata for drill-down")


class GraphEdge(BaseModel):
    """
    An edge in the execution graph.

    v1: type is str (rapid UI iteration).
    Future: Literal["next", "retry", "replan"] aligned with execution semantics.
    """

    source: str = Field(..., description="Source node id")
    target: str = Field(..., description="Target node id")
    type: str = Field(..., description="Edge type: next | retry | replan")


class ExecutionGraph(BaseModel):
    """
    Full execution graph for a single agent run.

    Source: agent_v2.schemas.trace.Trace (internal).
    Target: UI visualization (React Flow or equivalent).
    """

    trace_id: str = Field(..., description="Unique trace identifier")
    nodes: list[GraphNode] = Field(default_factory=list, description="All nodes in graph")
    edges: list[GraphEdge] = Field(default_factory=list, description="All edges in graph")
