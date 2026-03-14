"""
Phase 5 — Top-2 candidate router: PRIMARY SECONDARY CONFIDENCE (e.g. EDIT SEARCH 0.82).
"""

ROUTER_NAME = "dual"

from router_eval.utils.router_core import run_ensemble_dual


def route(instruction: str) -> dict:
    """Return primary, secondary category and confidence. Harness uses primary as category."""
    _, primary, secondary, avg_conf, routers_agree = run_ensemble_dual(instruction)
    return {
        "category": primary,
        "primary": primary,
        "secondary": secondary,
        "confidence": avg_conf,
        "routers_agree": routers_agree,
    }
