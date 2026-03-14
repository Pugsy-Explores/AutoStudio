"""
Few-shot + logit router: few-shot system prompt with logit-based category selection.
Uses FEWSHOT_SYSTEM and the same token-probability logic as logit_router.
"""

ROUTER_NAME = "fewshot_logit"

from router_eval.prompts.router_prompts import FEWSHOT_SYSTEM
from router_eval.routers.logit_router import chat_with_logprobs, pick_category_from_logprobs


def route(instruction: str) -> dict:
    """
    Few-shot system prompt + "Instruction: ... \\nCategory:" user message;
    max_tokens=1, logprobs; pick category with highest token probability.
    """
    try:
        user_message = f"Instruction: {instruction}\nCategory:"
        content, logprobs_content = chat_with_logprobs(FEWSHOT_SYSTEM, user_message)
    except Exception:
        return {"category": "GENERAL", "confidence": 0.0}

    category, confidence = pick_category_from_logprobs(content, logprobs_content)
    return {"category": category, "confidence": confidence}
