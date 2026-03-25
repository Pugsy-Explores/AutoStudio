# `agent_v2/` — Current agent runtime

Planner-centric pipeline: **exploration → `PlanDocument` → plan-driven execution**, with shared observability (Langfuse, execution graph).

## Purpose

- Own **`AgentRuntime`**, **`ModeManager`**, and execution **modes** (`act`, `plan`, `deep_plan`, `plan_execute`).
- Isolate **schema-validated** types (`schemas/`) from legacy `agent.*` except at explicit seams (`runtime/bootstrap.py`, `runtime/plan_argument_generator.py`).

## Layout

| Area | Responsibility |
|------|------------------|
| **`runtime/`** | `AgentRuntime`, `ModeManager`, `PlanExecutor`, `ExplorationRunner`, `Dispatcher`, `AgentLoop`, `bootstrap.create_runtime`, `cli_adapter` |
| **`planner/`** | `PlannerV2` → `PlanDocument` (no tools) |
| **`exploration/`** | Exploration engine v2, candidate selection, graph expansion, understanding analyzer |
| **`schemas/`** | Pydantic: `PlanDocument`, `ExplorationResult`, `ExecutionResult`, replan types |
| **`validation/`** | `PlanValidator` |
| **`observability/`** | Langfuse client, trace/graph builders, optional graph server |
| **`state/`** | `AgentState` dataclass |
| **`config.py`** | Exploration flags, step limits, policy defaults |

## Key classes

- **`AgentRuntime`** (`runtime/runtime.py`) — `run(instruction, mode=...)`, `explore(instruction)` for exploration-only.
- **`ModeManager`** (`runtime/mode_manager.py`) — mode switch; **does not** call `AgentLoop.run()` for `act`.
- **`ExplorationRunner`** (`runtime/exploration_runner.py`) — bounded read-only exploration; yields `ExplorationResult`.
- **`PlannerV2`** (`planner/planner_v2.py`) — LLM generates JSON plan; `deep=True` for `deep_plan` mode.
- **`PlanExecutor`** (`runtime/plan_executor.py`) — executes `PlanDocument` with retries and `Replanner`.
- **`AgentLoop`** (`runtime/agent_loop.py`) — generic action/dispatch/observe loop; used in tests; **not** the `act` path in `ModeManager`.

## Inputs / outputs

- **Input:** `instruction: str`, `mode` in `{"act","plan","deep_plan","plan_execute"}`.
- **Output:** `dict` with `status`, `trace`, optional `graph`, `state` (`normalize_run_result` in `runtime.py`); CLI uses `format_output` in `cli_adapter.py`.

## Dependencies

- **Uses:** `agent.models.model_client`, `agent.execution.step_dispatcher._dispatch_react`, `agent.execution.react_schema`, `agent.prompt_system` (via bootstrap only).
- **Used by:** `agent/cli/*`, `tests/utils/runtime_adapter.py`, tests.

## Example flow

```
create_runtime() → AgentRuntime.run("fix bug", mode="act")
  → ModeManager._run_act
  → ExplorationRunner.run → ExplorationResult
  → PlannerV2.plan(deep=False) → PlanDocument
  → PlanExecutor.run → Dispatcher → _dispatch_react
```
