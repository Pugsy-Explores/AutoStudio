"""Registry of router implementations. Wires router_eval routers for production use."""

import logging
from typing import Callable

from agent.routing.instruction_router import RouterDecision

logger = logging.getLogger(__name__)

# Map instruction-level categories (router_eval) to RouterDecision categories
_ROUTER_TO_INSTRUCTION = {
    "EDIT": "CODE_EDIT",
    "SEARCH": "CODE_SEARCH",
    "EXPLAIN": "CODE_EXPLAIN",
    "INFRA": "INFRA",
    "GENERAL": "GENERAL",
}


def _to_router_decision(result: str | dict) -> RouterDecision:
    """Normalize router output (str or dict) to RouterDecision."""
    if isinstance(result, dict):
        category = result.get("category") or result.get("primary", "GENERAL")
        confidence = float(result.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
    else:
        category = str(result).strip().upper() if result else "GENERAL"
        confidence = 0.5

    instruction_category = _ROUTER_TO_INSTRUCTION.get(category, "GENERAL")
    return RouterDecision(category=instruction_category, confidence=confidence)


_REGISTRY: dict[str, Callable[[str], str | dict]] = {}


def _load_registry() -> dict[str, Callable[[str], str | dict]]:
    """Lazy-load router implementations from router_eval."""
    if _REGISTRY:
        return _REGISTRY
    try:
        from router_eval.routers import baseline_router
        from router_eval.routers import ensemble_router
        from router_eval.routers import fewshot_router
        from router_eval.routers import final_router

        _REGISTRY["baseline"] = baseline_router.route
        _REGISTRY["fewshot"] = fewshot_router.route
        _REGISTRY["ensemble"] = ensemble_router.route
        _REGISTRY["final"] = final_router.route
    except ImportError as e:
        logger.warning("[router_registry] Could not load router_eval routers: %s", e)
    return _REGISTRY


def get_router(name: str) -> Callable[[str], RouterDecision] | None:
    """
    Return a route function that takes instruction and returns RouterDecision.
    name: baseline, fewshot, ensemble, or final.
    Returns None if name is unknown or routers unavailable.
    """
    registry = _load_registry()
    route_fn = registry.get((name or "").strip().lower())
    if route_fn is None:
        return None

    def _wrapped(instruction: str) -> RouterDecision:
        raw = route_fn(instruction)
        return _to_router_decision(raw)

    return _wrapped


def list_routers() -> list[str]:
    """Return available router names."""
    return list(_load_registry().keys())


def get_router_raw(name: str) -> Callable[[str], str | dict] | None:
    """
    Return the raw route function (instruction -> str | dict) for evaluation harness.
    Use this so router_eval uses the same implementation as production.
    """
    return _load_registry().get((name or "").strip().lower())
