# Phase 12 — Execution Graph Quick Start

**Goal:** Visualize agent execution with a navigable graph UI (Cursor/Devin-style).

---

## 1. Run the demo

```bash
cd /Users/shang/my_work/AutoStudio

# Demo the graph builder
python3 scripts/demo_execution_graph.py
```

**Output:** JSON execution graph with nodes (steps, retry events) and edges (next, retry, replan).

---

## 2. Start the API server

```bash
# Terminal 1 — Start FastAPI server
python3 -m agent_v2.observability.server

# Or with uvicorn
uvicorn agent_v2.observability.server:app --reload --port 8000
```

**Endpoints:**
- `GET /health` — Health check
- `POST /graph` — Generate graph from trace
- `GET /` — API info

---

## 3. Start the React UI

```bash
# Terminal 2 — Install dependencies (first time only)
cd ui && npm install

# Start dev server
npm run dev
```

**Opens:** `http://localhost:3000`

**Features:**
- Sample graph loads immediately
- Click nodes → detail panel (input, output, error, metadata)
- Status colors: green (success), red (failure), yellow (retry)
- Minimap, zoom, pan controls
- Retry event nodes, replan edges

---

## 4. Integrate with agent runtime

```python
from agent_v2.runtime.runtime import AgentRuntime
from agent_v2.runtime.bootstrap import create_runtime

# Create runtime
runtime = create_runtime(project_root=".")

# Run agent (returns trace + graph)
result = runtime.run("Add logging to execute_step", mode="act")

# Access graph
trace = result["trace"]  # Phase 9: internal Trace
graph = result["graph"]  # Phase 12: ExecutionGraph (JSON)
state = result["state"]  # AgentState

# Graph structure
print(f"Trace ID: {graph['trace_id']}")
print(f"Nodes: {len(graph['nodes'])}")
print(f"Edges: {len(graph['edges'])}")

# Send to API or save to file
import json
with open("execution_graph.json", "w") as f:
    json.dump(graph, f, indent=2)
```

---

## 5. View graph in UI

### Option A — Load from file

1. Save graph to `ui/public/graph.json`
2. Update `App.tsx` to fetch from `/graph.json`
3. Refresh browser

### Option B — Fetch from API

1. Ensure API server running (`python -m agent_v2.observability.server`)
2. Open `http://localhost:3000?trace_id=YOUR_TRACE_ID`
3. UI fetches graph via `POST /graph`

### Option C — Use sample graph

1. Open `http://localhost:3000`
2. Sample graph loads immediately (no backend needed)

---

## Graph Structure Examples

### Simple linear flow:

```text
(search) → (open_file) → (edit)
```

### With retry:

```text
(search) → (open_file) → [retry 2x] → (edit)
```

### With replan:

```text
(search) → (edit: failed) ═══▶ (search: new plan) → (edit)
                          replan
```

---

## Architecture

```text
agent_v2.runtime.runtime
        ↓
AgentRuntime.run()
        ↓
ModeManager → ExplorationRunner → Planner → PlanExecutor
        ↓
PlanExecutor builds Trace (Phase 9)
        ↓
runtime.normalize_run_result calls build_graph(trace)
        ↓
Returns {"trace": ..., "graph": ..., "state": ...}
```

---

## Files Overview

| File | Purpose |
|------|---------|
| `agent_v2/observability/graph_model.py` | Pydantic schemas (GraphNode, GraphEdge, ExecutionGraph) |
| `agent_v2/observability/graph_builder.py` | Convert Trace → ExecutionGraph |
| `agent_v2/observability/server.py` | FastAPI backend (optional) |
| `agent_v2/runtime/runtime.py` | Adds graph to runtime output |
| `tests/test_execution_graph.py` | 22 tests (100% pass) |
| `scripts/demo_execution_graph.py` | Demo script |
| `ui/src/App.tsx` | React main app |
| `ui/src/ExecutionGraphViewer.tsx` | React Flow wrapper |
| `ui/src/ExecutionNode.tsx` | Custom node component |
| `ui/src/DetailPanel.tsx` | Drill-down panel |
| `ui/src/layout.ts` | Dagre hierarchical layout |
| `ui/README.md` | UI documentation |

---

## Testing

```bash
# Run Phase 12 tests
python3 -m pytest tests/test_execution_graph.py -v

# Run Phase 11 + 12 + core tests
python3 -m pytest tests/test_execution_graph.py tests/test_langfuse_phase11.py tests/test_plan_executor.py -v

# Expected: 67 passed
```

---

## Production Checklist

**Backend:**
- [ ] Add authentication to `/graph` endpoint
- [ ] Configure CORS for production domains
- [ ] Add rate limiting
- [ ] Add request logging
- [ ] Deploy with proper ASGI server
- [ ] Add monitoring/alerts

**Frontend:**
- [ ] Deploy with CDN
- [ ] Add error boundaries
- [ ] Add loading spinners
- [ ] Add graph search/filter
- [ ] Add export to image/PDF
- [ ] Add keyboard shortcuts
- [ ] Add accessibility (ARIA)

---

## Next Steps

From `PHASE_12_EXECUTION_GRAPH_UI.md`:

1. **LLM nodes + prompt inspection** (recommended: high debugging value)
2. Diff viewer (edit visualization)
3. Multi-agent split (Explorer/Planner/Executor node types)
4. Replay (step-by-step playback)

---

## Troubleshooting

### Graph not showing in runtime output

```python
# Check trace exists
result = runtime.run(...)
assert result["trace"] is not None

# Check graph built
assert result["graph"] is not None
assert len(result["graph"]["nodes"]) > 0
```

### UI not loading

```bash
# Check API server running
curl http://localhost:8000/health

# Check UI dev server
cd ui && npm run dev
```

### Retry edges not showing

```python
# Must pass plan parameter to build_graph
graph = build_graph(trace, plan=plan_document)

# Check plan steps have execution.attempts
for step in plan.steps:
    print(f"{step.step_id}: {step.execution.attempts} attempts")
```

---

## Summary

Phase 12 provides **Cursor/Devin-style execution visibility**:

- ✅ Graph projection (Trace → ExecutionGraph)
- ✅ Runtime integration (graph in output)
- ✅ FastAPI backend (optional)
- ✅ React Flow UI (optional)
- ✅ Retry + replan edges
- ✅ Status colors
- ✅ Detail drill-down
- ✅ 22 comprehensive tests

**Ready for:** Production use, team debugging, execution monitoring.
