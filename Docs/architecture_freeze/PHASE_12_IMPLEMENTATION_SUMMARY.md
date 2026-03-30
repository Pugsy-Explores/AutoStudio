# Phase 12 — Execution Graph UI Implementation Summary

**Status:** ✅ COMPLETE

**Date:** 2026-03-25

---

## Overview

Phase 12 adds a **graph projection layer** and **UI visualization** on top of the existing trace system (Phase 9) and Langfuse observability (Phase 11). This provides **Cursor/Devin-style execution visibility** with nodes, edges, and a navigable graph UI.

**Key distinction:** This is **not** the symbol graph (`repo_graph/graph_builder.py`). This is the **execution graph** (steps → tools → results).

---

## Exit Criteria Verification

All Phase 12 exit criteria from `PHASE_12_EXECUTION_GRAPH_UI.md` are met:

| Criterion | Status | Implementation |
|-----------|--------|----------------|
| ✅ ExecutionGraph + graph_builder from Trace | COMPLETE | `agent_v2/observability/graph_model.py`, `graph_builder.py` |
| ✅ Runtime (or API) exposes serializable graph | COMPLETE | `agent_v2/runtime/runtime.py` + `agent_v2/observability/server.py` |
| ✅ UI renders nodes/edges with selection + detail panel | COMPLETE | `ui/src/` — React Flow + custom components |
| ✅ Status styling | COMPLETE | `ui/src/ExecutionNode.tsx` — color-coded nodes |
| ✅ Plan for Langfuse alignment | COMPLETE | Optional `plan` parameter, Langfuse API fetch planned |
| ✅ Retry/replan edges designed | COMPLETE | Synthetic retry nodes, replan edge detection |

---

## Implementation Details

### Step 1 — Graph Model (Pydantic v2)

**File:** `agent_v2/observability/graph_model.py`

```python
class GraphNode(BaseModel):
    id: str
    type: str  # "step" | "llm" | "event"
    label: str
    status: str  # "success" | "failure" | "retry" | "pending"
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
    nodes: list[GraphNode]
    edges: list[GraphEdge]
```

**Typing discipline:** v1 uses `str` for rapid UI iteration (acknowledged in spec). Future: `Literal` types aligned with `SCHEMAS.md` and `ErrorType`.

### Step 2 — Graph Builder

**File:** `agent_v2/observability/graph_builder.py`

**Function:** `build_graph(trace: Trace, plan: Optional[PlanDocument] = None) -> ExecutionGraph`

**v1 behavior:**
- One `GraphNode` per `TraceStep`
- Linear chain (`next` edges)
- Optional retry event nodes when `plan` provided

**Step 9 behavior (retry + replan):**

```python
# Retry: Synthetic event node when attempts > 1
if attempts > 1:
    retry_node = GraphNode(
        id=f"{node_id}_retry",
        type="event",
        label=f"retry ({attempts - 1}x)",
        status="retry",
    )
    edges.append(GraphEdge(source=retry_node_id, target=node_id, type="retry"))

# Replan: failure → plan_step_index=1
if prev_step_failed and step.plan_step_index == 1:
    edge_type = "replan"
```

### Step 3 — Runtime Integration

**File:** `agent_v2/runtime/runtime.py`

```python
from agent_v2.observability.graph_builder import build_graph

def normalize_run_result(mgr_out, state):
    if trace_obj is not None:
        graph_obj = build_graph(trace_obj).model_dump()
    return {
        "status": ...,
        "trace": trace_obj,
        "graph": graph_obj,  # NEW
        "state": state,
    }
```

**Output shape (Phase 10 + Phase 12):**

```json
{
  "status": "success",
  "trace": { /* Trace object */ },
  "graph": { /* ExecutionGraph object */ },
  "state": { /* AgentState */ }
}
```

### Step 4 — Graph API (FastAPI)

**File:** `agent_v2/observability/server.py`

**Endpoints:**

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check |
| `/graph` | POST | Generate graph from trace |
| `/` | GET | API info |

**Usage:**

```bash
# Start server
python -m agent_v2.observability.server
# or
uvicorn agent_v2.observability.server:app --reload

# Test
curl -X POST http://localhost:8000/graph \
  -H "Content-Type: application/json" \
  -d '{"trace": {...}}'
```

**Production TODO:** Authentication, CORS config, rate limiting, no arbitrary code execution.

### Step 5 — Graph UI (React Flow)

**Directory:** `ui/`

**Stack:**
- React 18
- React Flow (@xyflow/react) 12.x
- TypeScript 5.x
- Vite 5.x
- dagre (hierarchical layout)

**Components:**

| File | Purpose |
|------|---------|
| `App.tsx` | Main entry, fetches graph from API or sample |
| `ExecutionGraphViewer.tsx` | React Flow wrapper, handles selection |
| `ExecutionNode.tsx` | Custom node with status styling |
| `DetailPanel.tsx` | Right-side drill-down panel |
| `layout.ts` | Dagre-based hierarchical layout |
| `types.ts` | TypeScript mirrors Python schemas |

**Usage:**

```bash
cd ui
npm install
npm run dev  # http://localhost:3000
```

**Features:**
- Hierarchical layout (dagre, not random)
- Status-based colors (green/red/yellow/gray)
- Click node → detail panel (input/output/error/metadata)
- Animated retry/replan edges
- Minimap, zoom, pan controls
- Sample graph for demo

### Step 6 — Node Detail Panel

**File:** `ui/src/DetailPanel.tsx`

**On node click, shows:**

```text
ID: step_1
Type: step
Status: success
Input: {...}
Output: {...}
Error: (if any)
Metadata: {duration_ms, plan_step_index, action, attempts}
```

### Step 7 — Status Colors

**File:** `ui/src/ExecutionNode.tsx`

```typescript
const STATUS_COLORS = {
  success: '#d4edda',  // green
  failure: '#f8d7da',  // red
  retry: '#fff3cd',    // yellow
  pending: '#e2e8f0',  // gray
};
```

### Step 8 — Langfuse Alignment Plan

**Two modes:**

| Mode | Flow | Status |
|------|------|--------|
| **A — Simple** | Runtime returns `trace` + `graph` from same run | ✅ Implemented |
| **B — Better** | Fetch trace from Langfuse API by id → normalize → `build_graph` | 📋 Planned |

**Current:** Mode A (internal `Trace` → `ExecutionGraph`)

**Future:** Mode B (Langfuse API → normalized format → `ExecutionGraph`)

**Principle:** One conceptual graph, multiple data sources (internal trace OR Langfuse export).

### Step 9 — Retry + Replan Edges

**Retry edges:**

```text
(prev_step) → [retry event] → (step with attempts > 1)
              type="retry"
```

**Replan edges:**

```text
(failed step) → (step with plan_step_index=1)
                type="replan"
```

**Implementation:** `agent_v2/observability/graph_builder.py` lines 50-75

**Design choice:** Synthetic retry event nodes (not self-loops) for UI clarity.

### Step 10 — Final Experience

**User sees:**

```text
(search) → (open_file) → [retry 1x] → (edit) → (run_tests)
                                  ↘ replan
                                  ↘ (search again)
```

**Node drill-down:**
- Click node → detail panel
- Shows: input, output, error, duration_ms, attempts
- Later: LLM prompt snippets when LLM nodes exist

---

## Architecture

```text
Langfuse (storage + tracing)     ← Phase 11
        ↓
agent_v2.schemas.trace.Trace     ← Phase 9
        ↓
Graph builder (projection)       ← THIS PHASE
        ↓
ExecutionGraph (JSON)
        ↓
Graph API (FastAPI, optional)
        ↓
Graph UI (React Flow)
```

**Internal source of truth:** `agent_v2.schemas.trace.Trace` (Phase 9)

**External source (future):** Langfuse API (fetch by trace_id)

---

## Test Coverage

**File:** `tests/test_execution_graph.py`

**Test classes:**

1. `TestGraphModel` — GraphNode, GraphEdge, ExecutionGraph validation
2. `TestGraphBuilderBasic` — Empty trace, single step, linear chain, errors
3. `TestGraphBuilderRetryEdges` — Retry event nodes when attempts > 1
4. `TestGraphBuilderReplanEdges` — Replan edge when failure → step_index=1
5. `TestGraphBuilderIntegration` — Runtime output includes graph
6. `TestGraphBuilderEdgeCases` — Without plan, multiple retries
7. `TestGraphBuilderComplexFlow` — Retry + replan together
8. `TestGraphStatusColors` — Status field for UI styling
9. `TestGraphMetadata` — Metadata for drill-down

**Test results:** 22 passed (100%)

**Broader regression:** 60 tests passed (execution_graph, langfuse_phase11, plan_executor, planner_v2)

---

## Changes Summary

### New files:

**Backend:**
- `agent_v2/observability/graph_model.py` — Pydantic schemas for ExecutionGraph
- `agent_v2/observability/graph_builder.py` — Convert Trace → ExecutionGraph
- `agent_v2/observability/server.py` — FastAPI backend (optional)
- `tests/test_execution_graph.py` — Comprehensive test suite

**Frontend (optional):**
- `ui/package.json` — React + React Flow + dagre dependencies
- `ui/vite.config.ts` — Vite build config with API proxy
- `ui/tsconfig.json` — TypeScript config
- `ui/index.html` — HTML entry point
- `ui/src/main.tsx` — React entry point
- `ui/src/App.tsx` — Main app (fetch or sample data)
- `ui/src/ExecutionGraphViewer.tsx` — React Flow wrapper
- `ui/src/ExecutionNode.tsx` — Custom node component with status colors
- `ui/src/DetailPanel.tsx` — Click detail panel
- `ui/src/layout.ts` — Dagre hierarchical layout
- `ui/src/types.ts` — TypeScript types
- `ui/README.md` — UI documentation
- `ui/.gitignore` — UI gitignore

### Modified files:

- `agent_v2/runtime/runtime.py` — Added `build_graph` import and graph in output
- `requirements.txt` — Added `fastapi>=0.100.0`, `uvicorn>=0.23.0`

---

## Usage

### Python API

```python
from agent_v2.runtime.runtime import AgentRuntime

runtime = AgentRuntime(...)
result = runtime.run("Add logging to execute_step", mode="act")

# result contains:
# - result["trace"]  # Phase 9: internal Trace
# - result["graph"]  # Phase 12: ExecutionGraph (JSON-serializable)
# - result["state"]  # AgentState
```

### FastAPI Server

```bash
# Start server
python -m agent_v2.observability.server

# Or with uvicorn
uvicorn agent_v2.observability.server:app --reload --port 8000

# Endpoints
GET  /health        # Health check
POST /graph         # Generate graph from trace
GET  /              # API info
```

### React UI

```bash
# Install dependencies
cd ui && npm install

# Development (proxies /api to localhost:8000)
npm run dev  # http://localhost:3000

# Production build
npm run build
npm run preview
```

**Sample graph:** Loads immediately for demo

**API integration:** Add `?trace_id=...` to fetch from backend

---

## Graph Visualization Features

### Node Types

| Type | Color | Icon | Purpose |
|------|-------|------|---------|
| `step` | Status-based | — | Plan step execution |
| `event` | Yellow | — | Retry events |
| `llm` | — | — | LLM calls (future) |

### Edge Types

| Type | Style | Color | Purpose |
|------|-------|-------|---------|
| `next` | Solid | Gray | Normal flow |
| `retry` | Animated smoothstep | Orange | Retry attempt |
| `replan` | Animated with label | Red | Replan after failure |

### Status Colors

| Status | Background | Border |
|--------|------------|--------|
| success | `#d4edda` | `#28a745` |
| failure | `#f8d7da` | `#dc3545` |
| retry | `#fff3cd` | `#ffc107` |
| pending | `#e2e8f0` | `#94a3b8` |

### Interaction

- **Click node** → Detail panel (right side)
- **Click pane** → Deselect
- **Minimap** → Navigate large graphs
- **Controls** → Zoom, fit view, lock

---

## Architectural Compliance

Phase 12 implementation follows all architectural freeze rules:

- ✅ **Rule 1** — No execution engine redesign (only visualization layer)
- ✅ **Rule 17** — Extension over replacement (builds on Phase 9 Trace)
- ✅ **Rule 19** — Shared infrastructure (same Trace schema)
- ✅ No new control-plane features
- ✅ No modification to execution semantics

---

## Coexistence with Phase 9 + Phase 11

| Phase | Artifact | Purpose |
|-------|----------|---------|
| **Phase 9** | `agent_v2.schemas.trace.Trace` | Internal execution graph (CLI, replay, tests) |
| **Phase 11** | Langfuse trace/spans | External observability (team UI, retention, LLM visibility) |
| **Phase 12** | `ExecutionGraph` | Graph projection for UI visualization (nodes + edges) |

All three are **independent** and **complementary**:
- Phase 9: Serializable trace for persistence
- Phase 11: External observability for team debugging
- Phase 12: Graph visualization for execution flow understanding

---

## Expected UI Shape

### Linear execution:

```text
┌──────────┐    ┌──────────┐    ┌──────────┐
│  search  │───▶│open_file │───▶│   edit   │
│ success  │    │ success  │    │ success  │
└──────────┘    └──────────┘    └──────────┘
```

### With retry:

```text
┌──────────┐    ┌──────────┐    ┌──────────┐
│  search  │───▶│ retry 2x │───▶│   edit   │
│ success  │    │  event   │~~~▶│ success  │
└──────────┘    └──────────┘    └──────────┘
                                     (retry edge)
```

### With replan:

```text
┌──────────┐    ┌──────────┐
│   edit   │    │  search  │
│ failure  │═══▶│ success  │
└──────────┘    └──────────┘
                (replan edge)
```

Legend:
- `───▶` next edge (solid gray)
- `~~~▶` retry edge (animated orange)
- `═══▶` replan edge (animated red)

---

## Future Enhancements (Out of Scope)

Phase 12 spec lists next steps:

1. **LLM nodes + prompt inspection** (high debugging value)
2. **Diff viewer** (edit visualization)
3. **Multi-agent split** (Explorer/Planner/Executor as node types)
4. **Replay** (step-by-step playback)

**Recommendation:** LLM node visualization next (most debugging leverage).

---

## Common Mistakes Avoided

❌ Random node positions → ✅ Dagre hierarchical layout  
❌ Flat node list → ✅ Structured graph (nodes + edges)  
❌ No drill-down → ✅ Detail panel on click  
❌ No retry/replan visibility → ✅ Synthetic retry nodes + replan edges  
❌ Generic styling → ✅ Status-based colors  

---

## Dependencies Added

**Python:**
- `fastapi>=0.100.0` — Graph API server
- `uvicorn>=0.23.0` — ASGI server

**JavaScript (ui/):**
- `react@^18.2.0` — UI framework
- `@xyflow/react@^12.0.0` — Flow diagram library
- `dagre@^0.8.5` — Hierarchical layout
- `vite@^5.0.0` — Build tool
- `typescript@^5.3.0` — Type safety

---

## Principal Verdict

```text
Trace system (Phase 9) ✅
Langfuse observability (Phase 11) ✅
Graph projection (Phase 12) ✅
UI visualization (Phase 12) ✅
```

**Enables:**

- Visual execution flow (Cursor/Devin-style)
- Click-to-drill-down debugging
- Retry and replan visibility
- Production execution monitoring
- Team collaboration on failed runs

**Still missing (acknowledged in spec):**

- Memory layer
- Multi-agent roles
- Diff visualizer
- Code context viewer
- LLM prompt inspection

**Core execution visibility** is now **correct** with Phases 9–12.

---

## Production Checklist

**Backend (server.py):**
- [ ] Add authentication (API key or OAuth)
- [ ] Configure CORS for production origins
- [ ] Add rate limiting (per-user or per-IP)
- [ ] Add request validation
- [ ] Add logging and monitoring
- [ ] Deploy with proper process manager

**Frontend (ui/):**
- [ ] Add error boundaries
- [ ] Add loading states
- [ ] Handle empty graphs
- [ ] Add graph search/filter
- [ ] Add export to image
- [ ] Deploy with CDN
- [ ] Add analytics

---

## Notes

- **Naming:** `agent_v2/observability/graph_*` for execution graph vs `repo_graph/graph_*` for symbol graph (no collision)
- **Optional dependencies:** FastAPI and UI are optional; graph builder works standalone
- **Backward compatibility:** Runtime output includes graph when trace exists; no breaking changes
- **Test isolation:** Phase 12 tests don't depend on Phase 11 Langfuse keys
