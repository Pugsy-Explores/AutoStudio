"""
Router v2: 4-category taxonomy (EDIT, SEARCH, EXPLAIN, INFRA) with confidence and gating.
"""

import re

from router_eval.prompts.router_v2_prompt import ROUTER_V2_SYSTEM
from router_eval.utils.llama_client import llama_chat

ROUTER_NAME = "router_v2"

V2_CATEGORIES = ("EDIT", "SEARCH", "EXPLAIN", "INFRA")
_V2_CATEGORIES_SET = set(V2_CATEGORIES)


def _parse_category_confidence_v2(response: str) -> dict | None:
    """
    Parse "CATEGORY CONFIDENCE" from model response. Returns dict or None on failure.
    Validates category against V2_CATEGORIES and clamps confidence to [0, 1].
    """
    if not response or not response.strip():
        return None
    text = response.strip()
    first_line = text.split("\n")[0].strip()
    match = re.search(r"([A-Za-z]+)\s+([0-9]*\.?[0-9]+)", first_line)
    if not match:
        return None
    cat = match.group(1).upper()
    if cat not in _V2_CATEGORIES_SET:
        return None
    try:
        conf = float(match.group(2))
        conf = max(0.0, min(1.0, conf))
    except ValueError:
        return None
    return {"category": cat, "confidence": conf}


def route(instruction: str) -> dict:
    """
    Route instruction using v2 taxonomy. Returns {"category": str, "confidence": float}.
    On parse failure returns {"category": "EXPLAIN", "confidence": 0.0}.
    """
    response = llama_chat(ROUTER_V2_SYSTEM, instruction)
    parsed = _parse_category_confidence_v2(response)
    if parsed is None:
        return {"category": "EXPLAIN", "confidence": 0.0}
    return parsed


def route_with_fallback(instruction: str, threshold: float = 0.6) -> dict:
    """
    Run router_v2; if confidence >= threshold return result, else escalate to EXPLAIN.
    When falling back, returns {"category": "EXPLAIN", "confidence": confidence, "fallback": True}.
    """
    result = route(instruction)
    if result["confidence"] >= threshold:
        return result
    return {
        "category": "EXPLAIN",
        "confidence": result["confidence"],
        "fallback": True,
    }
