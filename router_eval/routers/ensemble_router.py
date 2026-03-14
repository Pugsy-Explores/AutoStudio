"""
Phase 3 — Multi-prompt ensemble: three prompts, majority vote.
"""

ROUTER_NAME = "ensemble"

from router_eval.prompts.router_prompts import (
    PROMPT_A_CLASSIFICATION,
    PROMPT_B_TOOL_SELECTION,
    PROMPT_C_INSTRUCTION_ANALYSIS,
)
from router_eval.utils.llama_client import llama_chat
from router_eval.utils.parsing import parse_category
from router_eval.utils.voting import majority_vote


def route(instruction: str) -> str:
    """Run three prompt variants and return majority vote."""
    prompts = [PROMPT_A_CLASSIFICATION, PROMPT_B_TOOL_SELECTION, PROMPT_C_INSTRUCTION_ANALYSIS]
    categories = []
    for system in prompts:
        response = llama_chat(system, instruction)
        categories.append(parse_category(response))
    return majority_vote(categories)
