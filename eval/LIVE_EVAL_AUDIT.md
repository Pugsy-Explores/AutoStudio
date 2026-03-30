# Live vs offline evaluation audit (AutoStudio)

Classification: **LIVE** = calls `call_reasoning_model` (or messages) for the graded component; **MOCKED** = no LLM; **PARTIAL** = mixed (e.g. real retrieval, mocked LLM).

## Tiered harness (`eval/`)

| Entry | Class | Notes |
|-------|--------|--------|
| `eval/runner.py` | MOCKED | Loads JSON, optional `ExecutorFn`; default executor is `None` (heuristic scores only). |
| `eval/live_executor.py` | LIVE | Canonical `live_executor` → `create_runtime().explore` → `planner.plan` → `synthesize_answer` → `validate_answer`. |
| `tiered-eval` CLI / `python -m eval.runner` | MOCKED | Unless `--live` and credentials. |

## `tests/evals/` (component LLM evals)

| Module | File | Class | Gate env |
|--------|------|--------|-----------|
| PlannerV2 decision | `test_planner_v2_eval.py` | LIVE | `PLANNER_V2_EVAL_LIVE=1` |
| Query intent | `test_query_intent_parser_eval.py` | LIVE | `QUERY_INTENT_PARSER_EVAL_LIVE=1` |
| Scoper | `test_scoper_eval.py` | LIVE | `SCOPER_EVAL_LIVE=1` |
| Selector batch | `test_selector_batch_eval.py` | LIVE | `SELECTOR_BATCH_EVAL_LIVE=1` |
| Analyzer | `test_analyzer_eval.py` | LIVE | `ANALYZER_EVAL_LIVE=1` |
| Agent benchmark | `test_software_agent_benchmark.py` | PARTIAL | Depends on fixtures |
| Harness | `agent_eval_harness.py` | MOCKED | Mocks execution loop; no planner LLM |

## Other eval entry points

| Module | Path | Class |
|--------|------|--------|
| Legacy planner | `planner/planner_eval.py` | MOCKED (structural / action match on `plan()` if wired) |
| Router | `router_eval/router_eval.py` | PARTIAL (`--mock` vs live) |
| Prompt bench | `agent/prompt_eval/eval_runner.py` | LIVE when `run_fn` calls model |
| Agent v2 phases | `tests/test_agent_v2_phases_live.py` | LIVE | `AGENT_V2_LIVE=1` |
| Exploration bounded | `tests/test_exploration_phase_126_bounded_read_live.py` | LIVE | same |

## Validator

| Path | Class |
|------|--------|
| `tests/test_answer_validation.py` | MOCKED / PARTIAL (rules-only; LLM path behind config + mocks) |
| Production | `validate_answer` | LIVE optional via `planner_loop.enable_answer_validation_llm` |

## Mocked layers (what to replace)

| Pattern | Replace with |
|---------|----------------|
| `executor=None` in `run_tiered_eval` | `live_executor` from `eval/live_executor.py` |
| YAML-only planner cases without `PlannerV2.plan` | Already live in `test_planner_v2_eval` — tiered JSON should call `live_executor` |
| `agent_eval_harness` mocked loop | Full `AgentRuntime.run` / `PlannerTaskRuntime` for E2E (separate from tiered capture) |

## Policy

- **CI default:** tiered JSON + heuristic scoring stays fast and offline.
- **Continuous benchmarking:** set `TIERED_EVAL_LIVE=1` or pass `--live` to `eval.runner` with secrets configured; same code path as production bootstrap.

## Pipeline capture (iteration + state)

Live runs populate `loop_meta.steps` (per outer iteration: `decision`, `validation`, `state_summary`), `total_iterations`, `validation_failures`, and `state.final` / `state.progression` (compressed phase snapshots). See `eval/schema/pipeline_capture.schema.json`.

**Note:** The Python package is named `eval` (shadows builtin `eval`); long-term rename to `tiered_eval` is recommended (see backlog).
