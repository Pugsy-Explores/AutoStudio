# UI — Execution graph (`ui/`)

React + React Flow **visualization** for AutoStudio execution traces and graphs (Phase 12). Consumes trace/graph payloads produced by **`agent_v2/observability/`** (see `graph_model.py` types mirrored in `src/types.ts`).

## Features

- Hierarchical layout (dagre), status styling, drill-down panels
- Retry/replan edge visualization
- Optional fetch by `trace_id` query param

## Quick start

```bash
cd ui && npm install && npm run dev
```

## API

Optional dev server: `python -m agent_v2.observability.server` (see comments in UI README for port/proxy).

## Relation to runtime

The UI **does not** run the agent; it displays **observability** data from completed or streaming runs. Primary execution is **`agent_v2`** + CLI.
