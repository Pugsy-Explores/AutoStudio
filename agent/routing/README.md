# Instruction Routing (`agent/routing/`)

Instruction-level routing before the planner. This layer classifies a user instruction into a high-level intent (SEARCH, DOC, EXPLAIN, EDIT, INFRA, COMPOUND, AMBIGUOUS), enabling cheap fast-paths and consistent planning behavior. Stage 38: unified production entrypoint; Stage 39: production-honest contract.

## Responsibilities

- **Production entrypoint:** `route_production_instruction(instruction)` returns `RoutedIntent` — single source for plan_resolver.
- **Deterministic checks:** docs-artifact intent (DOC), two-phase docs+code intent (COMPOUND) — before legacy model router.
- **Legacy router:** `route_instruction` (5 categories: CODE_SEARCH, CODE_EDIT, CODE_EXPLAIN, INFRA, GENERAL) when enabled.
- **Registry:** `router_registry` wires router_eval implementations for phased evolution and evaluation.

## Public API

Exports from `agent/routing/__init__.py`:

- **Production:** `route_production_instruction`, `RoutedIntent`
- **Legacy:** `RouterDecision`, `route_instruction`
- **Registry:** `get_router`, `get_router_raw`, `list_routers`
- **Contract (Stage 39):** `PRODUCTION_EMITTABLE_PRIMARY_INTENTS`, `DEFERRED_PRIMARY_INTENTS`, `is_production_emittable_primary`
- **Docs detection:** `is_docs_artifact_intent`, `is_two_phase_docs_code_intent`
- **Test-only:** `route_intent_simple` (simple_router) — richer than production, regression tests only; not production parity.

## Production-emission contract (Stage 39)

| Intent | Production-emittable? | Resolver branch |
|--------|------------------------|-----------------|
| SEARCH | yes (legacy CODE_SEARCH) | single SEARCH step |
| DOC | yes (deterministic docs) | `_docs_seed_plan` |
| EXPLAIN | yes (legacy CODE_EXPLAIN) | single EXPLAIN step |
| EDIT | yes (legacy CODE_EDIT) | `plan()` |
| INFRA | yes (legacy INFRA) | single INFRA step |
| VALIDATE | **no** (deferred) | — |
| COMPOUND | only two-phase docs+code | parent: two-phase; flat: `plan()` |
| AMBIGUOUS | yes (GENERAL, fallback) | `plan()` |

`VALIDATE` is in `PRIMARY_INTENTS` for deserialization but **never** emitted by `route_production_instruction`. `simple_router` emits VALIDATE and general COMPOUND — test-only.

## Invariants

- Routing must remain observable and evaluation-friendly.
- Routing influences planning, but must not directly execute tools.
- `route_production_instruction` is the single production entrypoint; plan_resolver consumes only `RoutedIntent`.

