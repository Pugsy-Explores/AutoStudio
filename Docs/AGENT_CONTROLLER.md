# Agent Controller — Full Pipeline

The **agent controller** (`run_controller`) orchestrates the complete development workflow: instruction → plan → retrieval → edit → conflict resolution → patch execution → change detection → test repair → task memory. All tool execution goes through `dispatch(step, state)`; EDIT steps use the full pipeline inside the dispatcher's `_edit_fn`. Mode routing: `deterministic` (default), `autonomous`, or `multi_agent`.

---

## CLI (Phase 6)

The `autostudio` CLI wraps `run_controller` for common workflows:

```bash
autostudio explain StepExecutor      # Explain a symbol
autostudio edit "add logging"        # Edit per instruction
autostudio run "Fix the null check"  # Single-shot (legacy)
autostudio chat                      # Interactive session with slash-commands
autostudio chat --live               # Session with live step visualization
```

**Slash-commands in chat:** `/explain <symbol>`, `/fix <desc>`, `/refactor <desc>`, `/add-logging`, `/find <symbol>`

**Trace inspection:**

```bash
autostudio trace <task_id>   # View trace (print mode)
autostudio debug last-run   # Interactive trace viewer for most recent run
```

**Phase 12 — Developer workflow:**

```bash
autostudio issue "Fix retry logic in StepExecutor"   # Full workflow: parse issue → solve → PR → CI → review
autostudio fix "add logging to execute_step"         # Multi-agent solve only (no PR/CI/review)
autostudio pr                                       # Generate PR from last workflow run
autostudio review                                   # Review last patch
autostudio ci                                       # Run CI (pytest, ruff) on project root
```

The `issue` and `fix` commands persist the last workflow result to `.agent_memory/last_workflow.json`; `pr` and `review` load from that file.

---

## Entry Point (Programmatic)

```python
from agent.orchestrator.agent_controller import run_controller

result = run_controller(
    instruction="Add a retry decorator to the fetch function",
    project_root="/path/to/repo",
    mode="deterministic",  # default; use "autonomous" or "multi_agent" for other modes
)
# Returns: { task_id, instruction, completed_steps, files_modified, errors, retrieved_symbols }
```

---

## Pipeline Flow

**Mode routing:** `mode="deterministic"` (default) runs the loop below; `mode="autonomous"` delegates to `run_autonomous`; `mode="multi_agent"` delegates to `run_multi_agent`.

```
instruction
  → [if mode != deterministic] _run_controller_by_mode → run_autonomous or run_multi_agent
  → build_repo_map() — spec format {modules, symbols, calls} → repo_map.json
  → search_similar_tasks() — vector index of past tasks (optional)
  → run_deterministic(instruction, project_root, trace_id, similar_tasks)
       → get_plan(instruction)
            → [if ENABLE_INSTRUCTION_ROUTER=1 (default)] route_instruction() → category
            → if CODE_SEARCH/CODE_EXPLAIN/INFRA: single-step plan, skip planner
            → if CODE_EDIT/GENERAL: planner.plan(instruction)
            → [if ENABLE_INSTRUCTION_ROUTER=0] planner.plan(instruction) directly
       → AgentState with plan, context
       → while not state.is_finished():
            step = state.next_step()
            result = dispatch(step, state)   # ALL steps via dispatch (including EDIT)
            validate_step; on failure → replan(state, failed_step=step, error=...)
            state.record(step, step_result)
  → save_task() — persist to .agent_memory/tasks/
  → finish_trace()
  → return task summary
```

---

## EDIT Flow (Extended)

When `action == "EDIT"`, the controller runs an extended pipeline instead of the standard policy-engine edit:

```
plan_diff(instruction, context)
  → changes: [{ file, symbol, action, patch, reason }]
  → safety checks: max 5 files, 200 lines per patch
  → detect_change_impact() — affected callers, risk level (LOW/MEDIUM/HIGH)
  → resolve_conflicts() — same symbol, same file, semantic overlap → sequential_groups
  → for each group:
        to_structured_patches()
        run_with_repair(patch_plan, project_root, context, max_attempts=3)
          → execute_patch (ast_patcher → patch_validator → write; rollback on invalid syntax, validation failure, or apply error)
          → run tests (pytest)
          → on failure: plan repair, retry (max 3 attempts)
          → flaky detection: re-run failing test with pytest --count=2
          → compile step (py_compile) before tests when COMPILE_BEFORE_TEST=1
  → update_index_for_file() for each modified file
  → update_repo_map_for_file() for each modified file (incremental repo_map refresh)
```

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
  - `step_executed` — step_id, action, tool (chosen_tool), success
  - `step_timeout` — step_id, action (Phase 7: per-step timeout exceeded)
  - `context_guardrail_triggered` — original_chars, capped_chars (Phase 7: context truncated before LLM)
  - `patch_result` — patches_applied, files_modified (when EDIT succeeds)
  - `error` — step failures, max runtime, max replan, exceptions
  - `high_risk_edit` — change impact when risk is HIGH
  - `task_complete` — task_id, completed_steps, errors, patches_applied, files_modified

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

- **Controller:** `agent/orchestrator/agent_controller.py` — `run_controller`, `_run_controller_by_mode` (routes to autonomous/multi_agent)
- **Deterministic runner:** `agent/orchestrator/deterministic_runner.py` — `run_deterministic` (plan → dispatch loop)
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
