"""ModeManager: thin entrypoint — routes ACT / PLAN / DEEP_PLAN / plan_execute to PlannerTaskRuntime."""

from __future__ import annotations

from typing import Any

from agent_v2.runtime.planner_task_runtime import PlannerTaskRuntime


class ModeManager:
    """
    Multi-mode agent runtime (Phase 8).
    - ACT / plan_execute: exploration → controller loop → PlanExecutor (full tools).
    - plan: exploration → safe controller loop → PlanExecutor (no edit; plan_safe validator).
    - plan_legacy: exploration → single planner call → plan only (legacy trace).
    - deep_plan: same as plan with deep=True (iterative safe execution).
    """

    def __init__(
        self,
        exploration_runner: Any,
        planner: Any,
        plan_executor: Any,
        *,
        loop: Any = None,
    ):
        self.exploration_runner = exploration_runner
        self.planner = planner
        self.plan_executor = plan_executor
        self.loop = loop
        self._task_runtime = PlannerTaskRuntime(exploration_runner, planner, plan_executor)

    def run(self, state: Any, mode: str = "act") -> Any:
        if mode == "act":
            return self._task_runtime.run_explore_plan_execute(state, deep=False)
        if mode == "plan_execute":
            return self._task_runtime.run_explore_plan_execute(state, deep=False)
        if mode == "plan":
            return self._task_runtime.run_plan_explore_execute_safe(state, deep=False)
        if mode == "plan_legacy":
            return self._task_runtime.run_plan_only(state)
        if mode == "deep_plan":
            return self._task_runtime.run_plan_explore_execute_safe(state, deep=True)
        raise ValueError(f"Unknown mode: {mode}")
