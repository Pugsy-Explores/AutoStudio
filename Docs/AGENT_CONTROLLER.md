# Agent Controller — Full Pipeline

The **agent controller** (`run_controller`) orchestrates the development workflow: instruction → build_repo_map → run_hierarchical → execution_loop (ReAct) → save_task. Model selects actions (search, open_file, edit, run_tests, finish). EDIT uses `_edit_react` → `_generate_patch_once` → execute_patch → run_tests. See [REACT_ARCHITECTURE.md](REACT_ARCHITECTURE.md).

---

## CLI

`autostudio explain/edit/chat/trace/debug` and Phase 12 `issue/fix/pr/review/ci`. See root [README.md](../README.md) Quick Start for full commands.

---

## Entry Point (Programmatic)

```python
from agent.orchestrator.agent_controller import run_controller

result = run_controller(
    instruction="Add a retry decorator to the fetch function",
    project_root="/path/to/repo",
)
# Returns: {
#   task_id,
#   instruction,
#   state,                    # AgentState (for backward compatibility with run_agent() callers)
#   completed_steps,          # len(AgentState.completed_steps)
#   files_modified,           # derived from AgentState.step_results
#   patches_applied,          # integer count of all applied patches in this attempt
#   errors,
#   retrieved_symbols,
# }
```

**CLI entrypoints:** `python -m agent` and `python -m agent.cli.run_agent` invoke `run_controller(instruction)` → `run_hierarchical` → **execution_loop** (ReAct). See [REACT_ARCHITECTURE.md](REACT_ARCHITECTURE.md).

---

## Pipeline Flow

**Current implementation:** The controller has no mode parameter. It always runs the ReAct path:

```
instruction
  → start_trace()
  → ensure_retrieval_daemon() (if RERANKER/EMBEDDING_USE_DAEMON)
  → build_repo_map() — spec format {modules, symbols, calls} → repo_map.json
  → search_similar_tasks() — vector index of past tasks (optional)
  → run_hierarchical(instruction, root, trace_id, similar_tasks)
       → execution_loop(state, instruction)  # ReAct: model selects actions
  → save_task() — persist to .agent_memory/tasks/
  → finish_trace()
  → return task summary
```

See [REACT_ARCHITECTURE.md](REACT_ARCHITECTURE.md) for the execution_loop flow.

**Legacy design (not in code):** run_attempt_loop, get_plan, GoalEvaluator, Critic, RetryPlanner. See [PHASE_5_ATTEMPT_LOOP.md](PHASE_5_ATTEMPT_LOOP.md).

---

## EDIT Flow

**ReAct:** EDIT → `_edit_react` → `_generate_patch_once` → execute_patch → run_tests. [REACT_ARCHITECTURE.md](REACT_ARCHITECTURE.md).

**Legacy:** plan_diff → resolve_conflicts → to_structured_patches → run_with_repair. [EDIT_PIPELINE_DETAILED_ANALYSIS.md](EDIT_PIPELINE_DETAILED_ANALYSIS.md).

---

## Safety Limits

| Limit | Value | Purpose |
|-------|-------|---------|
| `MAX_FILES_EDITED` | 5 | Max files per edit step |
| `MAX_PATCH_SIZE` | 200 lines | Max lines per patch |
| `MAX_TASK_RUNTIME_SECONDS` | 900 (15 min) | Max task runtime (from config; agent_loop uses 60s) |
| `MAX_STEP_TIMEOUT_SECONDS` | 15 | Per-step timeout (Phase 7); prevents single slow tool from consuming full budget |
| `MAX_CONTEXT_CHARS` | 32000 | Hard cap on context before LLM call (Phase 7); truncation logs context_guardrail_triggered |

All limits are defined in `config/` and support env overrides. See [CONFIGURATION.md](CONFIGURATION.md).

---

## Task Memory

- **Location:** `.agent_memory/tasks/`
- **Content:** `task_id`, `instruction`, `plan`, `steps`, `patches`, `files_modified`, `errors`, `project_root`
- **API:** `save_task()`, `load_task()`, `list_tasks()` from `agent/memory/task_memory.py`

---

## Trace Logging

- **Location:** `.agent_memory/traces/`
- **API:** `start_trace()`, `log_event()`, `finish_trace()` from `agent/observability/trace_logger.py`
- **Live visualization (Phase 6):** `add_event_listener()`, `add_stage_listener()` — used by `--live` flag
- **Events:**
  - `planner_decision` — plan with steps
  - `step_executed` — plan_id, step_id, action, tool (chosen_tool), success (Phase 4)
  - `step_timeout` — plan_id, step_id, action (Phase 7: per-step timeout exceeded)
  - `context_guardrail_triggered` — original_chars, capped_chars (Phase 7: context truncated before LLM)
  - `patch_result` — plan_id, step_id, patches_applied (count), files_modified (per EDIT step) (Phase 4)
  - `error` — plan_id, step_id, step failures, max runtime, max replan, exceptions; includes `classification` when available (Phase 4)
  - `goal_evaluation` / `goal_completed` / `goal_unresolved` — plan_id, completed_steps count (Phase 4)
  - `task_complete` — task_id, completed_steps (count), errors, patches_applied (total count), files_modified
  - **Phase 5 attempt loop:** `attempt_started`, `attempt_failed`, `attempt_retry`, `attempt_success`, `critic_analysis`, `strategy_hint_generated`, `trajectory_summary_generated` (attempt, summary_length; see [PHASE_5_ATTEMPT_LOOP.md](PHASE_5_ATTEMPT_LOOP.md))

---

## UX Metrics (Phase 6)

- **Location:** `reports/ux_metrics.json`
- **API:** `agent/observability/ux_metrics.py` — `record_task_metrics()`, `compute_patch_success_rate()`
- **Fields:** `interaction_latency`, `steps_per_task`, `tool_calls`, `patch_success` (for EDIT tasks)

---

## Session Memory (Phase 6)

- **Module:** `agent/memory/session_memory.py`
- **SessionState:** `conversation_history`, `recent_files`, `recent_symbols` — used by `autostudio chat`
- **Context injection:** `to_context_dict()` provides `session_recent_files`, `session_recent_symbols` for multi-turn workflows

---

## Environment Variables

See [CONFIGURATION.md](CONFIGURATION.md) for the full list. Key variables:

| Variable | Purpose |
|----------|---------|
| `ENABLE_INSTRUCTION_ROUTER` | 1 or 0 (default) — route instruction before planner; CODE_SEARCH/CODE_EXPLAIN/INFRA skip planner |
| `ROUTER_TYPE` | baseline, fewshot, ensemble, or final — use router from registry when instruction router enabled |
| `TEST_REPAIR_ENABLED` | 1 (default) or 0 — run tests after patch; 0 = patch only |
| `COMPILE_BEFORE_TEST` | 1 (default) or 0 — run py_compile before tests |
| `SERENA_PROJECT_DIR` | Project root (fallback when `project_root` not passed) |
| `MAX_AGENT_ATTEMPTS` | Phase 5: max attempt-loop iterations (default 3) |

---

## Observability Tests

`tests/test_observability.py` verifies trace creation and content:

- Trace file created in `.agent_memory/traces/`
- Trace contains plan, tool calls, errors, patch results
- `task_complete` includes summary (completed_steps, errors, patches_applied, files_modified)

```bash
python -m pytest tests/test_observability.py -v
```

---

## E2E Tests

`tests/test_agent_e2e.py` exercises the full pipeline with mocked LLM responses. For broader scenario evaluation (40 tasks), use `python scripts/run_principal_engineer_suite.py --scenarios`; see [dev/roadmap/phase_3_scenarios.md](../dev/roadmap/phase_3_scenarios.md). For Phase 5 capability eval (40 developer tasks), use `python scripts/run_capability_eval.py`.

| Scenario | Instruction | Flow |
|----------|-------------|------|
| Explain code | "Explain how StepExecutor works" | plan → search → retrieval → explain |
| Code edit | "Add logging to StepExecutor.execute_step" | plan → search → diff planner → patch → index update |
| Multi-file change | "Add logging to every executor class" | conflict resolver → sequential patch groups |

Assertions: no exceptions, patches applied, index updated, task memory saved. Uses `TEST_REPAIR_ENABLED=0` and `ENABLE_DIFF_PLANNER=1` for deterministic runs.

Default: tries real LLM; if unreachable, warns and falls back to mock. Use `--mock` to force mock mode.

```bash
python -m pytest tests/test_agent_e2e.py -v          # default: try LLM, fallback to mock
python -m pytest tests/test_agent_e2e.py -v --mock   # always use mock
```

---

## File Reference

- **Controller:** `agent/orchestrator/agent_controller.py` — `run_controller`, `run_attempt_loop` (Phase 5), `_run_controller_by_mode` (routes to autonomous/multi_agent)
- **Execution loop:** `agent/orchestrator/execution_loop.py` — `execution_loop` (shared by run_agent and run_deterministic; enable_goal_evaluator / enable_step_retries control behavior)
- **Deterministic runner:** `agent/orchestrator/deterministic_runner.py` — `run_deterministic` (get_plan → execution_loop with goal evaluator; Mode 1 source; accepts optional `retry_context`)
- **CLI:** `agent/cli/entrypoint.py` — autostudio subcommands; `agent/cli/session.py` — chat REPL; `agent/cli/command_parser.py` — slash-commands
- **Instruction router:** `agent/routing/instruction_router.py` — `route_instruction`
- **Task memory:** `agent/memory/task_memory.py`
- **Session memory:** `agent/memory/session_memory.py` — SessionState for chat
- **Trace logger:** `agent/observability/trace_logger.py`
- **UX metrics:** `agent/observability/ux_metrics.py`
- **Conflict resolver:** `editing/conflict_resolver.py`
- **Test repair:** `editing/test_repair_loop.py`
- **Change detector:** `repo_graph/change_detector.py`
- **Repo map updater:** `repo_graph/repo_map_updater.py` — update_repo_map_for_file (after update_index_for_file)
