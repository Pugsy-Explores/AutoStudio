"""Instruction-level routing before planner."""

from agent.routing.instruction_router import RouterDecision, route_instruction
from agent.routing.router_registry import get_router, get_router_raw, list_routers

__all__ = [
    "RouterDecision",
    "route_instruction",
    "get_router",
    "get_router_raw",
    "list_routers",
]
