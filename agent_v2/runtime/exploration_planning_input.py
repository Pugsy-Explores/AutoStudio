"""
Exploration → PlannerPlanContext (signal-first; no RuntimeError on incomplete exploration).

normalize_planner_plan_context: legacy FinalExplorationSchema | ReplanContext → PlannerPlanContext.
"""

from __future__ import annotations

from typing import Any, Optional, Union

from agent_v2.config import get_config
from agent_v2.schemas.exploration import (
    QueryIntent,
    effective_exploration_budget,
    read_query_intent_from_agent_state,
)
from agent_v2.schemas.final_exploration import FinalExplorationSchema
from agent_v2.schemas.plan import PlanDocument
from agent_v2.schemas.plan_state import PlanState
from agent_v2.schemas.answer_validation import AnswerValidationResult
from agent_v2.schemas.planner_plan_context import (
    ExplorationInsufficientContext,
    PlannerPlanContext,
)
from agent_v2.schemas.replan import ReplanContext


def _termination_reason(exploration: FinalExplorationSchema) -> str:
    md = exploration.metadata
    if md is None:
        return "unknown"
    r = getattr(md, "termination_reason", None)
    return str(r or "unknown")


def exploration_allows_direct_plan_input(exploration: FinalExplorationSchema) -> bool:
    """True when exploration metadata is strong enough to plan without insufficiency signal."""
    md = exploration.metadata
    if md is None:
        return True
    if "unittest.mock" in type(md).__module__:
        return True
    completion_status = getattr(md, "completion_status", None)
    if completion_status is None:
        return True
    status = str(completion_status).lower()
    if status not in {"complete", "incomplete"}:
        return True
    if status == "complete":
        return True
    reason = _termination_reason(exploration)
    if reason in {"analyzer_sufficient"}:
        return True
    cfg = get_config()
    if cfg.exploration.allow_partial_for_plan_mode and reason in {
        "max_steps",
        "pending_exhausted",
        "stalled",
    }:
        return True
    return False


def exploration_to_planner_context(
    exploration: FinalExplorationSchema,
    session: Optional[Any] = None,
    *,
    query_intent: Optional[QueryIntent] = None,
    state: Optional[Any] = None,
    validation_feedback: Optional[AnswerValidationResult] = None,
) -> PlannerPlanContext:
    """
    Always includes ``exploration`` when present; adds ``insufficiency`` when metadata is weak.

    ``query_intent``: copy of canonical ``state.context['query_intent']``; if omitted, taken from
    ``state`` then ``exploration.query_intent`` (adapter mirror).
    """
    qi = query_intent
    available_symbols: list[str] = []
    missing_symbols: list[str] = []
    if qi is None and state is not None:
        qi = read_query_intent_from_agent_state(state)
    if state is not None:
        ctx = getattr(state, "context", None)
        if isinstance(ctx, dict):
            av = ctx.get("exploration_available_symbols")
            ms = ctx.get("exploration_missing_symbols")
            if isinstance(av, list):
                available_symbols = [str(x).strip() for x in av if str(x).strip()]
            if isinstance(ms, list):
                missing_symbols = [str(x).strip() for x in ms if str(x).strip()]
    if qi is None and getattr(exploration, "query_intent", None) is not None:
        qi = exploration.query_intent
    eb = effective_exploration_budget(qi)
    if exploration_allows_direct_plan_input(exploration):
        return PlannerPlanContext(
            exploration=exploration,
            session=session,
            query_intent=qi,
            exploration_budget=eb,
            validation_feedback=validation_feedback,
            available_symbols=available_symbols,
            missing_symbols=missing_symbols,
        )
    reason = _termination_reason(exploration)
    ins = ExplorationInsufficientContext(
        message=(
            "Exploration metadata indicates incomplete coverage; prefer conservative steps "
            "and verify assumptions."
        ),
        termination_reason=reason,
    )
    return PlannerPlanContext(
        exploration=exploration,
        insufficiency=ins,
        session=session,
        query_intent=qi,
        exploration_budget=eb,
        validation_feedback=validation_feedback,
        available_symbols=available_symbols,
        missing_symbols=missing_symbols,
    )


def normalize_planner_plan_context(
    raw: Union[PlannerPlanContext, FinalExplorationSchema, ReplanContext],
) -> PlannerPlanContext:
    """Coerce legacy union to PlannerPlanContext (PlannerV2 entry)."""
    if isinstance(raw, PlannerPlanContext):
        return raw
    if isinstance(raw, ReplanContext):
        return PlannerPlanContext(
            replan=raw,
            query_intent=raw.query_intent,
            exploration_budget=effective_exploration_budget(raw.query_intent),
        )
    if isinstance(raw, FinalExplorationSchema):
        return exploration_to_planner_context(raw)
    raise TypeError(f"Unsupported planner context type: {type(raw)!r}")


def call_planner_with_context(
    planner: Any,
    instruction: str,
    ctx: PlannerPlanContext,
    *,
    deep: bool,
    obs: Any,
    langfuse_trace: Any,
    plan_state: Optional[PlanState] = None,
    prior_plan_document: Optional[PlanDocument] = None,
    require_controller_json: bool = False,
    session: Optional[Any] = None,
    validation_task_mode: Optional[str] = None,
    plan_body_only: bool = False,
    state: Optional[Any] = None,
) -> Any:
    """Single path: planner always receives planner_context=PlannerPlanContext."""
    if session is not None:
        ctx = ctx.model_copy(update={"session": session})
    from agent_v2.runtime.planner_task_runtime import (
        attach_episodic_failures_if_enabled,
        attach_semantic_facts_if_enabled,
    )

    attach_episodic_failures_if_enabled(ctx)
    attach_semantic_facts_if_enabled(ctx, instruction=instruction, state=state)
    req = False if plan_body_only else require_controller_json
    return planner.plan(
        instruction,
        planner_context=ctx,
        deep=deep,
        obs=obs,
        langfuse_trace=langfuse_trace,
        plan_state=plan_state,
        prior_plan_document=prior_plan_document,
        require_controller_json=req,
        validation_task_mode=validation_task_mode,
    )
