"""
Phase 1 — Baseline router: single prompt -> model -> parse category.
"""

ROUTER_NAME = "baseline"

from router_eval.prompts.router_prompts import BASELINE_SYSTEM
from router_eval.utils.llama_client import llama_chat
from router_eval.utils.parsing import parse_category


def route(instruction: str) -> str:
    """Route instruction to one of EDIT, SEARCH, EXPLAIN, INFRA, GENERAL."""
    response = llama_chat(BASELINE_SYSTEM, instruction)
    return parse_category(response)
