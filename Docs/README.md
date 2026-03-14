# AutoStudio Documentation

## Overview

| Document | Description |
|----------|--------------|
| [AGENT_LOOP_WORKFLOW.md](AGENT_LOOP_WORKFLOW.md) | End-to-end agent flow: instruction → plan → execute → validate → replan. Step dispatch, SEARCH/EDIT/INFRA/EXPLAIN paths, policy engine, model routing, graph retriever, vector retriever, diff planner. |
| [AGENT_CONTROLLER.md](AGENT_CONTROLLER.md) | Full pipeline: run_controller, instruction router, safety limits, test repair, task memory, trace logging. |
| [ROUTING_ARCHITECTURE_REPORT.md](ROUTING_ARCHITECTURE_REPORT.md) | Routing architecture: instruction router, tool graph (retrieve_graph/vector/grep/list_dir), Serena and filesystem rules, query rewrite prompts, replanner. |
| [REPOSITORY_SYMBOL_GRAPH.md](REPOSITORY_SYMBOL_GRAPH.md) | Symbol graph design and implementation: indexing, graph storage, repo map, change detector, vector search, retrieval cache, editing pipeline (diff_planner, patch_generator, ast_patcher, patch_validator, patch_executor), testing and validation. |
| [CODING_AGENT_ARCHITECTURE_GUIDE.md](CODING_AGENT_ARCHITECTURE_GUIDE.md) | Architecture patterns for coding agents: retrieval, code understanding, editing, fault tolerance, memory. |

## Agent robustness

The agent handles failure scenarios without crashing or corrupting the repository:

- **Nonexistent symbol search:** Policy retries with rewritten query; fallback chain (retrieve_graph → retrieve_vector → retrieve_grep → list_dir → Serena) exhausted before returning failure.
- **Invalid edit / patch validator failure:** Rollback restores all modified files; no partial writes.
- **Graph lookup empty:** Falls through to vector search, then Serena.
- **Replan on failure:** LLM-based replanner receives failed_step and error; up to 5 replan attempts; failed steps do not advance; remaining steps retried.

See `tests/test_agent_robustness.py` for coverage.

## Quick links

- **Run agent:** `python -m agent "instruction"`
- **Run controller:** `from agent.orchestrator.agent_controller import run_controller`
- **Index repo:** `python -m repo_index.index_repo <path>` (API supports `include_dirs` for partial indexing)
- **E2E tests:** `python -m pytest tests/test_agent_e2e.py -v` (default: try LLM, fallback to mock; `--mock` to force mock)
- **Robustness tests:** `python -m pytest tests/test_agent_robustness.py -v` (failure scenarios, replan, fallback, no corruption)
- **Trajectory tests:** `python -m pytest tests/test_agent_trajectory.py -v --mock` (complex tasks: multi-search, conflict resolver, repair loop)
- **Observability tests:** `python -m pytest tests/test_observability.py -v` (trace creation, plan, tool calls, errors, patch results)
- **Planner eval:** `python -m planner.planner_eval`
- **Router eval:** `python -m router_eval.router_eval`
