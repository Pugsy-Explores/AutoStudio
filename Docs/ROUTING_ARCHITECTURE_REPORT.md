# AutoStudio Agent Routing Architecture Report

Stage 38/39: Unified production routing via `route_production_instruction` → `RoutedIntent`; plan_resolver consumes RoutedIntent. Production-emission contract: VALIDATE deferred; COMPOUND production-real only for two-phase docs+code.

## SECTION 1 — ROUTER ENTRYPOINT

### Call Chain

**Path A — `agent_controller.py` (production):**

```
User Query (instruction)
  → run_controller() [agent/orchestrator/agent_controller.py]
  → get_parent_plan(instruction) or get_plan(instruction) [agent/orchestrator/plan_resolver.py]
       → route_production_instruction(instruction) [agent/routing/production_routing.py]
            → (1) is_docs_artifact_intent → DOC, docs_seed_lane
            → (2) is_two_phase_docs_code_intent → COMPOUND, two_phase_docs_code
            → (3) route_instruction() [agent/routing/instruction_router.py]
                 → category ∈ {CODE_SEARCH, CODE_EDIT, CODE_EXPLAIN, INFRA, GENERAL}
                 → routed_intent_from_router_decision → RoutedIntent
       → ri = RoutedIntent(primary_intent, suggested_plan_shape, ...)
       → if DOC + docs_seed_lane: _docs_seed_plan (skip planner)
       → if SEARCH/EXPLAIN/INFRA: single-step plan (skip planner)
       → if EDIT/VALIDATE/AMBIGUOUS/COMPOUND-flat: plan(instruction) [planner/planner.py]
  → (when ENABLE_INSTRUCTION_ROUTER=0) plan(instruction) directly [to disable router]
  → state.next_step() [agent/memory/state.py]
  → step = { id, action, description, reason }
  → action in { EDIT, SEARCH, EXPLAIN, INFRA }
  → result = dispatch(step, state)   # ALL steps via dispatch (including EDIT)
  → else: dispatch(step, state) [agent/execution/step_dispatcher.py]
       → ToolGraph.get_allowed_tools() + get_preferred_tool()
       → resolve_tool() [agent/execution/tool_graph_router.py]
       → ExecutionPolicyEngine.execute_with_policy()
       → (for EXPLAIN) get_model_for_task("EXPLAIN") [agent/models/model_router.py]
       → Tool execution (retrieve_graph, retrieve_vector, retrieve_grep, read_file, run_command, etc.)
```

**Path B — `agent_loop.py` (alternative loop):**

```
User Query
  → run_agent() [agent/orchestrator/agent_loop.py]
  → get_plan(instruction) [agent/orchestrator/plan_resolver.py]
  → StepExecutor.execute_step() [agent/execution/executor.py]
  → dispatch(step, state) [same as above]
```

### Findings

1. **Unified production entrypoint (Stage 38).** `route_production_instruction()` returns `RoutedIntent`; plan_resolver consumes it. Order: docs-artifact → two-phase docs+code → legacy `route_instruction()`.
2. **Instruction-level router in production (default).** When `ENABLE_INSTRUCTION_ROUTER=1` (default), legacy router classifies before planning; CODE_SEARCH/CODE_EXPLAIN/INFRA skip the planner. Set to 0 to disable.
3. **Planner is the routing source for CODE_EDIT/GENERAL.** `planner/planner.py` calls `call_reasoning_model()` and returns steps with `action` in `{EDIT, SEARCH, EXPLAIN, INFRA}`.
4. **`tool_graph_router` is deterministic.** It maps `action` → tool via `ACTION_TO_PREFERRED_TOOL`; no LLM.
5. **`model_router` is for model choice.** It selects SMALL vs REASONING for tasks like EXPLAIN, query rewriting, validation, replanner; it does not route instructions to actions.
6. **Production-emission contract (Stage 39).** `PRODUCTION_EMITTABLE_PRIMARY_INTENTS` documents what `route_production_instruction` can return. VALIDATE is deferred (no emission path). Telemetry: `resolver_consumption` (docs_seed | short_search | short_explain | short_infra | planner).

---

## SECTION 2 — ROUTER IMPLEMENTATION

### Production Routers (used in agent flow)

| Router | Location | Role |
|--------|----------|------|
| **model_router** | `agent/models/model_router.py` | Chooses SMALL vs REASONING for tasks (config-driven) |
| **tool_graph_router** | `agent/execution/tool_graph_router.py` | Maps action → tool (deterministic lookup) |

### Evaluation Routers (integrated via router_registry)

These live in `router_eval/` and are used by the evaluation harness. When `ROUTER_TYPE` is set (e.g. `baseline`, `final`), the production instruction router uses them via `agent/routing/router_registry.py`:

| Router | File | Input | Prompt | Categories | Decision Rule |
|--------|------|-------|--------|-------------|---------------|
| **baseline_router** | `routers/baseline_router.py` | instruction (str) | `BASELINE_SYSTEM` | EDIT, SEARCH, EXPLAIN, INFRA, GENERAL | Single LLM call → `parse_category()` |
| **fewshot_router** | `routers/fewshot_router.py` | instruction | `FEWSHOT_SYSTEM` | Same | Same + few-shot examples |
| **ensemble_router** | `routers/ensemble_router.py` | instruction | A/B/C prompts | Same | 3 prompts → majority vote |
| **confidence_router** | `routers/confidence_router.py` | instruction | A/B/C + CONFIDENCE_INSTRUCTION | Same | Majority vote + avg confidence |
| **dual_router** | `routers/dual_router.py` | instruction | A/B/C + DUAL_INSTRUCTION | Same | PRIMARY SECONDARY CONFIDENCE → majority on primary |
| **critic_router** | `routers/critic_router.py` | instruction | dual_route + critic | Same | If conf < 0.75 or primary≠secondary or disagree → critic |
| **final_router** | `routers/final_router.py` | instruction | ensemble_dual + critic | Same | If conf > 0.9 and agree → accept; else critic |
| **logit_router** | `routers/logit_router.py` | instruction | `router_logit_system.yaml` | Same | max_tokens=1, logprobs → highest prob token |
| **fewshot_logit_router** | `routers/fewshot_logit_router.py` | instruction | FEWSHOT_SYSTEM | Same | Few-shot + logprobs |

### Production model_router

- **Input:** `task_name` (e.g. `"EXPLAIN"`, `"query rewriting"`, `"validation"`, `"planner"`, `"routing"`).
- **Source:** `models_config.json` → `task_models`.
- **Output:** `ModelType` (SMALL, REASONING, REASONING_V2).
- **Fallback:** `route_task()` exists but is not used; production uses `get_model_for_task()` (config only).

---

## SECTION 3 — ROUTING CATEGORIES

### Planner Categories (production)

| Category | Source | Consumed By |
|----------|--------|-------------|
| **EDIT** | `planner/planner.py`, `planner_utils.py` | `step_dispatcher._edit_fn` (via dispatch) |
| **SEARCH** | Same | `step_dispatcher` → PolicyEngine → `_search_fn` |
| **EXPLAIN** | Same | `step_dispatcher` → `get_model_for_task("EXPLAIN")` → LLM call |
| **INFRA** | Same | `step_dispatcher` → PolicyEngine → `_infra_fn` |

Defined in `planner/planner_utils.py`:

```python
ALLOWED_ACTIONS = ("EDIT", "SEARCH", "EXPLAIN", "INFRA")
```

### Router_eval Categories (unified with planner)

| Category | Source | Consumed By |
|----------|--------|-------------|
| **EDIT** | `router_eval/dataset.py`, `parsing.py` | Evaluation harness, instruction_router (→ CODE_EDIT) |
| **SEARCH** | Same | Same (→ CODE_SEARCH) |
| **EXPLAIN** | Same | Same (→ CODE_EXPLAIN) |
| **GENERAL** | Same | Same |
| **INFRA** | Same | Same |

```python
CATEGORIES = ("EDIT", "SEARCH", "EXPLAIN", "INFRA", "GENERAL")
```

### Category Alignment

- Planner: EDIT, SEARCH, EXPLAIN, INFRA.
- Router_eval: EDIT, SEARCH, EXPLAIN, INFRA, GENERAL (DOCS replaced by EXPLAIN).
- Instruction router maps: EDIT→CODE_EDIT, SEARCH→CODE_SEARCH, EXPLAIN→CODE_EXPLAIN, INFRA→INFRA, GENERAL→GENERAL.

---

## SECTION 4 — TOOL GRAPH

### Implementation

- **File:** `agent/execution/tool_graph.py`
- **Class:** `ToolGraph`
- **Config:** `ENABLE_TOOL_GRAPH` (default `"1"`)

### Graph Structure

```
START
 ├─ retrieve_graph
 ├─ retrieve_vector
 ├─ retrieve_grep
 └─ list_dir

retrieve_graph
 ├─ read_file
 └─ find_referencing_symbols

retrieve_vector
 └─ read_file

retrieve_grep
 └─ read_file

list_dir
 ├─ read_file
 └─ retrieve_grep

read_file
 ├─ find_referencing_symbols
 └─ build_context

find_referencing_symbols
 ├─ read_file
 └─ build_context

build_context
 ├─ explain
 └─ edit

explain  (terminal)
edit     (terminal)
```

### Node Configuration

| Node | allowed_tools | preferred_tool |
|------|---------------|----------------|
| START | retrieve_graph, retrieve_vector, retrieve_grep, list_dir | retrieve_graph |
| retrieve_graph | read_file, find_referencing_symbols | read_file |
| retrieve_vector | read_file | read_file |
| retrieve_grep | read_file | read_file |
| read_file | find_referencing_symbols, build_context | find_referencing_symbols |
| find_referencing_symbols | read_file, build_context | read_file |
| build_context | explain, edit | explain |
| list_dir | read_file, retrieve_grep | read_file |
| explain | [] | None |
| edit | [] | None |

### Behavior

1. **allowed_tools:** From `ToolGraph.get_allowed_tools(current_node)`; if disabled or unknown node, effectively no restriction.
2. **preferred_tool:** From `ToolGraph.get_preferred_tool(current_node)`.
3. **Fallback:** If preferred not in allowed, use first allowed tool.
4. **Transitions:** `state.context["tool_node"]` is updated after each step (e.g. SEARCH → `chosen_tool`). The graph evolves naturally (no reset on SEARCH); `current_node = state.context.get("tool_node", "START")`.

---

## SECTION 5 — ROUTER ↔ TOOL GRAPH INTERACTION

### Flow

```
Planner → action (EDIT | SEARCH | EXPLAIN | INFRA)
    ↓
Dispatcher reads action from step
    ↓
ToolGraph: current_node → allowed_tools, preferred_tool
    ↓
tool_graph_router.resolve_tool(action, allowed_tools, preferred_from_graph, current_node)
    ↓
ACTION_TO_PREFERRED_TOOL[action] (at START) or preferred_from_graph (else)
    ↓
chosen_tool (e.g. retrieve_graph, explain, edit)
    ↓
PolicyEngine executes (search_fn, edit_fn, infra_fn) or EXPLAIN LLM call
```

### Mapping

- **Router does not choose the tool directly.** The planner chooses the action; the tool graph router maps action → tool.
- **Planner chooses category** (EDIT, SEARCH, EXPLAIN, INFRA).
- **Dispatcher maps category → tool** via `ACTION_TO_PREFERRED_TOOL` at START, or graph `preferred_tool` otherwise.
- **Tool graph restricts tools** per node; the router picks within that set.

### ACTION_TO_PREFERRED_TOOL (START node)

| Action | Preferred Tool |
|--------|----------------|
| SEARCH | retrieve_graph |
| EDIT | edit |
| INFRA | list_dir |
| EXPLAIN | explain |
| READ_FILE | read_file |
| FIND_REFERENCES | find_referencing_symbols |
| BUILD_CONTEXT | build_context |

---

## SECTION 6 — PROMPT DESIGN

### Planner (production)

- **File:** `agent/prompts/planner_system.yaml` (via `planner_system`)
- **Content:** Instructs planner to output JSON with steps; each step has `action` in EDIT, SEARCH, EXPLAIN, INFRA.

### Model Router (production)

- **File:** `agent/prompts/model_router.yaml`
- **Content:**

```yaml
prompt: |
  Classify which model should handle this task.
  Options: SMALL or REASONING
  - Use SMALL for: simple classification, routing, lightweight decisions.
  - Use REASONING for: planning, query rewriting, validation, explanation, multi-step reasoning.

  Task:
  {task_description}

  Return only the label: SMALL or REASONING.
```

- **Usage:** Only via `route_task()`; production uses `get_model_for_task()` (config), so this prompt is unused in the main flow.

### Router Logit (evaluation)

- **File:** `agent/prompts/router_logit_system.yaml`
- **Content:**

```yaml
system_prompt: |
  Reply with exactly one category word: EDIT, SEARCH, EXPLAIN, INFRA, or GENERAL.
```

### Query Rewrite (production)

- **Files:** `agent/prompts/query_rewrite.yaml`, `agent/prompts/query_rewrite_with_context.yaml`
- **Output:** JSON `{ "tool": "retrieve_graph"|"retrieve_vector"|"retrieve_grep"|"list_dir", "query": "", "reason": "" }`
- **Serena rules:** `retrieve_graph` (find_symbol: name_path, substring_matching); `retrieve_grep` (search_for_pattern: regex, DOTALL)
- **Filesystem rules:** `list_dir` — paths relative to project_root; no `~` or paths outside allowed dirs
- **Rewriter wires tool choice:** when tool is valid, sets `state.context["chosen_tool"]` for retrieval order

### Router_eval Prompts

- **File:** `router_eval/prompts/router_prompts.py`
- **Categories:** EDIT, SEARCH, EXPLAIN, INFRA, GENERAL
- **Formats:** Category only; `CATEGORY CONFIDENCE`; `PRIMARY SECONDARY CONFIDENCE`
- **Critic:** `router_eval/prompts/critic_prompt.py` — YES / NO <CATEGORY>

---

## SECTION 7 — FAILURE HANDLING

### Fallback Routing

- **Planner parse failure:** Default step `action = "EXPLAIN"` or `"SEARCH"` depending on error.
- **Tool graph router:** If preferred not in allowed, use first allowed tool.
- **Model router:** `get_model_for_task()` defaults to REASONING if task not in config.

### Confidence Thresholds (router_eval only)

- **critic_router:** Runs critic if confidence < 0.75, primary ≠ secondary, or routers disagree.
- **final_router:** Fast accept if confidence > 0.9 and all agree; otherwise run critic.

### Replanning

- **File:** `agent/orchestrator/replanner.py`
- **Trigger:** Step failure (`success=False`) or `validate_step()` failure.
- **Logic:** LLM-based replanner; receives `failed_step` and `error`; produces revised plan via `call_reasoning_model` or `call_small_model` (task_models["replanner"]). Fallback: returns remaining steps if LLM fails.
- **Limit:** agent_loop: `MAX_REPLAN_ATTEMPTS = 3`; agent_controller: `MAX_REPLAN_ATTEMPTS = 5` (from config).

### Policy Engine Retries

- **SEARCH:** Up to 5 attempts with query rewriting on empty results.
- **EDIT:** Up to 2 attempts with symbol retry.
- **INFRA:** Up to 2 attempts, same step.
- **EXPLAIN:** 1 attempt.

---

## SECTION 8 — LATENCY PATH

### Per-Step Flow

```
User Query
  → [if ENABLE_INSTRUCTION_ROUTER=1] Instruction Router LLM call (SMALL)
  → Planner LLM call (1× per task, or skipped for CODE_SEARCH/CODE_EXPLAIN/INFRA)
  → For each step:
       → Tool graph lookup (no LLM)
       → Tool graph router resolve_tool (no LLM)
       → PolicyEngine:
            → SEARCH: cache → chosen_tool order (retrieve_graph → retrieve_vector → retrieve_grep → list_dir) → search_code fallback; rewriter sets chosen_tool
            → EDIT: diff_planner LLM → patch_executor
            → INFRA: run_command, list_files
            → EXPLAIN: get_model_for_task("EXPLAIN") → LLM call
       → validate_step (optional LLM if use_llm=True)
  → On failure: replan (LLM-based with failed_step, error; fallback to remaining steps)
```

### LLM Calls Per Task

| Phase | LLM Calls |
|-------|-----------|
| Planning | 1 (reasoning model) |
| Per SEARCH step | 0–1 (query rewriting if enabled) |
| Per EDIT step | 1+ (diff_planner, patch_generator) |
| Per EXPLAIN step | 1 (reasoning or small model) |
| Validation | 0 or 1 per step (if use_llm) |

### Router Usage

- **Instruction-level router:** Used when `ENABLE_INSTRUCTION_ROUTER=1`; uses `ROUTER_TYPE` (registry) or inline SMALL model.
- **Model router:** Config lookup only; no LLM.
- **Tool graph router:** Deterministic; no LLM.

---

## SECTION 9 — ARCHITECTURAL STATUS (Post 5-Phase Refactor)

1. **Router_eval integrated.** When `ROUTER_TYPE` is set, `agent/routing/router_registry.py` wires router_eval routers into production. `run_all_routers --production` evaluates the same router used in production.

2. **Categories unified.** Planner and router_eval both use EDIT, SEARCH, EXPLAIN, INFRA, GENERAL (DOCS replaced by EXPLAIN).

3. **`route_task()` unused.** `model_router.route_task()` exists but is never called; production uses `get_model_for_task()` (config only).

4. **Tool graph aligned with execution.** Graph nodes `retrieve_graph`, `retrieve_vector`, `retrieve_grep` map to actual retrieval functions. `_search_fn` respects `chosen_tool` for retrieval order.

5. **Replanner is LLM-based.** Receives `failed_step` and `error`; produces revised plan; fallback to remaining steps on LLM failure.

6. **Instruction router optional.** `ENABLE_INSTRUCTION_ROUTER=1` enables routing before planner; reduces planner calls for CODE_SEARCH/CODE_EXPLAIN/INFRA.

---

## SECTION 10 — SUMMARY

### Simplified Routing Diagram

```
User Query
    │
    ▼
┌─────────────────────────────────────┐
│ route_production_instruction()      │  ← Stage 38: single production entrypoint
│ (1) docs-artifact → DOC             │
│ (2) two-phase docs+code → COMPOUND  │
│ (3) route_instruction() → RoutedIntent │  ← legacy 5-way when ENABLE_INSTRUCTION_ROUTER=1
└──────────┬──────────────────────────┘
           │ RoutedIntent(primary_intent, suggested_plan_shape, ...)
           ▼
┌─────────────────────┐
│ Plan Resolver       │  DOC→docs_seed | SEARCH/EXPLAIN/INFRA→single-step | else→planner
└──────────┬──────────┘
           │
           ▼
┌─────────────┐
│   Planner   │  ← LLM (reasoning model) when EDIT, AMBIGUOUS, COMPOUND-flat, etc.
└──────┬──────┘
       │ steps: [{ action, description }]
       ▼
┌─────────────────┐
│  Agent State    │
│  next_step()    │
└──────┬──────────┘
       │ step.action ∈ {EDIT, SEARCH, EXPLAIN, INFRA}
       ▼
┌─────────────────┐     ┌──────────────────┐
│  step_dispatcher │────▶│  Tool Graph      │
└────────┬────────┘     │  retrieve_graph  │
         │              │  retrieve_vector│
         │              │  retrieve_grep  │
         │              └────────┬─────────┘
         │                       │
         ▼                       ▼
┌─────────────────┐     ┌──────────────────┐
│ tool_graph_router│────▶│ resolve_tool()   │
│ ACTION_TO_      │     │ chosen_tool      │
│ PREFERRED_TOOL  │     └────────┬─────────┘
└─────────────────┘              │
         │                        ▼
         │              ┌──────────────────┐
         │              │ PolicyEngine     │
         │              │ or EXPLAIN LLM   │
         │              └────────┬─────────┘
         │                        │
         ▼                        ▼
┌─────────────────┐     ┌──────────────────┐
│ get_model_for_  │     │ Tool execution   │
│ task("EXPLAIN") │     │ (retrieve_*,     │
│ (config only)   │     │  edit, infra)    │
└─────────────────┘     └──────────────────┘
```

### Summary

- **route_production_instruction** (Stage 38) is the single production entrypoint; returns `RoutedIntent`. Plan resolver consumes it.
- **Instruction router** (optional, legacy path) classifies before planning; reduces planner calls for CODE_SEARCH/CODE_EXPLAIN/INFRA.
- **Production-emission contract** (Stage 39): VALIDATE deferred; COMPOUND production-real only for two-phase docs+code. Telemetry: `resolver_consumption`.
- **Planner** produces EDIT/SEARCH/EXPLAIN/INFRA steps for CODE_EDIT and GENERAL.
- **Tool graph router** maps actions to tools deterministically; graph nodes align with execution (retrieve_graph, retrieve_vector, retrieve_grep).
- **Model router** selects SMALL vs REASONING via config; no LLM.
- **Router_eval** routers can be used in production via `ROUTER_TYPE` and `agent/routing/router_registry.py`.
