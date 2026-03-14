"""
Phase 4 — Confidence router: ensemble with self-reported confidence; return {category, confidence}.
"""

ROUTER_NAME = "confidence"

from router_eval.prompts.router_prompts import (
    CONFIDENCE_INSTRUCTION,
    PROMPT_A_CLASSIFICATION,
    PROMPT_B_TOOL_SELECTION,
    PROMPT_C_INSTRUCTION_ANALYSIS,
)
from router_eval.utils.llama_client import llama_chat
from router_eval.utils.parsing import parse_category_confidence
from router_eval.utils.voting import majority_vote_with_confidence


def route(instruction: str) -> dict:
    """Run three prompts with confidence output; majority vote on category, average confidence."""
    prompts = [PROMPT_A_CLASSIFICATION, PROMPT_B_TOOL_SELECTION, PROMPT_C_INSTRUCTION_ANALYSIS]
    system_suffix = CONFIDENCE_INSTRUCTION
    predictions = []
    for system in prompts:
        full_system = system.rstrip() + system_suffix
        response = llama_chat(full_system, instruction)
        predictions.append(parse_category_confidence(response))
    category, confidence = majority_vote_with_confidence(predictions)
    categories = [p["category"] for p in predictions]
    routers_agree = len(categories) > 0 and categories.count(categories[0]) == len(categories)
    return {"category": category, "confidence": confidence, "routers_agree": routers_agree}
