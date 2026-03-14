"""Agent package: executor layer for coding agent."""

from agent.executor import StepExecutor
from agent.memory.state import AgentState
from agent.memory.step_result import StepResult
from agent.orchestrator.agent_loop import run_agent

__all__ = [
    "AgentState",
    "StepExecutor",
    "StepResult",
    "run_agent",
    "run_loop",
]


def __getattr__(name: str):
    # Lazy import so `python3 -m agent.agent_loop` does not load agent.agent_loop
    # during package init (avoids RuntimeWarning about module already in sys.modules).
    if name == "run_loop":
        from agent.agent_loop import run_loop
        return run_loop
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
