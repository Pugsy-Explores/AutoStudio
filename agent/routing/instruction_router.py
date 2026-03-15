"""Instruction router: classify developer query before planner. Uses SMALL model or registry."""

import json
import logging
import re
from dataclasses import dataclass

from agent.models.model_client import call_small_model
from config.router_config import ROUTER_TYPE

logger = logging.getLogger(__name__)

ROUTER_CATEGORIES = ("CODE_SEARCH", "CODE_EDIT", "CODE_EXPLAIN", "INFRA", "GENERAL")
_ROUTER_CATEGORIES_SET = set(ROUTER_CATEGORIES)


@dataclass
class RouterDecision:
    """Result of instruction routing."""

    category: str
    confidence: float


_ROUTER_SYSTEM = """Classify the developer query into exactly one category.

Categories:
CODE_SEARCH - Locate files, functions, classes, or usages in the codebase
CODE_EDIT - Modify or write source code
CODE_EXPLAIN - Explain APIs, modules, documentation, or how code works
INFRA - Infrastructure configuration (Docker, Kubernetes, CI/CD, Terraform, env variables)
GENERAL - General explanation or discussion about programming concepts (use when unclear)

Return ONLY valid JSON in this exact format:
{"category": "CATEGORY_NAME", "confidence": 0.0}

Example: {"category": "CODE_SEARCH", "confidence": 0.92}
"""


def _extract_json(text: str) -> str | None:
    """Strip markdown code fences and return the first JSON object string, or None."""
    if not text or not text.strip():
        return None
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def route_instruction(instruction: str) -> RouterDecision:
    """
    Classify instruction into one of CODE_SEARCH, CODE_EDIT, CODE_EXPLAIN, INFRA, GENERAL.
    Returns RouterDecision with category and confidence.
    When ROUTER_TYPE env is set (baseline, fewshot, ensemble, final), uses that router from registry.
    """
    if ROUTER_TYPE:
        from agent.routing.router_registry import get_router

        router_fn = get_router(ROUTER_TYPE)
        if router_fn is not None:
            return router_fn(instruction)
        logger.warning("[instruction_router] ROUTER_TYPE=%r not available, using inline model", ROUTER_TYPE)

    prompt = f"{_ROUTER_SYSTEM}\n\nInstruction:\n{instruction}"
    try:
        response = call_small_model(
            prompt,
            task_name="routing",
            max_tokens=128,
        )
    except Exception as e:
        logger.warning("[instruction_router] model call failed: %s, defaulting to GENERAL", e)
        return RouterDecision(category="GENERAL", confidence=0.0)

    raw_json = _extract_json(response)
    if not raw_json:
        raw_json = response.strip()
        if raw_json.startswith("{"):
            end = raw_json.rfind("}") + 1
            if end > 0:
                raw_json = raw_json[:end]

    try:
        data = json.loads(raw_json) if raw_json else {}
    except json.JSONDecodeError:
        logger.warning("[instruction_router] invalid JSON, defaulting to GENERAL")
        return RouterDecision(category="GENERAL", confidence=0.0)

    category = (data.get("category") or "GENERAL").strip().upper()
    if category not in _ROUTER_CATEGORIES_SET:
        category = "GENERAL"

    try:
        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    return RouterDecision(category=category, confidence=confidence)
