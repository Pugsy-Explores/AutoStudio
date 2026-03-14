"""
Phase 6 — Claude-style critic: run critic when low confidence, disagreement, or primary/secondary ambiguity.
"""

ROUTER_NAME = "critic"

from router_eval.prompts.critic_prompt import CRITIC_SYSTEM, build_critic_user_message
from router_eval.routers.dual_router import route as dual_route
from router_eval.utils.llama_client import llama_chat
from router_eval.utils.parsing import parse_critic_response

# Trigger thresholds
CONFIDENCE_THRESHOLD = 0.75


def route(instruction: str) -> dict:
    """
    Use dual router; if confidence < 0.75 or primary != secondary (ambiguity), run critic.
    Return dict with category (possibly corrected) and confidence.
    """
    result = dual_route(instruction)
    primary = result["primary"]
    secondary = result["secondary"]
    confidence = result["confidence"]
    routers_agree = result.get("routers_agree", True)

    routers_disagree = not routers_agree
    run_critic = (
        confidence < CONFIDENCE_THRESHOLD
        or primary != secondary
        or routers_disagree
    )
    if run_critic:
        user_msg = build_critic_user_message(instruction, primary)
        response = llama_chat(CRITIC_SYSTEM, user_msg)
        category = parse_critic_response(response, primary)
    else:
        category = primary

    return {"category": category, "confidence": confidence}
