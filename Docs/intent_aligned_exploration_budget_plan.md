# Intent-aligned exploration budget (planner signal)

**Status:** Implemented (see git history).  
**Scope:** Planner-only advisory signal — no exploration engine control-flow changes.

## Goals

- Bound planner EXPLORE appetite using intent-derived **budget** (cap 3).
- Surface **two** usage signals: planner EXPLORE decision count vs **last run inner steps** (`engine_loop_steps`).
- Strong **prompt framing** (not soft “you have a budget” only).
- Deterministic **fallback** when `intent_type` / `focus` / `relationship_hint` are sparse.

## Non-goals

- No changes to `EngineDecisionMapper`, `_explore_inner`, or forced stop on budget.
- No treating `explore_streak` or planner EXPLORE count as “engine work done.”

## Design

### Budget derivation (`effective_exploration_budget`)

1. If `intent_type` set → navigation=1, explanation=2, debugging=3, modification=2.
2. Elif `focus` set → relationships / internal_logic / usage → **2** (explanation-equivalent; no separate “relationships” tier).
3. Elif `relationship_hint` not `none` → **2**.
4. Else default **2** (explanation).

`effective = min(raw, EXPLORATION_BUDGET_GLOBAL_CAP)` (3).

### Session memory

- `explore_decisions_total`: incremented when planner output `decision == explore`.
- `last_exploration_engine_steps`: set from `FinalExplorationSchema.metadata.engine_loop_steps` after each `exploration_runner.run()`.
- **Reset** both counters on substantive `record_user_turn` (same path that refreshes `intent_anchor` — new user instruction / root task). Vague follow-ups do not reset.

### Metadata

- `ExplorationResultMetadata.engine_loop_steps`: final `ex_state.steps_taken` for that `explore()` run (adapter/engine boundary only).

### Planner surface

- `PlannerPlanContext.exploration_budget`: optional; populated in `exploration_to_planner_context` / `normalize_planner_plan_context`. Composer may fall back to `_effective_query_intent` if unset.
- Context block appended section: strong framing + `explores used / budget` + `last inner steps`.

### Prompts

- Qwen + ACT packaged prompts: **EXPLORATION COST** rules in system prompt (expensive, each EXPLORE consumes budget, unnecessary explore risks task failure).
- Default stem YAMLs: same block where a `system_prompt` exists; otherwise reliance on composed `context_block` only.

## Files touched (implementation checklist)

- `agent_v2/schemas/exploration.py` — `effective_exploration_budget`, `engine_loop_steps` on metadata.
- `agent_v2/schemas/planner_plan_context.py` — `exploration_budget`.
- `agent_v2/runtime/exploration_planning_input.py` — populate budget + replan normalize.
- `agent_v2/runtime/session_memory.py` — counters, reset, `record_last_exploration_engine_steps`.
- `agent_v2/runtime/planner_task_runtime.py` — sync session after every exploration run.
- `agent_v2/exploration/exploration_result_adapter.py` + `exploration_engine_v2.py` — pass steps into adapter.
- `agent_v2/planner/planner_v2.py` — budget section in exploration + replan context blocks.
- `agent_v2/runtime/replanner.py` — `exploration_budget` on `PlannerPlanContext`.
- `agent/prompt_versions/planner.decision.v1/...`, `planner.replan.v1/...`, `planner.decision.act`, `planner.replan.act` (+ default `v1.yaml` where applicable).

## Verification

- `pytest tests/test_session_memory.py tests/test_planner_v2.py` (+ exploration adapter tests if needed).
