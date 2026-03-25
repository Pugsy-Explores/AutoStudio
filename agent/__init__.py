"""Agent package: executor layer for coding agent."""

from agent.executor import StepExecutor
from agent.memory.state import AgentState
from agent.memory.step_result import StepResult

__all__ = [
    "AgentState",
    "StepExecutor",
    "StepResult",
    "run_agent",
    "run_loop",
]


def __getattr__(name: str):
    # Lazy imports to avoid circular dependency and heavy orchestration load at package init.
    # planner -> agent.models.model_client loads agent; agent must not import agent_loop
    # (which imports plan_resolver -> planner). Same pattern for run_loop.
    if name == "run_agent":
        from agent_v2.runtime.bootstrap import create_runtime

        def _run_agent(instruction: str):
            runtime = create_runtime()
            return runtime.run(instruction, mode="act")

        return _run_agent
    if name == "run_loop":
        from agent.agent_loop import run_loop
        return run_loop
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
