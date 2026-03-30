# REACT UPGRADE — MINIMAL PLAN GENERATION

## 1. Gap Analysis (File + Function Level)

| Required ReAct Component | Current State | Gap (Exact) |
|--------------------------|---------------|-------------|
| **Action selection** | `state.next_step()` returns step from pre-generated plan (`agent/memory/state.py` L39–50) | No per-step LLM. Plan from `get_plan()` once. Need per-iteration LLM that outputs Thought/Action/Args. |
| **Tool abstraction** | `dispatch(step, state)` routes by `step["action"]` (SEARCH, EDIT, EXPLAIN, INFRA, etc.). `agent/execution/step_dispatcher.py` L1103–1310. | ReAct tools (search, open_file, edit, run_tests, finish) need mapping to step dict. `open_file` and `run_tests` have no dispatch branch. |
| **Execution loop control** | `agent/orchestrator/execution_loop.py` L64–110: `while not state.is_finished()`; step from plan. | Loop lives in execution_loop; no per-step LLM call. Need branch: when ReAct mode, replace `step = state.next_step()` with `step = _react_get_action(state)` (LLM + parse). |
| **Observation flow** | `result.output` stored via `state.record()`; used by replanner. | ReAct needs raw observation appended to prompt for next LLM call. No `react_history` or equivalent. |
| **Termination control** | Plan exhaustion (`next_step() is None`) or goal_evaluator. | ReAct needs `Action=finish` as explicit terminate. |

**Exact gaps:**
- `agent/orchestrator/execution_loop.py`: No per-step LLM; no ReAct step source.
- `agent/execution/step_dispatcher.py`: No branch for `READ` (open_file) or `RUN_TEST` (run_tests).
- No `_react_get_action(instruction, state)` that calls LLM and parses Thought/Action/Args.
- No ReAct prompt + observation history in state.

---

## 2. Minimal Change Plan (5 Changes Max)

| # | File(s) | Function(s) | Change |
|---|---------|-------------|--------|
| 1 | `config/agent_runtime.py` | — | Add `ENABLE_REACT_MODE = int(os.getenv("REACT_MODE", "0"))`. |
| 2 | `agent/orchestrator/execution_loop.py` | `execution_loop()` L144–145 | When `REACT_MODE`: replace `step = state.next_step()` with `step = _react_get_next_action(instruction, state)`. If `step` is `None` (finish) or parse fails, break. Add `_react_get_next_action()`: build prompt from instruction + `state.context.setdefault("react_history", [])`, call `call_reasoning_model`, parse `Thought:/Action:/Args:` (regex), return `{"action": "finish"}` or `{"action": tool, "description": args}`, append Observation to react_history after each step. |
| 3 | `agent/execution/step_dispatcher.py` | `dispatch()` | Add branch for `action == "READ"`: `path = step.get("description") or step.get("path", "")`; call `read_file(path)`; return `{success, output: content}`. Add branch for `action == "RUN_TEST"`: `val_scope = resolve_inner_loop_validation(project_root, context)`; `test_result = run_tests(project_root, timeout=..., test_cmd=val_scope["test_cmd"])`; return `{success: test_result["passed"], output: stdout+stderr}`. |
| 4 | `agent/orchestrator/execution_loop.py` | `_react_get_next_action()` | Map ReAct tool names to step action: `search`→SEARCH, `open_file`→READ, `edit`→EDIT, `run_tests`→RUN_TEST. Pass `description` from Args (query/path/instruction). For `finish`, return None to break loop. |
| 5 | `agent/core/actions.py` | — | READ and RUN_TEST already in Action enum; `valid_action_values()` includes them. No change unless dispatch's `validate_step_input` needs update for ReAct step source. |

---

## 3. Action Schema (Exact Format)

```
Thought: <free text>
Action: <tool_name>
Args: <json>
```

**Allowed tools:** `search` | `open_file` | `edit` | `run_tests` | `finish`

**Args format (JSON):**
- `search`: `{"query": "<string>"}`
- `open_file`: `{"path": "<string>"}`
- `edit`: `{"instruction": "<string>"}`
- `run_tests`: `{}`
- `finish`: `{}` (or any)

**Parse:** Regex `Thought:\s*(.*?)\s*Action:\s*(\w+)\s*Args:\s*(\{.*\}|)`; extract thought, action, args. JSON.parse args. Default `Args: {}` if missing.

---

## 4. Loop Integration

**Where loop lives:** `agent/orchestrator/execution_loop.py` L110–145.

**Loop condition when REACT_MODE:** Use `state.context.get("react_finish")` instead of `state.is_finished()` — set `react_finish=True` when Action=finish. Caller must pass plan with `steps=[{id:1, action:"__REACT__"}]` so loop runs (or use `while True` with break on finish/limits).

**ReAct branch (when REACT_MODE):**
1. `step = _react_get_next_action(instruction, state)` — calls LLM, parses, maps to step dict.
2. If `step is None` (finish) or action invalid → break.
3. `result = executor.execute_step(step, state)` — same as now; dispatch handles READ/RUN_TEST/EDIT/SEARCH.
4. `observation = json.dumps({"success": result.success, "output": result.output, "error": result.error})` — raw.
5. Append `{"thought": ..., "action": ..., "args": ..., "observation": observation}` to `state.context["react_history"]`.
6. Continue loop. Next iteration LLM sees full history.

**Retries:** On parse failure → append observation "Parse error: ...", retry same LLM call once (optional). No step retries; treat as single failed step.

**max_steps:** Existing `len(state.completed_steps) >= MAX_STEPS` (L128) and `tool_call_count >= MAX_TOOL_CALLS` (L135) enforce. No change.

---

## 5. Non-Goals Checklist

- [ ] Does NOT change execution engine (dispatcher, executor, policy_engine).
- [ ] Does NOT remove tracing or citation (log_event, trace_id flow unchanged).
- [ ] Does NOT modify retrieval pipeline (SEARCH still uses existing retrieval).
- [ ] Does NOT reintroduce validation layers before execute_patch.
- [ ] Does NOT add planning layers (no upfront planner; ReAct replaces plan-driven step source).
- [ ] Does NOT introduce new frameworks or services.
- [ ] EDIT remains a tool invoked via dispatch; internal edit pipeline (generate_patch → execute_patch → run_tests) unchanged.
