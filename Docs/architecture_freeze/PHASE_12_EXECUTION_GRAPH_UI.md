# Phase 12 — Execution graph + UI (Cursor/Devin-style visibility)

**Scope:** This document is the authoritative Phase 12 specification. It adds a **graph projection layer** and **UI** on top of traces — **not** Langfuse alone. Langfuse provides **storage + traces**; this phase provides **nodes, edges, and a navigable graph**. This file is not executable.

---

## What Cursor / Devin-style visibility implies

They maintain an **execution graph**:

```text
Execution graph
  nodes = steps (and later LLM / events)
  edges = flow (next, retry, replan)
```

With **inputs**, **outputs**, **errors**, **retries**, and **branching** (replan).

---

## Target architecture

```text
Langfuse (storage + tracing)     ← Phase 11
        ↓
Graph builder (projection)       ← THIS PHASE
        ↓
Graph API (backend, optional)
        ↓
Graph UI (frontend)
```

**Internal source of truth for v1:** `agent_v2.schemas.trace.Trace` (Phase 9). **Langfuse** can be a second source (fetch by id) once IDs align.

---

## Step 1 — Define graph model (critical)

**Create:** `agent_v2/observability/graph_model.py`

**Illustrative schema (Pydantic v2):**

```python
from pydantic import BaseModel
from typing import List, Optional


class GraphNode(BaseModel):
    id: str
    type: str  # "step" | "llm" | "event"

    label: str
    status: str  # success | failure | retry | pending

    input: Optional[dict] = None
    output: Optional[dict] = None
    error: Optional[str] = None

    metadata: dict = {}


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str  # "next" | "retry" | "replan"


class ExecutionGraph(BaseModel):
    trace_id: str
    nodes: List[GraphNode]
    edges: List[GraphEdge]
```

**Typing backlog (required before production graph UI):** Replace loose **`str`** with **`Literal`** / shared enums aligned with **`SCHEMAS.md`** and **`ErrorType`**:

| Field | Target typing |
|-------|----------------|
| **`GraphNode.type`** | `Literal["step", "llm", "event", "diff", "memory"]` (extend only via SCHEMAS amendment; Phases 14 / 16) |
| **`GraphNode.status`** | `Literal["success", "failure", "retry", "pending"]` |
| **`GraphEdge.type`** | `Literal["next", "retry", "replan"]` |
| **`GraphNode.error`** | Structured (`ErrorType` + message) or `null` — match **`TraceStep`** / **`ExecutionResult`** |

**Status:** **Deferred** to a follow-up pass after Phase 10 enum discipline; v1 graph may use **`str`** for rapid UI iteration **only** if documented as **non-compliant** with strict enum policy.

---

## Step 2 — Build graph from `Trace`

**Create:** `agent_v2/observability/graph_builder.py`

**Input:** `Trace` from **`agent_v2.schemas.trace`**.

**v1 behavior:** Linear chain of **step** nodes linked by **`next`** edges.

**Illustrative:**

```python
from agent_v2.schemas.trace import Trace
from .graph_model import ExecutionGraph, GraphNode, GraphEdge


def build_graph(trace: Trace) -> ExecutionGraph:

    nodes = []
    edges = []

    prev_node_id = None

    for step in trace.steps:

        node_id = step.step_id

        node = GraphNode(
            id=node_id,
            type="step",
            label=f"{step.action}",

            status="success" if step.success else "failure",

            input={},
            output={"target": step.target},
            error=step.error,

            metadata={
                "duration_ms": step.duration_ms,
                "plan_step_index": step.plan_step_index,
            },
        )

        nodes.append(node)

        if prev_node_id:
            edges.append(
                GraphEdge(
                    source=prev_node_id,
                    target=node_id,
                    type="next",
                )
            )

        prev_node_id = node_id

    return ExecutionGraph(
        trace_id=trace.trace_id,
        nodes=nodes,
        edges=edges,
    )
```

**Note:** **`TraceStep`** (Phase 1) exposes **`target`**, **`error`**, **`duration_ms`** — not full I/O summaries. Extend **`TraceStep`** or carry **parallel structured records** if you need rich **input/output** on nodes.

**Later (required for product parity):**

```text
retry edges
replan branch edges
LLM nodes (generations)
event nodes
```

---

## Step 3 — Add graph to runtime output

**After** internal **`trace`** is built:

```python
from agent_v2.observability.graph_builder import build_graph

graph = build_graph(trace)

return {
    "status": result["status"],
    "trace": trace,
    "graph": graph.model_dump(),
    "state": state,
}
```

Align with **Phase 10** stable CLI shape — **`graph`** should be **JSON-serializable** (`model_dump()` / `model_dump_json()`).

---

## Step 4 — Graph API (backend, optional)

**Create:** `agent_v2/observability/server.py`

**Illustrative FastAPI:**

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


# Wire create_runtime from actual bootstrap; add auth before production
```

```bash
uvicorn agent_v2.observability.server:app --reload
```

**Production:** Authentication, CORS, rate limits, no arbitrary code execution via query params.

---

## Step 5 — Graph UI (React Flow)

**Stack:** React + **React Flow** (package historically `react-flow-renderer`; current ecosystem often **`reactflow`** / **`@xyflow/react`** — **verify** the maintained package for your React version).

**Illustrative node/edge construction:**

```javascript
import ReactFlow from "react-flow-renderer"; // or @xyflow/react

function buildElements(graph) {
  const nodes = graph.nodes.map((n) => ({
    id: n.id,
    data: { label: `${n.label} (${n.status})`, node: n },
    position: { x: index * 200, y: 0 }, // prefer layout algorithm over random
  }));

  const edges = graph.edges.map((e, i) => ({
    id: `e-${i}`,
    source: e.source,
    target: e.target,
    type: e.type,
  }));

  return { nodes, edges };
}
```

**Avoid** random positions for production — use **dagre**, **elk**, or fixed grid.

---

## Step 6 — Click detail panel (Cursor-like)

On **node click**, show:

```text
input
output
error
duration
metadata
```

(Optional: link to **Langfuse** span id when Phase 11 links IDs.)

```javascript
onNodeClick={(event, node) => {
  setSelected(node.data.node);
}}
```

Render a **side panel** with structured fields.

---

## Step 7 — Status colors

```javascript
style={{
  background:
    n.status === "success"
      ? "#d4edda"
      : n.status === "failure"
      ? "#f8d7da"
      : "#fff3cd",
}}
```

Centralize **theme** tokens.

---

## Step 8 — Connect Langfuse

**Two modes:**

| Mode | Flow |
|------|------|
| **A — Simple** | Runtime returns **`trace` + `graph`** from same run |
| **B — Better** | Fetch trace/spans from **Langfuse API** by id → **normalize** → **`build_graph`** (requires a **normalized** intermediate format if Langfuse shape ≠ internal `Trace`) |

**Principle:** **One conceptual graph** — either built from **internal `Trace`** or from **Langfuse export** mapped into **`ExecutionGraph`**.

---

## Step 9 — Retry + replan edges

**Extend** `graph_builder`:

- **Retry:** Either **self-loop** on the same node with edge `type="retry"` or a **synthetic retry node** — prefer **explicit retry child** for clarity.
- **Replan:** Edge `type="replan"` from **failure node** to **new plan root** or **first step of new segment** — requires **trace / metadata** to record replan boundaries (Phase 7).

**Illustrative (self-loop is ambiguous in UI — document choice):**

```python
# Example: separate event node for retry instead of source==target
if had_retry:
    edges.append(GraphEdge(source=prev_id, target=retry_node_id, type="retry"))
```

---

## Step 10 — Final experience

**User sees:**

```text
(search) → (open_file) → (edit)
                   ↘ retry
                   ↘ replan
```

**Node drill-down:** inputs, outputs, errors; later **LLM prompt** snippets when LLM nodes exist.

---

## Principal verdict

```text
Agent runtime ✅
Trace system ✅
Graph projection ✅
UI visualization ✅
```

This is **close** to internal tooling patterns — **not** yet: **memory**, **multi-agent roles**, **diff visualizer**, **full code context** (listed as follow-ons).

---

## Reality check — still missing (out of scope)

```text
memory layer
multi-agent roles
diff visualizer
code context viewer
```

**Core execution visibility** can still be **correct** with Phases 9–12.

---

## Next steps (pick one)

1. **LLM nodes + prompt inspection** in graph (**high debugging value**)
2. **Diff viewer** (edit visualization)
3. **Multi-agent split** (Explorer / Planner / Executor as node types)
4. **Replay** (step-by-step playback)

**Recommendation:** **LLM node visualization** next — most debugging leverage.

---

## Phase 12 exit criteria

```text
✅ ExecutionGraph + graph_builder from Trace
✅ Runtime (or API) exposes serializable graph
✅ UI renders nodes/edges with selection + detail panel
✅ Status styling
✅ Plan for Langfuse alignment (same graph from internal trace or API)
✅ Retry/replan edges designed (even if minimal v1)
```

**Phase 12 done** when a run produces a **navigable graph** with **step-level** detail comparable to the spec above.
