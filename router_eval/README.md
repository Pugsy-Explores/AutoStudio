# Router Eval

Phased router evolution and evaluation harness. Same dataset for all phases; swap router by changing one import in `router_eval.py`. Categories: EDIT, SEARCH, EXPLAIN, INFRA, GENERAL.

## Run evaluation

From the **AutoStudio** project root:

```bash
python -m router_eval.router_eval
```

Use `--mock` to run without an LLM server (stub router; verifies dataset load and metrics):

```bash
python -m router_eval.router_eval --mock
```

Run all routers (or `--mock` for no LLM):

```bash
python -m router_eval.run_all_routers
python -m router_eval.run_all_routers --mock
```

Run with production router integration (uses `ROUTER_TYPE` from env):

```bash
python -m router_eval.run_all_routers --production
```

**Config:** Reads from `agent/models/models_config.json`:
- `task_models.routing` → model key (e.g. SMALL)
- `models.SMALL.endpoint` → LLM endpoint (e.g. `http://localhost:8081/v1/chat/completions`)
- `task_params.routing` → temperature, max_tokens, request_timeout_seconds

Override with env: `ROUTER_LLM_BASE_URL`, `ROUTER_LLM_API_KEY` (when agent package not importable).

## Swap router

Edit `router_eval.py` and change the active import:

```python
from router_eval.routers.baseline_router import route
# from router_eval.routers.fewshot_router import route
# from router_eval.routers.ensemble_router import route
# ...
```

## Phases

| Phase | Router | Description |
|-------|--------|-------------|
| 1 | baseline_router | Single prompt → category |
| 2 | fewshot_router | Few-shot examples in prompt |
| 3 | ensemble_router | Three prompts, majority vote |
| 4 | confidence_router | Category + confidence, ensemble |
| 5 | dual_router | Primary + secondary + confidence |
| 6 | critic_router | Critic when low conf or ambiguity |
| 7 | final_router | Fast accept if high conf + agree, else critic |

**Rule:** Do not change the dataset and a router in the same change. The dataset is the fixed test suite.

## See also

- [Docs/PROMPT_ARCHITECTURE.md](../Docs/PROMPT_ARCHITECTURE.md) — Prompt layer: router prompts, few-shot strategy, evaluation prompts
- [Docs/CONFIGURATION.md](../Docs/CONFIGURATION.md) — Centralized config (ROUTER_TYPE, etc.)
- [Docs/AGENT_LOOP_WORKFLOW.md](../Docs/AGENT_LOOP_WORKFLOW.md) — Agent execution flow
- [README.md](../README.md) — Project overview
