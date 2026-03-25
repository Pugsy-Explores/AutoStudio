"""
agent_v2.observability — Observability layer (Phase 11 + 12).

Phase 11: Langfuse integration (external tracing)
Phase 12: Execution graph UI (nodes + edges visualization)
"""

# Phase 11 — Langfuse observability
from .langfuse_client import (
    langfuse,
    create_agent_trace,
    finalize_agent_trace,
    LFTraceHandle,
    LFSpanHandle,
    LFGenerationHandle,
)

# Phase 12 — Execution graph UI
from .graph_model import GraphNode, GraphEdge, ExecutionGraph
from .graph_builder import build_graph

__all__ = [
    # Phase 11
    "langfuse",
    "create_agent_trace",
    "finalize_agent_trace",
    "LFTraceHandle",
    "LFSpanHandle",
    "LFGenerationHandle",
    # Phase 12
    "GraphNode",
    "GraphEdge",
    "ExecutionGraph",
    "build_graph",
]
