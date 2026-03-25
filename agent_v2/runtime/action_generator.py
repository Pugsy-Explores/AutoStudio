"""ReAct action generator wrapper (model chooses next action + args).

For plan-driven execution (Phase 5), use PlanArgumentGenerator instead: the plan
fixes PlanStep.action; the model fills arguments only. PlanExecutor does not use
next_action().
"""
# DO NOT import from agent.* here

from typing import Any


class ActionGenerator:
    """
    Thin wrapper for obtaining next action steps.

    Supports two generation modes:
      - next_action(state): main ReAct loop action (full AgentState)
      - next_action_exploration(instruction, items): exploration-phase action
        (isolated; receives instruction + collected (step, result) pairs)
    """

    def __init__(self, fn, exploration_fn=None):
        """
        Args:
            fn: callable(state) -> dict | None — main loop action generator.
            exploration_fn: callable(instruction, items) -> dict | None — exploration
                action generator. When None, next_action_exploration() returns None
                (exploration produces no steps; ExplorationRunner returns empty result).
        """
        self._fn = fn
        self._exploration_fn = exploration_fn

    def next_action(self, state):
        """Generate the next step for the main ReAct / plan-execute loop."""
        return self._fn(state)

    def next_action_exploration(
        self,
        instruction: str,
        items: list,
        *,
        langfuse_trace: Any = None,
    ):
        """
        Generate the next step for the bounded exploration phase.

        Args:
            instruction: original user instruction.
            items: list of (step_dict, ExecutionResult) tuples collected so far
                   in this exploration run.

        Returns:
            A step dict (same shape as next_action) or None to stop exploration.
        """
        if self._exploration_fn is None:
            return None
        return self._exploration_fn(instruction, items, langfuse_trace=langfuse_trace)
