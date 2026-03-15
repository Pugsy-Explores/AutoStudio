"""
Retry planner: based on critic diagnosis, produces retry hints for the next attempt.

Strategies: rewrite_retrieval_query, expand_search_scope, generate_new_plan,
retry_edit_with_different_patch, search_symbol_dependencies.
"""

import json
import logging
from dataclasses import dataclass

from agent.meta.critic import Diagnosis

logger = logging.getLogger(__name__)

RETRY_STRATEGIES = frozenset({
    "rewrite_retrieval_query",
    "expand_search_scope",
    "generate_new_plan",
    "retry_edit_with_different_patch",
    "search_symbol_dependencies",
})


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


def _load_retry_planner_system_prompt() -> str:
    """Load retry planner system prompt from YAML."""
    from pathlib import Path

    config_dir = Path(__file__).resolve().parent.parent / "prompts"
    path = config_dir / "retry_planner_system.yaml"
    if path.is_file():
        try:
            import yaml

            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return (data.get("system_prompt") or "").strip()
        except Exception as e:
            logger.debug("[retry_planner] failed to load prompt: %s", e)
    return """You are the retry planner. Given a diagnosis, produce retry hints.
Return JSON only: {"strategy": "...", "rewrite_query": "...", "plan_override": null, "retrieve_files": []}"""


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

        system = _load_retry_planner_system_prompt()
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
                strategy = str(obj.get("strategy", default_strategy)).strip()
                if strategy not in RETRY_STRATEGIES:
                    strategy = default_strategy
                return RetryHints(
                    strategy=strategy,
                    rewrite_query=str(obj.get("rewrite_query", "") or "")[:500],
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
