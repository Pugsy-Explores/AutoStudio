"""
Decide which model (SMALL or REASONING) should handle a task.
Reads from models_config.json task_models; falls back to LLM-based routing if task not in config.
"""

import logging

from agent.models.model_config import TASK_MODELS
from agent.models.model_client import call_small_model
from agent.models.model_types import ModelType

logger = logging.getLogger(__name__)

_ROUTER_PROMPT = """Classify which model should handle this task.
Options: SMALL or REASONING
- Use SMALL for: simple classification, routing, lightweight decisions.
- Use REASONING for: planning, query rewriting, validation, explanation, multi-step reasoning.

Task:
{task_description}

Return only the label: SMALL or REASONING."""


def get_model_for_task(task_name: str) -> ModelType:
    """
    Return which model to use for this task/step from config (models_config.json task_models).
    task_name must match a key in task_models (e.g. "query rewriting", "validation", "EXPLAIN").
    If not found, defaults to REASONING.
    """
    print(f"[workflow] model_router task={task_name!r}")
    name = (TASK_MODELS.get(task_name) or "REASONING").upper()
    if name == "SMALL":
        return ModelType.SMALL
    return ModelType.REASONING


def route_task(task_description: str) -> ModelType:
    """
    Ask the small model which model should handle this task (fallback when config not used).
    Returns ModelType.SMALL or ModelType.REASONING.
    """
    prompt = _ROUTER_PROMPT.format(task_description=task_description.strip())
    try:
        raw = call_small_model(prompt, max_tokens=16).strip().upper()
        if "REASONING" in raw:
            return ModelType.REASONING
        return ModelType.SMALL
    except Exception as e:
        logger.warning("Route task failed, defaulting to REASONING: %s", e)
        return ModelType.REASONING
