# Bug ID
BUG-008

# Title
Planner v2 steps_raw[:8] dropped terminal finish when model emitted >8 steps

# Area
execution | planner

# Severity
high

# Description
`_build_plan` used `steps_raw[:8]`. When the LLM returned more than eight steps with `action=finish` only in the tail (e.g. step 10), the truncated list had no `finish`, so `PlanValidator` raised `Plan must include a step with action 'finish'`. Observed in live `agent_v2` test `test_act_mode_returns_same_shape_as_plan_execute`.

# Steps to Reproduce
1. Run planner with a model that returns a JSON plan with >8 steps and `finish` after position 8.
2. Call `PlannerV2.plan` → validation error before execute.

# Expected Behavior
Plans are capped at eight steps while preserving a valid terminal `finish` when the model provided one beyond the cap.

# Actual Behavior
Hard slice to eight rows could omit every `finish` row.

# Logs / Trace
`PlanValidationError: Plan must include a step with action 'finish'`

# Root Cause
Naive `steps_raw[:8]` truncation with no splice of terminal `finish` from the remainder.

# Fix
`agent_v2/planner/planner_v2.py`: `_trim_plan_steps_preserving_finish()` — keep first seven rows plus a `finish` row taken from the tail when present. Unit test: `tests/test_planner_v2.py::TestTrimPlanStepsPreservingFinish`.

# Status
resolved
