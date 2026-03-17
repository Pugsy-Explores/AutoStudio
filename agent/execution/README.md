# Execution Subsystem (`agent/execution/`)

Deterministic execution layer: takes planned steps and runs them through routing, policy checks, tool resolution, and dispatch. This is the **mechanical core** that turns “structured actions” into controlled side effects.

## Responsibilities

- **Step execution**: execute a sequence of structured steps and record results.
- **Dispatch**: route each step to the correct tool/action path via the dispatcher.
- **Policy enforcement**: ensure safety constraints and budgets are respected.
- **Tool graph**: maintain a queryable tool dependency graph and resolve tools deterministically.

## Public API

Exports from `agent/execution/__init__.py`:

- `StepExecutor` (`agent/execution/executor.py`)
- `dispatch(...)` (`agent/execution/step_dispatcher.py`)
- `ExecutionPolicyEngine` (`agent/execution/policy_engine.py`)
- `ToolGraph` (`agent/execution/tool_graph.py`)
- `resolve_tool(...)` (`agent/execution/tool_graph_router.py`)

## Invariants

- **LLMs do not directly select tools**: execution consumes structured steps; tool resolution is deterministic.
- **Dispatcher is the only tool entry point**: no bypass paths.
- **Every decision is observable**: execution emits trace events and records step results.

