"""Long-horizon planner: multi-module planning with architecture context."""

import logging

from planner.planner import plan

logger = logging.getLogger(__name__)


def _format_architecture_context(architecture_map: dict) -> str:
    """Format architecture map as a brief context block for the planner."""
    parts: list[str] = []
    for layer, mods in architecture_map.items():
        if mods:
            parts.append(f"{layer}: {', '.join(mods[:15])}{'...' if len(mods) > 15 else ''}")
    if not parts:
        return ""
    return "[Repository architecture]\n" + "\n".join(parts) + "\n\n"


def plan_long_horizon(goal: str, architecture_map: dict | None = None) -> dict:
    """
    Plan with architecture context. Prepends module/layer info to instruction,
    then delegates to planner.plan(). Does not replace the planner.
    """
    if not architecture_map:
        return plan(goal)

    ctx = _format_architecture_context(architecture_map)
    enriched = f"{ctx}[Task]\n{goal}"
    logger.info("[long_horizon_planner] planning with architecture context (%d layers)", len(architecture_map))
    return plan(enriched)
