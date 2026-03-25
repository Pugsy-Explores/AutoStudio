/**
 * Phase 12 — Execution graph types.
 *
 * Mirrors agent_v2/observability/graph_model.py
 */

export interface GraphNode {
  id: string;
  type: "step" | "llm" | "event";
  label: string;
  status: "success" | "failure" | "retry" | "pending";
  input?: Record<string, any> | null;
  output?: Record<string, any> | null;
  error?: string | null;
  metadata: Record<string, any>;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: "next" | "retry" | "replan";
}

export interface ExecutionGraph {
  trace_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}
