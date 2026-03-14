"""
Phase 7 — Final router: fast accept when confidence > 0.9 and routers agree; else critic.
"""

ROUTER_NAME = "final"

from router_eval.prompts.critic_prompt import CRITIC_SYSTEM, build_critic_user_message
from router_eval.utils.llama_client import llama_chat
from router_eval.utils.parsing import parse_critic_response
from router_eval.utils.router_core import run_ensemble_dual

FAST_ACCEPT_CONFIDENCE = 0.9


def route(instruction: str) -> dict:
    """
    Ensemble (A/B/C) with dual output. If confidence > 0.9 and all three agree, accept.
    Otherwise run critic and return corrected category.
    """
    _, primary, _, avg_conf, routers_agree = run_ensemble_dual(instruction)

    fast_accept = avg_conf > FAST_ACCEPT_CONFIDENCE and routers_agree
    if fast_accept:
        return {"category": primary, "confidence": avg_conf, "routers_agree": routers_agree}

    user_msg = build_critic_user_message(instruction, primary)
    response = llama_chat(CRITIC_SYSTEM, user_msg)
    category = parse_critic_response(response, primary)
    return {"category": category, "confidence": avg_conf, "routers_agree": routers_agree}
