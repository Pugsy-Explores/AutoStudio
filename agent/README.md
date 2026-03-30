# Agent Module (`agent/`)

Integration layer for **tools, retrieval, models, policies, and CLI** used by the **`agent_v2`** runtime. The **control plane** (exploration → plan → execute) lives in **`agent_v2/`**, not here.

## Responsibilities

- **Execution dispatch:** `agent/execution/step_dispatcher.py` — routes structured steps (`SEARCH`, `READ`, `EDIT`, `RUN_TEST`) and ReAct-shaped JSON; **`_dispatch_react`** is the default backend for `agent_v2.runtime.Dispatcher`.
- **Policy engine:** `agent/execution/policy_engine.py` — retries, query handling; respects `REACT_MODE` and `state.context["react_mode"]`.
- **Retrieval:** `agent/retrieval/` — hybrid pipeline (graph, vector, grep, rerank, …).
- **Models:** `agent/models/` — routing and `call_reasoning_model` for LLM calls.
- **Prompts:** `agent/prompt_system/` — registry used by `agent_v2.runtime.bootstrap` for ReAct-style exploration prompts.
- **CLI:** `agent/cli/` — `autostudio` entrypoints calling **`create_runtime()`** from `agent_v2.runtime.bootstrap`.
- **Editing / runtime safety:** `agent/runtime/execution_loop.py` etc. — used inside EDIT and test paths from dispatch.

## Orchestration (important)

- **`agent/orchestrator/run_controller` is removed** — `agent/orchestrator/__init__.py` raises if called.
- Use **`from agent_v2.runtime.bootstrap import create_runtime`** or **`tests.utils.runtime_adapter.run_controller`** (wrapper).

## Key entrypoints

| Entry | Location |
|-------|----------|
| CLI | `agent/cli/entrypoint.py`, `run_agent.py`, `session.py` |
| Dispatch | `agent/execution/step_dispatcher.py` |
| Planner (live) | **`agent_v2/planner/planner_v2.py`** (not `planner/` package) |

## Major subpackages

- **`agent/execution/`** — dispatcher, policy, step executor, react schema
- **`agent/retrieval/`** — hybrid retrieval
- **`agent/tools/`** — tool adapters
- **`agent/models/`** — model client
- **`agent/observability/`** — legacy trace helpers (v2 adds `agent_v2/observability/`)

See nested `README.md` files under each subpackage where present.

## Invariants

- Tools run through **dispatch** paths invoked by **`agent_v2`** `PlanExecutor` / `Dispatcher`, not ad-hoc.
- Retrieval remains the repository context source for search/read paths (see `Docs/RETRIEVAL_ARCHITECTURE.md`).
