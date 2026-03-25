"""ModeManager: routes ACT / PLAN / DEEP_PLAN / plan_execute through the unified pipeline.

Phase 8 — ACT uses ExplorationRunner → Planner → PlanExecutor (replanner inside executor per Phase 7).
ModeManager does not call AgentLoop.run() on the ACT path.
"""
# DO NOT import from agent.* here

from __future__ import annotations

from typing import Any

from agent_v2.schemas.plan import PlanDocument


def _plan_to_state_payload(plan: Any) -> object:
    """Serialize planner output for AgentState.current_plan (JSON-friendly)."""
    if hasattr(plan, "model_dump"):
        return plan.model_dump(mode="json")
    if isinstance(plan, dict):
        return plan.get("steps", plan)
    return plan


def _attach_plan_view(state: Any, plan: Any) -> None:
    """Set current_plan (JSON) and current_plan_steps for trace / CLI."""
    payload = _plan_to_state_payload(plan)
    state.current_plan = payload
    if isinstance(payload, dict) and isinstance(payload.get("steps"), list):
        state.current_plan_steps = payload["steps"]
    elif isinstance(payload, list):
        state.current_plan_steps = payload
    elif hasattr(plan, "steps"):
        steps = getattr(plan, "steps", [])
        if steps and hasattr(steps[0], "model_dump"):
            state.current_plan_steps = [s.model_dump(mode="json") for s in steps]
        else:
            state.current_plan_steps = None
    else:
        state.current_plan_steps = None


class ModeManager:
    """
    Multi-mode agent runtime (Phase 8).
    - ACT: exploration → plan (deep=False) → plan_executor.run (includes replan loop)
    - plan_execute: same as ACT (alias for backward compatibility)
    - PLAN: exploration → plan only (no execution)
    - DEEP_PLAN: exploration → plan (deep=True) only
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

    def run(self, state: Any, mode: str = "act") -> Any:
        if mode == "act":
            return self._run_act(state)
        if mode == "plan_execute":
            return self._run_plan_execute(state)
        if mode == "plan":
            return self._run_plan(state)
        if mode == "deep_plan":
            return self._run_deep_plan(state)
        raise ValueError(f"Unknown mode: {mode}")

    def _run_act(self, state: Any) -> Any:
        return self._run_explore_plan_execute(state, deep=False)

    def _run_plan_execute(self, state: Any) -> Any:
        """Same pipeline as ACT; kept for callers that explicitly request plan_execute."""
        return self._run_explore_plan_execute(state, deep=False)

    def _run_explore_plan_execute(self, state: Any, *, deep: bool) -> Any:
        if self.plan_executor is None:
            raise ValueError(
                "ACT and plan_execute require PlanExecutor; pass plan_argument_generator to AgentRuntime."
            )
        if self.exploration_runner is None:
            raise ValueError("ACT requires exploration_runner.")

        state.context["react_mode"] = True
        lf = state.metadata.get("langfuse_trace")
        exploration = self.exploration_runner.run(state.instruction, langfuse_trace=lf)
        state.exploration_result = exploration
        state.context["exploration_summary_text"] = exploration.summary.overall
        state.context["exploration_result"] = exploration.model_dump(mode="json")

        plan_doc = self.planner.plan(
            state.instruction,
            deep=deep,
            exploration=exploration,
            langfuse_trace=lf,
        )
        if not isinstance(plan_doc, PlanDocument):
            raise TypeError(
                f"Planner must return PlanDocument for ACT path, got {type(plan_doc).__name__}"
            )
        _attach_plan_view(state, plan_doc)

        exec_out = self.plan_executor.run(plan_doc, state)

        final_plan = plan_doc
        ctx = getattr(state, "context", None)
        if isinstance(ctx, dict):
            active = ctx.get("active_plan_document")
            if active is not None and hasattr(active, "model_dump"):
                final_plan = active
        state.current_plan = final_plan.model_dump(mode="json")
        if isinstance(state.current_plan, dict) and isinstance(state.current_plan.get("steps"), list):
            state.current_plan_steps = state.current_plan["steps"]

        if isinstance(exec_out, dict) and "trace" in exec_out:
            return exec_out
        return {"state": state}

    def _run_plan(self, state: Any) -> Any:
        if self.exploration_runner is None:
            raise ValueError("plan mode requires exploration_runner.")

        lf = state.metadata.get("langfuse_trace")
        exploration = self.exploration_runner.run(state.instruction, langfuse_trace=lf)
        state.exploration_result = exploration
        state.context["exploration_summary_text"] = exploration.summary.overall
        state.context["exploration_result"] = exploration.model_dump(mode="json")

        plan = self.planner.plan(
            state.instruction,
            deep=False,
            exploration=exploration,
            langfuse_trace=lf,
        )
        if not isinstance(plan, PlanDocument):
            raise TypeError(
                f"Planner must return PlanDocument for plan mode, got {type(plan).__name__}"
            )
        _attach_plan_view(state, plan)
        return state

    def _run_deep_plan(self, state: Any) -> Any:
        if self.exploration_runner is None:
            raise ValueError("deep_plan mode requires exploration_runner.")

        lf = state.metadata.get("langfuse_trace")
        exploration = self.exploration_runner.run(state.instruction, langfuse_trace=lf)
        state.exploration_result = exploration
        state.context["exploration_summary_text"] = exploration.summary.overall
        state.context["exploration_result"] = exploration.model_dump(mode="json")

        plan = self.planner.plan(
            state.instruction,
            deep=True,
            exploration=exploration,
            langfuse_trace=lf,
        )
        if not isinstance(plan, PlanDocument):
            raise TypeError(
                f"Planner must return PlanDocument for deep_plan mode, got {type(plan).__name__}"
            )
        _attach_plan_view(state, plan)
        return state
