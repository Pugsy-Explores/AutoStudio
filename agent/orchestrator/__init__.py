"""Orchestrator: agent loop, controller, replanner, validator."""

from agent.orchestrator.replanner import replan
from agent.orchestrator.validator import validate_step

__all__ = ["run_agent", "run_controller", "replan", "validate_step"]


def run_agent(instruction: str):
    from agent_v2.runtime.bootstrap import create_runtime

    runtime = create_runtime()
    return runtime.run(instruction, mode="act")


def run_controller(*_args, **_kwargs):
    raise RuntimeError("Legacy controller path removed. Use agent_v2 runtime.")
