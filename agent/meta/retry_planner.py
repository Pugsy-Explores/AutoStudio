"""
Retry planner: based on critic diagnosis, produces retry hints for the next attempt.

Strategies: rewrite_retrieval_query, expand_search_scope, generate_new_plan,
retry_edit_with_different_patch, search_symbol_dependencies.

Phase 5: RetryPlanner.build_retry_context() produces retry_context (previous_attempts,
critic_feedback) for the planner. No LLM calls.
"""

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent.meta.critic import Diagnosis

if TYPE_CHECKING:
    from agent.meta.trajectory_memory import TrajectoryMemory

logger = logging.getLogger(__name__)


class RetryPlanner:
    """
    Phase 5: builds retry context from trajectory memory and critic feedback.
    No LLM calls; context is passed to the existing planner.
    """

    def build_retry_context(
        self,
        instruction: str,
        trajectory_memory: "TrajectoryMemory",
        critic_feedback: dict,
    ) -> dict:
        """
        Produce retry_context for the planner:
        {
          "previous_attempts": trajectory_memory.all_attempts(),
          "critic_feedback": critic_feedback,
          "strategy_hint": critic_feedback["strategy_hint"]
        }
        """
        return {
            "previous_attempts": trajectory_memory.all_attempts(),
            "critic_feedback": critic_feedback,
            "strategy_hint": critic_feedback.get("strategy_hint") or "",
        }

RETRY_STRATEGIES = frozenset({
    "rewrite_retrieval_query",
    "expand_search_scope",
    "generate_new_plan",
    "retry_edit_with_different_patch",
    "search_symbol_dependencies",
})
# Model may output "rewrite_query"; treat as rewrite_retrieval_query
STRATEGY_ALIASES = {"rewrite_query": "rewrite_retrieval_query"}
FALLBACK_STRATEGY = "generate_new_plan"


@dataclass
class RetryHints:
    """Hints for the next retry attempt."""

    strategy: str
    rewrite_query: str
    plan_override: str | None
    retrieve_files: list[str]

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "rewrite_query": self.rewrite_query,
            "plan_override": self.plan_override,
            "retrieve_files": self.retrieve_files or [],
        }


def _strategy_from_diagnosis(diagnosis: Diagnosis) -> str:
    """Map failure_type to preferred strategy."""
    ft = diagnosis.failure_type
    if ft == "retrieval_miss":
        return "rewrite_retrieval_query"
    if ft == "missing_dependency":
        return "search_symbol_dependencies"
    if ft == "bad_plan":
        return "generate_new_plan"
    if ft == "bad_patch":
        return "retry_edit_with_different_patch"
    if ft == "timeout":
        return "expand_search_scope"
    return "rewrite_retrieval_query"


def plan_retry(goal: str, diagnosis: Diagnosis) -> RetryHints:
    """
    Produce retry hints based on critic diagnosis. Uses call_reasoning_model.

    Args:
        goal: Original task string
        diagnosis: Diagnosis from critic

    Returns:
        RetryHints with strategy, rewrite_query, plan_override, retrieve_files
    """
    prompt = f"""Goal: {goal}
Diagnosis:
  failure_type: {diagnosis.failure_type}
  affected_step: {diagnosis.affected_step}
  suggestion: {diagnosis.suggestion}

Produce retry hints as JSON."""

    default_strategy = _strategy_from_diagnosis(diagnosis)

    try:
        from agent.models.model_client import call_reasoning_model
        from agent.prompt_system import get_registry

        system = get_registry().get_instructions("retry_planner")
        out = call_reasoning_model(
            prompt,
            system_prompt=system,
            task_name="retry_planning",
            max_tokens=1024,
        )
        out = (out or "").strip()
        idx = out.find("{")
        if idx >= 0:
            end = out.rfind("}")
            if end > idx:
                obj = json.loads(out[idx : end + 1])
                strategy = str(obj.get("strategy", "")).strip()
                if strategy in STRATEGY_ALIASES:
                    strategy = STRATEGY_ALIASES[strategy]
                if strategy not in RETRY_STRATEGIES:
                    logger.warning("[retry_planner] unrecognised strategy %r, using fallback", strategy or "(empty)")
                    strategy = FALLBACK_STRATEGY
                rq = obj.get("rewrite_query", "") or ""
                if not rq and obj.get("rewrite_queries"):
                    rq_list = obj.get("rewrite_queries")
                    rq = rq_list[0] if isinstance(rq_list, list) and rq_list else ""
                rewrite_query = str(rq)[:500]
                return RetryHints(
                    strategy=strategy,
                    rewrite_query=rewrite_query,
                    plan_override=str(obj.get("plan_override") or "") if obj.get("plan_override") else None,
                    retrieve_files=obj.get("retrieve_files") or [],
                )
    except Exception as e:
        logger.warning("[retry_planner] plan_retry failed: %s", e)

    return RetryHints(
        strategy=default_strategy,
        rewrite_query=diagnosis.suggestion[:200] if diagnosis.suggestion else "",
        plan_override=None,
        retrieve_files=[],
    )
