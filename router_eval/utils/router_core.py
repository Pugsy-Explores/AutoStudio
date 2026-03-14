"""
Shared ensemble/dual logic for routers. Used by dual_router and final_router.
"""

from typing import Any

from router_eval.prompts.router_prompts import (
    DUAL_INSTRUCTION,
    PROMPT_A_CLASSIFICATION,
    PROMPT_B_TOOL_SELECTION,
    PROMPT_C_INSTRUCTION_ANALYSIS,
)
from router_eval.utils.llama_client import llama_chat
from router_eval.utils.parsing import parse_dual
from router_eval.utils.voting import majority_vote

PROMPTS = [
    PROMPT_A_CLASSIFICATION,
    PROMPT_B_TOOL_SELECTION,
    PROMPT_C_INSTRUCTION_ANALYSIS,
]


def run_ensemble_dual(instruction: str) -> tuple[list[dict[str, Any]], str, str, float, bool]:
    """
    Run the three ensemble prompts with dual (PRIMARY SECONDARY CONFIDENCE) output.
    Returns (results, primary, secondary, avg_conf, routers_agree).
    """
    system_suffix = DUAL_INSTRUCTION
    results = []
    for system in PROMPTS:
        full_system = system.rstrip() + system_suffix
        response = llama_chat(full_system, instruction)
        results.append(parse_dual(response))
    primaries = [r["primary"] for r in results]
    primary = majority_vote(primaries)
    secondaries = [r["secondary"] for r in results]
    secondary = majority_vote(secondaries)
    avg_conf = sum(r["confidence"] for r in results) / len(results) if results else 0.5
    routers_agree = len(primaries) > 0 and primaries.count(primaries[0]) == len(primaries)
    return results, primary, secondary, avg_conf, routers_agree
