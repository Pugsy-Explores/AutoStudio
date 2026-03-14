# AutoStudio Documentation

## Overview

| Document | Description |
|----------|--------------|
| [AGENT_LOOP_WORKFLOW.md](AGENT_LOOP_WORKFLOW.md) | End-to-end agent flow: instruction → plan → execute → validate → replan. Step dispatch, SEARCH/EDIT/INFRA/EXPLAIN paths, policy engine, model routing, graph retriever, vector retriever, diff planner. |
| [AGENT_CONTROLLER.md](AGENT_CONTROLLER.md) | Full pipeline: run_controller, safety limits, test repair, task memory, trace logging. |
| [REPOSITORY_SYMBOL_GRAPH.md](REPOSITORY_SYMBOL_GRAPH.md) | Symbol graph design and implementation: indexing, graph storage, repo map, change detector, vector search, retrieval cache. |
| [CODING_AGENT_ARCHITECTURE_GUIDE.md](CODING_AGENT_ARCHITECTURE_GUIDE.md) | Architecture patterns for coding agents: retrieval, code understanding, editing, fault tolerance, memory. |

## Quick links

- **Run agent:** `python -m agent "instruction"`
- **Run controller:** `from agent.orchestrator.agent_controller import run_controller`
- **Index repo:** `python -m repo_index.index_repo <path>`
- **Planner eval:** `python -m planner.planner_eval`
- **Router eval:** `python -m router_eval.router_eval`
