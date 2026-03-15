"""
Planner system prompt.
Loaded from agent/prompt_system PromptRegistry.
"""

from agent.prompt_system import get_registry

PLANNER_SYSTEM_PROMPT = get_registry().get_instructions("planner")
