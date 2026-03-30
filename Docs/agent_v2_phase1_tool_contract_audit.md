# Agent v2 — Phase 1 tool contract and planner wiring (locked)

Staff-engineer audit follow-up: single source of truth for planner tools, executor actions, validation, and naming. **No aliases** for removed tools; **no** documenting workarounds for dishonest mappings.

**Date locked:** 2026-03-29

---

## Phase 1 tool set (planner-facing)

| Planner tool   | Role | Executor `PlanStep.action` | Notes |
|----------------|------|----------------------------|--------|
| `explore`      | Orchestration (exploration loop) | (no direct PlanExecutor tool step from engine synthesis) | `decision=explore`, non-empty `query` |
| `search_code`  | Repo search | `search` | Prompts use **search_code** only |
| `open_file`    | Read file | `open_file` | |
| `run_shell`    | Shell | `shell` | |
| `edit`         | Patch | `edit` | |
| `run_tests`    | Tests | `run_tests` | Empty `step.input` allowed |
| `none`         | `stop` / `replan` | — | |

**Explicitly not supported in phase 1**

- `analyze_code` — **removed entirely** (was mapped to `open_file` / READ; semantic lie: “snippet analysis” vs opening a path).
- `search_web` — disabled in phase 1 (prompt + schema).

---

## Critical fix 1 — Remove `analyze_code` (no alias)

**Problem:** Planner “analyze snippet” implied a different capability than executor “open file”.

**Decision:** Remove from `PlannerPlannerTool`, all planner prompts, `_TOOL_TO_STEP_ACTION`, and `PlannerEngineStepSpec.action` (dropped `analyze`). Do **not** map legacy values; invalid JSON fails at validation.

**Code touchpoints:** `agent_v2/schemas/plan.py`, `agent_v2/planner/planner_v2.py`.

---

## Critical fix 2 — Enable `run_tests` end-to-end

**Problem:** Executor and `PlanStep` supported `run_tests`; `PlanValidator.ALLOWED_ACTIONS` omitted it → valid-looking plans failed validation.

**Decision:** Add `run_tests` to `ALLOWED_ACTIONS` in `agent_v2/validation/plan_validator.py`. Registry: `get_tool_by_name("run_tests")`; `agent.execution.react_schema.validate_action("run_tests", {})` passes; `plan_executor` maps `run_tests` → `RUN_TEST`.

---

## Major — Strict act inputs in planner (fail early)

**Location:** `PlannerV2._validate_act_tool_inputs` (invoked from `_validate_engine_tool_pairing` after tool ↔ `step.action` alignment).

| Tool / pairing | Requirement |
|----------------|---------------|
| `search_code`  | Non-empty query: `step.input` or `metadata.query` |
| `open_file`    | Non-empty path: `step.input` or `metadata.path` |
| `run_shell`    | Non-empty command: `step.input` or `metadata.command` |
| `run_tests`    | Empty OK |
| `edit`         | (not tightened in this pass) |

---

## Naming — planner vs executor

- **Prompts and `engine.tool`:** always **`search_code`** (and the other planner tool ids above).
- **Synthesized `PlanStep.action` / dispatcher:** **`search`** (and existing executor ids). Do not expose raw executor action names in planner prompts as the primary vocabulary.

---

## Tool exposure module (implementation)

Single import surface: `agent_v2/runtime/phase1_tool_exposure.py`

- `PHASE_1_PLANNER_TOOL_IDS` — full planner tool vocabulary (`explore`, act tools, `none`).
- `PLANNER_ACT_TOOL_IDS` — act tools only.
- `PLANNER_TOOL_TO_PLAN_STEP_ACTION` — planner id → `PlanStep.action`.
- `ALLOWED_PLAN_STEP_ACTIONS` — validator allowlist (work actions + `finish`).
- `PLAN_STEP_TO_LEGACY_REACT_ACTION` — `PlanStep.action` → uppercase legacy ReAct (excludes `shell`).

Wired from: `PlannerV2` (pairing + synthesis map), `PlanValidator.ALLOWED_ACTIONS`, `PlanExecutor._to_dispatch_step`.

---

## Test coverage

- Reject act JSON with empty search / path / shell command when tool is `search_code` / `open_file` / `run_shell`.
- Allow `run_tests` with empty input; `PlanValidator.validate_plan` accepts multi-step plans containing `action=run_tests`.
- `get_tool_by_name("run_tests")` and `validate_action("run_tests", {})` smoke tests.

See `tests/test_planner_v2.py` and `tests/test_phase1_tool_exposure.py`.

---

## Implementation checklist (completed)

- [x] `agent_v2/runtime/phase1_tool_exposure.py` — central exposure + wiring
- [x] `PlannerPlannerTool` — drop `analyze_code`
- [x] `PlannerEngineStepSpec.action` — drop `analyze`
- [x] Prompts + `TOOL_REPAIR_SUFFIX` + JSON examples — no `analyze_code` / no `analyze` in `step.action`
- [x] `_TOOL_TO_STEP_ACTION` — no `analyze_code`
- [x] `_step_spec_to_plan_step` — remove `analyze` → `open_file` coercion
- [x] `_infer_planner_tool` — remove `analyze` mapping
- [x] `PlanValidator.ALLOWED_ACTIONS` — add `run_tests`
- [x] `_validate_act_tool_inputs` — strict search / open_file / shell
- [x] Tests updated / added
