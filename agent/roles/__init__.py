"""Phase 9: Hierarchical multi-agent orchestration.

Role-specialized agents coordinated by a supervisor.
All agents reuse: dispatcher, retrieval pipeline, editing pipeline, trace logger.
"""

from agent.roles.workspace import AgentWorkspace
from agent.roles.supervisor_agent import run_multi_agent

__all__ = ["AgentWorkspace", "run_multi_agent"]
