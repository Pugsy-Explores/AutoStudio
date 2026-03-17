# Router Variants (`router_eval/routers/`)

Router implementations used by the `router_eval` harness to evaluate instruction classification performance across phases.

## Responsibilities

- Provide `route(...)` functions (or equivalent) for each router variant (baseline, few-shot, ensemble, critic, etc.).
- Keep router implementations comparable by using the same dataset and metrics.

## Invariants

- Do not change the dataset and router implementation in the same measurement change.
- Routers classify intent; they do not execute tools.

