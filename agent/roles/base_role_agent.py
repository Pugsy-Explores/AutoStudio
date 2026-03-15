"""Base class for Phase 9 role agents. All agents use dispatch and trace_logger."""

from abc import ABC, abstractmethod

from agent.execution.step_dispatcher import dispatch
from agent.observability.trace_logger import log_event
from agent.roles.workspace import AgentWorkspace


class BaseRoleAgent(ABC):
    """Abstract base for planner, localization, edit, test, critic agents."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent identifier for trace events."""
        ...

    def _emit_trace(self, workspace: AgentWorkspace, event_type: str, payload: dict | None = None) -> None:
        """Emit trace event. Uses trace_id from workspace.state.context."""
        trace_id = workspace.state.context.get("trace_id")
        if trace_id:
            log_event(trace_id, event_type, payload or {})

    def _dispatch(self, workspace: AgentWorkspace, step: dict) -> dict:
        """Execute step via dispatcher. Returns { success, output, error }."""
        return dispatch(step, workspace.state)

    @abstractmethod
    def run(self, workspace: AgentWorkspace) -> AgentWorkspace:
        """Execute agent logic. Mutates workspace, returns updated workspace."""
        ...
