"""
Planner system prompt.
Loaded from centralized agent/prompts/planner_system.json.
"""

from agent.prompts import get_prompt

PLANNER_SYSTEM_PROMPT = get_prompt("planner_system", "system_prompt")
