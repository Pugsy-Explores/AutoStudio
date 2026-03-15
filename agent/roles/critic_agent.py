"""Critic agent: analyze failures, diagnose cause, suggest retry strategy."""

from agent.meta.critic import diagnose
from agent.meta.evaluator import EvaluationResult

from agent.roles.base_role_agent import BaseRoleAgent
from agent.roles.workspace import AgentWorkspace


class CriticAgent(BaseRoleAgent):
    """Uses model_router (critique/SMALL) to produce diagnosis + retry instruction."""

    @property
    def name(self) -> str:
        return "critic"

    def run(self, workspace: AgentWorkspace) -> AgentWorkspace:
        self._emit_trace(workspace, "agent_started", {"agent": self.name})
        try:
            tr = workspace.test_results or {}
            reason = tr.get("stderr", "") or tr.get("stdout", "") or "tests failed"
            if len(reason) > 500:
                reason = reason[:500] + "..."
            evaluation = EvaluationResult(status="FAILURE", reason=reason, score=0.0)

            diagnosis = diagnose(workspace.state, evaluation)
            workspace.retry_instruction = diagnosis.suggestion
            self._emit_trace(
                workspace,
                "agent_completed",
                {"agent": self.name, "failure_type": diagnosis.failure_type},
            )
        except Exception as e:
            self._emit_trace(workspace, "agent_failed", {"agent": self.name, "error": str(e)})
            workspace.retry_instruction = "Retry with expanded search scope and revised edit."
        return workspace
