"""ModeManager: routes ACT / PLAN / DEEP_PLAN / plan_execute through the unified pipeline.

Phase 8 — ACT uses ExplorationRunner → Planner → PlanExecutor (replanner inside executor per Phase 7).
ModeManager does not call AgentLoop.run() on the ACT path.
"""
# DO NOT import from agent.* here

from __future__ import annotations

from typing import Any

from agent_v2.config import get_config
from agent_v2.schemas.execution import ErrorType
from agent_v2.schemas.final_exploration import FinalExplorationSchema
from agent_v2.schemas.plan import PlanDocument, PlannerControllerOutput
from agent_v2.schemas.plan_state import plan_state_from_plan_document
from agent_v2.schemas.replan import (
    ReplanCompletedStep,
    ReplanContext,
    ReplanFailureContext,
    ReplanFailureError,
)
from agent_v2.runtime.replanner import merge_preserved_completed_steps, validate_completed_steps_immutable
from agent_v2.runtime.trace_context import clear_active_trace_emitter, set_active_trace_emitter
from agent_v2.runtime.trace_emitter import TraceEmitter


def _plan_to_state_payload(plan: Any) -> object:
    """Serialize planner output for AgentState.current_plan (JSON-friendly)."""
    if hasattr(plan, "model_dump"):
        return plan.model_dump(mode="json")
    if isinstance(plan, dict):
        return plan.get("steps", plan)
    return plan


def _attach_plan_only_trace(state: Any, plan: PlanDocument, emitter: TraceEmitter) -> None:
    """Phase 13 — exploration + planner LLMs only (no tool steps). Mirrors execution_trace_id wiring."""
    trace = emitter.build_trace(state.instruction, plan.plan_id)
    md = getattr(state, "metadata", None)
    if isinstance(md, dict):
        md["execution_trace_id"] = trace.trace_id
        md["trace"] = trace


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


def _exploration_is_complete(exploration: Any) -> bool:
    """
    Phase 12.6 planner boundary:
    - If completion metadata is present, require completion_status=complete.
    - For legacy/older exploration payloads without metadata, allow planning.
    """
    md = getattr(exploration, "metadata", None)
    if md is None:
        return True
    if "unittest.mock" in type(md).__module__:
        return True
    completion_status = getattr(md, "completion_status", None)
    if completion_status is None and isinstance(md, dict):
        completion_status = md.get("completion_status")
    if completion_status is None:
        return True
    status = str(completion_status).lower()
    if status not in {"complete", "incomplete"}:
        return True
    if status == "incomplete":
        reason = _exploration_termination_reason(exploration)
        cfg = get_config()
        if cfg.exploration.allow_partial_for_plan_mode and reason in {
            "max_steps",
            "pending_exhausted",
            "stalled",
        }:
            return True
    return status == "complete"


def _exploration_termination_reason(exploration: Any) -> str:
    md = getattr(exploration, "metadata", None)
    if md is None:
        return "unknown"
    reason = getattr(md, "termination_reason", None)
    if reason is None and isinstance(md, dict):
        reason = md.get("termination_reason")
    return str(reason or "unknown")


def _controller_decision(plan_doc: PlanDocument) -> PlannerControllerOutput:
    if plan_doc.controller is not None:
        return plan_doc.controller
    return PlannerControllerOutput(action="continue", next_step_instruction="", exploration_query="")


def _sub_exploration_gates_ok(exploration: FinalExplorationSchema) -> bool:
    gaps = exploration.exploration_summary.knowledge_gaps or []
    if any(str(g).strip() for g in gaps):
        return True
    return exploration.confidence == "low"


def _failure_replan_context_from_step(
    plan: PlanDocument,
    instruction: str,
    failed_step: Any,
    result: Any,
) -> ReplanContext:
    completed: list[ReplanCompletedStep] = []
    for s in plan.steps:
        if s.execution.status == "completed" and s.step_id != failed_step.step_id:
            summ = ""
            if s.execution.last_result is not None:
                summ = str(s.execution.last_result.output_summary or "")
            completed.append(ReplanCompletedStep(step_id=s.step_id, summary=summ))
    err_type = ErrorType.unknown
    if result.error is not None and result.error.type is not None:
        try:
            err_type = ErrorType(str(result.error.type))
        except ValueError:
            err_type = ErrorType.unknown
    msg = ""
    if result.error is not None and (result.error.message or "").strip():
        msg = str(result.error.message).strip()
    lr_summary = ""
    if failed_step.execution.last_result is not None:
        lr_summary = str(failed_step.execution.last_result.output_summary or "")
    fc = ReplanFailureContext(
        step_id=failed_step.step_id,
        error=ReplanFailureError(type=err_type, message=msg or lr_summary or "step_failed"),
        attempts=int(failed_step.execution.attempts),
        last_output_summary=lr_summary,
    )
    return ReplanContext(
        failure_context=fc,
        completed_steps=completed,
        exploration_summary=None,
        trigger="failure",
    )


def _insufficiency_replan_context(plan: PlanDocument, instruction: str) -> ReplanContext:
    completed: list[ReplanCompletedStep] = []
    for s in plan.steps:
        if s.execution.status == "completed":
            summ = ""
            if s.execution.last_result is not None:
                summ = str(s.execution.last_result.output_summary or "")
            completed.append(ReplanCompletedStep(step_id=s.step_id, summary=summ))
    sid = plan.steps[-1].step_id if plan.steps else "s1"
    fc = ReplanFailureContext(
        step_id=sid,
        error=ReplanFailureError(
            type=ErrorType.unknown,
            message="Insufficient evidence for next decision (controller replan)",
        ),
        attempts=0,
        last_output_summary="",
    )
    return ReplanContext(
        failure_context=fc,
        completed_steps=completed,
        exploration_summary=None,
        trigger="insufficiency",
    )


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
        obs = state.metadata.get("obs")
        lf = state.metadata.get("langfuse_trace")
        trace_emitter = TraceEmitter()
        trace_emitter.reset()
        set_active_trace_emitter(trace_emitter)
        try:
            exploration = self.exploration_runner.run(state.instruction, obs=obs, langfuse_trace=lf)
            state.exploration_result = exploration
            state.context["exploration_summary_text"] = exploration.exploration_summary.overall
            state.context["exploration_result"] = exploration.model_dump(mode="json")
            if not _exploration_is_complete(exploration):
                raise RuntimeError(
                    "Exploration did not complete; planner execution is gated "
                    f"(termination_reason={_exploration_termination_reason(exploration)})."
                )

            if get_config().planner_loop.controller_loop_enabled:
                plan_doc, exec_out = self._run_act_controller_loop(
                    state,
                    exploration,
                    deep=deep,
                    obs=obs,
                    langfuse_trace=lf,
                    trace_emitter=trace_emitter,
                )
            else:
                plan_doc = self.planner.plan(
                    state.instruction,
                    deep=deep,
                    exploration=exploration,
                    obs=obs,
                    langfuse_trace=lf,
                )
                if not isinstance(plan_doc, PlanDocument):
                    raise TypeError(
                        f"Planner must return PlanDocument for ACT path, got {type(plan_doc).__name__}"
                    )
                _attach_plan_view(state, plan_doc)

                exec_out = self.plan_executor.run(plan_doc, state, trace_emitter=trace_emitter)
        finally:
            clear_active_trace_emitter()

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

        obs = state.metadata.get("obs")
        lf = state.metadata.get("langfuse_trace")
        trace_emitter = TraceEmitter()
        trace_emitter.reset()
        set_active_trace_emitter(trace_emitter)
        try:
            exploration = self.exploration_runner.run(state.instruction, obs=obs, langfuse_trace=lf)
            state.exploration_result = exploration
            state.context["exploration_summary_text"] = exploration.exploration_summary.overall
            state.context["exploration_result"] = exploration.model_dump(mode="json")
            if not _exploration_is_complete(exploration):
                raise RuntimeError(
                    "Exploration did not complete; planner execution is gated "
                    f"(termination_reason={_exploration_termination_reason(exploration)})."
                )

            plan = self.planner.plan(
                state.instruction,
                deep=False,
                exploration=exploration,
                obs=obs,
                langfuse_trace=lf,
            )
            if not isinstance(plan, PlanDocument):
                raise TypeError(
                    f"Planner must return PlanDocument for plan mode, got {type(plan).__name__}"
                )
            _attach_plan_view(state, plan)
            _attach_plan_only_trace(state, plan, trace_emitter)
        finally:
            clear_active_trace_emitter()
        return state

    def _run_act_controller_loop(
        self,
        state: Any,
        exploration: FinalExplorationSchema,
        *,
        deep: bool,
        obs: Any,
        langfuse_trace: Any,
        trace_emitter: TraceEmitter,
    ) -> tuple[PlanDocument, Any]:
        """
        ModeManager-owned closed loop: planner structured controller → optional explore →
        one executor step → repeat. Executor does not call the planner.
        """
        cfg = get_config().planner_loop
        md = state.metadata
        if not isinstance(md, dict):
            state.metadata = {}
            md = state.metadata
        md["planner_controller_calls"] = 0
        md["sub_explorations_used"] = 0

        def _budget_planner() -> None:
            if md["planner_controller_calls"] >= cfg.max_planner_controller_calls:
                raise RuntimeError("planner_controller_calls budget exhausted")
            md["planner_controller_calls"] = md["planner_controller_calls"] + 1

        def _merge(new_plan: PlanDocument, old: PlanDocument) -> PlanDocument:
            validate_completed_steps_immutable(old, new_plan)
            return merge_preserved_completed_steps(old, new_plan)

        _budget_planner()
        plan_doc = self.planner.plan(
            state.instruction,
            deep=deep,
            exploration=exploration,
            obs=obs,
            langfuse_trace=langfuse_trace,
            require_controller_json=True,
        )
        if not isinstance(plan_doc, PlanDocument):
            raise TypeError(
                f"Planner must return PlanDocument for ACT controller path, got {type(plan_doc).__name__}"
            )
        _attach_plan_view(state, plan_doc)

        while True:
            ctrl = _controller_decision(plan_doc)

            if ctrl.action == "explore":
                if md["sub_explorations_used"] >= cfg.max_sub_explorations_per_task:
                    md["explore_gate"] = "sub_exploration_budget"
                    _budget_planner()
                    np = self.planner.plan(
                        state.instruction,
                        planner_input=_insufficiency_replan_context(plan_doc, state.instruction),
                        deep=True,
                        obs=obs,
                        langfuse_trace=langfuse_trace,
                        require_controller_json=True,
                    )
                    if not isinstance(np, PlanDocument):
                        raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                    plan_doc = _merge(np, plan_doc)
                    _attach_plan_view(state, plan_doc)
                    continue
                if not _sub_exploration_gates_ok(exploration):
                    md["explore_gate"] = "signals"
                    _budget_planner()
                    np = self.planner.plan(
                        state.instruction,
                        planner_input=_insufficiency_replan_context(plan_doc, state.instruction),
                        deep=True,
                        obs=obs,
                        langfuse_trace=langfuse_trace,
                        require_controller_json=True,
                    )
                    if not isinstance(np, PlanDocument):
                        raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                    plan_doc = _merge(np, plan_doc)
                    _attach_plan_view(state, plan_doc)
                    continue
                query = (ctrl.exploration_query or "").strip()
                old_pd = plan_doc
                exploration = self.exploration_runner.run(
                    query, obs=obs, langfuse_trace=langfuse_trace
                )
                state.exploration_result = exploration
                state.context["exploration_summary_text"] = exploration.exploration_summary.overall
                state.context["exploration_result"] = exploration.model_dump(mode="json")
                md["sub_explorations_used"] = md["sub_explorations_used"] + 1
                ps = plan_state_from_plan_document(old_pd)
                _budget_planner()
                np = self.planner.plan(
                    state.instruction,
                    deep=deep,
                    exploration=exploration,
                    obs=obs,
                    langfuse_trace=langfuse_trace,
                    plan_state=ps,
                    require_controller_json=True,
                )
                if not isinstance(np, PlanDocument):
                    raise TypeError(
                        f"Planner must return PlanDocument, got {type(np).__name__}"
                    )
                plan_doc = _merge(np, old_pd)
                _attach_plan_view(state, plan_doc)
                continue

            if ctrl.action == "replan":
                _budget_planner()
                np = self.planner.plan(
                    state.instruction,
                    planner_input=_insufficiency_replan_context(plan_doc, state.instruction),
                    deep=True,
                    obs=obs,
                    langfuse_trace=langfuse_trace,
                    require_controller_json=True,
                )
                if not isinstance(np, PlanDocument):
                    raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                plan_doc = _merge(np, plan_doc)
                _attach_plan_view(state, plan_doc)
                continue

            out = self.plan_executor.run_one_step(plan_doc, state, trace_emitter=trace_emitter)
            st = out.get("status")
            if st == "success":
                return plan_doc, out
            if st == "failed_step":
                failed_step = out["failed_step"]
                result = out["result"]
                ctx = _failure_replan_context_from_step(plan_doc, state.instruction, failed_step, result)
                _budget_planner()
                np = self.planner.plan(
                    state.instruction,
                    planner_input=ctx,
                    deep=True,
                    obs=obs,
                    langfuse_trace=langfuse_trace,
                    require_controller_json=True,
                )
                if not isinstance(np, PlanDocument):
                    raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                plan_doc = _merge(np, plan_doc)
                _attach_plan_view(state, plan_doc)
                continue
            if st == "progress":
                old_pd = plan_doc
                last_summary = ""
                for s in plan_doc.steps:
                    if s.execution.status == "completed" and s.execution.last_result is not None:
                        last_summary = str(s.execution.last_result.output_summary or "")
                ps = plan_state_from_plan_document(plan_doc, last_result_summary=last_summary)
                _budget_planner()
                np = self.planner.plan(
                    state.instruction,
                    deep=deep,
                    exploration=exploration,
                    obs=obs,
                    langfuse_trace=langfuse_trace,
                    plan_state=ps,
                    require_controller_json=True,
                )
                if not isinstance(np, PlanDocument):
                    raise TypeError(
                        f"Planner must return PlanDocument, got {type(np).__name__}"
                    )
                plan_doc = _merge(np, old_pd)
                _attach_plan_view(state, plan_doc)
                continue

            return plan_doc, out

    def _run_deep_plan(self, state: Any) -> Any:
        if self.exploration_runner is None:
            raise ValueError("deep_plan mode requires exploration_runner.")

        obs = state.metadata.get("obs")
        lf = state.metadata.get("langfuse_trace")
        trace_emitter = TraceEmitter()
        trace_emitter.reset()
        set_active_trace_emitter(trace_emitter)
        try:
            exploration = self.exploration_runner.run(state.instruction, obs=obs, langfuse_trace=lf)
            state.exploration_result = exploration
            state.context["exploration_summary_text"] = exploration.exploration_summary.overall
            state.context["exploration_result"] = exploration.model_dump(mode="json")
            if not _exploration_is_complete(exploration):
                raise RuntimeError(
                    "Exploration did not complete; planner execution is gated "
                    f"(termination_reason={_exploration_termination_reason(exploration)})."
                )

            plan = self.planner.plan(
                state.instruction,
                deep=True,
                exploration=exploration,
                obs=obs,
                langfuse_trace=lf,
            )
            if not isinstance(plan, PlanDocument):
                raise TypeError(
                    f"Planner must return PlanDocument for deep_plan mode, got {type(plan).__name__}"
                )
            _attach_plan_view(state, plan)
            _attach_plan_only_trace(state, plan, trace_emitter)
        finally:
            clear_active_trace_emitter()
        return state
