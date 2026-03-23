# SYSTEM AUDIT — ReAct Simplification with Traceability

**Goal:** Audit the entire agent system and identify ALL components that are NOT required for a clean ReAct-style agent loop, while preserving tool-level traceability and component-level logging/observability.

---

## Step 1 — Enumeration of ALL Components

### 1.1 Orchestration & Execution Loop

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **run_controller** | `agent/orchestrator/agent_controller.py` | Entry point; instruction → plan → retrieval → edit → repair → task memory | On `python -m agent` | instruction | result dict |
| **run_attempt_loop** | `agent/orchestrator/agent_controller.py` | Per-instruction attempt loop; MAX_AGENT_ATTEMPTS; trajectory memory, critic, retry_context | Per instruction | instruction, project_root, trace_id | (state, loop_output) |
| **run_deterministic** | `agent/orchestrator/deterministic_runner.py` | Mode 1 deterministic pipeline: plan → execution_loop; goal evaluator; no step retries | Per attempt | instruction, project_root, retry_context | (state, loop_output) |
| **execution_loop** (outer) | `agent/orchestrator/execution_loop.py` | Shared step loop: next_step → execute_step → record → replan or goal_evaluate | While plan has steps | state, instruction, mode | LoopResult |
| **run_edit_test_fix_loop** | `agent/runtime/execution_loop.py` | Edit → test → fix inner loop; snapshot rollback; validation; semantic feedback retry | EDIT step in dispatcher | instruction, context, project_root | success, files_modified, patches_applied |
| **_run_loop** | `agent/runtime/execution_loop.py` | Per-attempt loop: plan_diff → to_structured_patches → validate → verify → execute_patch → run_tests | Each edit attempt | instruction, context, max_attempts | success/failure dict |

### 1.2 Policy Engine

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **ExecutionPolicyEngine** | `agent/execution/policy_engine.py` | Retry with tool-specific policies (SEARCH max 5, EDIT max 2, etc.); mutation strategies | Before every tool call via step_dispatcher | step, state | dict(success, output, error, classification) |
| **validate_step_input** | `agent/execution/policy_engine.py` | Schema validation for steps | At start of dispatch() | step dict | None or InvalidStepError |
| **classify_result** | `agent/execution/policy_engine.py` | SUCCESS / RETRIEYABLE_FAILURE / FATAL_FAILURE | After each tool call | action, raw result | ResultClassification |
| **POLICIES** | `agent/execution/policy_engine.py` | Per-action retry config | Inside execute_with_policy | — | — |

### 1.3 Step Dispatcher & Tools

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **dispatch** | `agent/execution/step_dispatcher.py` | Maps step action to tool; ToolGraph → Router → PolicyEngine | Every step | step, state | dict(success, output, error) |
| **_edit_fn** | `agent/execution/step_dispatcher.py` | EDIT path: plan_diff → resolve_conflicts → run_edit_test_fix_loop | action == EDIT | step, state | success, output, error |
| **_search_fn** | `agent/execution/step_dispatcher.py` | SEARCH: cache → hybrid_retrieve / graph/vector/grep | action == SEARCH | step, state | results, query |
| **_explain_fn** | `agent/execution/step_dispatcher.py` | EXPLAIN: assemble_reasoning_context → reasoning model | action == EXPLAIN | step, state | success, output |
| **_infra_fn** | `agent/execution/step_dispatcher.py` | INFRA: run_command, list_files | action == INFRA | step, state | success, output |
| **ToolGraph** | `agent/execution/tool_graph.py` | Graph of allowed tools per node; enforces tool flow | Before dispatch chooses tool | current_node | allowed_tools |
| **resolve_tool** | `agent/execution/tool_graph_router.py` | Chooses tool by action and graph | After ToolGraph lookup | step_action, allowed_tools | chosen_tool |
| **StepExecutor** | `agent/execution/executor.py` | Wraps dispatch; records results; stops after successful EDIT | Each step | step, state | StepResult |

### 1.4 Planner & Plan Resolution

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **plan** | `planner/planner.py` | Converts instruction to sequence of EDIT, SEARCH, EXPLAIN, INFRA | When plan needed | instruction, retry_context | plan dict |
| **get_plan** | `agent/orchestrator/plan_resolver.py` | Router decides; planner plans; short-circuits for SEARCH/EXPLAIN/INFRA | Before execution_loop | state, instruction | plan |
| **replan** | `agent/orchestrator/replanner.py` | Revises plan on failure; LLM or fallback to remaining steps | On step failure or goal_not_satisfied | state, instruction | new plan |
| **GoalEvaluator** | `agent/orchestrator/goal_evaluator.py` | Checks if goal satisfied when plan exhausted | Plan exhausted | instruction, state | bool |
| **validate_step** | `agent/orchestrator/validator.py` | Rule-based (and optional LLM) step validation | After step execution in agent mode | step, result, state | (valid, feedback) |

### 1.5 Edit Proposal Generator

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **generate_edit_proposals** | `agent/edit/edit_proposal_generator.py` | LLM produces patches from context | Via patch_generator / diff_planner | binding, instruction, context | list of proposals |
| **_build_proposal_from_binding** | `agent/edit/edit_proposal_generator.py` | Builds single proposal from binding | Called by generate_edit_proposals | binding, instruction | proposal dict |

### 1.6 Retry Planner

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **RetryPlanner.build_retry_context** | `agent/meta/retry_planner.py` | Build retry context from trajectory and critic | Before replan on failure (attempt loop) | instruction, trajectory_memory, critic_feedback | retry_context dict |
| **RETRY_STRATEGIES** | `agent/meta/retry_planner.py` | Canonical strategy names | Used by critic/planner | — | — |

### 1.7 Semantic Feedback

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **extract_semantic_feedback** | `editing/semantic_feedback.py` | Structured failure signal from test output | After run_tests in execution_loop | test_result | failure_summary, failing_tests |
| **derive_failure_explanation** | `editing/semantic_feedback.py` | Builds failure explanation from test output | For retry hints | test_result | explanation str |
| **format_causal_feedback_for_retry** | `editing/semantic_feedback.py` | Formats causal feedback for retry prompt | On retry after test failure | feedback dict | formatted str |
| **format_stateful_feedback_for_retry** | `editing/semantic_feedback.py` | Formats stateful feedback (failures, attempted_actions) | Same | feedback dict | formatted str |
| **check_structural_improvement** | `editing/semantic_feedback.py` | Rejects patch if unchanged or same as attempted_patches; same_target check | **Before applying patch** | patch_plan, previous_patch, binding, attempted_patches | (changed, same_target, reject_reason) |
| **extract_previous_patch** | `editing/semantic_feedback.py` | Extracts previous patch for structural comparison | Before check_structural_improvement | patch_plan | previous_patch |

### 1.8 Context Builder / Pruner

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **prune_context** | `agent/retrieval/context_pruner.py` | Limits ranked context by snippets and char budget; deduplicates | After context ranking in retrieval pipeline | ranked_context | pruned list |
| **assemble_reasoning_context** | `agent/retrieval/context_builder_v2.py` | Assembles FILE/SYMBOL/LINES/SNIPPET context for reasoning | Before EXPLAIN/EDIT | snippets | formatted str |
| **build_context** | `agent/retrieval/context_builder.py` | Turns search results into files/snippets | In pipeline | search_results | files, snippets |

### 1.9 Patch Generator & Diff Planner

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **to_structured_patches** | `editing/patch_generator.py` | Converts diff_planner output to AST patches; uses edit_proposal_generator | In _run_loop after plan_diff | changes, instruction, context | patch_plan |
| **plan_diff** | `editing/diff_planner.py` | Plans safe edits; identifies affected symbols and files | In _edit_fn and _run_loop | instruction, context | diff_plan |

### 1.10 Validators

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **validate_syntax_plan** | `editing/syntax_validation.py` | Syntax validation of patches **before apply** | In _run_loop before verify_patch | patch_plan, snapshot | (ok, result dict) |
| **verify_patch_plan** | `editing/patch_verification.py` | Verifies patch plan (has_effect, targets_correct_file, is_local) **before apply** | In _run_loop | patch_plan, snapshot, instruction | verify result |
| **verify_patch** | `editing/patch_verification.py` | Single patch checks | Per change | proposal, full_content, binding | valid, reason, checks |
| **validate_patch** | `editing/patch_validator.py` | Used by patch_executor | Pre-apply in patch_executor | patch, file_path | valid/reject |
| **validate_project** | `agent/runtime/syntax_validator.py` | Project-level syntax check **after apply** | In execution_loop | project_root | valid |

### 1.11 Patch Executor

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **execute_patch** | `editing/patch_executor.py` | Applies validated patches with rollback on failure | In _run_loop after validation | changes, project_root | success, files_modified |

### 1.12 Failure Tracking

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **failure_state** | `agent/runtime/execution_loop.py` | Tracks failures, attempted_patches, stagnation_count | Per attempt in _run_loop | context | failure_state dict |
| **_update_failure_state** | `agent/runtime/execution_loop.py` | Updates failure_state; returns True if stagnation limit reached | On patch/test failure | context, prev_patch, failure_explanation | bool (terminate?) |
| **_update_same_error** | `agent/runtime/execution_loop.py` | Tracks same-error repetition | On failure | last_error, same_error_count, err | (last_error, count) |
| **classify_failure_reason** | `agent/meta/failure_attribution.py` | Maps task record to canonical failure_reason | Post-task | record | SUCCESS, RETRIEVAL_FAILURE, etc. |
| **Critic.analyze** | `agent/meta/critic.py` | Analyzes failure trace; returns diagnosis | After attempt fails | instruction, attempt_data | failure_reason, recommendation, strategy_hint |

### 1.13 Instruction Satisfaction Gating

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **is_instruction_satisfied** | `agent/runtime/execution_loop.py` | Heuristic: checks if instruction intent already in code | NO-OP / already_correct / no_meaningful_diff paths | instruction, full_content, binding | bool |

### 1.14 Logging & Tracing

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **start_trace** | `agent/observability/trace_logger.py` | Starts trace | At task start | task_id, project_root | trace_id |
| **log_event** | `agent/observability/trace_logger.py` | Logs event to trace | Throughout run | trace_id, event_type, payload | — |
| **log_stage** | `agent/observability/trace_logger.py` | Logs stage with latency | Per stage | trace_id, stage_name, latency | — |
| **trace_stage** | `agent/observability/trace_logger.py` | Context manager for stage tracing | Per stage | trace_id, stage_name | summary dict |
| **finish_trace** | `agent/observability/trace_logger.py` | Finalizes trace | At task end | trace_id | path to written file |

### 1.15 Retrieval Pipeline

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **run_retrieval_pipeline** | `agent/retrieval/retrieval_pipeline.py` | Anchor → expand → read → find_references → build_context | After SEARCH in dispatcher | results, state | populates state.context |
| **search_candidates** | `agent/retrieval/retrieval_pipeline.py` | BM25, vector, repo_map, grep candidate discovery | SEARCH_CANDIDATES | query, state | candidates |
| **rank_context** | `agent/retrieval/context_ranker.py` | LLM + symbol/filename/reference scoring | In pipeline | candidates, query | ranked |
| **deduplicate_candidates** | `agent/retrieval/reranker/deduplicator.py` | Deduplicates candidates | In pipeline | candidates | deduplicated |

### 1.16 Memory & State

| Component | File(s) | Purpose | When Executed | Inputs | Outputs |
|-----------|---------|---------|---------------|--------|---------|
| **AgentState** | `agent/memory/state.py` | instruction, plan, completed_steps, step_results, context | All execution | — | — |
| **TrajectoryMemory** | `agent/meta/trajectory_memory.py` | Stores attempt history | Per attempt | attempt_data | — |
| **StepResult** | `agent/memory/step_result.py` | step_id, action, success, output, latency, error, classification | After each step | — | — |

---

## Step 2 — Classification of Each Component

| Component | Category | Reason |
|-----------|----------|--------|
| **execution_loop (outer)** | CORE | Required for ReAct loop; orchestrates thought → action → observation |
| **StepExecutor** | CORE | Executes steps and records results |
| **dispatch** | CORE | Maps actions to tools; single entry point for tools |
| **_edit_fn, _search_fn, _explain_fn, _infra_fn** | CORE | Tool implementations |
| **plan_diff** | CORE | Produces edit plan from instruction + context |
| **to_structured_patches** | CORE | Converts diff plan to patches |
| **execute_patch** | CORE | Applies patches |
| **run_tests** | CORE | Execution feedback (ReAct: observe result) |
| **extract_semantic_feedback** | CORE | Converts test output to structured feedback for next thought |
| **format_causal_feedback_for_retry** | CORE | Injects feedback into instruction for iterative correction |
| **assemble_reasoning_context / build_context** | CORE | Minimal deterministic context for reasoning |
| **prune_context** | CORE (SIMPLIFY) | Budget limits are needed; heuristics may be over-engineered |
| **AgentState, StepResult** | CORE | State and step results |
| **trace_logger (start_trace, log_event, log_stage, finish_trace)** | CORE (KEEP) | Mandatory for traceability |
| **ranked_context, retrieved_files** | CORE | Citation source for tool output attribution |
| **generate_edit_proposals** | CORE | LLM produces patches (thought → action) |
| **edit_proposal_generator** | CORE | Part of edit pipeline |
| **plan** | CORE | Converts instruction to steps (ReAct: initial thought) |
| **get_plan** | CORE | Plan resolution |
| **GoalEvaluator** | CORE | Determines completion |
| **validate_step_input** | CORE (MINIMAL) | Schema validation only; reject malformed steps |
| **run_edit_test_fix_loop** | CORE | Inner edit loop; may be simplified |
| **validate_project** (post-apply) | CORE | Execution feedback; catches syntax errors after apply |
| **run_retrieval_pipeline** | CORE | Retrieval for SEARCH action |
| | | |
| **check_structural_improvement** | REMOVE | **Blocks actions before execution.** ReAct relies on execution feedback; rejecting patches pre-apply conflicts with iterative correction. Same for no_progress_repeat (attempted_patches). |
| **failure_state / _update_failure_state** | REMOVE | **Over-validates before execution.** Stagnation detection and termination block the loop. ReAct should rely on max_attempts and execution feedback. |
| **_update_same_error / MAX_SAME_ERROR_RETRIES** | REMOVE | **Blocks retries.** Same error repeated → early termination. ReAct allows iterative correction; model may need multiple similar attempts. |
| **validate_syntax_plan** (pre-apply) | SIMPLIFY | Optional: ReAct can apply and observe syntax error from validate_project. Keeping a lightweight check may avoid noisy failures. |
| **verify_patch_plan** | REMOVE | **Over-validates before execution.** has_effect, targets_correct_file, is_local block patches. ReAct: apply and observe. |
| **is_instruction_satisfied** | REMOVE | **Instruction satisfaction gating.** Blocks NO-OP success paths with heuristics. ReAct: run tests; if pass, done. |
| **policy_engine POLICIES / execute_with_policy** | SIMPLIFY | Retry logic can stay but should not block. Classification (RETRIEYABLE/FATAL) is useful for replan. Simplify: reduce retry counts, avoid mutation strategies that override agent decisions. |
| **ExecutionPolicyEngine mutation strategies** | REMOVE or OPTIONAL | query_variants, symbol_retry mutate inputs; can override agent. ReAct: agent proposes, execute, observe. |
| **ToolGraph / resolve_tool** | OPTIONAL | Tool flow enforcement; useful for structured plans. For pure ReAct, may be redundant if agent outputs action directly. |
| **replan** | OPTIONAL | Replan on failure is a form of "thought" revision. For simple ReAct, could be folded into single "rethink" step. For multi-step plans, keep. |
| **RetryPlanner.build_retry_context** | OPTIONAL | Used only when replan triggered (attempt loop). Deep mode. |
| **Critic.analyze** | OPTIONAL | Used after attempt fails; feeds retry_context. Deep mode. |
| **TrajectoryMemory** | OPTIONAL | Attempt-level memory. Lightweight "recent steps" is enough for ReAct. |
| **validate_step** (orchestrator) | REMOVE | Rule-based/LLM step validation after execution. Redundant; result already observed. |
| **lane_violation / lane contract** | REMOVE or OPTIONAL | Blocks actions that violate tool graph. For pure ReAct, unnecessary. |
| **strategy_explorer / _run_strategy_explorer** | REMOVE | Explores alternative strategies on exhaustion. Over-engineering; max_attempts suffices. |
| **_critic_and_retry / _apply_hints** | OPTIONAL | Injects critic hints into instruction. Deep mode. |
| **retry_guard / should_retry_strategy** | REMOVE | Gates retries by strategy. ReAct: retry until max_attempts. |
| **weakly_grounded_patch rejection** | SIMPLIFY | Patch generator can reject; consider moving to "observe apply failure" rather than pre-reject. |
| **no_meaningful_diff special handling** | REMOVE | Complex branch with is_instruction_satisfied. ReAct: apply (no-op) → run tests → pass = success. |
| **already_correct / no_changes** | SIMPLIFY | If no changes, run tests. Pass = success. Don't need instruction_satisfied gate. |

---

## Step 3 — Traceability (CRITICAL)

### 3.1 Components Responsible for Traceability

| Component | Purpose | Classification |
|-----------|---------|----------------|
| **trace_logger** (`start_trace`, `log_event`, `log_stage`, `finish_trace`) | Records events and stage timings | **KEEP** |
| **log_event(trace_id, "step_executed", {...})** | Step-level: plan_id, step_id, action, tool, success, error, classification | **KEEP** |
| **log_event(trace_id, "execution_limits", {...})** | Limits at start | **KEEP** |
| **log_event(trace_id, "goal_evaluation", {...})** | Goal check | **KEEP** |
| **trace_stage(trace_id, "retrieval", ...)** | Retrieval stage | **KEEP** |
| **trace_stage(trace_id, "reasoning", ...)** | Reasoning stage | **KEEP** |
| **state.step_results** | StepResult list: step_id, action, success, output, latency, error, classification | **KEEP** |
| **state.record(step, result)** | Appends to step_results and completed_steps | **KEEP** |
| **ranked_context** | Source of retrieved snippets for EDIT; enables "which context led to this" | **KEEP** |
| **retrieved_files** | Files used in trajectory | **KEEP** |
| **chosen_tool** in context | Tool used for step | **KEEP** |

### 3.2 Gap: ReAct Step Trace Format

**Current format:** Trace has `events` (type + payload) and `stages` (stage_name, latency_ms, step_id, summary). There is no explicit **Thought / Action / Observation / Source** per step.

**Gap:** Each step should produce:

```
Step Trace:
- Thought: (from plan step description or reasoning output)
- Action: <tool_name(args)>
- Observation: <result>
- Source: <file/tool/output origin>
```

**Current `step_executed` payload:** `plan_id`, `step_id`, `action`, `tool`, `success`, `error`, `classification`. Missing: structured Observation, Source (citation).

**Recommendation:** Add `observation_summary` and `source` (e.g. `ranked_context` indices or file paths) to step_executed payload. Ensure `state.step_results` and trace events carry `output` (observation) and `files_modified` / `retrieved_files` for citation.

### 3.3 Citation Flow

- **SEARCH:** `ranked_context` → `state.context["ranked_context"]` → used by EDIT. Citation = (file, symbol, snippet) from ranked_context.
- **EDIT:** `edit_binding` + `ranked_context` → patch. Citation = binding.file, binding.symbol, and ranked_context rows that informed the patch.
- **Current:** Citation data exists in context but is not consistently attached to step trace. **Action:** Ensure each StepResult and log_event for EDIT includes `source_files` and `source_symbols` from ranked_context.

---

## Step 4 — Redundant / Harmful Layers

### 4.1 check_structural_improvement

- **Location:** `editing/semantic_feedback.py`; called in `agent/runtime/execution_loop.py` ~L573.
- **What it does:** Before applying a patch, checks: (1) new patch != previous patch, (2) new patch not in attempted_patches, (3) same_target (file/symbol).
- **Conflict with ReAct:** Rejects patches **before execution**. ReAct: execute → observe failure → iterate. Blocking pre-apply prevents the agent from learning from execution feedback.
- **Where it blocks:** `structural_reject = previous_patch and (not changed or not same_target)` → returns `patch_result` with `success: False` without calling `execute_patch`.

### 4.2 failure_state / stagnation / attempted_patches

- **Location:** `agent/runtime/execution_loop.py`; `_update_failure_state` ~L1172.
- **What it does:** Tracks `attempted_patches`, `failures`, `stagnation_count`. Terminates when `stagnation >= MAX_STAGNATION and dominant` (last 2 failures same).
- **Conflict with ReAct:** Early termination and rejection of "repeat" patches. ReAct allows retries; feedback injection (format_stateful_feedback_for_retry) is enough. Stagnation logic overrides the loop.
- **Where it blocks:** `_update_failure_state` returns True → return `no_progress`; also feeds `attempted_patches` into `check_structural_improvement` → rejects before apply.

### 4.3 validate_syntax_plan (pre-apply)

- **Location:** `editing/syntax_validation.py`; called in `_run_loop` ~L643.
- **What it does:** Validates patch produces valid AST before apply.
- **Conflict:** Minor. ReAct could apply and observe. Keeping a lightweight syntax check reduces noisy apply→rollback cycles. **Recommendation:** SIMPLIFY (keep but minimal).

### 4.4 verify_patch_plan

- **Location:** `editing/patch_verification.py`; called in `_run_loop` ~L673.
- **What it does:** has_effect (old != new), targets_correct_file, is_local.
- **Conflict with ReAct:** Blocks patches before apply. ReAct: apply → observe. No-op patches can be observed as "no change" from execute_patch.
- **Where it blocks:** Returns invalid → `patch_result` with success False, never calls execute_patch.

### 4.5 is_instruction_satisfied

- **Location:** `agent/runtime/execution_loop.py` ~L153.
- **What it does:** Heuristic: "def X" in instruction and binding.symbol in content → satisfied. Used for NO-OP / already_correct / no_meaningful_diff paths.
- **Conflict with ReAct:** Gates success on heuristic. ReAct: run tests; if pass, success. No need for instruction_satisfied.
- **Where it blocks:** `already_correct` path: if tests pass but `not is_instruction_satisfied` → return `noop_rejected`.

### 4.6 Policy engine retry / mutation

- **Location:** `agent/execution/policy_engine.py`; `execute_with_policy`.
- **What it does:** SEARCH: up to 5 retries with query_variants; EDIT: up to 2 with symbol_retry. Mutates step/query before retry.
- **Conflict:** Mutation can override agent output. ReAct: agent proposes → execute → observe. Retries are OK; mutation strategies are not.
- **Where it overrides:** Policy rewrites query or binding before tool call.

### 4.7 retry_guard / should_retry_strategy

- **Location:** `agent/runtime/retry_guard.py`; used in `_run_loop` ~L925.
- **What it does:** Gates continue/return based on strategy.
- **Conflict:** Adds another layer of "should we retry". ReAct: retry until max_attempts.

### 4.8 Context pruning heuristics

- **Location:** `agent/retrieval/context_pruner.py`.
- **What it does:** Prefer symbol over region over file; MIN_FALLBACK_CHARS for implementation_body.
- **Assessment:** Budget limits are necessary. Heuristics (kind rank) are a simplification. **SIMPLIFY** if over-engineered; otherwise keep.

---

## Step 5 — Actual Execution Path

```
User input
  → run_controller → run_attempt_loop
    → run_deterministic
      → get_plan (plan_resolver)
        → [intent_router → planner or short-circuit]
      → execution_loop (outer)
        → state.next_step()
        → StepExecutor.execute_step(step, state)
          → dispatch(step, state)
            → validate_step_input(step)           ← schema only
            → [lane contract / ToolGraph]         ← can block
            → resolve_tool(step_action)
            → ExecutionPolicyEngine.execute_with_policy  ← retries, mutations
              → _edit_fn / _search_fn / etc.
                [EDIT path]
                → plan_diff
                → resolve_conflicts
                → run_edit_test_fix_loop
                  → _run_loop
                    → plan_diff
                    → to_structured_patches
                    → [NO-OP / already_correct] → is_instruction_satisfied?  ← BLOCK
                    → check_structural_improvement        ← BLOCK (structural_reject)
                    → validate_syntax_plan                ← BLOCK (syntax_ok)
                    → verify_patch_plan                   ← BLOCK (verify_ok)
                    → execute_patch
                    → validate_project (post-apply)       ← rollback if invalid
                    → run_tests
                    → [if fail] extract_semantic_feedback
                    → _update_failure_state               ← BLOCK (stagnation)
                    → _update_same_error                  ← BLOCK (MAX_SAME_ERROR_RETRIES)
                    → _should_retry_strategy              ← BLOCK
                    → _critic_and_retry, _apply_hints
                    → continue
        → state.record(step, result)
        → [if not success] replan?                       ← control leaves loop
        → [if plan exhausted] GoalEvaluator.evaluate
        → [if not goal_met] replan
  → [if attempt fails] Critic.analyze, RetryPlanner.build_retry_context
  → next attempt with retry_context
```

**Decision override points:**
- Lane violation → block
- check_structural_improvement → block (no execute_patch)
- validate_syntax_plan → block
- verify_patch_plan → block
- is_instruction_satisfied → block (noop_rejected)
- _update_failure_state (stagnation) → terminate
- _update_same_error (MAX_SAME_ERROR_RETRIES) → terminate
- _should_retry_strategy → terminate
- Policy mutation → overrides agent's step/query
- replan → control leaves step loop

---

## Step 6 — Removal Plan

### PHASE 1 (Safe Removal)

| Component | Action |
|-----------|--------|
| **check_structural_improvement** | Remove; stop blocking patches before apply. Feed previous_patch + attempted_patches as **feedback** into instruction only. |
| **failure_state stagnation termination** | Remove early exit from _update_failure_state. Keep failure_state for feedback injection only. |
| **attempted_patches in check_structural_improvement** | Remove (check_structural_improvement removed). |
| **is_instruction_satisfied** | Remove. For NO-OP / already_correct: run tests; if pass → success. |
| **verify_patch_plan (pre-apply)** | Remove. Let execute_patch apply; observe result. |
| **no_meaningful_diff special branch** | Simplify: run tests; if pass → success. Remove instruction_satisfied gate. |
| **_should_retry_strategy** | Remove. Retry until max_attempts. |
| **strategy_explorer** | Remove. |
| **validate_step** (orchestrator) | Remove. |

### PHASE 2 (Simplification)

| Component | Action |
|-----------|--------|
| **validate_syntax_plan** | Keep minimal pre-apply check or remove; prefer post-apply validate_project. |
| **Policy engine mutations** | Remove query_variants, symbol_retry; keep retry count and classification only. |
| **_update_same_error / MAX_SAME_ERROR_RETRIES** | Remove or raise limit significantly. |
| **context_pruner** | Review heuristics; keep budget limits. |
| **weakly_grounded_patch** | Consider moving to "apply and observe" rather than pre-reject. |

### PHASE 3 (Deep Mode Migration)

| Component | Action |
|-----------|--------|
| **Critic.analyze** | Move to optional "deep mode" when attempt fails. |
| **RetryPlanner.build_retry_context** | Move to optional deep mode. |
| **_critic_and_retry / _apply_hints** | Move to optional deep mode. |
| **TrajectoryMemory** | Simplify to recent steps only for core loop. |
| **ToolGraph / lane contract** | Optional; keep for structured plans, remove for pure ReAct. |
| **replan** | Keep for plan-based mode; in pure ReAct, could be single "rethink" step. |

---

## Step 7 — Final Target Architecture

### 7.1 Clean ReAct Loop

```
LOOP:
  1. Thought (from plan step or reasoning)
  2. Action (tool call: SEARCH, EDIT, EXPLAIN, INFRA)
  3. Observation (tool result)
  4. [If not done] Update context with observation → next Thought
```

### 7.2 Tools

| Tool | Purpose |
|------|---------|
| SEARCH | Hybrid retrieve (BM25, graph, vector, grep) → ranked_context |
| EDIT | plan_diff → to_structured_patches → execute_patch → run_tests |
| EXPLAIN | Assemble context → reasoning model |
| INFRA | run_command, list_files |
| apply_patch | (inside EDIT) Apply patches |
| run_tests | (inside EDIT) Validation |

### 7.3 Execution Flow

```
User instruction
  → get_plan (plan = [step1, step2, ...])
  → execution_loop:
      while plan has steps:
        step = next_step()
        if step is None:
          if GoalEvaluator.evaluate(instruction, state): break
          else: replan (optional) or break
        result = dispatch(step, state)  # validate_step_input only
        state.record(step, result)
        if result.success and step.action == EDIT: break  # optional
        if result.classification == FATAL: break
        # Feedback injected into context for next step
```

**EDIT inner flow (simplified):**
```
plan_diff → to_structured_patches → execute_patch → validate_project → run_tests
  [on failure] extract_semantic_feedback → inject into instruction → retry (up to max_attempts)
```

### 7.4 Logging + Tracing Flow

- `start_trace` at task start
- `log_event(trace_id, "step_executed", {plan_id, step_id, action, tool, success, error, classification, observation_summary, source})`
- `trace_stage` for retrieval, reasoning
- `finish_trace` at task end
- `state.step_results`: list of StepResult with output, files_modified, latency

### 7.5 Citation Flow

- **SEARCH:** `ranked_context` → `state.context["ranked_context"]`; each row has file, symbol, snippet
- **EDIT:** `edit_binding` (file, symbol) + `ranked_context` rows used
- **StepResult / log_event:** Include `source_files`, `source_symbols` from ranked_context for attribution

### 7.6 Example Step Trace Format

```
Thought: Add a function that returns the sum of two numbers in src/math.py

Action: EDIT(instruction="Add a function that returns the sum of two numbers", context={ranked_context: [...], edit_binding: {file: "src/math.py", symbol: null}})

Observation: success=true, files_modified=["src/math.py"], patches_applied=1, tests_passed=true

Source: ranked_context[0] (file=src/math.py, snippet="..."), edit_binding.file=src/math.py
```

---

## Summary

| Category | Count | Examples |
|----------|-------|----------|
| CORE | ~25 | execution_loop, dispatch, plan_diff, execute_patch, run_tests, trace_logger, ranked_context |
| REMOVE | 10+ | check_structural_improvement, failure_state stagnation, verify_patch_plan, is_instruction_satisfied, retry_guard, strategy_explorer |
| SIMPLIFY | 5+ | validate_syntax_plan, policy mutations, _update_same_error, context_pruner |
| OPTIONAL (deep mode) | 6+ | Critic, RetryPlanner, TrajectoryMemory, ToolGraph, replan |

**Traceability:** Keep trace_logger, step_executed events, step_results. Add observation_summary and source (citation) to each step. Ensure ranked_context and edit_binding are preserved for attribution.
