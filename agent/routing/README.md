# Instruction Routing (`agent/routing/`)

Instruction-level routing before the planner. This layer classifies a user instruction into a high-level intent (e.g. SEARCH vs EDIT vs EXPLAIN vs INFRA), enabling cheap fast-paths and consistent planning behavior.

## Responsibilities

- Provide router decisions for an instruction (`route_instruction`).
- Maintain a registry of router implementations (for phased evolution and evaluation).

## Public API

Exports from `agent/routing/__init__.py`:

- `RouterDecision`, `route_instruction`
- `get_router`, `get_router_raw`, `list_routers`

## Invariants

- Routing must remain observable and evaluation-friendly.
- Routing influences planning, but must not directly execute tools.

