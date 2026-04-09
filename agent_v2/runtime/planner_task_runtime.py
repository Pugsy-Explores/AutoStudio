"""
PlannerTaskRuntime — orchestration for exploration → plan → execute (Anthropic-style outer loop).

ModeManager delegates here; control flow branches on PlannerDecision only (see planner_decision_mapper).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

_LOG = logging.getLogger(__name__)

from agent_v2.config import get_config
from agent_v2.exploration.answer_synthesizer import maybe_synthesize_to_state
from agent_v2.memory.conversation_memory import (
    get_or_create_in_memory_store,
    get_session_id_from_state,
)
from agent_v2.memory.task_working_memory import (
    TASK_WORKING_MEMORY_VERSION,
    CompletedStepRecord,
    reset_task_working_memory,
    task_working_memory_from_state,
)
from agent_v2.planning.decision_snapshot import (
    build_planner_decision_snapshot,
    plan_document_fingerprint,
)
from agent_v2.planning.exploration_outcome_policy import (
    should_stop_after_exploration,
    sub_exploration_allowed,
)
from agent_v2.planning.planner_action_mapper import (
    exploration_query_hash,
    is_duplicate_explore_proposal,
)
from agent_v2.planning.planner_v2_invocation import (
    plan_document_valid_for_v2_gate,
    should_call_planner_v2,
)
from agent_v2.planning.task_planner import default_task_planner_service
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
from agent_v2.schemas.answer_synthesis import AnswerSynthesisResult
from agent_v2.schemas.answer_validation import AnswerValidationResult
from agent_v2.schemas.planner_plan_context import PlannerPlanContext
from agent_v2.validation.answer_validator import validate_answer
from agent_v2.runtime.planner_decision_mapper import planner_decision_from_plan_document
from agent_v2.runtime.replanner import merge_preserved_completed_steps, validate_completed_steps_immutable
from agent_v2.runtime.trace_context import clear_active_trace_emitter, set_active_trace_emitter
from agent_v2.runtime.trace_emitter import TraceEmitter
from agent_v2.schemas.execution import ErrorType
from agent_v2.schemas.final_exploration import FinalExplorationSchema
from agent_v2.schemas.plan import PlanDocument
from agent_v2.schemas.planner_action import PlannerDecisionSnapshot
from agent_v2.schemas.planner_decision import PlannerDecision
from agent_v2.schemas.plan_state import plan_state_from_plan_document
from agent_v2.schemas.replan import (
    ReplanCompletedStep,
    ReplanContext,
    ReplanFailureContext,
    ReplanFailureError,
)


def _snapshot_hash(snap: PlannerDecisionSnapshot) -> str:
    payload = snap.model_dump(mode="json", exclude_none=True)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def _decision_mismatch(a: PlannerDecision, b: PlannerDecision) -> bool:
    aq = (a.query or "").strip()
    bq = (b.query or "").strip()
    return a.type != b.type or aq != bq


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


def build_explore_query_after_validation_failure(
    vf: Optional[AnswerValidationResult],
    instruction: str,
    *,
    max_chars: int = 2000,
) -> str:
    """
    Sub-exploration query when synthesize is coerced to explore after failed validation.
    Public for unit tests.
    """
    parts: list[str] = []
    if vf is not None:
        parts.extend(str(x).strip() for x in (vf.missing_context or []) if str(x).strip())
        for issue in vf.issues:
            s = str(issue).strip()
            if s and s not in parts:
                parts.append(s)
    joined = " | ".join(parts) if parts else ""
    if len(joined) > max_chars:
        joined = joined[: max_chars - 1] + "…"
    if joined.strip():
        return joined
    hint = (instruction or "").strip()
    if len(hint) > max_chars:
        hint = hint[: max_chars - 1] + "…"
    return hint or "retrieve more evidence for task"


def _validation_feedback_from_state(state: Any) -> Optional[AnswerValidationResult]:
    ctx = getattr(state, "context", None)
    if not isinstance(ctx, dict):
        return None
    raw = ctx.get("validation_feedback")
    if raw is None:
        return None
    if isinstance(raw, AnswerValidationResult):
        return raw
    if isinstance(raw, dict):
        try:
            return AnswerValidationResult.model_validate(raw)
        except Exception:
            return None
    return None


def _exploration_to_planner_ctx_with_validation(
    state: Any,
    exploration: FinalExplorationSchema,
    mem: SessionMemory,
) -> PlannerPlanContext:
    vf = _validation_feedback_from_state(state)
    return exploration_to_planner_context(
        exploration, session=mem, state=state, validation_feedback=vf
    )


def _planner_context_for_replan(
    ctx: ReplanContext,
    mem: SessionMemory,
    *,
    validation_feedback: Optional[AnswerValidationResult] = None,
) -> PlannerPlanContext:
    qi = ctx.query_intent
    return PlannerPlanContext(
        replan=ctx,
        session=mem,
        query_intent=qi,
        exploration_budget=effective_exploration_budget(qi),
        validation_feedback=validation_feedback,
    )


def _record_session_after_plan(mem: SessionMemory, plan: PlanDocument) -> None:
    eng = plan.engine
    if eng is None:
        return
    mem.record_planner_output(
        decision=str(eng.decision),
        tool=str(getattr(eng, "tool", "none") or "none"),
    )


def _record_session_after_executor_step(
    mem: SessionMemory, plan: PlanDocument, state: Any, plan_executor: Any
) -> None:
    eng = plan.engine
    tool = str(getattr(eng, "tool", "") or "")
    summ = ""
    active: str | None = None
    tasks: dict = {}
    if plan_executor is not None and hasattr(plan_executor, "get_tasks_by_id"):
        tasks = plan_executor.get_tasks_by_id()

    for s in plan.steps:
        t = tasks.get(s.step_id)
        if t is None or getattr(t, "status", None) != "completed":
            continue
        if s.action == "finish":
            continue
        lr = getattr(t, "last_result", None)
        xs = ""
        if lr is not None and getattr(lr, "output", None) is not None:
            xs = str(lr.output.summary or "").strip()[:120]
        if xs:
            summ = xs
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


def _sub_exploration_allowed(state: Any, exploration: FinalExplorationSchema) -> bool:
    """Legacy gate + optional stop policy (full-planner-arch-freeze-impl)."""
    cfg = get_config()
    wm = task_working_memory_from_state(state)
    return sub_exploration_allowed(exploration, wm, cfg=cfg)


def _explore_block_details_from_exploration(exploration: FinalExplorationSchema) -> dict[str, Any]:
    gaps = exploration.exploration_summary.knowledge_gaps or []
    gn = len([g for g in gaps if str(g).strip()])
    return {
        "gaps_count": gn,
        "confidence": str(exploration.confidence) if exploration.confidence else None,
    }


def _set_explore_blocked(md: dict, exploration: FinalExplorationSchema, reason: str) -> None:
    """State transition: sub-explore was not run; planner must see outcome + details next tick."""
    md["task_planner_last_loop_outcome"] = f"explore_blocked:{reason}"
    md["explore_block_details"] = _explore_block_details_from_exploration(exploration)
    md["explore_gate"] = reason


def _record_task_memory_after_exploration(
    state: Any,
    exploration: FinalExplorationSchema,
    explore_query: str,
) -> None:
    from agent_v2.planning.exploration_outcome_policy import normalize_understanding

    wm = task_working_memory_from_state(state)
    nu = normalize_understanding(exploration)
    gaps = exploration.exploration_summary.knowledge_gaps or []
    gaps_nonempty = any(str(g).strip() for g in gaps)
    qh = exploration_query_hash(explore_query)
    wm.record_exploration_tick(
        exploration_id=str(exploration.exploration_id),
        query_hash=qh,
        confidence=str(exploration.confidence) if exploration.confidence else None,
        gaps_nonempty=gaps_nonempty,
        understanding=nu,
    )
    stop, reason = should_stop_after_exploration(
        exploration, wm, chat=get_config().chat_planning
    )
    md = getattr(state, "metadata", None)
    if isinstance(md, dict) and stop and reason:
        md["stop_reason"] = reason


def _maybe_thin_planner_observability(
    state: Any,
    exploration: FinalExplorationSchema,
    task_planner: Any,
) -> None:
    """When enabled, record thin planner proposal on metadata (no control transfer)."""
    if not get_config().chat_planning.enable_thin_task_planner:
        return
    md = getattr(state, "metadata", None)
    if not isinstance(md, dict):
        return
    store = get_or_create_in_memory_store(state)
    sid = get_session_id_from_state(state)
    rolling = store.load(sid).rolling_summary
    snap = build_planner_decision_snapshot(
        state, exploration, rolling_conversation_summary=rolling
    )
    decision = task_planner.decide(snap)
    md["thin_planner_decision"] = decision.model_dump(mode="json")
    md["thin_planner_action"] = md["thin_planner_decision"]


def _sync_chat_planning_metadata(state: Any) -> None:
    md = getattr(state, "metadata", None)
    if not isinstance(md, dict):
        return
    md["task_working_memory_version"] = TASK_WORKING_MEMORY_VERSION
    store = get_or_create_in_memory_store(state)
    sid = get_session_id_from_state(state)
    n = len(store.load(sid).turns)
    md["conversation_memory_turns"] = n


def _conversation_append_assistant_summary(state: Any) -> None:
    store = get_or_create_in_memory_store(state)
    sid = get_session_id_from_state(state)
    ctx = getattr(state, "context", None)
    fa = ""
    if isinstance(ctx, dict):
        fa = str(ctx.get("final_answer") or "").strip()
    summ = fa[:2000] if fa else str(getattr(state, "instruction", ""))[:200]
    store.append_turn(sid, "assistant", summ)
    store.set_last_final_answer_summary(sid, summ)


def _failure_replan_context_from_step(
    plan: PlanDocument,
    instruction: str,
    failed_task: Any,
    result: Any,
    state: Any,
    *,
    tasks_by_id: dict,
) -> ReplanContext:
    from agent_v2.runtime.replanner import completed_steps_for_replan_from_tasks

    completed = [
        x
        for x in completed_steps_for_replan_from_tasks(plan, tasks_by_id)
        if x.step_id != failed_task.id
    ]
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
    if result.output is not None:
        lr_summary = str(result.output.summary or "")
    fc = ReplanFailureContext(
        step_id=failed_task.id,
        error=ReplanFailureError(type=err_type, message=msg or lr_summary or "step_failed"),
        attempts=int(getattr(failed_task, "attempts", 0) or 0),
        last_output_summary=lr_summary,
    )
    return ReplanContext(
        failure_context=fc,
        completed_steps=completed,
        exploration_summary=None,
        trigger="failure",
        query_intent=read_query_intent_from_agent_state(state),
    )


def _insufficiency_replan_context(
    plan: PlanDocument, instruction: str, state: Any, plan_executor: Any | None = None
) -> ReplanContext:
    from agent_v2.runtime.replanner import completed_steps_for_replan_from_tasks

    tb: dict = {}
    if plan_executor is not None and hasattr(plan_executor, "get_tasks_by_id"):
        tb = plan_executor.get_tasks_by_id()
    completed = list(completed_steps_for_replan_from_tasks(plan, tb))
    sid = plan.steps[-1].step_id if plan.steps else "s1"
    msg = "Insufficient evidence for next decision (controller replan)"
    md = getattr(state, "metadata", None)
    tc_lo: str | None = None
    ebd: dict[str, Any] | None = None
    if isinstance(md, dict):
        raw_tc = md.get("task_planner_last_loop_outcome")
        if raw_tc is not None and str(raw_tc).strip():
            tc_lo = str(raw_tc).strip()
        raw_ebd = md.get("explore_block_details")
        if isinstance(raw_ebd, dict):
            ebd = dict(raw_ebd)
        if tc_lo and tc_lo.startswith("explore_blocked:"):
            msg = (
                f"{msg} Task control: {tc_lo}. "
                "Sub-exploration was not run; do not re-issue the same explore until gate clears."
            )
        elif tc_lo == "replan_no_progress":
            msg = (
                f"{msg} Task control: replan_no_progress — merged plan unchanged; "
                "choose synthesize, stop, or a materially different plan."
            )
    fc = ReplanFailureContext(
        step_id=sid,
        error=ReplanFailureError(
            type=ErrorType.unknown,
            message=msg,
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
        task_control_last_outcome=tc_lo,
        explore_block_details=ebd,
    )


class PlannerTaskRuntime:
    """Owns exploration → plan → executor loops; ModeManager is a thin entrypoint."""

    def __init__(
        self,
        exploration_runner: Any,
        planner: Any,
        plan_executor: Any,
        *,
        task_planner: Any = None,
    ) -> None:
        self.exploration_runner = exploration_runner
        self.planner = planner
        self.plan_executor = plan_executor
        self._task_planner = (
            task_planner if task_planner is not None else default_task_planner_service()
        )

    def run_explore_plan_execute(self, state: Any, *, deep: bool) -> Any:
        if self.plan_executor is None:
            raise ValueError(
                "ACT and plan_execute require DagExecutor; pass plan_argument_generator to AgentRuntime."
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
            reset_task_working_memory(state)
            mem = _planner_session_memory_from_state(state)
            mem.record_user_turn(state.instruction)
            _st = get_or_create_in_memory_store(state)
            _sid = get_session_id_from_state(state)
            _st.append_turn(_sid, "user", str(state.instruction)[:2000])

            exploration = self.exploration_runner.run(state.instruction, obs=obs, langfuse_trace=lf)
            state.exploration_result = exploration
            state.context["exploration_summary_text"] = exploration.exploration_summary.overall
            state.context["exploration_result"] = exploration.model_dump(mode="json")
            _sync_session_after_exploration(mem, exploration)
            if isinstance(exploration, FinalExplorationSchema):
                _record_task_memory_after_exploration(state, exploration, str(state.instruction))
            maybe_synthesize_to_state(state, exploration, lf)
            if isinstance(exploration, FinalExplorationSchema):
                _maybe_thin_planner_observability(state, exploration, self._task_planner)
            _sync_chat_planning_metadata(state)

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
                pctx = _exploration_to_planner_ctx_with_validation(state, exploration, mem)
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
                _record_session_after_executor_step(mem, plan_doc, state, self.plan_executor)
        finally:
            clear_active_trace_emitter()
            _restore_planner_tool_policy(self.planner, prev_policy)
            md_fin = getattr(state, "metadata", None)
            if isinstance(md_fin, dict):
                md_fin.pop("plan_validation_task_mode", None)

        _conversation_append_assistant_summary(state)
        _sync_chat_planning_metadata(state)

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
        with PLAN_MODE_TOOL_POLICY, DagExecutor for safe tools only, ACT-style trace.

        Does not set react_mode (AgentRuntime sets plan_safe_execute for this path).
        """
        if self.plan_executor is None:
            raise ValueError(
                "plan mode (safe loop) requires DagExecutor; pass plan_argument_generator to AgentRuntime."
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

            reset_task_working_memory(state)
            mem = _planner_session_memory_from_state(state)
            mem.record_user_turn(state.instruction)
            _st = get_or_create_in_memory_store(state)
            _sid = get_session_id_from_state(state)
            _st.append_turn(_sid, "user", str(state.instruction)[:2000])

            exploration = self.exploration_runner.run(state.instruction, obs=obs, langfuse_trace=lf)
            state.exploration_result = exploration
            state.context["exploration_summary_text"] = exploration.exploration_summary.overall
            state.context["exploration_result"] = exploration.model_dump(mode="json")
            _sync_session_after_exploration(mem, exploration)
            if isinstance(exploration, FinalExplorationSchema):
                _record_task_memory_after_exploration(state, exploration, str(state.instruction))
            maybe_synthesize_to_state(state, exploration, lf)
            if isinstance(exploration, FinalExplorationSchema):
                _maybe_thin_planner_observability(state, exploration, self._task_planner)
            _sync_chat_planning_metadata(state)

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
                pctx = _exploration_to_planner_ctx_with_validation(state, exploration, mem)
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
                _record_session_after_executor_step(mem, plan_doc, state, self.plan_executor)
        finally:
            clear_active_trace_emitter()
            _restore_planner_tool_policy(self.planner, prev_policy)
            md_fin = getattr(state, "metadata", None)
            if isinstance(md_fin, dict):
                md_fin.pop("plan_validation_task_mode", None)

        _conversation_append_assistant_summary(state)
        _sync_chat_planning_metadata(state)

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
            reset_task_working_memory(state)
            mem = _planner_session_memory_from_state(state)
            mem.record_user_turn(state.instruction)
            _st = get_or_create_in_memory_store(state)
            _sid = get_session_id_from_state(state)
            _st.append_turn(_sid, "user", str(state.instruction)[:2000])

            exploration = self.exploration_runner.run(state.instruction, obs=obs, langfuse_trace=lf)
            state.exploration_result = exploration
            state.context["exploration_summary_text"] = exploration.exploration_summary.overall
            state.context["exploration_result"] = exploration.model_dump(mode="json")
            _sync_session_after_exploration(mem, exploration)
            if isinstance(exploration, FinalExplorationSchema):
                _record_task_memory_after_exploration(state, exploration, str(state.instruction))
            maybe_synthesize_to_state(state, exploration, lf)
            if isinstance(exploration, FinalExplorationSchema):
                _maybe_thin_planner_observability(state, exploration, self._task_planner)
            _sync_chat_planning_metadata(state)

            pctx = _exploration_to_planner_ctx_with_validation(state, exploration, mem)
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
        _conversation_append_assistant_summary(state)
        _sync_chat_planning_metadata(state)
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
        md["act_controller_iteration_count"] = 0
        md["post_validation_synthesize_blocked"] = False
        md["answer_validation_rounds"] = 0

        def _budget_planner() -> bool:
            if md["planner_controller_calls"] >= cfg.max_planner_controller_calls:
                return False
            md["planner_controller_calls"] = md["planner_controller_calls"] + 1
            return True

        def _merge(new_plan: PlanDocument, old: PlanDocument) -> PlanDocument:
            cids: set[str] = set()
            if self.plan_executor is not None and hasattr(
                self.plan_executor, "get_completed_step_ids"
            ):
                cids = self.plan_executor.get_completed_step_ids()
            merged = merge_preserved_completed_steps(old, new_plan, completed_step_ids=cids)
            validate_completed_steps_immutable(old, merged, completed_step_ids=cids)
            return merged

        authoritative = cfg.task_planner_authoritative_loop
        shadow = cfg.task_planner_shadow_loop and not authoritative
        plan_body_only = authoritative and cfg.planner_plan_body_only_when_task_planner_authoritative

        def _pcw(
            ctx: PlannerPlanContext,
            *,
            deep_kw: bool,
            require_controller_json: bool = True,
            plan_state=None,
            prior_plan_document=None,
        ) -> Any:
            return call_planner_with_context(
                self.planner,
                state.instruction,
                ctx,
                deep=deep_kw,
                obs=obs,
                langfuse_trace=langfuse_trace,
                plan_state=plan_state,
                prior_plan_document=prior_plan_document,
                require_controller_json=require_controller_json,
                session=mem,
                validation_task_mode=validation_task_mode,
                plan_body_only=plan_body_only,
            )

        def _rolling_store_summary() -> str:
            st = get_or_create_in_memory_store(state)
            sid = get_session_id_from_state(state)
            return st.load(sid).rolling_summary

        def _resolve_decision(plan_doc: PlanDocument) -> PlannerDecision:
            snap = build_planner_decision_snapshot(
                state,
                exploration,
                rolling_conversation_summary=_rolling_store_summary(),
                plan_doc=plan_doc,
            )
            if shadow:
                tp = self._task_planner.decide(snap)
                eng = planner_decision_from_plan_document(plan_doc, state=state)
                md["decision_source"] = "shadow"
                md["task_planner_decision"] = tp.model_dump(mode="json")
                md["engine_decision"] = eng.model_dump(mode="json")
                md["task_planner_shadow_mismatch"] = _decision_mismatch(tp, eng)
                md["decision_snapshot_hash"] = _snapshot_hash(snap)
                return eng
            if authoritative:
                d = self._task_planner.decide(snap)
                md["decision_source"] = "task_planner"
                md["task_planner_decision"] = d.model_dump(mode="json")
                md["decision_snapshot_hash"] = _snapshot_hash(snap)
                return d
            return planner_decision_from_plan_document(plan_doc, state=state)

        if not _budget_planner():
            raise RuntimeError(
                "planner_controller_calls budget misconfigured (max_planner_controller_calls < 1)"
            )
        pctx0 = _exploration_to_planner_ctx_with_validation(state, exploration, mem)
        plan_doc = _pcw(
            pctx0,
            deep_kw=deep,
            require_controller_json=True,
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
            return plan_doc, self.plan_executor.finalize_run(state, plan_doc, "failed")

        while True:
            md["act_controller_iteration_count"] = int(md.get("act_controller_iteration_count", 0)) + 1
            md.pop("decision_coerced_from_synthesize", None)
            decision = _resolve_decision(plan_doc)

            if md.get("post_validation_synthesize_blocked") and decision.type == "synthesize":
                vf_blk = _validation_feedback_from_state(state)
                q_coerce = build_explore_query_after_validation_failure(
                    vf_blk, str(getattr(state, "instruction", "") or "")
                )
                decision = PlannerDecision(
                    type="explore", step=None, query=q_coerce, tool="explore"
                )
                md["decision_coerced_from_synthesize"] = True

            if decision.type == "synthesize":
                maybe_synthesize_to_state(state, exploration, langfuse_trace)
                twm = task_working_memory_from_state(state)
                twm.record_completed(
                    CompletedStepRecord(kind="synthesize", summary="answer_synthesis")
                )
                loop_cfg = get_config().planner_loop
                sctx = getattr(state, "context", None)
                if loop_cfg.enable_answer_validation and isinstance(sctx, dict):
                    rounds = int(md.get("answer_validation_rounds") or 0)
                    if rounds >= loop_cfg.max_answer_validation_rounds_per_task:
                        md["post_validation_synthesize_blocked"] = False
                        md["answer_validation_bypass_max_rounds"] = True
                        _LOG.warning(
                            "answer_validation bypassed after %s rounds (max=%s)",
                            rounds,
                            loop_cfg.max_answer_validation_rounds_per_task,
                        )
                        md["task_planner_last_loop_outcome"] = "synthesize_completed"
                    elif "answer_synthesis" in sctx:
                        try:
                            syn = AnswerSynthesisResult.model_validate(sctx["answer_synthesis"])
                        except Exception:
                            syn = AnswerSynthesisResult(
                                synthesis_success=False,
                                error="invalid_answer_synthesis_payload",
                            )
                        v = validate_answer(
                            instruction=str(getattr(state, "instruction", "") or ""),
                            exploration=exploration,
                            synthesis=syn,
                            langfuse_parent=langfuse_trace,
                        )
                        rounds += 1
                        md["answer_validation_rounds"] = rounds
                        sctx["answer_validation"] = v.model_dump(mode="json")
                        sctx["validation_feedback"] = v.model_dump(mode="json")
                        if v.is_complete:
                            md["post_validation_synthesize_blocked"] = False
                            md["task_planner_last_loop_outcome"] = "validation_complete"
                        else:
                            md["post_validation_synthesize_blocked"] = True
                            md["task_planner_last_loop_outcome"] = "validation_incomplete"
                    else:
                        md["task_planner_last_loop_outcome"] = "synthesize_completed"
                else:
                    md["task_planner_last_loop_outcome"] = "synthesize_completed"
                continue

            if decision.type == "plan":
                if not _budget_planner():
                    return _exit_budget_exhausted()
                if authoritative:
                    assert should_call_planner_v2(
                        context="task_decision",
                        decision=decision,
                        plan_valid=plan_document_valid_for_v2_gate(plan_doc),
                    )
                np = _pcw(
                    _exploration_to_planner_ctx_with_validation(state, exploration, mem),
                    deep_kw=deep,
                    require_controller_json=True,
                )
                if not isinstance(np, PlanDocument):
                    raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                plan_doc = _merge(np, plan_doc)
                twm = task_working_memory_from_state(state)
                twm.record_completed(
                    CompletedStepRecord(kind="plan_refresh", summary="planner_refresh")
                )
                _record_session_after_plan(mem, plan_doc)
                _attach_plan_view(state, plan_doc)
                continue

            if decision.type == "explore":
                if md["sub_explorations_used"] >= cfg.max_sub_explorations_per_task:
                    _set_explore_blocked(md, exploration, "sub_exploration_budget")
                    if authoritative:
                        continue
                    if not _budget_planner():
                        return _exit_budget_exhausted()
                    assert should_call_planner_v2(context="failure_or_insufficiency_replan")
                    np = _pcw(
                        _planner_context_for_replan(
                            _insufficiency_replan_context(plan_doc, state.instruction, state, self.plan_executor),
                            mem,
                            validation_feedback=_validation_feedback_from_state(state),
                        ),
                        deep_kw=True,
                        require_controller_json=True,
                    )
                    if not isinstance(np, PlanDocument):
                        raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                    plan_doc = _merge(np, plan_doc)
                    _record_session_after_plan(mem, plan_doc)
                    _attach_plan_view(state, plan_doc)
                    continue
                if not _sub_exploration_allowed(state, exploration):
                    _set_explore_blocked(md, exploration, "signals")
                    if authoritative:
                        continue
                    if not _budget_planner():
                        return _exit_budget_exhausted()
                    assert should_call_planner_v2(context="failure_or_insufficiency_replan")
                    np = _pcw(
                        _planner_context_for_replan(
                            _insufficiency_replan_context(plan_doc, state.instruction, state, self.plan_executor),
                            mem,
                            validation_feedback=_validation_feedback_from_state(state),
                        ),
                        deep_kw=True,
                        require_controller_json=True,
                    )
                    if not isinstance(np, PlanDocument):
                        raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                    plan_doc = _merge(np, plan_doc)
                    _record_session_after_plan(mem, plan_doc)
                    _attach_plan_view(state, plan_doc)
                    continue
                query = (decision.query or "").strip()
                twm_pre = task_working_memory_from_state(state)
                if query and is_duplicate_explore_proposal(
                    twm_pre.last_exploration_query_hash, query
                ):
                    _set_explore_blocked(md, exploration, "duplicate_query")
                    if authoritative:
                        continue
                    if not _budget_planner():
                        return _exit_budget_exhausted()
                    assert should_call_planner_v2(context="failure_or_insufficiency_replan")
                    np = _pcw(
                        _planner_context_for_replan(
                            _insufficiency_replan_context(plan_doc, state.instruction, state, self.plan_executor),
                            mem,
                            validation_feedback=_validation_feedback_from_state(state),
                        ),
                        deep_kw=True,
                        require_controller_json=True,
                    )
                    if not isinstance(np, PlanDocument):
                        raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                    plan_doc = _merge(np, plan_doc)
                    _record_session_after_plan(mem, plan_doc)
                    _attach_plan_view(state, plan_doc)
                    continue
                old_pd = plan_doc
                md["post_validation_synthesize_blocked"] = False
                exploration = self.exploration_runner.run(
                    query, obs=obs, langfuse_trace=langfuse_trace
                )
                state.exploration_result = exploration
                state.context["exploration_summary_text"] = exploration.exploration_summary.overall
                state.context["exploration_result"] = exploration.model_dump(mode="json")
                _sync_session_after_exploration(mem, exploration)
                _record_task_memory_after_exploration(state, exploration, query)
                maybe_synthesize_to_state(state, exploration, langfuse_trace)
                md["sub_explorations_used"] = md["sub_explorations_used"] + 1
                ps = plan_state_from_plan_document(old_pd, state=state)
                if not _budget_planner():
                    return _exit_budget_exhausted()
                pctx_sub = _exploration_to_planner_ctx_with_validation(state, exploration, mem)
                assert should_call_planner_v2(context="post_exploration_merge")
                np = _pcw(
                    pctx_sub,
                    deep_kw=deep,
                    require_controller_json=True,
                    plan_state=ps,
                    prior_plan_document=old_pd,
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
                fp_before = plan_document_fingerprint(plan_doc)
                if authoritative:
                    assert should_call_planner_v2(
                        context="task_decision",
                        decision=decision,
                        plan_valid=plan_document_valid_for_v2_gate(plan_doc),
                    )
                np = _pcw(
                    _planner_context_for_replan(
                        _insufficiency_replan_context(plan_doc, state.instruction, state, self.plan_executor),
                        mem,
                        validation_feedback=_validation_feedback_from_state(state),
                    ),
                    deep_kw=True,
                    require_controller_json=True,
                )
                if not isinstance(np, PlanDocument):
                    raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                plan_doc = _merge(np, plan_doc)
                fp_after = plan_document_fingerprint(plan_doc)
                if fp_after == fp_before:
                    md["task_planner_last_loop_outcome"] = "replan_no_progress"
                    md.pop("explore_block_details", None)
                    md["replan_same_plan_streak"] = int(md.get("replan_same_plan_streak", 0) or 0) + 1
                else:
                    md["replan_same_plan_streak"] = 0
                _record_session_after_plan(mem, plan_doc)
                _attach_plan_view(state, plan_doc)
                continue

            if decision.type == "stop":
                return plan_doc, {"status": "success", "state": state, "stopped_by": "planner_decision"}

            # act (PlannerDecision type act; controller "continue")
            out = self.plan_executor.run_one_step(plan_doc, state, trace_emitter=trace_emitter)
            st = out.get("status")
            if st == "success":
                md["post_validation_synthesize_blocked"] = False
                _record_session_after_executor_step(mem, plan_doc, state, self.plan_executor)
                return plan_doc, out
            if st == "failed_step":
                failed_task = out["failed_task"]
                result = out["result"]
                ctx = _failure_replan_context_from_step(
                    plan_doc,
                    state.instruction,
                    failed_task,
                    result,
                    state,
                    tasks_by_id=self.plan_executor.get_tasks_by_id(),
                )
                if not _budget_planner():
                    return _exit_budget_exhausted()
                assert should_call_planner_v2(context="failure_or_insufficiency_replan")
                np = _pcw(
                    _planner_context_for_replan(
                        ctx,
                        mem,
                        validation_feedback=_validation_feedback_from_state(state),
                    ),
                    deep_kw=True,
                    require_controller_json=True,
                )
                if not isinstance(np, PlanDocument):
                    raise TypeError(f"Planner must return PlanDocument, got {type(np).__name__}")
                plan_doc = _merge(np, plan_doc)
                _record_session_after_plan(mem, plan_doc)
                _attach_plan_view(state, plan_doc)
                continue
            if st == "progress":
                md["post_validation_synthesize_blocked"] = False
                old_pd = plan_doc
                last_summary = ""
                if self.plan_executor is not None and hasattr(
                    self.plan_executor, "get_tasks_by_id"
                ):
                    tasks = self.plan_executor.get_tasks_by_id()
                    done = [t for t in tasks.values() if getattr(t, "status", None) == "completed"]
                    done.sort(key=lambda t: t.id, reverse=True)
                    if done and done[0].last_result is not None and done[0].last_result.output is not None:
                        last_summary = str(done[0].last_result.output.summary or "")
                ps = plan_state_from_plan_document(
                    plan_doc, last_result_summary=last_summary, state=state
                )
                if not _budget_planner():
                    return _exit_budget_exhausted()
                pctx_pr = _exploration_to_planner_ctx_with_validation(state, exploration, mem)
                assert should_call_planner_v2(context="progress_refresh")
                np = _pcw(
                    pctx_pr,
                    deep_kw=deep,
                    require_controller_json=True,
                    plan_state=ps,
                    prior_plan_document=old_pd,
                )
                if not isinstance(np, PlanDocument):
                    raise TypeError(
                        f"Planner must return PlanDocument, got {type(np).__name__}"
                    )
                plan_doc = _merge(np, old_pd)
                _record_session_after_executor_step(mem, plan_doc, state, self.plan_executor)
                _record_session_after_plan(mem, plan_doc)
                _attach_plan_view(state, plan_doc)
                continue

            return plan_doc, out
