"""
PlannerTaskRuntime — orchestration for exploration → plan → execute (Anthropic-style outer loop).

ModeManager delegates here; control flow branches on PlannerDecision only (see planner_decision_mapper).
"""

from __future__ import annotations

from typing import Any, Optional

from agent_v2.config import get_config
from agent_v2.exploration.answer_synthesizer import maybe_synthesize_to_state
from agent_v2.schemas.exploration import (
    effective_exploration_budget,
    read_query_intent_from_agent_state,
)
from agent_v2.runtime.session_memory import SessionMemory
from agent_v2.runtime.tool_policy import (
    ACT_MODE_TOOL_POLICY,
    PLAN_MODE_TOOL_POLICY,
    ToolPolicy,
)
from agent_v2.runtime.exploration_planning_input import (
    call_planner_with_context,
    exploration_to_planner_context,
)
from agent_v2.schemas.planner_plan_context import PlannerPlanContext
from agent_v2.runtime.planner_decision_mapper import planner_decision_from_plan_document
from agent_v2.runtime.replanner import merge_preserved_completed_steps, validate_completed_steps_immutable
from agent_v2.runtime.trace_context import clear_active_trace_emitter, set_active_trace_emitter
from agent_v2.runtime.trace_emitter import TraceEmitter
from agent_v2.schemas.execution import ErrorType
from agent_v2.schemas.final_exploration import FinalExplorationSchema
from agent_v2.schemas.plan import PlanDocument
from agent_v2.schemas.plan_state import plan_state_from_plan_document
from agent_v2.schemas.replan import (
    ReplanCompletedStep,
    ReplanContext,
    ReplanFailureContext,
    ReplanFailureError,
)


def _plan_to_state_payload(plan: Any) -> object:
    if hasattr(plan, "model_dump"):
        return plan.model_dump(mode="json")
    if isinstance(plan, dict):
        return plan.get("steps", plan)
    return plan


def _attach_plan_only_trace(state: Any, plan: PlanDocument, emitter: TraceEmitter) -> None:
    trace = emitter.build_trace(state.instruction, plan.plan_id)
    md = getattr(state, "metadata", None)
    if isinstance(md, dict):
        md["execution_trace_id"] = trace.trace_id
        md["trace"] = trace


def _attach_plan_view(state: Any, plan: Any) -> None:
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


def _planner_session_memory_from_state(state: Any) -> SessionMemory:
    ctx = getattr(state, "context", None)
    if not isinstance(ctx, dict):
        raise TypeError("state.context must be a dict for planner session memory")
    key = "planner_session_memory"
    existing = ctx.get(key)
    if isinstance(existing, SessionMemory):
        return existing
    mem = SessionMemory()
    ctx[key] = mem
    return mem


def _planner_inner(planner: Any) -> Any:
    return getattr(planner, "_inner", planner)


def _set_planner_tool_policy(planner: Any, policy: ToolPolicy) -> ToolPolicy | None:
    """
    Point PlannerV2._tool_policy at the mode-appropriate policy (plan vs act).

    Returns the previous ToolPolicy for restoration in finally blocks.
    """
    inner = _planner_inner(planner)
    if inner is None or not hasattr(inner, "_tool_policy"):
        return None
    prev = getattr(inner, "_tool_policy", None)
    inner._tool_policy = policy
    return prev if isinstance(prev, ToolPolicy) else None


def _restore_planner_tool_policy(planner: Any, previous: ToolPolicy | None) -> None:
    if previous is None:
        return
    inner = _planner_inner(planner)
    if inner is not None and hasattr(inner, "_tool_policy"):
        inner._tool_policy = previous


def _sync_tool_policy_mode_to_state(state: Any, planner: Any) -> None:
    """Expose planner ToolPolicy.mode on state for tool_execution logs (plan vs act)."""
    inner = getattr(planner, "_inner", None)
    if inner is None:
        return
    pol = getattr(inner, "_tool_policy", None)
    mode = getattr(pol, "mode", None)
    if mode is None:
        return
    md = getattr(state, "metadata", None)
    if not isinstance(md, dict):
        return
    md["tool_policy_mode"] = str(mode)


def _sync_session_after_exploration(mem: SessionMemory, exploration: Any) -> None:
    if not isinstance(exploration, FinalExplorationSchema):
        return
    md = exploration.metadata
    steps = int(getattr(md, "engine_loop_steps", 0) or 0) if md is not None else 0
    mem.record_last_exploration_engine_steps(steps)


def _planner_context_for_replan(ctx: ReplanContext, mem: SessionMemory) -> PlannerPlanContext:
    qi = ctx.query_intent
    return PlannerPlanContext(
        replan=ctx,
        session=mem,
        query_intent=qi,
        exploration_budget=effective_exploration_budget(qi),
    )


def _record_session_after_plan(mem: SessionMemory, plan: PlanDocument) -> None:
    eng = plan.engine
    if eng is None:
        return
    mem.record_planner_output(
        decision=str(eng.decision),
        tool=str(getattr(eng, "tool", "none") or "none"),
    )


def _record_session_after_executor_step(mem: SessionMemory, plan: PlanDocument) -> None:
    eng = plan.engine
    tool = str(getattr(eng, "tool", "") or "")
    summ = ""
    active: str | None = None
    for s in plan.steps:
        if s.execution.status != "completed" or s.action == "finish":
            continue
        if s.execution.last_result is not None:
            summ = str(s.execution.last_result.output_summary or "").strip()[:120]
        if s.action == "open_file" and isinstance(s.inputs, dict):
            p = s.inputs.get("path")
            if p:
                active = str(p).strip()[:500]
    mem.record_executor_event(
        decision_kind="act",
        tool=tool or "act",
        summary=summ or "step completed",
        active_file=active,
    )


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
    state: Any,
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
        query_intent=read_query_intent_from_agent_state(state),
    )


def _insufficiency_replan_context(plan: PlanDocument, instruction: str, state: Any) -> ReplanContext:
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
        query_intent=read_query_intent_from_agent_state(state),
    )


class PlannerTaskRuntime:
    """Owns exploration → plan → executor loops; ModeManager is a thin entrypoint."""

    def __init__(
        self,
        exploration_runner: Any,
        planner: Any,
        plan_executor: Any,
    ) -> None:
        self.exploration_runner = exploration_runner
        self.planner = planner
        self.plan_executor = plan_executor

    def run_explore_plan_execute(self, state: Any, *, deep: bool) -> Any:
        if self.plan_executor is None:
            raise ValueError(
                "ACT and plan_execute require PlanExecutor; pass plan_argument_generator to AgentRuntime."
            )
        if self.exploration_runner is None:
            raise ValueError("ACT requires exploration_runner.")

        prev_policy = _set_planner_tool_policy(self.planner, ACT_MODE_TOOL_POLICY)
        obs = state.metadata.get("obs")
        lf = state.metadata.get("langfuse_trace")
        trace_emitter = TraceEmitter()
        trace_emitter.reset()
        set_active_trace_emitter(trace_emitter)
        try:
            mem = _planner_session_memory_from_state(state)
            mem.record_user_turn(state.instruction)
            exploration = self.exploration_runner.run(state.instruction, obs=obs, langfuse_trace=lf)
            state.exploration_result = exploration
            state.context["exploration_summary_text"] = exploration.exploration_summary.overall
            state.context["exploration_result"] = exploration.model_dump(mode="json")
            _sync_session_after_exploration(mem, exploration)
            maybe_synthesize_to_state(state, exploration, lf)

            if get_config().planner_loop.controller_loop_enabled:
                plan_doc, exec_out = self._run_act_controller_loop(
                    state,
                    exploration,
                    deep=deep,
                    obs=obs,
                    langfuse_trace=lf,
                    trace_emitter=trace_emitter,
                    session_memory=mem,
                    validation_task_mode=None,
                )
            else:
                pctx = exploration_to_planner_context(exploration, session=mem, state=state)
                plan_doc = call_planner_with_context(
                    self.planner,
                    state.instruction,
                    pctx,
                    deep=deep,
                    obs=obs,
                    langfuse_trace=lf,
                    require_controller_json=False,
                    session=mem,
                )
                if not isinstance(plan_doc, PlanDocument):
                    raise TypeError(
                        f"Planner must return PlanDocument for ACT path, got {type(plan_doc).__name__}"
                    )
                _record_session_after_plan(mem, plan_doc)
                _attach_plan_view(state, plan_doc)
                _sync_tool_policy_mode_to_state(state, self.planner)
                exec_out = self.plan_executor.run(plan_doc, state, trace_emitter=trace_emitter)
                _record_session_after_executor_step(mem, plan_doc)
        finally:
            clear_active_trace_emitter()
            _restore_planner_tool_policy(self.planner, prev_policy)
            md_fin = getattr(state, "metadata", None)
            if isinstance(md_fin, dict):
                md_fin.pop("plan_validation_task_mode", None)

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

    def run_plan_explore_execute_safe(self, state: Any, *, deep: bool) -> Any:
        """
        PLAN mode (post–plan_mode_safe_loop_upgrade): exploration → ACT-style controller loop
        with PLAN_MODE_TOOL_POLICY, PlanExecutor for safe tools only, ACT-style trace.

        Does not set react_mode (AgentRuntime sets plan_safe_execute for this path).
        """
        if self.plan_executor is None:
            raise ValueError(
                "plan mode (safe loop) requires PlanExecutor; pass plan_argument_generator to AgentRuntime."
            )
        if self.exploration_runner is None:
            raise ValueError("plan mode requires exploration_runner.")

        prev_policy = _set_planner_tool_policy(self.planner, PLAN_MODE_TOOL_POLICY)
        obs = state.metadata.get("obs")
        lf = state.metadata.get("langfuse_trace")
        trace_emitter = TraceEmitter()
        trace_emitter.reset()
        set_active_trace_emitter(trace_emitter)
        try:
            md0 = getattr(state, "metadata", None)
            if not isinstance(md0, dict):
                state.metadata = {}
                md0 = state.metadata
            md0["plan_validation_task_mode"] = "plan_safe"

            mem = _planner_session_memory_from_state(state)
            mem.record_user_turn(state.instruction)
            exploration = self.exploration_runner.run(state.instruction, obs=obs, langfuse_trace=lf)
            state.exploration_result = exploration
            state.context["exploration_summary_text"] = exploration.exploration_summary.overall
            state.context["exploration_result"] = exploration.model_dump(mode="json")
            _sync_session_after_exploration(mem, exploration)
            maybe_synthesize_to_state(state, exploration, lf)

            if get_config().planner_loop.controller_loop_enabled:
                plan_doc, exec_out = self._run_act_controller_loop(
                    state,
                    exploration,
                    deep=deep,
                    obs=obs,
                    langfuse_trace=lf,
                    trace_emitter=trace_emitter,
                    session_memory=mem,
                    validation_task_mode="plan_safe",
                )
            else:
                pctx = exploration_to_planner_context(exploration, session=mem, state=state)
                plan_doc = call_planner_with_context(
                    self.planner,
                    state.instruction,
                    pctx,
                    deep=deep,
                    obs=obs,
                    langfuse_trace=lf,
                    require_controller_json=False,
                    session=mem,
                    validation_task_mode="plan_safe",
                )
                if not isinstance(plan_doc, PlanDocument):
                    raise TypeError(
                        f"Planner must return PlanDocument for plan safe path, got {type(plan_doc).__name__}"
                    )
                _record_session_after_plan(mem, plan_doc)
                _attach_plan_view(state, plan_doc)
                _sync_tool_policy_mode_to_state(state, self.planner)
                exec_out = self.plan_executor.run(plan_doc, state, trace_emitter=trace_emitter)
                _record_session_after_executor_step(mem, plan_doc)
        finally:
            clear_active_trace_emitter()
            _restore_planner_tool_policy(self.planner, prev_policy)
            md_fin = getattr(state, "metadata", None)
            if isinstance(md_fin, dict):
                md_fin.pop("plan_validation_task_mode", None)

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

    def run_plan_only(self, state: Any) -> Any:
        if self.exploration_runner is None:
            raise ValueError("plan_legacy mode requires exploration_runner.")

        prev_policy = _set_planner_tool_policy(self.planner, PLAN_MODE_TOOL_POLICY)
        obs = state.metadata.get("obs")
        lf = state.metadata.get("langfuse_trace")
        trace_emitter = TraceEmitter()
        trace_emitter.reset()
        set_active_trace_emitter(trace_emitter)
        try:
            mem = _planner_session_memory_from_state(state)
            mem.record_user_turn(state.instruction)
            exploration = self.exploration_runner.run(state.instruction, obs=obs, langfuse_trace=lf)
            state.exploration_result = exploration
            state.context["exploration_summary_text"] = exploration.exploration_summary.overall
            state.context["exploration_result"] = exploration.model_dump(mode="json")
            _sync_session_after_exploration(mem, exploration)
            maybe_synthesize_to_state(state, exploration, lf)

            pctx = exploration_to_planner_context(exploration, session=mem, state=state)
            plan = call_planner_with_context(
                self.planner,
                state.instruction,
                pctx,
                deep=False,
                obs=obs,
                langfuse_trace=lf,
                require_controller_json=False,
                session=mem,
            )
            if not isinstance(plan, PlanDocument):
                raise TypeError(
                    f"Planner must return PlanDocument for plan_legacy mode, got {type(plan).__name__}"
                )
            _record_session_after_plan(mem, plan)
            _attach_plan_view(state, plan)
            _attach_plan_only_trace(state, plan, trace_emitter)
        finally:
            clear_active_trace_emitter()
            _restore_planner_tool_policy(self.planner, prev_policy)
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
        session_memory: SessionMemory,
        validation_task_mode: Optional[str] = None,
    ) -> tuple[PlanDocument, Any]:
        cfg = get_config().planner_loop
        mem = session_memory
        md = state.metadata
        if not isinstance(md, dict):
            state.metadata = {}
            md = state.metadata
        md["planner_controller_calls"] = 0
        md["sub_explorations_used"] = 0

        def _budget_planner() -> bool:
            if md["planner_controller_calls"] >= cfg.max_planner_controller_calls:
                return False
            md["planner_controller_calls"] = md["planner_controller_calls"] + 1
            return True

        def _merge(new_plan: PlanDocument, old: PlanDocument) -> PlanDocument:
            merged = merge_preserved_completed_steps(old, new_plan)
            validate_completed_steps_immutable(old, merged)
            return merged

        if not _budget_planner():
            raise RuntimeError(
                "planner_controller_calls budget misconfigured (max_planner_controller_calls < 1)"
            )
        pctx0 = exploration_to_planner_context(exploration, session=mem, state=state)
        plan_doc = call_planner_with_context(
            self.planner,
            state.instruction,
            pctx0,
            deep=deep,
            obs=obs,
            langfuse_trace=langfuse_trace,
            require_controller_json=True,
            session=mem,
            validation_task_mode=validation_task_mode,
        )
        if not isinstance(plan_doc, PlanDocument):
            raise TypeError(
                f"Planner must return PlanDocument for ACT controller path, got {type(plan_doc).__name__}"
            )
        _record_session_after_plan(mem, plan_doc)
        _attach_plan_view(state, plan_doc)
        _sync_tool_policy_mode_to_state(state, self.planner)

        def _exit_budget_exhausted() -> tuple[PlanDocument, Any]:
            md["planner_loop_abort"] = "planner_controller_budget_exhausted"
            self.plan_executor.trace_emitter = trace_emitter
            return plan_doc, self.plan_executor._finalize_run(state, plan_doc, "failed")

        while True:
            decision = planner_decision_from_plan_document(plan_doc)

            if decision.type == "explore":
                if md["sub_explorations_used"] >= cfg.max_sub_explorations_per_task:
                    md["explore_gate"] = "sub_exploration_budget"
                    if not _budget_planner():
                        return _exit_budget_exhausted()
                    np = call_planner_with_context(
                        self.planner,
                        state.instruction,
                        _planner_context_for_replan(
                            _insufficiency_replan_context(plan_doc, state.instruction, state),
                            mem,
                        ),
                        deep=True,
                        obs=obs,
                        langfuse_trace=langfuse_trace,
                        require_controller_json=True,
                        session=mem,
                        validation_task_mode=validation_task_mode,
                    )
                    if not isinstance(np, PlanDocument):
                        raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                    plan_doc = _merge(np, plan_doc)
                    _record_session_after_plan(mem, plan_doc)
                    _attach_plan_view(state, plan_doc)
                    continue
                if not _sub_exploration_gates_ok(exploration):
                    md["explore_gate"] = "signals"
                    if not _budget_planner():
                        return _exit_budget_exhausted()
                    np = call_planner_with_context(
                        self.planner,
                        state.instruction,
                        _planner_context_for_replan(
                            _insufficiency_replan_context(plan_doc, state.instruction, state),
                            mem,
                        ),
                        deep=True,
                        obs=obs,
                        langfuse_trace=langfuse_trace,
                        require_controller_json=True,
                        session=mem,
                        validation_task_mode=validation_task_mode,
                    )
                    if not isinstance(np, PlanDocument):
                        raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                    plan_doc = _merge(np, plan_doc)
                    _record_session_after_plan(mem, plan_doc)
                    _attach_plan_view(state, plan_doc)
                    continue
                query = (decision.query or "").strip()
                old_pd = plan_doc
                exploration = self.exploration_runner.run(
                    query, obs=obs, langfuse_trace=langfuse_trace
                )
                state.exploration_result = exploration
                state.context["exploration_summary_text"] = exploration.exploration_summary.overall
                state.context["exploration_result"] = exploration.model_dump(mode="json")
                _sync_session_after_exploration(mem, exploration)
                maybe_synthesize_to_state(state, exploration, langfuse_trace)
                md["sub_explorations_used"] = md["sub_explorations_used"] + 1
                ps = plan_state_from_plan_document(old_pd)
                if not _budget_planner():
                    return _exit_budget_exhausted()
                pctx_sub = exploration_to_planner_context(exploration, session=mem, state=state)
                np = call_planner_with_context(
                    self.planner,
                    state.instruction,
                    pctx_sub,
                    deep=deep,
                    obs=obs,
                    langfuse_trace=langfuse_trace,
                    plan_state=ps,
                    prior_plan_document=old_pd,
                    require_controller_json=True,
                    session=mem,
                    validation_task_mode=validation_task_mode,
                )
                if not isinstance(np, PlanDocument):
                    raise TypeError(
                        f"Planner must return PlanDocument, got {type(np).__name__}"
                    )
                plan_doc = _merge(np, old_pd)
                _record_session_after_plan(mem, plan_doc)
                _attach_plan_view(state, plan_doc)
                continue

            if decision.type == "replan":
                if not _budget_planner():
                    return _exit_budget_exhausted()
                np = call_planner_with_context(
                    self.planner,
                    state.instruction,
                    _planner_context_for_replan(
                        _insufficiency_replan_context(plan_doc, state.instruction, state),
                        mem,
                    ),
                    deep=True,
                    obs=obs,
                    langfuse_trace=langfuse_trace,
                    require_controller_json=True,
                    session=mem,
                    validation_task_mode=validation_task_mode,
                )
                if not isinstance(np, PlanDocument):
                    raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                plan_doc = _merge(np, plan_doc)
                _record_session_after_plan(mem, plan_doc)
                _attach_plan_view(state, plan_doc)
                continue

            if decision.type == "stop":
                return plan_doc, {"status": "success", "state": state, "stopped_by": "planner_decision"}

            # act (PlannerDecision type act; controller "continue")
            out = self.plan_executor.run_one_step(plan_doc, state, trace_emitter=trace_emitter)
            st = out.get("status")
            if st == "success":
                _record_session_after_executor_step(mem, plan_doc)
                return plan_doc, out
            if st == "failed_step":
                failed_step = out["failed_step"]
                result = out["result"]
                ctx = _failure_replan_context_from_step(
                    plan_doc, state.instruction, failed_step, result, state
                )
                if not _budget_planner():
                    return _exit_budget_exhausted()
                np = call_planner_with_context(
                    self.planner,
                    state.instruction,
                    _planner_context_for_replan(ctx, mem),
                    deep=True,
                    obs=obs,
                    langfuse_trace=langfuse_trace,
                    require_controller_json=True,
                    session=mem,
                    validation_task_mode=validation_task_mode,
                )
                if not isinstance(np, PlanDocument):
                    raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                plan_doc = _merge(np, plan_doc)
                _record_session_after_plan(mem, plan_doc)
                _attach_plan_view(state, plan_doc)
                continue
            if st == "progress":
                old_pd = plan_doc
                last_summary = ""
                for s in plan_doc.steps:
                    if s.execution.status == "completed" and s.execution.last_result is not None:
                        last_summary = str(s.execution.last_result.output_summary or "")
                ps = plan_state_from_plan_document(plan_doc, last_result_summary=last_summary)
                if not _budget_planner():
                    return _exit_budget_exhausted()
                pctx_pr = exploration_to_planner_context(exploration, session=mem, state=state)
                np = call_planner_with_context(
                    self.planner,
                    state.instruction,
                    pctx_pr,
                    deep=deep,
                    obs=obs,
                    langfuse_trace=langfuse_trace,
                    plan_state=ps,
                    prior_plan_document=old_pd,
                    require_controller_json=True,
                    session=mem,
                    validation_task_mode=validation_task_mode,
                )
                if not isinstance(np, PlanDocument):
                    raise TypeError(
                        f"Planner must return PlanDocument, got {type(np).__name__}"
                    )
                plan_doc = _merge(np, old_pd)
                _record_session_after_executor_step(mem, plan_doc)
                _record_session_after_plan(mem, plan_doc)
                _attach_plan_view(state, plan_doc)
                continue

            return plan_doc, out
