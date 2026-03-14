# Agent Loop Workflow Diagram

End-to-end flow of the AutoStudio agent: instruction → plan → execute steps → validate → optional replan → return state. Includes details on model routing, query rewriting, policy-engine retries, fallbacks, and heuristics.

---

## High-level flow

```mermaid
flowchart TB
    subgraph ENTRY[" "]
        A["User instruction"] --> B["run_agent"]
        B --> C["plan instruction"]
    end

    subgraph PLAN["Planner"]
        C --> D{"Planner model OK?"}
        D -->|yes| E["Parse JSON steps"]
        D -->|no or exception| F["Fallback: single EXPLAIN step"]
        E --> G["normalize_actions and validate_plan"]
        F --> G
        G --> H["Plan: steps with id, action, description, reason"]
    end

    subgraph STATE["State"]
        H --> I["AgentState with instruction, plan, completed_steps, results, context"]
    end

    subgraph LOOP["Execution loop"]
        I --> J{"state.is_finished?"}
        J -->|no| K["state.next_step"]
        K --> L["StepExecutor.execute_step"]
        L --> M["dispatch step and state"]
        M --> N["state.record step and result"]
        N --> O{"result.success and validate_step?"}
        O -->|no| P["replan state with failed_step and error"]
        P --> Q["state.update_plan"]
        Q --> J
        O -->|yes| J
        J -->|yes| R["Return state"]
    end
```

---

## Step dispatch (action routing)

```mermaid
flowchart LR
    M["dispatch"] --> A{"action"}
    A -->|SEARCH| S["Policy engine: _execute_search"]
    A -->|EDIT| E["Policy engine: _execute_edit"]
    A -->|INFRA| I["Policy engine: _execute_infra"]
    A -->|EXPLAIN or unknown| X["EXPLAIN path"]
```

- **SEARCH / EDIT / INFRA** → `ExecutionPolicyEngine.execute_with_policy` (retries, mutation).
- **EXPLAIN** → Direct model call in `step_dispatcher`; no policy engine.

---

## Model routing (task → model)

Config: `models_config.json` → `task_models`. Defaults in code: `query rewriting` → SMALL, `validation` → SMALL, `EXPLAIN` → REASONING.

```mermaid
flowchart LR
    T["Task name"] --> R["get_model_for_task"]
    R --> C{"task_models lookup"}
    C -->|SMALL| S["SMALL_MODEL_ENDPOINT"]
    C -->|REASONING or missing| Re["REASONING_MODEL_ENDPOINT"]
    S --> MC["_call_chat"]
    Re --> MC
```

- **Query rewriting** (SEARCH steps): `task_models["query rewriting"]` → REASONING or SMALL → `call_reasoning_model` / `call_small_model`.
- **Validation** (optional LLM): `task_models["validation"]` → model answers YES/NO.
- **EXPLAIN**: `task_models["EXPLAIN"]` → model; empty output replaced with `"[EXPLAIN: no model output]"` in dispatcher.

---

## SEARCH path (policy engine + query rewrite)

```mermaid
flowchart TB
    subgraph SEARCH["SEARCH execution"]
        S1["Policy: max_attempts 5, retry_on empty_results, mutation query_variants"]
        S1 --> S2["For attempt 1 to max_attempts"]
        S2 --> S3{"rewrite_query_fn set?"}
        S3 -->|yes| S4["rewrite_query_with_context(planner_step, user_request, attempts, state)"]
        S3 -->|no| S5["query = description"]
        S4 --> S6{"use_llm?"}
        S6 -->|True| S7["get_model_for_task query rewriting"]
        S7 --> S8["call_reasoning_model or call_small_model"]
        S8 --> S9{"Model output empty?"}
        S9 -->|yes| S10["Raise ValueError - Query rewrite returned empty response"]
        S9 -->|no| S11["cleaned = output.strip"]
        S6 -->|False| S12["query = planner_step.strip (passthrough)"]
        S12 --> S11
        S11 --> S13["_search_fn: chosen_tool order retrieve_graph/vector/grep/list_dir, else search_code"]
        S13 --> S14{"_is_valid_search_result?"}
        S14 -->|yes| S15["Store context: search_query_rewritten, search_results, files, snippets"]
        S15 --> S16["Return success"]
        S14 -->|no| S17["Append to attempt_history and try next query variant or attempt"]
        S17 --> S2
        S2 --> S18["Exhausted"] --> S19["Return success False, error all search attempts empty"]
    end
```

**Details:**

- **Retrieval stack (respects chosen_tool from tool graph or rewriter):**  
  1. `retrieval_cache.get_cached(query)` (if `RETRIEVAL_CACHE_SIZE > 0`)  
  2. Order by `chosen_tool`: `retrieve_graph` → graph; `retrieve_vector` → vector; `retrieve_grep` → Serena `search_for_pattern`; `list_dir` → `list_files(path)`  
  3. Fallback chain if chosen returns nothing: try remaining retrievers  
  4. Final fallback: `search_code` (Serena MCP)  
  - On success: `retrieval_cache.set_cached(query, results)`.

- **Query rewrite (LLM)**  
  - `rewrite_query_with_context(planner_step, user_request, previous_attempts, use_llm=True, state=state)`.  
  - Returns JSON: `{ "tool": "retrieve_graph"|"retrieve_vector"|"retrieve_grep"|"list_dir", "query": "", "reason": "" }`.  
  - **Rewriter wires tool choice:** when tool is valid, sets `state.context["chosen_tool"]` so retrieval order prefers it.  
  - Model from `get_model_for_task("query rewriting")`.  
  - **No heuristic fallback**: if model returns empty → `ValueError("Query rewrite returned empty response")`.  
  - Prompts: `query_rewrite_with_context.yaml` (Serena rules, filesystem rules, tool graph).

- **Query rewrite (use_llm=False)**  
  - Passthrough: `query = planner_step.strip()` (no tokenize/stopwords/dedupe).

- **Fallback when rewrite_query_fn is None**  
  - Policy engine uses `query = description`; if still empty, `query = description` again (line 185).

- **Success criteria**  
  - `_is_valid_search_result(results)`: first result has non-empty `file` and non-empty `snippet`.

- **Retrieval pipeline (after search success)**  
  - `expand_search_results` → `build_context_from_symbols` → (when `ENABLE_CONTEXT_RANKING=1`) `rank_context` → `prune_context` → `state.context["ranked_context"]`.  
  - Ranker: **batch LLM** (one prompt for all snippets); hybrid score = 0.6×LLM + 0.2×symbol_match + 0.1×filename_match + 0.1×reference_score − **same_file_penalty** (diversity).  
  - Pruner: max 6 snippets, 8000 chars; deduplicate by (file, symbol).

- **Mutation**  
  - SEARCH uses `query_variants` conceptually (attempt loop + new rewrite each time with attempt_history). No explicit `generate_query_variants` in loop; each attempt gets a fresh LLM rewrite (or heuristic) with previous attempts in context.

---

## EDIT path (policy engine)

```mermaid
flowchart TB
    subgraph EDIT["EDIT execution"]
        E1["Policy: max_attempts 2, retry_on symbol_not_found, mutation symbol_retry"]
        E1 --> E2["symbol_retry step to steps_to_try"]
        E2 --> E3["For each step variant: _edit_fn with step and state"]
        E3 --> E4["edit_fn: plan_diff (if ENABLE_DIFF_PLANNER) else read_file/list_files"]
        E4 --> E5{"_is_failure EDIT?"}
        E5 -->|no| E6["Return success and output"]
        E5 -->|yes| E7["Next variant or exhausted"]
        E7 --> E8["Return success False with attempt_history"]
    end
```

- **Diff planner (ENABLE_DIFF_PLANNER=1):** `plan_diff` → `conflict_resolver` → `patch_generator.to_structured_patches` → `patch_executor.execute_patch` (ast_patcher + patch_validator) → `run_with_repair` (test repair loop). Validation: compile + AST reparse before write; rollback on invalid syntax, >200 lines, >5 files, or apply error. Max 5 files, 200 lines per patch.
- **Mutation**: `symbol_retry(step)` → currently returns `[step]` (single variant). Placeholder for future symbol/path variants.
- **Retry condition**: `result.error` or `result.success is False`.

---

## INFRA path (policy engine)

```mermaid
flowchart TB
    subgraph INFRA["INFRA execution"]
        I1["Policy: max_attempts 2, retry_on non_zero_exit, mutation retry_same"]
        I1 --> I2["retry_same step returns step"]
        I2 --> I3["For attempt: _infra_fn with step and state"]
        I3 --> I4["run_command true, list_files, returncode in output"]
        I4 --> I5{"returncode equals 0?"}
        I5 -->|yes| I6["Return success"]
        I5 -->|no| I7["Retry same step or exhausted"]
        I7 --> I8["Return success False"]
    end
```

- **Mutation**: `retry_same(step)` → same step retried.
- **Retry condition**: `output.returncode != 0`.

---

## EXPLAIN path (no policy engine)

```mermaid
flowchart LR
    X["dispatch EXPLAIN"] --> X1["get_model_for_task EXPLAIN"]
    X1 --> X2["call_reasoning_model or call_small_model"]
    X2 --> X3["out_str = output.strip or fallback string"]
    X3 --> X4["Return success True, output out_str"]
```

- **Fallback**: If model returns empty → `"[EXPLAIN: no model output]"` (string substitute in `step_dispatcher`).
- No retries; single attempt.

---

## Validation (after each step)

```mermaid
flowchart TB
    V["validate_step with step and result"] --> U{"use_llm?"}
    U -->|False| R["_validate_step_rules"]
    U -->|True| L["get_model_for_task validation"]
    L --> M["call_reasoning_model or call_small_model"]
    M --> P["Answer YES or NO"]
    P --> R2["yes in output.lower then True"]
    R2 --> R
    R --> SEARCH_RULE["SEARCH: _is_valid_search_result"]
    R --> EDIT_RULE["EDIT: result.success"]
    R --> INFRA_RULE["INFRA: returncode equals 0"]
    R --> EXPLAIN_RULE["EXPLAIN: True"]
```

- **Rule-based (default)**: SEARCH → non-empty first result with file + snippet; EDIT → success; INFRA → returncode 0; EXPLAIN → True.
- **LLM**: On exception, fallback to rule-based.

---

## Replan (on step failure or validation failure)

```mermaid
flowchart LR
    RP["replan state, failed_step, error"] --> R1["Log last step failure"]
    R1 --> R2["LLM call with instruction, plan, failed_step, error"]
    R2 --> R3{"Valid JSON plan?"}
    R3 -->|yes| R4["Return revised plan"]
    R3 -->|no| R5["Fallback: remaining steps"]
    R4 --> R6["state.update_plan with new_plan"]
    R5 --> R6
```

- LLM-based: receives `failed_step` and `error`; produces revised plan via `call_reasoning_model` (task_models["replanner"]).
- Fallback: if LLM fails or returns invalid JSON, returns remaining steps only.
- Loop continues with `state.next_step()` (next remaining step).

---

## Context and tool memories

`state.context` is updated by the policy engine on successful tool use. Two memory mechanisms:

| Key | Set when | Shape | Used by |
|-----|----------|-------|---------|
| `ranked_context` | SEARCH succeeds (when `ENABLE_CONTEXT_RANKING=1`) | List of `{ file, symbol, snippet, type }`; ranked and pruned (max 6 snippets, 8000 chars) | EXPLAIN step: primary evidence in `_format_explain_context`. |
| `search_memory` | SEARCH succeeds | `{ "query": str, "results": [ { "file", "snippet" } ] }`; snippets truncated to 500 chars | EXPLAIN step: fallback when `ranked_context` empty. |
| `tool_memories` | SEARCH / EDIT / INFRA succeed | List of records, one per successful tool call. SEARCH: `{ tool, query, result_count, files, snippets_preview }`; EDIT: `{ tool, path, success }`; INFRA: `{ tool, returncode, success }`. | Available for downstream steps or logging. |

- **When set:** In `ExecutionPolicyEngine`, on success path of `_execute_search`, `_execute_edit`, `_execute_infra` (via `_append_tool_memory`). SEARCH also sets legacy keys: `search_query_rewritten`, `search_results`, `files`, `snippets`.
- **EXPLAIN:** In `step_dispatcher`, `_format_explain_context(state)` prefers `ranked_context` (when non-empty); otherwise falls back to `search_memory` and `context_snippets`.

---

## Policy summary (POLICIES)

| Action  | max_attempts | retry_on           | mutation      |
|---------|--------------|--------------------|---------------|
| SEARCH  | 5            | empty_results      | query_variants (via rewrite + attempt_history) |
| EDIT    | 2            | symbol_not_found   | symbol_retry  |
| INFRA   | 2            | non_zero_exit      | retry_same    |
| EXPLAIN | 1            | —                 | —             |

- **max_total_attempts** (engine cap): 10.
- EXPLAIN and unknown actions skip policy and use `_run_once`.

---

## Component map

| Component              | Role |
|------------------------|------|
| `run_agent`            | Entry; plan → state → loop execute → validate → replan until finished. |
| `plan(instruction)`    | Planner; reasoning model + JSON parse; fallback single EXPLAIN step. |
| `StepExecutor`         | Calls `dispatch(step, state)`; wraps result in `StepResult`. |
| `dispatch`             | Routes by action to policy engine (SEARCH/EDIT/INFRA) or EXPLAIN. |
| `ExecutionPolicyEngine`| Retry loop + mutation; injects search_fn, edit_fn, infra_fn, rewrite_query_fn. |
| `rewrite_query_with_context` | LLM returns `{tool, query, reason}`; wires `chosen_tool` when valid; prompts: Serena rules, filesystem rules; **empty LLM output → raise**. |
| `get_model_for_task`   | Config-driven: task_models → SMALL or REASONING. |
| `_call_chat`           | Single non-streaming chat call; extracts `choices[0].message.content`. |
| `validate_step`        | Rules or LLM YES/NO; fallback to rules on LLM error. |
| `replan`               | LLM-based: receives failed_step, error; produces revised plan; fallback to remaining steps. |
| `context["search_memory"]` / `context["tool_memories"]` | Set in policy engine on SEARCH/EDIT/INFRA success; EXPLAIN uses `search_memory` via `_format_explain_context`. |

---

## File reference

- **Agent loop**: `agent/orchestrator/agent_loop.py` — `run_agent`, loop, validate, replan.
- **Executor**: `agent/execution/executor.py` — `StepExecutor.execute_step`, `execute_plan`.
- **Dispatch**: `agent/execution/step_dispatcher.py` — `dispatch`, _search_fn, _edit_fn, _infra_fn, _rewrite_for_search, EXPLAIN.
- **Policy**: `agent/execution/policy_engine.py` — POLICIES, _execute_search, _execute_edit, _execute_infra, _run_once.
- **Query rewriter**: `agent/retrieval/query_rewriter.py` — rewrite_query_with_context (wires chosen_tool), rewrite_query; prompts: `agent/prompts/query_rewrite.yaml`, `query_rewrite_with_context.yaml`.
- **Mutation**: `agent/execution/mutation_strategies.py` — symbol_retry, retry_same, generate_query_variants.
- **Model**: `agent/models/model_client.py` — _call_chat, call_reasoning_model, call_small_model; `agent/models/model_router.py` — get_model_for_task.
- **Validation**: `agent/orchestrator/validator.py` — validate_step, _validate_step_rules.
- **Replan**: `agent/orchestrator/replanner.py` — replan.
- **Planner**: `planner/planner.py` — plan.
- **Graph retriever**: `agent/retrieval/graph_retriever.py` — retrieve_symbol_context.
- **Vector retriever**: `agent/retrieval/vector_retriever.py` — search_by_embedding (fallback).
- **Retrieval cache**: `agent/retrieval/retrieval_cache.py` — LRU cache for search results.
- **Diff planner**: `editing/diff_planner.py` — plan_diff.
- **Patch pipeline**: `editing/patch_generator.py` — to_structured_patches; `editing/ast_patcher.py` — apply_patch; `editing/patch_validator.py` — validate_patch; `editing/patch_executor.py` — execute_patch (rollback on failure).
- **Agent controller**: `agent/orchestrator/agent_controller.py` — run_controller (full pipeline); _get_plan (instruction router + planner).
- **Instruction router**: `agent/routing/instruction_router.py` — route_instruction (when ENABLE_INSTRUCTION_ROUTER=1).
- **Router registry**: `agent/routing/router_registry.py` — get_router, get_router_raw (ROUTER_TYPE integration).

---

## Repository symbol graph (implemented)

**Indexing:** `python -m repo_index.index_repo <path>` creates `.symbol_graph/index.sqlite`; optionally `.symbol_graph/embeddings/` when `INDEX_EMBEDDINGS=1`.

**Retrieval flow:**
- Cache → chosen_tool order (retrieve_graph → retrieve_vector → retrieve_grep → list_dir) → Serena fallback; rewriter can set chosen_tool

**Additional modules:**
- `repo_graph/repo_map_builder.py` — high-level architectural map
- `repo_graph/change_detector.py` — affected callers, risk levels
- `agent/retrieval/vector_retriever.py`, `agent/retrieval/retrieval_cache.py`

**Files:** `repo_index/`, `repo_graph/`, `agent/retrieval/`, `editing/`
