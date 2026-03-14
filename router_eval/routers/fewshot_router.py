"""
Phase 2 — Few-shot router: same flow with 5–7 examples in system prompt.
"""

ROUTER_NAME = "fewshot"

from router_eval.prompts.router_prompts import FEWSHOT_SYSTEM
from router_eval.utils.llama_client import llama_chat
from router_eval.utils.parsing import parse_category


def route(instruction: str) -> str:
    """Route instruction using few-shot prompt."""
    response = llama_chat(FEWSHOT_SYSTEM, instruction)
    return parse_category(response)
