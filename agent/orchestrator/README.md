# Orchestrator Subsystem (`agent/orchestrator/`)

Top-level agent coordination for the deterministic pipeline: controller, main agent loop, plan resolution (router + planner), replanning, and step validation. This is where "instruction → plan → execute" is assembled and wrapped with attempt-level retries.

## Responsibilities

- **Controller**: `run_controller(...)` is the main programmatic entrypoint; it sets up traces, optional bootstraps, memory hooks, and returns a task summary.
- **Plan resolution**: `get_plan()` / `get_parent_plan()` consume `RoutedIntent` from `route_production_instruction`; branch on primary intent (DOC, SEARCH, EXPLAIN, INFRA → short-circuit; EDIT, VALIDATE, AMBIGUOUS, COMPOUND-flat → planner).
- **Two-phase**: `get_parent_plan` builds hierarchical two-phase parent plans for COMPOUND + two_phase_docs_code.
- **Agent loop**: execute planned steps deterministically.
- **Replanning**: adjust plan on failures within bounded limits.
- **Step validation**: ensure the planner output matches the allowed action schema before execution.

## Plan resolver consumption (Stage 38/39)

`get_plan()` and `get_parent_plan()` use `RoutedIntent` from `route_production_instruction`. Resolver branches:

| `resolver_consumption` | Trigger | Planner called? |
|------------------------|---------|-----------------|
| `docs_seed` | DOC + docs_seed_lane | no |
| `short_search` | SEARCH | no |
| `short_explain` | EXPLAIN | no |
| `short_infra` | INFRA | no |
| `planner` | EDIT, VALIDATE, AMBIGUOUS, COMPOUND-flat, router disabled | yes |

Telemetry: `resolver_consumption`, `routed_intent_primary`, `routing_overridden_downstream` (e.g. COMPOUND on flat get_plan).

## Public API

Exports from `agent/orchestrator/__init__.py`:

- `run_controller` (`agent/orchestrator/agent_controller.py`)
- `run_agent` (`agent/orchestrator/agent_loop.py`)
- `get_plan`, `get_parent_plan` (`agent/orchestrator/plan_resolver.py`)
- `replan` (`agent/orchestrator/replanner.py`)
- `validate_step` (`agent/orchestrator/validator.py`)

## Invariants

- Do not bypass the execution layer: all tools/actions must go through dispatcher + policy engine.
- Keep stop conditions deterministic (retry caps, runtime limits).
- Preserve "retrieval before reasoning" by requiring retrieval context before code reasoning.
