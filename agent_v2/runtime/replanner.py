"""
Phase 7 — Replanner: failure → ReplanRequest → ReplanContext → Planner → ReplanResult → new PlanDocument.

Structured replan only; no silent in-place plan mutation. Validation via agent_v2.validation
(PlanValidator, ReplanResultValidator) per VALIDATION_REGISTRY.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from agent_v2.schemas.execution import ExecutionResult, ErrorType
from agent_v2.schemas.exploration import (
    effective_exploration_budget,
    read_query_intent_from_agent_state,
)
from agent_v2.schemas.plan import PlanDocument, PlanStep
from agent_v2.schemas.planner_plan_context import PlannerPlanContext
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.schemas.replan import (
    ReplanCompletedStep,
    ReplanConstraints,
    ReplanContext,
    ReplanExecutionContext,
    ReplanExplorationContext,
    ReplanExplorationSummary,
    ReplanFailureContext,
    ReplanFailureError,
    ReplanMetadata,
    ReplanNewPlan,
    ReplanOriginalPlan,
    ReplanPartialResult,
    ReplanRequest,
    ReplanResult,
    ReplanChanges,
    ReplanReasoning,
    ReplanValidation,
)
from agent_v2.validation.plan_validator import PlanValidationError, PlanValidator
from agent_v2.validation.replan_result_validator import ReplanResultValidator

_DEFAULT_POLICY = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)


def _dag_completed_id_set(state: Any) -> set[str]:
    ctx = getattr(state, "context", None)
    if not isinstance(ctx, dict):
        return set()
    raw = ctx.get("dag_completed_step_ids")
    if isinstance(raw, list):
        return {str(x) for x in raw}
    return set()


def _summary_from_dag_task_row(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    rt = row.get("runtime")
    if not isinstance(rt, dict):
        return ""
    lr = rt.get("last_result")
    if not isinstance(lr, dict):
        return ""
    out = lr.get("output")
    if isinstance(out, dict):
        return str(out.get("summary") or "")
    return ""


def completed_steps_for_replan(state: Any, plan: PlanDocument) -> list[ReplanCompletedStep]:
    """Completed step summaries from DAG runtime in state.context (not PlanStep)."""
    ctx = getattr(state, "context", None)
    raw_tasks: dict[str, Any] = {}
    if isinstance(ctx, dict) and isinstance(ctx.get("dag_graph_tasks"), dict):
        raw_tasks = ctx["dag_graph_tasks"]
    done = _dag_completed_id_set(state)
    out: list[ReplanCompletedStep] = []
    for s in sorted(plan.steps, key=lambda x: x.index):
        if s.step_id not in done:
            continue
        summ = _summary_from_dag_task_row(raw_tasks.get(s.step_id))
        out.append(ReplanCompletedStep(step_id=s.step_id, summary=summ))
    return out


def failure_attempts_from_dag(state: Any, step_id: str) -> int:
    ctx = getattr(state, "context", None)
    if not isinstance(ctx, dict):
        return 0
    raw_tasks = ctx.get("dag_graph_tasks")
    if not isinstance(raw_tasks, dict):
        return 0
    row = raw_tasks.get(step_id)
    if not isinstance(row, dict):
        return 0
    rt = row.get("runtime")
    if not isinstance(rt, dict):
        return 0
    try:
        return int(rt.get("attempts", 0))
    except (TypeError, ValueError):
        return 0


class Replanner:
    """
    Builds ReplanRequest from runtime state, maps to ReplanContext for PlannerInput,
    invokes planner (PlannerV2 / V2PlannerAdapter with PlannerPlanContext(replan=...)), returns ReplanResult.
    """

    def __init__(self, planner: Any, policy: Optional[ExecutionPolicy] = None):
        self.planner = planner
        self._policy = policy or _DEFAULT_POLICY

    def build_replan_request(
        self,
        state: Any,
        plan: PlanDocument,
        failed_step: PlanStep,
        last_result: ExecutionResult,
    ) -> ReplanRequest:
        md = getattr(state, "metadata", None)
        if not isinstance(md, dict):
            md = {}
        prev = int(md.get("replan_attempt", 0))
        next_attempt = prev + 1
        replan_id = f"replan_{next_attempt}"

        err_type = (
            last_result.error.type
            if last_result.error is not None
            else ErrorType.unknown
        )
        if isinstance(err_type, ErrorType):
            err_type_enum = err_type
        else:
            try:
                err_type_enum = ErrorType(str(err_type))
            except ValueError:
                err_type_enum = ErrorType.unknown

        msg_parts: list[str] = []
        if last_result.error is not None and (last_result.error.message or "").strip():
            msg_parts.append(last_result.error.message.strip())
        lr_summary = ""
        if last_result.output is not None:
            lr_summary = str(last_result.output.summary or "")
        if lr_summary and (not msg_parts or msg_parts[0] != lr_summary):
            msg_parts.append(lr_summary)
        message = " | ".join(msg_parts) if msg_parts else lr_summary or err_type_enum.value

        attempts = failure_attempts_from_dag(state, failed_step.step_id)

        failure_context = ReplanFailureContext(
            step_id=failed_step.step_id,
            error=ReplanFailureError(type=err_type_enum, message=message),
            attempts=attempts,
            last_output_summary=lr_summary,
        )

        completed = completed_steps_for_replan(state, plan)

        partial = [
            ReplanPartialResult(
                step_id=failed_step.step_id,
                result_summary=lr_summary,
            )
        ]
        execution_context = ReplanExecutionContext(
            completed_steps=completed,
            partial_results=partial,
        )

        exploration_context = _exploration_context_from_state(state)

        constraints = ReplanConstraints(
            max_steps=min(6, self._policy.max_steps),
            preserve_completed=True,
        )

        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        meta = ReplanMetadata(timestamp=ts, replan_attempt=next_attempt)

        return ReplanRequest(
            replan_id=replan_id,
            instruction=state.instruction,
            original_plan=ReplanOriginalPlan(
                plan_id=plan.plan_id,
                failed_step_id=failed_step.step_id,
                current_step_index=failed_step.index,
            ),
            failure_context=failure_context,
            execution_context=execution_context,
            exploration_context=exploration_context,
            constraints=constraints,
            metadata=meta,
            query_intent=read_query_intent_from_agent_state(state),
        )

    def build_replan_context(self, request: ReplanRequest) -> ReplanContext:
        ec = request.exploration_context
        exploration_summary: ReplanExplorationSummary | None = None
        if ec.key_findings or ec.knowledge_gaps:
            exploration_summary = ReplanExplorationSummary(
                key_findings=list(ec.key_findings),
                knowledge_gaps=list(ec.knowledge_gaps),
                overall="(summarized for replan; see key_findings / knowledge_gaps)",
            )
        return ReplanContext(
            failure_context=request.failure_context,
            completed_steps=list(request.execution_context.completed_steps),
            exploration_summary=exploration_summary,
            trigger="failure",
            query_intent=request.query_intent,
        )

    def replan(
        self,
        request: ReplanRequest,
        *,
        langfuse_trace: Any = None,
        obs: Any = None,
        session: Any = None,
        validation_task_mode: Optional[str] = None,
    ) -> tuple[ReplanResult, Optional[PlanDocument]]:
        """
        ``session`` should be the run's ``SessionMemory`` (``state.context['planner_session_memory']``).
        PlanExecutor always passes a concrete instance (creates/pins one if missing) so replan does not
        drop planner continuity. Callers that omit ``session`` get no memory in the planner prompt.
        """
        replan_context = self.build_replan_context(request)
        try:
            new_plan = self.planner.plan(
                request.instruction,
                planner_context=PlannerPlanContext(
                    replan=replan_context,
                    session=session,
                    query_intent=replan_context.query_intent,
                    exploration_budget=effective_exploration_budget(replan_context.query_intent),
                ),
                deep=True,
                langfuse_trace=langfuse_trace,
                obs=obs,
                validation_task_mode=validation_task_mode,
            )
        except (PlanValidationError, ValueError, TypeError) as e:
            ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            failed = ReplanResult(
                replan_id=request.replan_id,
                status="failed",
                new_plan=None,
                changes=ReplanChanges(
                    type="full_replacement",
                    summary="Replanner planner call failed",
                    modified_steps=[request.original_plan.failed_step_id],
                    added_steps=[],
                    removed_steps=[],
                ),
                reasoning=ReplanReasoning(
                    failure_analysis=request.failure_context.error.message,
                    strategy="abort",
                ),
                validation=ReplanValidation(is_valid=False, issues=[str(e)]),
                metadata=ReplanMetadata(
                    timestamp=ts,
                    replan_attempt=request.metadata.replan_attempt,
                ),
            )
            ReplanResultValidator.validate_replan_result(failed)
            return failed, None

        PlanValidator.validate_plan(
            new_plan, policy=self._policy, task_mode=validation_task_mode
        )
        result = self._build_replan_result(request, new_plan)
        ReplanResultValidator.validate_replan_result(result)
        return result, new_plan

    def _build_replan_result(self, request: ReplanRequest, new_plan: PlanDocument) -> ReplanResult:
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return ReplanResult(
            replan_id=request.replan_id,
            status="success",
            new_plan=ReplanNewPlan(plan_id=new_plan.plan_id),
            changes=ReplanChanges(
                type="partial_update",
                summary="Adjusted plan after execution failure",
                modified_steps=[request.original_plan.failed_step_id],
                added_steps=[],
                removed_steps=[],
            ),
            reasoning=ReplanReasoning(
                failure_analysis=request.failure_context.error.message,
                strategy="Re-attempt with adjusted approach",
            ),
            validation=ReplanValidation(is_valid=True, issues=[]),
            metadata=ReplanMetadata(
                timestamp=ts,
                replan_attempt=request.metadata.replan_attempt,
            ),
        )


def _exploration_context_from_state(state: Any) -> ReplanExplorationContext:
    ctx = getattr(state, "context", None)
    if not isinstance(ctx, dict):
        return ReplanExplorationContext(key_findings=[], knowledge_gaps=[])
    raw = ctx.get("exploration_result")
    if not isinstance(raw, dict):
        return ReplanExplorationContext(key_findings=[], knowledge_gaps=[])
    summ = raw.get("summary") or {}
    if not isinstance(summ, dict):
        summ = {}
    kf = summ.get("key_findings") or []
    kg = summ.get("knowledge_gaps") or []
    if not isinstance(kf, list):
        kf = []
    if not isinstance(kg, list):
        kg = []
    return ReplanExplorationContext(
        key_findings=[str(x) for x in kf],
        knowledge_gaps=[str(x) for x in kg],
    )


def validate_completed_steps_immutable(
    old: PlanDocument,
    new: PlanDocument,
    *,
    completed_step_ids: set[str],
) -> None:
    """Completed steps (by id set from DAG runtime) must not change goal/action/inputs."""
    old_by_id = {s.step_id: s for s in old.steps}
    for ns in new.steps:
        o = old_by_id.get(ns.step_id)
        if o is None or o.step_id not in completed_step_ids:
            continue
        if (o.goal, o.action, o.inputs) != (ns.goal, ns.action, ns.inputs):
            raise ValueError(
                f"Immutable completed step {ns.step_id!r} must not change goal/action/inputs "
                f"(old action={o.action!r}, new action={ns.action!r})"
            )


def merge_preserved_completed_steps(
    old: PlanDocument,
    new: PlanDocument,
    *,
    completed_step_ids: set[str],
) -> PlanDocument:
    """
    Freeze planner-owned fields for step_ids in ``completed_step_ids`` using ``old`` as source.

    Prepends completed steps from ``old`` missing from ``new.steps`` (controller tail-only replans).
    """
    old_by_id = {s.step_id: s for s in old.steps}
    new_ids = {s.step_id for s in new.steps}
    prefix: list[PlanStep] = []
    for s in sorted(old.steps, key=lambda x: x.index):
        if s.step_id in completed_step_ids and s.step_id not in new_ids:
            prefix.append(s.model_copy())

    merged: list[PlanStep] = []
    for s in new.steps:
        o = old_by_id.get(s.step_id)
        if o is not None and o.step_id in completed_step_ids:
            merged.append(o.model_copy())
        else:
            merged.append(s)
    combined = prefix + merged
    reindexed = [s.model_copy(update={"index": i}) for i, s in enumerate(combined, start=1)]
    return new.model_copy(update={"steps": reindexed})
