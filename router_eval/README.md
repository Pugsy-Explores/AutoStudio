# Router Eval (`router_eval/`)

Harness for evaluating **instruction routers** (category routing: EDIT, SEARCH, EXPLAIN, INFRA, …). Production routing uses **`route_production_instruction`** in `agent/routing/`; this package isolates datasets and metrics.

## Run

```bash
python -m router_eval.router_eval
python -m router_eval.router_eval --mock
python -m router_eval.run_all_routers
python -m router_eval.run_all_routers --production
```

## Config

Reads from `agent/models/models_config.json` (task models, endpoints). Env overrides: `ROUTER_LLM_BASE_URL`, `ROUTER_LLM_API_KEY`.

## Relation to `agent_v2`

Router output may **precede** planning in older **`agent/`** flows. **`agent_v2`** `PlanExecutor` does not depend on this package directly; routing is still relevant for CLI paths that classify intent before invoking the runtime.

## See also

[`agent/routing/README.md`](../agent/routing/README.md) for production routing contract.
