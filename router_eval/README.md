# Router Eval

Phased router evolution and evaluation harness. Same dataset for all phases; swap router by changing one import in `router_eval.py`.

## Run evaluation

From the **AutoStudio** project root:

```bash
python -m router_eval.router_eval
```

Optional: set `ROUTER_LLM_BASE_URL` and `ROUTER_LLM_MODEL` to point at your LLM (OpenAI-compatible API).

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
