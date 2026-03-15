# Agent Loop Workflow Diagram

End-to-end flow of the AutoStudio agent: instruction → plan → execute steps → validate → optional replan → return state. Includes details on model routing, query rewriting, policy-engine retries, fallbacks, and heuristics.

**Architecture (phase.md):** router decides, planner plans, dispatcher executes.

---

## High-level flow

```mermaid
flowchart TB
    subgraph ENTRY[" "]
        A["User instruction"] --> B["run_agent"]
        B --> C["get_plan"]
    end

    subgraph PLAN["Plan resolver (router + planner)"]
        C --> D{"ENABLE_INSTRUCTION_ROUTER?"}
        D -->|yes| E["route_instruction"]
        E --> F{"category"}
        F -->|CODE_SEARCH| G1["Single SEARCH step"]
        F -->|CODE_EXPLAIN| G2["Single EXPLAIN step"]
        F -->|INFRA| G3["Single INFRA step"]
        F -->|CODE_EDIT/GENERAL| H["plan instruction"]
        D -->|no| H
        H --> I1{"Planner OK?"}
        I1 -->|yes| J["Parse JSON steps"]
        I1 -->|no| K["Fallback: single EXPLAIN"]
        J --> L["Plan: steps with id, action, description, reason"]
        K --> L
        G1 --> L
        G2 --> L
        G3 --> L
    end

    subgraph STATE["State"]
        L --> M["AgentState with instruction, plan, completed_steps, results, context"]
    end

    subgraph LOOP["Execution loop"]
        M --> N{"Termination?"}
        N -->|max_iter/runtime/replan| O["Return state"]
        N -->|no| P["state.next_step"]
        P --> Q["StepExecutor.execute_step"]
        Q --> R["dispatch step and state"]
        R --> S["state.record step and result"]
        S --> T{"result.success and validate_step?"}
        T -->|no| U["undo_last_step, replan with failed_step and error"]
        U --> V["state.update_plan"]
        V --> N
        T -->|yes| N
        N -->|is_finished| O
    end
```

**Context initialization:** `run_agent` sets `context.project_root` (from `SERENA_PROJECT_DIR` or cwd) so retrieval expansion can resolve relative paths. See `Docs/REPOSITORY_SYMBOL_GRAPH.md` for path normalization.

**Termination conditions (best practice):** task complete, max replan (5), max runtime (15 min), max iterations (100). `agent_controller` uses `config/agent_config.py`; `agent_loop` uses module-level constants. See [CONFIGURATION.md](CONFIGURATION.md).

---

## Step dispatch (action routing)

```mermaid
flowchart LR
    M["dispatch"] --> T["ToolGraph.get_allowed_tools"]
    T --> R["Router: resolve_tool (preferred or first allowed)"]
    R --> A{"action"}
    A -->|SEARCH| S["Policy engine: _execute_search"]
    A -->|EDIT| E["Policy engine: _execute_edit"]
    A -->|INFRA| I["Policy engine: _execute_infra"]
    A -->|EXPLAIN or unknown| X["EXPLAIN path"]
```

- **ToolGraph → Router:** Dispatcher reads `current_node` from state; ToolGraph returns allowed tools; Router chooses tool (preferred for action, or first allowed if preferred not in set—no hard reject). Dispatcher sets `state.context["tool_node"]` after each step.
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
        S3 -->|yes| S4["rewrite_query_with_context"]
        S3 -->|no| S5["queries_to_try = description only"]
        S4 --> S4a["queries_to_try = str or list from rewriter"]
        S4a --> S6
        S5 --> S6["For each query in queries_to_try"]
        S6 --> S6a{"more queries?"}
        S6a -->|yes| S7["_search_fn query state"]
        S6a -->|no| S13["Try next attempt with fresh rewrite"]
        S7 --> S8["RepoMapLookup, detect_anchor, cache, hybrid_retrieve"]
        S8 --> S9{"valid search result?"}
        S9 -->|yes| S10["Store context, append attempt_history"]
        S10 --> S11["Return success"]
        S9 -->|no| S12["Append to attempt_history, try next query"]
        S12 --> S6
        S13 --> S2
        S2 --> S14["Exhausted"] --> S15["Return success False"]
    end
```

**Details:**

- **Retrieval stack:**  
  1. **Repo map lookup** (before cache): `lookup_repo_map(query)`, `detect_anchor(query, repo_map)` → `state.context["repo_map_anchor"]`, `state.context["repo_map_candidates"]`. When anchor confidence ≥ 0.9, graph retriever uses anchor symbol.  
  2. `retrieval_cache.get_cached(query)` (if `RETRIEVAL_CACHE_SIZE > 0`)  
  3. **Hybrid retrieval** (when `ENABLE_HYBRID_RETRIEVAL=1`): run graph (with anchor when present), vector, grep in parallel via `hybrid_retrieve()`; merge and dedupe; return top 20.  
  4. **Sequential fallback** (when hybrid disabled or returns empty): order by `chosen_tool`; try `retrieve_graph` → `retrieve_vector` → `retrieve_grep` → `list_dir`; final fallback `search_code` (Serena MCP).  
  - On success: `retrieval_cache.set_cached(query, results)`.

- **Query rewrite (LLM)**  
  - `rewrite_query_with_context(planner_step, user_request, previous_attempts, use_llm=True, state=state)`.  
  - Returns JSON: `{ "tool": "retrieve_graph"|"retrieve_vector"|"retrieve_grep"|"list_dir", "query": "", "reason": "" }`.  
  - **Rewriter wires tool choice:** when tool is valid, sets `state.context["chosen_tool"]` so retrieval order prefers it.  
  - Model from `get_model_for_task("query rewriting")`.  
  - **Fallbacks:** on LLM/format error → heuristic (strip filler words); if heuristic empty → stripped planner_step. Policy engine also catches rewriter exceptions and uses description.  
  - Prompts: `query_rewrite_with_context.yaml` (Serena rules, filesystem rules, tool graph). Template braces escaped as `{{`/`}}`.

- **Query rewrite (use_llm=False)**  
  - Passthrough: `query = planner_step.strip()` (no tokenize/stopwords/dedupe).

- **Fallback when rewrite_query_fn is None**  
  - Policy engine uses `query = description`; if still empty, `query = description` again (line 185).

- **Success criteria**  
  - `_is_valid_search_result(results)`: first result has non-empty `file` and non-empty `snippet`.

- **Retrieval pipeline (after search success)**  
  - Dispatcher calls `run_retrieval_pipeline(search_results, state, query)` (no inline logic). Pipeline: `anchor_detector.detect_anchors` (filter to symbol/class/def matches; fallback top N) → `symbol_expander.expand_from_anchors` (when graph exists; anchor → expand depth=2 → fetch bodies → rank → prune to 6; max 15 symbols) → `expand_search_results` (capped at MAX_SYMBOL_EXPANSION) → read_symbol_body/read_file → find_referencing_symbols → `build_context_from_symbols` → (when `ENABLE_CONTEXT_RANKING=1`) `rank_context` (max 20 candidates) → `prune_context` (max 6 snippets, 8000 chars) → `state.context["ranked_context"]`, `state.context["context_snippets"]` (list of `{file, symbol, snippet}`).  
  - **Symbol expander:** Uses repository symbol graph; `expand_from_anchors(anchors, query, project_root)` merges graph-expanded snippets with expansion results.  
  - Ranker: **batch LLM**; hybrid score = 0.6×LLM + 0.2×symbol_match + 0.1×filename_match + 0.1×reference_score − **same_file_penalty** (diversity).  
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
        E4 --> E5{"EDIT failure?"}
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
flowchart TB
    X["dispatch EXPLAIN"] --> X0{"ensure_context_before_explain"}
    X0 -->|ranked_context empty| XG["Context gate: _search_fn and run_retrieval_pipeline"]
    XG --> XG2{"Valid results?"}
    XG2 -->|no| XF["Return failure: No context for EXPLAIN"]
    XG2 -->|yes| X1
    X0 -->|ranked_context present| X1["get_model_for_task EXPLAIN"]
    X1 --> X2["_format_explain_context: anchored blocks"]
    X2 --> X3["call_reasoning_model or call_small_model"]
    X3 --> X4["out_str = output.strip or fallback string"]
    X4 --> X5["Return success True, output out_str"]
```

- **Context gate:** If `ranked_context` is empty, inject SEARCH (call `_search_fn` with step description; no LLM rewrite), then `run_retrieval_pipeline()`. If no valid results, return failure without calling the model. Avoids wasted LLM calls.
- **Anchored context:** `context_builder_v2.assemble_reasoning_context()` emits FILE/SYMBOL/LINES/SNIPPET blocks (~8000 char budget); deduplicates by (file, symbol).
- **Fallback**: If model returns empty → `"[EXPLAIN: no model output]"`.
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
    R --> EXPLAIN_RULE["EXPLAIN: not fallback, needs context or triggers replan"]
```

- **Rule-based (default)**: SEARCH → non-empty first result with file + snippet; EDIT → success; INFRA → returncode 0; EXPLAIN → if output contains "I cannot answer without relevant code context" → invalid (triggers replanner to add SEARCH); else `_is_valid_explain`: False when output length < 40 chars (triggers replan to add SEARCH). No LLM phrase detection.
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
| `repo_map_anchor` | SEARCH: before retrieval in `_search_fn` | `{symbol, confidence}` or None; from `detect_anchor(query, repo_map)` | `hybrid_retrieve` run_graph: uses anchor symbol when confidence ≥ 0.9 |
| `repo_map_candidates` | SEARCH: before retrieval in `_search_fn` | `[{anchor, file}, ...]` from `lookup_repo_map(query)` | Available for downstream or logging |
| `ranked_context` | SEARCH succeeds via `run_retrieval_pipeline` (when `ENABLE_CONTEXT_RANKING=1`) | List of `{ file, symbol, snippet, type, line? }`; ranked and pruned (max 6 snippets, 8000 chars) | EXPLAIN step: primary evidence in `_format_explain_context` (anchored FILE/SYMBOL/LINES/SNIPPET blocks). |
| `context_snippets` | SEARCH succeeds via `run_retrieval_pipeline` | List of `{ file, symbol, snippet }`; built by context_builder | EXPLAIN step: fallback in `_format_explain_context` when ranked_context empty. |
| `search_memory` | SEARCH succeeds | `{ "query": str, "results": [ { "file", "snippet" } ] }`; snippets truncated to 500 chars | EXPLAIN step: fallback when `ranked_context` empty. |
| `tool_memories` | SEARCH / EDIT / INFRA succeed | List of records, one per successful tool call. SEARCH: `{ tool, query, result_count, files, snippets_preview }`; EDIT: `{ tool, path, success }`; INFRA: `{ tool, returncode, success }`. | Available for downstream steps or logging. |

- **When set:** In `ExecutionPolicyEngine`, on success path of `_execute_search`, `_execute_edit`, `_execute_infra` (via `_append_tool_memory`). SEARCH also sets legacy keys: `search_query_rewritten`, `search_results`, `files`, `snippets`. Retrieval pipeline (`run_retrieval_pipeline`) sets `retrieved_symbols`, `retrieved_references`, `retrieved_files`, `context_snippets`, `ranked_context`, `search_memory`.
- **EXPLAIN:** In `step_dispatcher`, `_format_explain_context(state)` prefers `ranked_context` (when non-empty); otherwise falls back to `search_memory` and `context_snippets` (each item `{file, symbol, snippet}`).

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
| `run_agent`            | Entry; get_plan → state → loop execute → validate → replan until finished. |
| `get_plan`             | Plan resolver; instruction router (when enabled) or planner; single-step for CODE_SEARCH/CODE_EXPLAIN/INFRA. |
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

- **Agent loop**: `agent/orchestrator/agent_loop.py` — `run_agent`, loop, validate, replan, termination conditions.
- **Plan resolver**: `agent/orchestrator/plan_resolver.py` — `get_plan`, router + planner integration.
- **Executor**: `agent/execution/executor.py` — `StepExecutor.execute_step`, `execute_plan`.
- **Dispatch**: `agent/execution/step_dispatcher.py` — `dispatch`, _search_fn, _edit_fn, _infra_fn, _rewrite_for_search, _format_explain_context, EXPLAIN.
- **Explain gate**: `agent/execution/explain_gate.py` — `ensure_context_before_explain` (inject SEARCH when ranked_context empty).
- **Search pipeline**: `agent/retrieval/search_pipeline.py` — `hybrid_retrieve` (parallel graph + vector + grep), `_merge_results`.
- **Policy**: `agent/execution/policy_engine.py` — POLICIES, _execute_search, _execute_edit, _execute_infra, _run_once.
- **Query rewriter**: `agent/retrieval/query_rewriter.py` — rewrite_query_with_context (wires chosen_tool), rewrite_query; prompts: `agent/prompts/query_rewrite.yaml`, `query_rewrite_with_context.yaml`.
- **Mutation**: `agent/execution/mutation_strategies.py` — symbol_retry, retry_same, generate_query_variants.
- **Model**: `agent/models/model_client.py` — _call_chat, call_reasoning_model, call_small_model; `agent/models/model_router.py` — get_model_for_task.
- **Validation**: `agent/orchestrator/validator.py` — validate_step, _validate_step_rules.
- **Replan**: `agent/orchestrator/replanner.py` — replan.
- **Planner**: `planner/planner.py` — plan.
- **Repo map lookup**: `agent/retrieval/repo_map_lookup.py` — lookup_repo_map, load_repo_map.
- **Graph retriever**: `agent/retrieval/graph_retriever.py` — retrieve_symbol_context.
- **Anchor detector**: `agent/retrieval/anchor_detector.py` — detect_anchors (search results), detect_anchor (query + repo_map).
- **Symbol expander**: `agent/retrieval/symbol_expander.py` — expand_from_anchors (graph depth=2, fetch bodies, rank, prune).
- **Context builder v2**: `agent/retrieval/context_builder_v2.py` — assemble_reasoning_context (FILE/SYMBOL/LINES/SNIPPET).
- **Vector retriever**: `agent/retrieval/vector_retriever.py` — search_by_embedding (fallback).
- **Retrieval cache**: `agent/retrieval/retrieval_cache.py` — LRU cache for search results.
- **Diff planner**: `editing/diff_planner.py` — plan_diff.
- **Patch pipeline**: `editing/patch_generator.py` — to_structured_patches; `editing/ast_patcher.py` — apply_patch; `editing/patch_validator.py` — validate_patch; `editing/patch_executor.py` — execute_patch (rollback on failure).
- **Agent controller**: `agent/orchestrator/agent_controller.py` — run_controller (full pipeline); get_plan from plan_resolver (instruction router + planner).
- **Instruction router**: `agent/routing/instruction_router.py` — route_instruction (when ENABLE_INSTRUCTION_ROUTER=1).
- **Router registry**: `agent/routing/router_registry.py` — get_router, get_router_raw (ROUTER_TYPE integration).

---

## Repository symbol graph (implemented)

**Indexing:** `python -m repo_index.index_repo <path>` creates `.symbol_graph/index.sqlite`; optionally `.symbol_graph/embeddings/` when `INDEX_EMBEDDINGS=1`.

**Retrieval flow:**
- Cache → hybrid_retrieve (parallel graph + vector + grep) when ENABLE_HYBRID_RETRIEVAL=1; else chosen_tool order (retrieve_graph → retrieve_vector → retrieve_grep → list_dir) → Serena fallback

**Additional modules:**
- `repo_graph/repo_map_builder.py` — build_repo_map, build_repo_map_from_storage (spec: modules, symbols, calls)
- `repo_graph/repo_map_updater.py` — update_repo_map_for_file (incremental; call after update_index_for_file)
- `repo_graph/change_detector.py` — affected callers, risk levels
- `agent/retrieval/repo_map_lookup.py` — lookup_repo_map, load_repo_map
- `agent/retrieval/anchor_detector.py` — detect_anchor (query + repo_map → symbol + confidence)
- `agent/retrieval/vector_retriever.py`, `agent/retrieval/retrieval_cache.py`

**Files:** `repo_index/`, `repo_graph/`, `agent/retrieval/`, `editing/`
