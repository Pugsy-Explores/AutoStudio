# AutoStudio Anti-Cheating Model Usage Audit

**Date:** 2025-03-20  
**Method:** Code-only trace from entrypoints; no reliance on README, stage docs, or benchmark summaries.

---

## Executive Summary

**Verdict: Mostly heuristic/stub-driven in benchmark paths.**

The agent_eval benchmark (`--execution-mode real`) **never** calls the hosted local LLM. All model entry points are patched by `offline_llm_stubs()` in `run_structural_agent_real()`. Production CLI (`python -m agent`) does call the real model when run without the harness.

---

## 1. Entry Point Trace

### Production / Runtime Path

| Entry | File | Function | Downstream Model Call |
|-------|------|----------|------------------------|
| CLI | `agent/cli/run_agent.py` | `main()` | → `run_controller()` |
| Controller | `agent/orchestrator/agent_controller.py` | `run_controller()` | → `run_attempt_loop()` → `run_deterministic()` |
| Deterministic | `agent/orchestrator/deterministic_runner.py` | `run_deterministic()` | → `get_plan()` (planner), `execution_loop()` |
| Plan resolver | `agent/orchestrator/plan_resolver.py` | `get_plan()` | → `plan()` (planner) or `route_instruction()` (router) |
| Planner | `planner/planner.py` | `plan()` | → `call_reasoning_model()` |
| Execution loop | `agent/orchestrator/execution_loop.py` | `execution_loop()` | → step dispatcher, retrieval, editing |
| Step dispatcher | `agent/execution/step_dispatcher.py` | EXPLAIN handler | → `call_small_model()`, `call_reasoning_model()` |
| Query rewriter | `agent/retrieval/query_rewriter.py` | `rewrite_query()` | → `call_reasoning_model()`, `call_small_model()` |
| Context ranker | `agent/retrieval/context_ranker.py` | `rank_context()` | → `call_reasoning_model()` |
| Validator | `agent/orchestrator/validator.py` | validation | → `call_reasoning_model()`, `call_small_model()` |
| Replanner | `agent/orchestrator/replanner.py` | replan | → `call_reasoning_model()`, `call_small_model()` |
| Instruction router | `agent/routing/instruction_router.py` | `route_instruction()` | → `call_small_model()` |
| Context summarizer | `agent/prompt_system/context/context_summarizer.py` | `summarize_large_block()` | → `call_small_model()` |
| Critic | `agent/meta/critic.py` | `_generate_strategy_hint_llm()` | → `call_reasoning_model()` |

**Production path:** Uses real `model_client._call_chat()` → HTTP to OpenAI-compatible endpoint. No stubs when run via `python -m agent`.

### Benchmark Harness Path

| Entry | File | Function | Model Call? |
|-------|------|----------|-------------|
| Runner | `tests/agent_eval/runner.py` | `main()` | → `run_suite()` |
| Runner | `tests/agent_eval/runner.py` | `run_suite()` | → `run_single_task()` per spec |
| Harness | `tests/agent_eval/harness.py` | `run_single_task()` | If `execution_mode=="real"` → `run_structural_agent_real()` |
| Real exec | `tests/agent_eval/real_execution.py` | `run_structural_agent_real()` | **Always** wraps run in `offline_llm_stubs(spec)` |
| Real exec | `tests/agent_eval/real_execution.py` | `offline_llm_stubs()` | Patches 13 model entry points; **no HTTP** |

---

## 2. Per-Path Model Call Analysis

### A. Model Invocation Inventory

| File | Function | Model Client | Purpose | Runtime or Test Path | Hard Dependency? |
|------|----------|--------------|---------|----------------------|------------------|
| `agent/models/model_client.py` | `call_small_model` | OpenAI HTTP / urllib | All small-model tasks | runtime | yes |
| `agent/models/model_client.py` | `call_reasoning_model` | OpenAI HTTP / urllib | All reasoning tasks | runtime | yes |
| `planner/planner.py` | `plan()` | `call_reasoning_model` | Plan generation | runtime | yes (when planner used) |
| `agent/retrieval/query_rewriter.py` | `rewrite_query()` | both | Query rewrite | runtime | yes |
| `agent/retrieval/context_ranker.py` | `rank_context()` | `call_reasoning_model` | Context ranking | runtime | yes |
| `agent/execution/step_dispatcher.py` | EXPLAIN handler | both | Explain step output | runtime | yes |
| `agent/orchestrator/validator.py` | validation | both | Validation | runtime | yes |
| `agent/orchestrator/replanner.py` | replan | both | Replan on failure | runtime | yes |
| `agent/routing/instruction_router.py` | `route_instruction()` | `call_small_model` | Intent routing | runtime | yes (when router used) |
| `agent/prompt_system/context/context_summarizer.py` | `summarize_large_block()` | `call_small_model` | Context summarization | runtime | yes |
| `agent/meta/critic.py` | `_generate_strategy_hint_llm()` | `call_reasoning_model` | Critic strategy hint | runtime (agent_controller only) | no (fallback to deterministic) |

**Benchmark path:** All of the above are **patched** by `offline_llm_stubs()` when `run_structural_agent_real()` runs. No real model calls.

### B. Bypass Inventory

| File | Function | Bypass Type | What It Replaces | Impact on Benchmark |
|------|----------|-------------|------------------|---------------------|
| `tests/agent_eval/real_execution.py` | `offline_llm_stubs()` | monkeypatch | All `call_small_model`, `call_reasoning_model` at 13 import sites | **All LLM calls stubbed**; runs stay offline |
| `tests/agent_eval/real_execution.py` | `_stub_small` | stub | Small model output | Returns `{"query":"benchmark","tool":"","reason":""}` |
| `tests/agent_eval/real_execution.py` | `_stub_reasoning_json` | stub | Reasoning model output | Returns `{"steps":[]}` |
| `tests/agent_eval/real_execution.py` | `_reasoning_router` | stub | Critic/retry/validation | Returns task-specific fixed JSON |
| `tests/agent_eval/real_execution.py` | `_make_explain_stub_with_substrings` | synthetic | Explain output | For `explain_artifact` tasks: returns text containing `explain_required_substrings` so validation passes |
| `tests/agent_eval/real_execution.py` | `_stub_rank_scores` | stub | Context ranker | Returns `"0.95\n0.85"` |
| `tests/agent_eval/real_execution.py` | `_stub_router` | stub | Instruction router | Returns `{"category":"CODE_EDIT","confidence":0.9}` |
| `tests/agent_eval/harness.py` | `run_structural_agent()` | mock | `execution_loop` | `_exec_side_effect_success` returns fake success; no real loop |
| `tests/agent_eval/harness.py` | `_compat_get_plan`, `_parent_plan_for_spec` | deterministic | `get_plan`, `get_parent_plan` | Benchmark-injected plans; planner never called |
| `tests/agent_eval/real_execution.py` | `_compat_plan_dict_for_audit` | deterministic | Plan for compat tasks | SEARCH+EDIT or single EXPLAIN from task tags |
| `editing/grounded_patch_generator.py` | `generate_grounded_candidates()` | heuristic | LLM patch generation | Rule-based strategies only; no model |
| `editing/grounded_patch_generator.py` | `_apply_semantic_ranking()` | heuristic | Semantic ranking | Regex/substring matching; no model |
| `agent/retrieval/target_resolution.py` | single-part fallback | heuristic | Target resolution | `module.py` when single part; no model |
| `agent/orchestrator/plan_resolver.py` | `_docs_seed_plan`, `_is_docs_artifact_intent` | heuristic | Planner | Docs-intent bypass; no planner call |
| `agent/orchestrator/plan_resolver.py` | `_SHORT_CIRCUIT_ROUTER_CATEGORIES` | deterministic | Planner | CODE_SEARCH/CODE_EXPLAIN/INFRA → single step; no planner |
| `tests/agent_eval/semantic_rca.py` | `classify_wrong_patch_root_cause()` | heuristic | RCA classification | No model; rule-based |
| `tests/conftest.py` | `e2e_use_mock` | env/fallback | E2E LLM probe | On probe failure, uses mock; `--mock` forces mock |
| `agent/models/model_client.py` | `ENABLE_PROMPT_GUARDRAILS=0` | env flag | Guardrails | Disables injection check (eval/tests) |

---

## 3. Real-Mode Truth Check

### Command

```bash
python3 -m tests.agent_eval.runner --execution-mode real
```

### Call Chain

1. `runner.main()` → `run_suite(..., execution_mode="real")`
2. `run_suite()` → for each spec: `run_single_task(spec, ws, execution_mode="real")`
3. `run_single_task()` (harness.py:369) → `run_structural_agent_real(spec, str(workspace), trace_id=trace_id)`
4. `run_structural_agent_real()` (real_execution.py:143):
   - Line 163: `with offline_llm_stubs(spec):` — **enters stub context**
   - Line 164–166: patches `execution_loop` to `_execution_loop_drop_max_runtime` (real loop, not mocked)
   - Line 168–171: patches `get_parent_plan`, `get_plan` (for compat)
   - Line 176–182: calls `run_hierarchical(...)`

5. Inside `run_hierarchical()`:
   - `get_parent_plan` returns patched parent (no model)
   - For compat: `run_deterministic()` with patched `get_plan` (no planner call)
   - `execution_loop` runs (real loop)
   - During loop: retrieval → `query_rewriter`, `context_ranker` — **patched**
   - Step dispatch → EXPLAIN → `call_small_model`/`call_reasoning_model` — **patched**
   - Validator, replanner — **patched**

### Does It Reach a Real Model Client?

**No.** Every model call goes through a patched reference. `offline_llm_stubs()` patches:

- `agent.models.model_client.call_reasoning_model`
- `agent.models.model_client.call_small_model`
- `planner.planner.call_reasoning_model`
- `agent.retrieval.query_rewriter.call_reasoning_model`
- `agent.retrieval.query_rewriter.call_small_model`
- `agent.execution.step_dispatcher.call_reasoning_model`
- `agent.execution.step_dispatcher.call_small_model`
- `agent.retrieval.context_ranker.call_reasoning_model`
- `agent.orchestrator.replanner.call_small_model`
- `agent.orchestrator.replanner.call_reasoning_model`
- `agent.routing.instruction_router.call_small_model`
- `agent.orchestrator.validator.call_small_model`
- `agent.orchestrator.validator.call_reasoning_model`
- `agent.prompt_system.context.context_summarizer.call_small_model`

`model_client._call_chat()` is never invoked in this path.

---

## 4. Evidence of Benchmark Cheating Risk

| Mechanism | Location | Evidence |
|-----------|----------|----------|
| **offline_llm_stubs** | `real_execution.py:104–131` | All model entry points patched; runs stay offline |
| **Synthetic patch generation** | `grounded_patch_generator.py` | Rule-based strategies; no LLM for patch content |
| **Grounded patch generation** | `grounded_patch_generator.py` | Content-driven heuristics; `_apply_semantic_ranking` is regex-based |
| **Task-shape logic** | `real_execution.py:33–45` | `_compat_plan_dict_for_audit` builds SEARCH+EDIT from tags |
| **Validation-scope tricks** | `real_execution.py:75–85` | `_make_explain_stub_with_substrings` returns text containing `explain_required_substrings` so `explain_artifact_ok` passes |
| **Artifact-writing shortcuts** | `harness.py:371–378` | `explain_artifact_ok` checks file content for substrings; stub can satisfy |
| **Fixture-aware logic** | `harness.py:206–210` | `_build_phase_1_steps` uses `_is_docs_consistency_task`, `_is_explain_artifact_task` from spec |
| **Plan injection** | `real_execution.py:154–155` | `get_parent_plan`, `get_plan` patched; planner never called |
| **Deterministic router short-circuit** | `plan_resolver.py:244–268` | CODE_SEARCH/CODE_EXPLAIN/INFRA → single step without planner |

---

## 5. Paths That Do NOT Require a Model

| Path | Condition | Evidence |
|------|-----------|----------|
| Router short-circuit | `ENABLE_INSTRUCTION_ROUTER=1`, category in CODE_SEARCH/CODE_EXPLAIN/INFRA, confidence ≥ threshold | `plan_resolver.py:244–268` |
| Docs-artifact intent | `_is_docs_artifact_intent(instruction)` | `plan_resolver.py:205–217` → `_docs_seed_plan()` |
| Benchmark compat plan | `orchestration_path=="compat"` | `_compat_plan_dict_for_audit`; no planner |
| Patch generation | All grounded strategies | `grounded_patch_generator.py`; rule-based only |
| Target resolution fallback | Single module part | `target_resolution.py` → `module.py` |
| Goal evaluation | Always | `goal_evaluator.py`; deterministic |
| Semantic RCA | Post-run classification | `semantic_rca.py`; heuristic |

---

## 6. Normal Runtime vs Benchmark

| Aspect | Production (`python -m agent`) | Benchmark (`--execution-mode real`) |
|--------|--------------------------------|-------------------------------------|
| Entry | `run_controller` | `run_structural_agent_real` |
| Planner | `get_plan()` → `plan()` → `call_reasoning_model` | `get_plan` patched; planner skipped |
| Retrieval | `query_rewriter`, `context_ranker` → model | Patched; stubs |
| Step dispatch | EXPLAIN → model | Patched; stubs |
| Validator | model | Patched; stubs |
| Replanner | model | Patched; stubs |
| Instruction router | model | Patched; stubs |
| Model client | `_call_chat` → HTTP | Never reached |

---

## 7. Confidence-Rated Verdict

**Verdict: Mostly heuristic/stub-driven in benchmark paths.**

**Evidence:**

1. **`run_structural_agent_real` always uses `offline_llm_stubs`** (real_execution.py:163). There is no branch that runs without it.
2. **13 patches** cover every module that imports `call_small_model` or `call_reasoning_model` in the execution path.
3. **Plan injection** bypasses planner; `get_parent_plan` and `get_plan` return benchmark-defined plans.
4. **Grounded patch generator** is rule-based; no model calls.
5. **explain_artifact** tasks can pass via `_make_explain_stub_with_substrings`, which returns text containing `explain_required_substrings`.

**Production path** is model-backed when run via `python -m agent`; no stubs there.

---

## 8. Top 10 Most Suspicious Files/Functions

1. **`tests/agent_eval/real_execution.py`** — `offline_llm_stubs()`: Ensures real mode never hits the model.
2. **`tests/agent_eval/real_execution.py`** — `run_structural_agent_real()`: Always wraps run in `offline_llm_stubs`.
3. **`tests/agent_eval/real_execution.py`** — `_make_explain_stub_with_substrings()`: explain_artifact validation can pass from stub.
4. **`tests/agent_eval/harness.py`** — `run_structural_agent()`: Mocks `execution_loop` entirely in mocked mode.
5. **`tests/agent_eval/harness.py`** — `_parent_plan_for_spec`, `_compat_get_plan`: Injected plans; planner bypassed.
6. **`editing/grounded_patch_generator.py`** — `generate_grounded_candidates()`: No model; rule-based only.
7. **`tests/agent_eval/runner.py`** — `run_suite()`: No option to run real mode without stubs.
8. **`agent/orchestrator/plan_resolver.py`** — Router short-circuit: Planner skipped for CODE_SEARCH/CODE_EXPLAIN/INFRA.
9. **`agent/orchestrator/plan_resolver.py`** — `_is_docs_artifact_intent`, `_docs_seed_plan`: Planner bypass for docs intent.
10. **`tests/conftest.py`** — `e2e_use_mock`: E2E can fall back to mock on probe failure.

---

## 9. Minimum Experiment to Verify

**Experiment:** Run the benchmark with a model client that logs or fails on first call.

1. Add a temporary wrapper in `agent/models/model_client.py`:

```python
def _call_chat(...):
    raise RuntimeError("REAL_MODEL_CALL_DETECTED")  # temporary
```

2. Run:

```bash
python3 -m tests.agent_eval.runner --execution-mode real --suite audit6 --task <one_task_id>
```

3. **Expected:** No `RuntimeError`. The exception would only occur if `_call_chat` were invoked; with stubs, it never is.

4. **Alternative:** Run production CLI with the same wrapper:

```bash
python -m agent "Add a function that returns 42"
```

**Expected:** `RuntimeError("REAL_MODEL_CALL_DETECTED")` — production path does call the model.

---

## 10. Direct Answer

**Is Cursor cheating the benchmark by avoiding the real local LLM?**

**Yes.** The benchmark harness (`--execution-mode real`) is designed to run offline. `run_structural_agent_real()` always wraps execution in `offline_llm_stubs(spec)`, which patches every model entry point. No HTTP requests are made to the hosted local LLM during agent_eval runs. Benchmark success can occur entirely from stubs, heuristics, and deterministic plan injection.
