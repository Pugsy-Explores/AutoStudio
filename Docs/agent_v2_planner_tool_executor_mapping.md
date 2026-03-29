# Planner tool ↔ executor mapping (LOCKED reference)

Three layers appear in the codebase. **Do not conflate** them when debugging or logging.

## 1. Planner engine (`engine.tool`)

Values include: `explore`, `search_code`, `open_file`, `run_shell`, `edit`, `run_tests`, `none` (for `stop` / `replan`).

## 2. Plan step (`PlanStep.action`)

Consumed by **PlanValidator** and **PlanExecutor**. Defined in `agent_v2/runtime/phase1_tool_exposure.py` as `PLANNER_TOOL_TO_PLAN_STEP_ACTION`.

| Planner `engine.tool` | `PlanStep.action` |
|----------------------|-------------------|
| `search_code` | `search` |
| `open_file` | `open_file` |
| `run_shell` | `shell` |
| `edit` | `edit` |
| `run_tests` | `run_tests` |

**Note:** Some prose says “read” for file open. In this repo, **PlanStep.action is `open_file`**, not `read`.

## 3. Legacy ReAct row (`_react_action` / uppercase)

`PLAN_STEP_TO_LEGACY_REACT_ACTION` maps PlanStep.action → dispatcher string (shell uses a separate path, not this table).

| `PlanStep.action` | Legacy ReAct `action` |
|-------------------|------------------------|
| `search` | `SEARCH` |
| `open_file` | `READ` |
| `edit` | `EDIT` |
| `run_tests` | `RUN_TEST` |
| `shell` | *(not in table — `PlanExecutor._dispatch_shell`)* |

## 4. `tool: "none"`

**Keep.** For `decision` `stop` or `replan`, `tool` must be `none` — clean separation from `act` decisions.

## 5. Code anchors

- `PLANNER_TOOL_TO_PLAN_STEP_ACTION`, `PLAN_STEP_TO_LEGACY_REACT_ACTION` — `agent_v2/runtime/phase1_tool_exposure.py`
- `PLAN_STEP_ACTION_TO_PLANNER_TOOL` — inverse map for `tool_execution` logging (`tool` = planner id)
