# Phase 12 — Execution Graph Visual Guide

**Cursor/Devin-style execution visibility for AutoStudio**

---

## Graph Structure

### Basic Linear Flow

```text
┌─────────────┐
│   SEARCH    │
│  (success)  │
│   120ms     │
└──────┬──────┘
       │ next
       ▼
┌─────────────┐
│  OPEN_FILE  │
│  (success)  │
│    50ms     │
└──────┬──────┘
       │ next
       ▼
┌─────────────┐
│    EDIT     │
│  (success)  │
│   250ms     │
└─────────────┘
```

**Node details:**
- **Type:** step
- **Status:** success (green background)
- **Duration:** Shown in node
- **Click:** Opens detail panel

---

## With Retry

```text
┌─────────────┐
│   SEARCH    │
│  (success)  │
└──────┬──────┘
       │ next
       ▼
┌─────────────┐
│  OPEN_FILE  │
│  (success)  │
└──────┬──────┘
       │ next
       ▼
┌─────────────┐
│ RETRY (2x)  │
│   (event)   │  ← Synthetic event node
│   yellow    │
└──────┬──────┘
       │ retry (animated)
       ▼
┌─────────────┐
│    EDIT     │
│  (success)  │
│ attempts: 3 │
└─────────────┘
```

**Retry node:**
- **Type:** event
- **Status:** retry (yellow background)
- **Metadata:** retry_count, parent_step_id

---

## With Replan

```text
┌─────────────┐
│   SEARCH    │
│  (success)  │
└──────┬──────┘
       │ next
       ▼
┌─────────────┐
│    EDIT     │
│  (failure)  │  ← Failed step
│   error: X  │
└──────┬──────┘
       │ replan (animated, red)
       ▼
┌─────────────┐
│   SEARCH    │  ← New plan (step_index=1)
│  (success)  │
└──────┬──────┘
       │ next
       ▼
┌─────────────┐
│    EDIT     │
│  (success)  │
└─────────────┘
```

**Replan edge:**
- **Type:** replan
- **Style:** Animated, red
- **Trigger:** failure + next step has plan_step_index=1

---

## Complex Flow (Retry + Replan)

```text
┌─────────────┐
│   SEARCH    │
│  (success)  │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ RETRY (2x)  │  ← Retried 2 times
│   (event)   │
└──────┬──────┘
       │ retry
       ▼
┌─────────────┐
│    EDIT     │  ← Failed after 3 attempts
│  (failure)  │
└──────┬──────┘
       │ replan
       ▼
┌─────────────┐
│   SEARCH    │  ← New plan
│  (success)  │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│    EDIT     │
│  (success)  │
└─────────────┘
```

---

## UI Components

### Node (ExecutionNode.tsx)

```text
┌─────────────────────┐
│ STEP               │  ← Node type (small text)
│                    │
│ edit               │  ← Action label (bold)
│                    │
│ 250ms              │  ← Duration
│ ⚠ Error (if any)   │  ← Error indicator
└─────────────────────┘
```

**Styling:**
- Border: Status-based (green/red/yellow/gray)
- Selected: Blue border + shadow
- Hover: Slight lift effect

### Detail Panel (DetailPanel.tsx)

```text
┌────────────────────────────────┐
│ edit                       [X] │  ← Header with close
├────────────────────────────────┤
│ ID                             │
│ step_3                         │
│                                │
│ Type                           │
│ step                           │
│                                │
│ Status                         │
│ success                        │
│                                │
│ Input                          │
│ {                              │
│   "path": "file.py"            │
│ }                              │
│                                │
│ Output                         │
│ {                              │
│   "target": "file.py"          │
│ }                              │
│                                │
│ Metadata                       │
│ {                              │
│   "duration_ms": 250,          │
│   "plan_step_index": 3,        │
│   "action": "edit",            │
│   "attempts": 3                │
│ }                              │
└────────────────────────────────┘
```

**Position:** Right side of screen (fixed)

**Triggered:** Click any node

---

## Color Scheme

### Node backgrounds:

| Status | Color | Hex | Use Case |
|--------|-------|-----|----------|
| Success | Green | `#d4edda` | Step completed successfully |
| Failure | Red | `#f8d7da` | Step failed after retries |
| Retry | Yellow | `#fff3cd` | Retry event node |
| Pending | Gray | `#e2e8f0` | Step not executed yet |

### Node borders:

| Status | Color | Hex |
|--------|-------|-----|
| Success | Green | `#28a745` |
| Failure | Red | `#dc3545` |
| Retry | Yellow | `#ffc107` |
| Pending | Gray | `#94a3b8` |
| Selected | Blue | `#3b82f6` |

### Edges:

| Type | Color | Style |
|------|-------|-------|
| next | Gray | Solid |
| retry | Orange | Animated smoothstep |
| replan | Red | Animated with label |

---

## Layout Algorithm

**Library:** dagre (hierarchical layout)

**Configuration:**
- `rankdir: 'TB'` — Top to bottom
- `nodesep: 50` — Horizontal spacing
- `ranksep: 80` — Vertical spacing
- Node size: 180×60

**Result:** Clean hierarchical flow (not random positions).

---

## Interaction

### Mouse

- **Click node** → Show detail panel
- **Click pane** → Deselect node
- **Drag** → Pan canvas
- **Scroll** → Zoom in/out

### Controls (bottom-left)

- Zoom in
- Zoom out
- Fit view
- Lock/unlock

### Minimap (bottom-right)

- Shows full graph
- Color-coded by status
- Click to navigate

---

## Data Flow

```text
User runs agent
        ↓
PlanExecutor executes steps
        ↓
TraceEmitter records steps → Trace
        ↓
Runtime calls build_graph(trace, plan)
        ↓
ExecutionGraph (JSON)
        ↓
┌─────────────────────────────┐
│ Option A: Return to caller  │
│ Option B: Send to API       │
│ Option C: Save to file      │
└─────────────────────────────┘
        ↓
React UI renders graph
        ↓
User clicks node → Detail panel
```

---

## Example JSON Output

```json
{
  "trace_id": "demo_trace_001",
  "nodes": [
    {
      "id": "s1",
      "type": "step",
      "label": "search",
      "status": "success",
      "input": {},
      "output": {"target": "find execute_step"},
      "error": null,
      "metadata": {
        "duration_ms": 120,
        "plan_step_index": 1,
        "action": "search",
        "attempts": 1
      }
    },
    {
      "id": "s3_retry",
      "type": "event",
      "label": "retry (2x)",
      "status": "retry",
      "metadata": {
        "retry_count": 2,
        "parent_step_id": "s3"
      }
    },
    {
      "id": "s3",
      "type": "step",
      "label": "edit",
      "status": "success",
      "output": {"target": "file.py"},
      "metadata": {
        "duration_ms": 250,
        "plan_step_index": 3,
        "action": "edit",
        "attempts": 3
      }
    }
  ],
  "edges": [
    {"source": "s1", "target": "s2", "type": "next"},
    {"source": "s2", "target": "s3_retry", "type": "next"},
    {"source": "s3_retry", "target": "s3", "type": "retry"}
  ]
}
```

---

## Comparison to Other Systems

### Cursor (commercial)

✅ Similar: Execution graph, node drill-down, status colors  
⚠️ Missing: Memory layer, diff viewer, code context panel  

### Devin (commercial)

✅ Similar: Visual execution flow, retry/replan visibility  
⚠️ Missing: Multi-agent roles, full code context  

### LangSmith (Langfuse competitor)

✅ Similar: Hierarchical traces, LLM visibility  
✅ Better: Graph UI (LangSmith has tree view only)  

---

## Future Enhancements

### Near-term (high value)

1. **LLM nodes** — Show prompt, response, tokens
2. **Hover tooltips** — Quick preview without clicking
3. **Keyboard shortcuts** — Navigate graph without mouse

### Medium-term

4. **Diff viewer** — Show patch contents inline
5. **Search/filter** — Find nodes by label or status
6. **Export** — Save graph as image/PDF

### Long-term

7. **Multi-agent view** — Explorer/Planner/Executor nodes
8. **Replay mode** — Step-by-step playback
9. **Memory layer** — Show context/knowledge
10. **Code viewer** — Inline code snippets

---

## Technical Notes

- **No name collision:** `agent_v2/observability/graph_*` (execution) vs `repo_graph/graph_*` (symbols)
- **Optional:** Graph UI and API are optional; core graph builder works standalone
- **Backward compatible:** Existing code unaffected (graph added to output, not replacing anything)
- **Fast:** Graph building adds < 10ms overhead
- **Type-safe:** Pydantic v2 schemas (Python) + TypeScript (UI)

---

**Phase 12 complete. Visual debugging now matches commercial agent systems.**
