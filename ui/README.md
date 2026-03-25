# Phase 12 — Execution Graph UI

React + React Flow visualization for AutoStudio execution traces.

## Features

- **Hierarchical layout** using dagre (not random positions)
- **Status-based styling** (success=green, failure=red, retry=yellow)
- **Interactive drill-down** (click node → detail panel with input/output/error/metadata)
- **Retry visualization** (synthetic event nodes with retry edges)
- **Replan visualization** (animated replan edges)
- **Sample data** (loads immediately for demo)
- **API integration** (fetch graph via `?trace_id=...`)

## Quick Start

```bash
# Install dependencies
cd ui && npm install

# Run dev server (proxies API to localhost:8000)
npm run dev

# Build for production
npm run build
```

## Usage

### View sample graph

```bash
npm run dev
# Open http://localhost:3000
```

### Fetch graph from API

```bash
# Start API server in another terminal
cd .. && python -m agent_v2.observability.server

# Open with trace_id
http://localhost:3000?trace_id=trace_123
```

## Architecture

```text
ExecutionGraph (from API or sample)
        ↓
    layoutGraph (dagre)
        ↓
    ReactFlow (nodes + edges)
        ↓
    ExecutionNode (custom component)
        ↓
    DetailPanel (click → drill-down)
```

## Components

| Component | Purpose |
|-----------|---------|
| `App.tsx` | Main entry, fetches graph from API or uses sample |
| `ExecutionGraphViewer.tsx` | React Flow wrapper, handles selection |
| `ExecutionNode.tsx` | Custom node component with status styling |
| `DetailPanel.tsx` | Right-side panel showing node details |
| `layout.ts` | Dagre-based hierarchical layout |
| `types.ts` | TypeScript types mirroring Python schemas |

## Status Colors

| Status | Background | Border | Use Case |
|--------|------------|--------|----------|
| success | `#d4edda` | `#28a745` | Step completed successfully |
| failure | `#f8d7da` | `#dc3545` | Step failed after retries |
| retry | `#fff3cd` | `#ffc107` | Retry event node |
| pending | `#e2e8f0` | `#94a3b8` | Step not yet executed |

## Edge Types

| Type | Style | Use Case |
|------|-------|----------|
| next | Solid gray | Normal flow |
| retry | Animated orange | Retry attempt |
| replan | Animated red | Replan after failure |

## Production Checklist

- [ ] Add authentication to API
- [ ] Configure CORS properly
- [ ] Add rate limiting
- [ ] Deploy with proper build process
- [ ] Add error boundaries
- [ ] Add loading states
- [ ] Add empty state handling

## Future Enhancements

- LLM nodes (show prompt/response)
- Event nodes (errors, system events)
- Diff viewer integration
- Timeline view
- Filter by status
- Search nodes
- Export graph as image
